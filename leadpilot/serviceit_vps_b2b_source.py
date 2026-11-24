"""B2B-Center Gmail notices -> VPS PostgreSQL -> ONE verified Bitrix HOLD.

Independent Gmail readonly OAuth (never ChatGPT connector tokens), no GitHub
event transport. Source OFF unless explicitly configured. This adapter never
qualifies buyers, contacts customers or reprocesses prior mailbox history.
Its CRM write is a distinct one-use POST; a timeout is POST_UNCERTAIN forever
until manual READONLY reconciliation. Native five-minute worker handles AI.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
import json
import os
import re
import time

import httpx

from .b2b_email_opportunity_bridge import parse_email, clean_tender_url
from .b2b_github_to_bitrix import (
    BridgeBlocked, hold_payload, exact_duplicate, bitrix_call,
    SIGNAL, KEY,
)
from .b2b_bitrix_readiness import ReadOnlyBitrix, preflight
from .bitrix_metadata_audit import validate_webhook
from .serviceit_b2b_hold_ai_fields import AI_FIELDS
from .serviceit_vps_pg_queue import db, insert_new, secret_file, VpsBlocked
from .serviceit_vps_worker import live_permitted

INTERVAL_SECONDS = 300
MAX_PER_CYCLE = 1
MAX_PER_DAY = 3
GMAIL_ROOT = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SENDER = "marketplace@b2b-center.ru"
DDL = """
CREATE TABLE IF NOT EXISTS serviceit_vps_b2b_deliveries (
 source_id TEXT PRIMARY KEY CHECK(source_id ~ '^B2B-CENTER-[0-9]{5,12}$'),
 state TEXT NOT NULL CHECK(state IN
 ('POST_UNCERTAIN','VERIFIED','EXISTING_IN_CRM')),
 card_id BIGINT CHECK(card_id BETWEEN 8 AND 2000000000),
 first_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""
class SourceBlocked(ValueError):
    pass


def must(yes: bool, code: str):
    if not yes:
        raise SourceBlocked(code)


def enabled(env: dict) -> tuple[bool, bool]:
    source = env.get("SERVICEIT_VPS_B2B_SOURCE_ENABLED", "NO")
    writer = env.get("SERVICEIT_VPS_B2B_CRM_WRITE_ENABLED", "NO")
    must(source in ("NO", "YES") and writer in ("NO", "YES"),
         "INVALID_SOURCE_FLAGS")
    must(not (writer == "YES" and source != "YES"), "SOURCE_DISABLED_WRITE_FORBIDDEN")
    if source == "NO":
        return False, False
    must(live_permitted(env), "VPS_NATIVE_LIVE_CUTOVER_REQUIRED")
    must(env.get("SERVICEIT_VPS_INTAKE_ENABLED", "NO") == "NO",
         "PARALLEL_CRM_INGRESS_NOT_ALLOWED")
    must(env.get("SERVICEIT_VPS_B2B_LEGACY_WRITERS_DISABLED") == "YES",
         "LEGACY_GH_WRITERS_NOT_CONFIRMED_DISABLED")
    raw = env.get("SERVICEIT_VPS_B2B_ACTIVATION_UTC", "")
    try:
        since = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        must(since.tzinfo is not None, "SOURCE_ACTIVATION_TZ_REQUIRED")
        age = (datetime.now(timezone.utc) - since.astimezone(timezone.utc)).total_seconds()
    except (ValueError, TypeError):
        raise SourceBlocked("SOURCE_ACTIVATION_INVALID") from None
    must(0 <= age <= 86400 * 14, "SOURCE_ACTIVATION_MUST_BE_RECENT")
    return True, writer == "YES"


def token(client: httpx.Client, env: dict) -> str:
    form = {
        "client_id": secret_file(env, "SERVICEIT_B2B_GMAIL_CLIENT_ID"),
        "client_secret": secret_file(env, "SERVICEIT_B2B_GMAIL_CLIENT_SECRET"),
        "refresh_token": secret_file(env, "SERVICEIT_B2B_GMAIL_REFRESH_TOKEN"),
        "grant_type": "refresh_token",
    }
    res = client.post(TOKEN_URL, data=form, timeout=15, follow_redirects=False)
    must(res.status_code == 200 and len(res.content) <= 16384,
         "GOOGLE_TOKEN_REFRESH_UNAVAILABLE")
    data = res.json()
    access = data.get("access_token") if isinstance(data, dict) else None
    must(isinstance(access, str) and 20 < len(access) <= 4096,
         "GOOGLE_ACCESS_TOKEN_INVALID")
    return access


def gmail_get(client, bearer, endpoint, params=None):
    must(endpoint in ("profile", "messages")
         or re.fullmatch(r"messages/[A-Za-z0-9_-]{8,128}", endpoint) is not None,
         "GMAIL_ENDPOINT_DENIED")
    res = client.get(GMAIL_ROOT + "/" + endpoint,
                     headers={"Authorization": "Bearer " + bearer},
                     params=params, timeout=15, follow_redirects=False)
    must(res.status_code == 200 and len(res.content) <= 700000,
         "GMAIL_REQUEST_FAILED")
    data = res.json()
    must(type(data) is dict, "GMAIL_FORMAT_INVALID")
    return data


def verified_google_sender(raw: bytes):
    must(len(raw) <= 256000, "GMAIL_MIME_OVERSIZED")
    message = BytesParser(policy=policy.default).parsebytes(raw)
    must(parseaddr(str(message.get("From", "")))[1].lower() == SENDER,
         "SENDER_UNEXPECTED")
    results = message.get_all("Authentication-Results", [])
    # Gmail's own MX-authenticated DKIM+DMARC verdict, NOT vendor's
    # untrusted self-declared headers. If unavailable, block the notice.
    good = False
    for item in results:
        val = " ".join(str(item).split())
        if (val.startswith("mx.google.com;")
            and re.search(r"\bdkim=pass\b", val)
            and re.search(r"\bheader\.i=@b2b-center\.ru\b", val)
            and re.search(r"\bdmarc=pass\b", val)
            and re.search(r"\bheader\.from=b2b-center\.ru\b", val)):
            good = True
    must(good, "GMAIL_AUTHENTICATION_NOT_PROVEN")


def recent_observations(client, bearer, env):
    profile = gmail_get(client, bearer, "profile")
    expected = env.get("SERVICEIT_VPS_B2B_GMAIL_ACCOUNT", "")
    must(bool(re.fullmatch(r"[^@\s]+@gmail\.com", expected, re.I))
         and profile.get("emailAddress", "").lower() == expected.lower(),
         "GMAIL_ACCOUNT_IDENTITY_MISMATCH")
    since = datetime.fromisoformat(
        env["SERVICEIT_VPS_B2B_ACTIVATION_UTC"].replace("Z", "+00:00")
    ).astimezone(timezone.utc)
    # Gmail SEARCH is coarse. Server internalDate is the authoritative
    # cutoff; never replay historical B2B-Center messages.
    query = "from:" + SENDER + " after:" + since.strftime("%Y/%m/%d")
    listing = gmail_get(client, bearer, "messages",
                        {"q": query, "maxResults": 100})
    must(not listing.get("nextPageToken"), "GMAIL_PAGINATION_UNBOUNDED")
    messages = listing.get("messages", [])
    must(type(messages) is list and len(messages) <= 100,
         "GMAIL_MESSAGE_LIST_INVALID")
    ids = []
    for item in messages:
        mid = item.get("id") if type(item) is dict else None
        must(type(mid) is str and
             re.fullmatch(r"[A-Za-z0-9_-]{8,128}", mid) is not None,
             "GMAIL_MESSAGE_ID_INVALID")
        ids.append(mid)
    must(len(ids) == len(set(ids)), "GMAIL_DUPLICATE_MESSAGE_ID")
    found = {}
    for mid in ids:
        msg = gmail_get(client, bearer, "messages/" + mid,
                        {"format": "raw"})
        must(msg.get("id") == mid, "GMAIL_MESSAGE_READBACK_MISMATCH")
        date = msg.get("internalDate", "")
        must(type(date) is str and date.isdecimal(),
             "GMAIL_INTERNAL_DATE_INVALID")
        when = datetime.fromtimestamp(int(date) / 1000, tz=timezone.utc)
        if when <= since:
            continue
        encoded = msg.get("raw", "")
        must(type(encoded) is str and len(encoded) <= 400000,
             "GMAIL_RAW_INVALID")
        try:
            raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        except (ValueError, TypeError):
            raise SourceBlocked("GMAIL_RAW_BASE64_INVALID") from None
        verified_google_sender(raw)
        parsed = parse_email(raw)
        for row in parsed["observations"]:
            if not row["source_service_groups"]:
                continue
            tid = row.get("tender_id", "")
            if not isinstance(tid, str) or not re.fullmatch(r"\d{5,12}", tid):
                continue
            if clean_tender_url(row["source_url"]) != (row["source_url"], tid):
                continue
            # Never call the same procedure a new lead based on repeated mail.
            found.setdefault(tid, row)
    return [found[k] for k in sorted(found, reverse=True)]


def notice_for(row):
    return {
        "source": "B2B-Center",
        "tender_id": row["tender_id"],
        "source_url": row["source_url"],
        "subject": row["subject"],
        "organizer": row.get("organizer_as_named_in_email") or "НЕ УСТАНОВЛЕН",
        "buyer_inn": None,
        "fns_msp": None,
        "region": None,
        "original_publication_verified": False,
        "contact_approved": False,
        "qualified_lead": False,
    }


def reservation(connection, source_id) -> bool:
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute("INSERT INTO serviceit_vps_b2b_deliveries(source_id,state) "
                        "VALUES (%s,'POST_UNCERTAIN') "
                        "ON CONFLICT(source_id) DO NOTHING RETURNING source_id",
                        (source_id,))
            return cur.fetchone() is not None


def day_budget(connection) -> int:
    with connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM serviceit_vps_b2b_deliveries "
                    "WHERE state!='EXISTING_IN_CRM' AND first_seen > now()-INTERVAL '24 hours'")
        return int(cur.fetchone()[0])


def mark(connection, source_id, state, card=None):
    must(state in ("VERIFIED", "EXISTING_IN_CRM"), "SOURCE_STATE_INVALID")
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute("UPDATE serviceit_vps_b2b_deliveries "
                        "SET state=%s, card_id=%s, updated_at=now() "
                        "WHERE source_id=%s AND state='POST_UNCERTAIN'",
                        (state, card, source_id))


def write_hold(client, connection, webhook, row):
    payload = hold_payload(notice_for(row), row["source_service_groups"])
    fields = payload["fields"]
    # RouterAI native VPS, not this source, owns the nine actual AI fields.
    for key in AI_FIELDS:
        fields.pop(key)
    source_id = fields[SIGNAL]
    base = validate_webhook(webhook)
    # Demonstrate that source-ID CRM listing can actually see the old
    # known item; otherwise a false empty would cause duplicate creation.
    must(len(exact_duplicate(client, base, "B2B-CENTER-4665948")) == 1,
         "CRM_DUPLICATE_POSITIVE_CONTROL_FAILED")
    prior = exact_duplicate(client, base, source_id)
    if prior:
        if reservation(connection, source_id):
            mark(connection, source_id, "EXISTING_IN_CRM")
        return "ALREADY_IN_CRM"
    if not reservation(connection, source_id):
        return "ALREADY_RESERVED_NO_RETRY"
    # At this point all transport ambiguity leaves POST_UNCERTAIN permanently.
    added = bitrix_call(client, base, "crm.item.add", payload)
    result = added["result"].get("item")
    must(type(result) is dict and type(result.get("id")) is int
         and result["id"] >= 8, "CRM_ADD_AMBIGUOUS_NO_RETRY")
    card = result["id"]
    exact = bitrix_call(client, base, "crm.item.get", {
        "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y"
    })["result"].get("item")
    must(type(exact) is dict and str(exact.get("id")) == str(card)
         and str(exact.get("entityTypeId")) == "1062"
         and str(exact.get("categoryId")) == "21"
         and all(exact.get(k) == v for k, v in fields.items()),
         "CRM_READBACK_UNCERTAIN_NO_RETRY")
    mark(connection, source_id, "VERIFIED", card)
    # Native coordinator has 5-minute REST discovery as fallback; explicit
    # durable handoff avoids missing the first card at a poll boundary.
    insert_new(connection, card)
    return "HOLD_VERIFIED_NATIVE_QUEUE"


def run_once(env, *, client):
    on, write = enabled(env)
    if not on:
        return {"status": "B2B_SOURCE_DISABLED", "crm_writes": 0}
    with db(env) as connection:
        with connection.transaction():
            with connection.cursor() as cur:
                cur.execute(DDL)
        bearer = token(client, env)
        source = recent_observations(client, bearer, env)
        if not write:
            return {"status": "B2B_GMAIL_READONLY", "observations": len(source),
                    "crm_writes": 0}
        if day_budget(connection) >= MAX_PER_DAY:
            return {"status": "B2B_DAILY_BUDGET_REACHED", "crm_writes": 0}
        base = secret_file(env, "SERVICEIT_BITRIX_WEBHOOK")
        preflight(ReadOnlyBitrix(base, client=client))
        output = {"status": "B2B_VPS_LIVE_CYCLE", "new_holds": 0,
                  "already_seen": 0, "crm_writes": 0}
        # Do not let an already-reserved newest tender starve older distinct
        # notices. Limit actual new CRM writes, not scanned messages.
        for row in source:
            if output["new_holds"] >= MAX_PER_CYCLE:
                break
            # Invalid or risky vendor descriptions are skipped, not coerced.
            try:
                hold_payload(notice_for(row), row["source_service_groups"])
            except (BridgeBlocked, ValueError, TypeError):
                continue
            kind = write_hold(client, connection, base, row)
            output["new_holds"] += int(kind == "HOLD_VERIFIED_NATIVE_QUEUE")
            output["already_seen"] += int(kind != "HOLD_VERIFIED_NATIVE_QUEUE")
            output["crm_writes"] += int(kind == "HOLD_VERIFIED_NATIVE_QUEUE")
        return output


def main():
    env = dict(os.environ)
    while True:
        started = time.monotonic()
        try:
            with httpx.Client(trust_env=False, follow_redirects=False) as client:
                result = run_once(env, client=client)
            print(json.dumps(result, sort_keys=True), flush=True)
        except Exception:
            print(json.dumps({"status": "B2B_SOURCE_BLOCKED_OR_UNCERTAIN",
                              "no_blind_retry": True,
                              "crm_writes": "UNVERIFIED"}), flush=True)
        time.sleep(max(0, INTERVAL_SECONDS - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
