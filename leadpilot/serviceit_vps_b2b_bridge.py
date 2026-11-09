"""VPS-only signed Apps Script B2B-Center -> PostgreSQL inbox + guarded HOLD delivery.

No GitHub event transport, no Gmail OAuth on VPS and no source CRM write
in the public ingress. All database changes precede any external CRM POST.
Uncertain POST is NEVER retried automatically. Source is OFF by default.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from datetime import datetime, timezone

import httpx

from .serviceit_b2b_appsscript_ingest import (
    validate_dispatch, IngestBlocked, EVENT_TYPE, REPO,
)
from .b2b_github_to_bitrix import (
    notice_from_event, hold_payload, exact_duplicate, bitrix_call,
    BridgeBlocked, SIGNAL,
)
from .b2b_bitrix_readiness import ReadOnlyBitrix, preflight
from .serviceit_b2b_hold_ai_fields import AI_FIELDS
from .serviceit_vps_pg_queue import db, insert_new, secret_file, VpsBlocked
from .serviceit_vps_worker import live_permitted
from .b2b_email_opportunity_bridge import classify_services
from .bitrix_metadata_audit import validate_webhook

PATH = "/integrations/serviceit/b2b-center/notice"
MAX_BODY = 6000
MAX_PER_CYCLE = 4
MAX_PER_24H = 240
INTERVAL_SECONDS = 300
DDL = """
CREATE TABLE IF NOT EXISTS serviceit_vps_b2b_inbox (
 source_id TEXT PRIMARY KEY CHECK (source_id ~ '^B2B-CENTER-[0-9]{5,12}$'),
 notice_json JSONB NOT NULL,
 state TEXT NOT NULL DEFAULT 'PENDING'
   CHECK(state IN ('PENDING','POST_UNCERTAIN','VERIFIED','EXISTING','REVIEW')),
 card_id BIGINT CHECK(card_id BETWEEN 8 AND 2000000000),
 received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 write_started_at TIMESTAMPTZ,
 completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS serviceit_vps_b2b_inbox_pending_idx
 ON serviceit_vps_b2b_inbox(state,received_at);
"""


class B2BBlocked(ValueError):
    pass


def must(condition, code):
    if not condition:
        raise B2BBlocked(code)


def enabled(env: dict, *, writer=False) -> bool:
    names = ("SERVICEIT_VPS_B2B_RECEIVER_ENABLED", "SERVICEIT_VPS_B2B_WRITER_ENABLED")
    for key in names:
        must(env.get(key, "NO") in ("NO", "YES"), "B2B_FLAG_INVALID")
    if env.get("SERVICEIT_VPS_B2B_RECEIVER_ENABLED") != "YES":
        return False
    must(env.get("SERVICEIT_VPS_B2B_GH_WRITERS_DISABLED_ACK") == "YES",
         "B2B_GH_WRITERS_MUST_BE_OFF")
    if writer:
        must(env.get("SERVICEIT_VPS_B2B_WRITER_ENABLED") == "YES",
             "B2B_WRITER_NOT_AUTHORIZED")
        must(live_permitted(env), "B2B_NATIVE_CUTOVER_NOT_LIVE")
    return True


def signed_notice(payload: dict, key: str, *, now: float | None = None):
    """Reuse existing, tested Apps Script/GitHub envelope without Github IO."""
    must(type(payload) is dict and set(payload) ==
         {"timestamp", "payload_json", "signature"}, "B2B_ENVELOPE_INVALID")
    event = {
        "action": EVENT_TYPE,
        "repository": {"full_name": REPO},
        "client_payload": payload,
    }
    synthetic, dispatch_id = validate_dispatch(event, key, now=now)
    notice, groups = notice_from_event(synthetic)
    must(bool(groups), "B2B_SOURCE_RELEVANCE_REQUIRED")
    return notice, groups, dispatch_id


def verified_probe(payload, key, *, now=None):
    """Side-effect-free proof that Apps Script and VPS share an HMAC key."""
    must(type(payload) is dict and set(payload) ==
         {"timestamp", "nonce", "signature"}, "B2B_PROBE_SHAPE_INVALID")
    ts, nonce, sig = (payload.get(k) for k in
                      ("timestamp", "nonce", "signature"))
    must(type(ts) is str and re.fullmatch(r"[0-9]{10,12}", ts),
         "B2B_PROBE_TIME_INVALID")
    must(type(nonce) is str and re.fullmatch(r"[a-zA-Z0-9-]{20,80}", nonce),
         "B2B_PROBE_NONCE_INVALID")
    must(type(sig) is str and re.fullmatch(r"sha256=[0-9a-f]{64}", sig),
         "B2B_PROBE_SIGNATURE_INVALID")
    must(type(key) is str and len(key) >= 32, "B2B_PROBE_KEY_MISSING")
    must(abs((time.time() if now is None else now) - int(ts)) <= 300,
         "B2B_PROBE_EXPIRED")
    expected = "sha256=" + hmac.new(
        key.encode("utf-8"),
        ("v1|b2b-probe|" + ts + "|" + nonce).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    must(hmac.compare_digest(sig, expected), "B2B_PROBE_HMAC_MISMATCH")
    return True


def migrate(connection):
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute(DDL)


def accept(connection, notice: dict, groups: list[str]) -> bool:
    # Store only signed, already sanitized public tender fields.
    source = "B2B-CENTER-" + notice["tender_id"]
    safe = json.dumps(notice, ensure_ascii=False, separators=(",", ":"))
    must(len(safe.encode("utf-8")) <= 3500, "B2B_NOTICE_TOO_LARGE")
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO serviceit_vps_b2b_inbox(source_id,notice_json) "
                "VALUES(%s,%s::jsonb) "
                "ON CONFLICT(source_id) DO NOTHING RETURNING source_id",
                (source, safe))
            return cur.fetchone() is not None


def statistics(connection) -> dict:
    with connection.cursor() as cur:
        cur.execute("SELECT state,count(*) FROM serviceit_vps_b2b_inbox GROUP BY state")
        return {str(a): int(b) for a,b in cur.fetchall()}


def read_candidate(connection):
    """Read-only: work is only claimed after positive CRM controls have passed."""
    with connection.cursor() as cur:
        cur.execute(
            "SELECT source_id,notice_json FROM serviceit_vps_b2b_inbox "
            "WHERE state='PENDING' ORDER BY received_at,source_id LIMIT 1"
        )
        row = cur.fetchone()
    if not row:
        return None
    data = row[1] if isinstance(row[1], dict) else json.loads(row[1])
    return str(row[0]), data


def set_existing(connection, source):
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE serviceit_vps_b2b_inbox SET state='EXISTING',completed_at=now() "
                "WHERE source_id=%s AND state='PENDING' RETURNING source_id",
                (source,))
            return cur.fetchone() is not None


def set_review(connection, source):
    """Terminal safety hold for unparseable notice, not an external CRM action."""
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE serviceit_vps_b2b_inbox SET state='REVIEW', completed_at=now() "
                "WHERE source_id=%s AND state='PENDING' RETURNING source_id",
                (source,))
            return cur.fetchone() is not None


def reserve_post(connection, source):
    """Commit irreversible POST_UNCERTAIN before issuing external mutation."""
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE serviceit_vps_b2b_inbox SET state='POST_UNCERTAIN',"
                "write_started_at=now() "
                "WHERE source_id=%s AND state='PENDING' "
                "RETURNING source_id", (source,))
            return cur.fetchone() is not None


def confirm(connection, source, card):
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE serviceit_vps_b2b_inbox SET state='VERIFIED',card_id=%s,"
                "completed_at=now() WHERE source_id=%s AND state='POST_UNCERTAIN' "
                "RETURNING source_id", (card, source))
            must(cur.fetchone() is not None, "B2B_POST_ACK_STATE_UNVERIFIED")


def attempted_24h(connection):
    with connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM serviceit_vps_b2b_inbox "
            "WHERE write_started_at > now()-INTERVAL '24 hours'")
        return int(cur.fetchone()[0])


def deliver_one(connection, client, webhook):
    if attempted_24h(connection) >= MAX_PER_24H:
        return "DAILY_BUDGET_THROTTLED"
    candidate = read_candidate(connection)
    if candidate is None:
        return "NO_PENDING"
    source, notice = candidate
    try:
        payload = hold_payload(notice, classify_services(notice["subject"]))
        must(payload["fields"][SIGNAL] == source, "B2B_SOURCE_ID_CHANGED")
    except (BridgeBlocked, B2BBlocked, KeyError, TypeError, ValueError):
        return "REVIEW" if set_review(connection, source) else "ALREADY_RESERVED"
    base = validate_webhook(webhook)
    # Existing known source MUST be visible (the portal once gave false empties).
    must(len(exact_duplicate(client, base, "B2B-CENTER-4665948")) == 1,
         "B2B_POSITIVE_CRM_DUPLICATE_CONTROL_FAILED")
    if exact_duplicate(client, base, source):
        return "EXISTING" if set_existing(connection, source) else "ALREADY_RESERVED"
    must(reserve_post(connection, source), "B2B_SOURCE_RESERVED_ELSEWHERE")
    for name in AI_FIELDS:
        payload["fields"].pop(name)
    # Irreversible external side effect: no automatic re-POST in any exception.
    result = bitrix_call(client, base, "crm.item.add", payload)
    obj = result["result"].get("item")
    must(type(obj) is dict and type(obj.get("id")) is int and obj["id"] >= 8,
         "B2B_CRM_ADD_UNCERTAIN")
    card = obj["id"]
    actual = bitrix_call(client, base, "crm.item.get", {
        "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y",
    })["result"].get("item")
    must(type(actual) is dict and str(actual.get("id")) == str(card)
         and str(actual.get("entityTypeId")) == "1062"
         and str(actual.get("categoryId")) == "21"
         and all(actual.get(k) == v for k,v in payload["fields"].items()),
         "B2B_CRM_READBACK_UNCERTAIN")
    confirm(connection, source, card)
    # The normal VPS coordinator polls new CRM cards every 5min as a fallback.
    # Explicit new-card id bypasses any discovery timing uncertainty.
    insert_new(connection, card)
    return "ONE_REAL_HOLD_QUEUED"


def worker_once(env: dict, *, client):
    """Drain a bounded batch every five minutes; PENDING resumes automatically.

    Not a one-time trial: all future signed notices remain in the queue.
    A rolling protective budget prevents runaway API/model charges.
    """
    if not enabled(env, writer=True):
        return {"status": "B2B_WRITER_DISABLED", "crm_writes": 0}
    with db(env) as connection:
        migrate(connection)
        webhook = secret_file(env, "SERVICEIT_BITRIX_WEBHOOK")
        preflight(ReadOnlyBitrix(webhook, client=client))
        outcomes = []
        for _ in range(MAX_PER_CYCLE):
            outcome = deliver_one(connection, client, webhook)
            outcomes.append(outcome)
            if outcome in ("NO_PENDING", "DAILY_BUDGET_THROTTLED"):
                break
        return {
            "status": "B2B_VPS_CONTINUOUS_CYCLE",
            "batch_processed": len(outcomes),
            "new_holds": outcomes.count("ONE_REAL_HOLD_QUEUED"),
            "existing": outcomes.count("EXISTING"),
            "review": outcomes.count("REVIEW"),
            "queue_counts": statistics(connection),
            "backlog_throttled": "DAILY_BUDGET_THROTTLED" in outcomes,
            "qualified_leads": 0,
            "outbound_contacts": 0,
        }

def main():
    env = dict(os.environ)
    while True:
        started = time.monotonic()
        try:
            with httpx.Client(trust_env=False, follow_redirects=False) as client:
                print(json.dumps(worker_once(env, client=client),sort_keys=True),
                      flush=True)
        except Exception:
            print(json.dumps({"status": "B2B_WORKER_BLOCKED_OR_UNCERTAIN",
                              "no_blind_retry": True,
                              "crm_writes": "UNVERIFIED"}), flush=True)
        time.sleep(max(0, INTERVAL_SECONDS - (time.monotonic()-started)))


if __name__ == "__main__":
    main()
