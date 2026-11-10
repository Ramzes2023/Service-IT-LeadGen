"""Server-native official FNS MSP snapshot: public ZIP -> PostgreSQL HOLD evidence.

NOT lead intake, NOT EGRUL/EGRIP verification and NOT permission to contact.
No GitHub API / GitHub Fernet ledger. No raw INNs are stored in PostgreSQL:
only per-receipt salted SHA-256. Downloads are strictly conditional on
real future-only card INNs and a separately mounted disk scratch directory.
Default OFF. Never writes Bitrix or personal/customer output in logs.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile
import time

import httpx

from .bitrix_1062_mapping import _is_valid_inn
from .bitrix_metadata_audit import validate_webhook
from .serviceit_bitrix_batch_worker import list_only
from .serviceit_bitrix_poll_fallback import (
    after_activation, calibrate_known_card, discover, date_value,
)
from .serviceit_stage4_fns_dedupe_watch import read_item
from .serviceit_enrichment_worker import PREFIX
from .serviceit_vps_pg_queue import db, secret_file, VpsBlocked
from .serviceit_fns_msp_official_live import (
    discover_official_url, fetch_archive, scan_zip_for_inns,
)

SCRATCH = Path("/var/cache/serviceit-msp")
MAX_CARDS = 20
INTERVAL_SECONDS = 86400
DDL = """
CREATE TABLE IF NOT EXISTS serviceit_vps_msp_snapshot (
    card_id BIGINT NOT NULL CHECK (card_id BETWEEN 8 AND 2000000000),
    publication DATE NOT NULL,
    inn_salt BYTEA NOT NULL CHECK(octet_length(inn_salt) = 16),
    inn_sha256 BYTEA NOT NULL CHECK(octet_length(inn_sha256) = 32),
    msp_category TEXT CHECK(msp_category IN ('MICRO', 'SMALL', 'MEDIUM')),
    source_zip_sha256 TEXT NOT NULL CHECK(source_zip_sha256 ~ '^[0-9a-f]{64}$'),
    verified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (card_id, publication)
);
"""


def migrate(connection):
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute(DDL)


def enabled(env):
    flag = env.get("SERVICEIT_VPS_FNS_MSP_ENABLED", "NO")
    if flag not in ("YES", "NO"):
        raise VpsBlocked("NATIVE_FNS_FLAG_INVALID")
    return flag == "YES"


def gate(env):
    from .serviceit_vps_worker import live_permitted
    if not live_permitted(env):
        raise VpsBlocked("NATIVE_FNS_CUTOVER_NOT_APPROVED")
    if not env.get("SERVICEIT_VPS_NATIVE_ACTIVATION_UTC"):
        raise VpsBlocked("NATIVE_FNS_ACTIVATION_MISSING")


def already_checked(connection, card, publication, inn):
    with connection.cursor() as cur:
        cur.execute(
            "SELECT inn_salt, inn_sha256 FROM serviceit_vps_msp_snapshot "
            "WHERE card_id=%s AND publication=%s",
            (card, publication))
        existing = cur.fetchone()
    if not existing:
        return False
    digest = hashlib.sha256(bytes(existing[0]) + inn.encode("ascii")).digest()
    if not secrets.compare_digest(digest, bytes(existing[1])):
        raise VpsBlocked("NATIVE_FNS_INN_CHANGED_SAME_CARD_HOLD")
    return True


def save_receipt(connection, card, publication, inn, category, checksum):
    if type(card) is not int or card < 8 or card in (3, 7):
        raise VpsBlocked("NATIVE_FNS_CARD_INVALID")
    if type(inn) is not str or not _is_valid_inn(inn):
        raise VpsBlocked("NATIVE_FNS_INN_INVALID")
    if category not in ("1", "2", "3", None):
        raise VpsBlocked("NATIVE_FNS_CATEGORY_INVALID")
    if type(checksum) is not str or len(checksum) != 64:
        raise VpsBlocked("NATIVE_FNS_SOURCE_DIGEST_INVALID")
    salt = secrets.token_bytes(16)
    digest = hashlib.sha256(salt + inn.encode("ascii")).digest()
    category_label = {"1": "MICRO", "2": "SMALL", "3": "MEDIUM"}.get(category)
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO serviceit_vps_msp_snapshot "
                "(card_id, publication, inn_salt, inn_sha256, "
                "msp_category, source_zip_sha256) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT(card_id, publication) DO NOTHING "
                "RETURNING card_id",
                (card, publication, salt, digest, category_label, checksum))
            return cur.fetchone() is not None


def scratch_ready() -> None:
    if SCRATCH.is_symlink() or not SCRATCH.is_dir():
        raise VpsBlocked("NATIVE_FNS_DISK_SCRATCH_REQUIRED")
    if not os.access(SCRATCH, os.W_OK):
        raise VpsBlocked("NATIVE_FNS_DISK_NOT_WRITABLE")


def calibrate_msp_verified_card(client, base, connection):
    """Independent positive control from a persisted VERIFIED 1062/21 B2B.

    Never trust an empty list as proof. CRM item.get is strictly read-only
    and validates real item/category/date; no historical ID 3 dependency.
    """
    with connection.cursor() as cur:
        cur.execute(
            "SELECT card_id FROM serviceit_vps_b2b_inbox "
            "WHERE state='VERIFIED' AND card_id IS NOT NULL "
            "ORDER BY received_at,source_id LIMIT 1")
        row = cur.fetchone()
    if not row or type(row[0]) is not int or row[0] < 8:
        raise VpsBlocked("NATIVE_FNS_NO_VERIFIED_POSITIVE_CONTROL")
    exact = read_item(client, base, row[0])
    date_value(exact.get("createdTime"))
    return True


def run_once(env, *, client: httpx.Client, connection,
             now: datetime | None = None) -> dict:
    out = {"status": "NATIVE_FNS_DISABLED", "crm_writes": 0,
           "new_cards_seen": 0, "valid_inn_candidates": 0,
           "receipts_inserted": 0, "official_zip_downloads": 0,
           "egrul_verified": False, "contact_authorized": False}
    if not enabled(env):
        return out
    gate(env)
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    base = validate_webhook(secret_file(env, "SERVICEIT_BITRIX_WEBHOOK"))
    start = after_activation({
        **env, "SERVICEIT_BITRIX_POLL_START_UTC":
            env["SERVICEIT_VPS_NATIVE_ACTIVATION_UTC"],
    }, now)
    read_only_list = lambda m,p: list_only(client, base, m,p)
    # FNS daily collector calibrates its OWN immutable runtime image.
    # The MSP container and AUX image are intentionally different releases.
    calibrate_msp_verified_card(client, base, connection)
    cards = discover(read_only_list, start, now)
    out["new_cards_seen"] = len(cards)
    if not cards:
        out["status"] = "NATIVE_FNS_NO_NEW_CARDS"
        return out
    eligible = []
    for card in cards[:MAX_CARDS]:
        item = read_item(client, base, card)
        created = date_value(item.get("createdTime"))
        if not start <= created <= now + timedelta(minutes=2):
            raise VpsBlocked("NATIVE_FNS_HISTORIC_CARD")
        inn = item.get(PREFIX+"COMPANY_INN")
        if type(inn) is str and _is_valid_inn(inn):
            eligible.append((card, inn))
    out["valid_inn_candidates"] = len(eligible)
    if not eligible:
        out["status"] = "NATIVE_FNS_NO_VALID_INNS"
        return out
    # The public FNS endpoint is NEVER queried with INNs.
    url, publication = discover_official_url(client, today=now.date())
    pending = [(card, inn) for card, inn in eligible
               if not already_checked(connection, card, publication, inn)]
    if not pending:
        out["status"] = "NATIVE_FNS_EXISTING_RECEIPTS"
        return out
    scratch_ready()
    with tempfile.TemporaryDirectory(prefix="snapshot-", dir=SCRATCH) as temp:
        target = Path(temp)/"official-msp.zip"
        archive = fetch_archive(client, url, publication, now.date(), target)
        matches, examined = scan_zip_for_inns(
            target, {inn for _,inn in pending})
        if examined <= 0:
            raise VpsBlocked("NATIVE_FNS_ZERO_OFFICIAL_DOCUMENTS")
    out["official_zip_downloads"] = 1
    for card,inn in pending:
        out["receipts_inserted"] += int(save_receipt(
            connection, card, publication, inn, matches.get(inn),
            archive["sha256"]))
    out["status"] = "NATIVE_FNS_SNAPSHOT_SAVED_PG_HOLD_ONLY"
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--once", action="store_true")
    g.add_argument("--loop", action="store_true")
    g.add_argument("--schema-only", action="store_true")
    args = p.parse_args(argv)
    env = dict(os.environ)
    if args.schema_only:
        try:
            with db(env) as conn:
                migrate(conn)
            print(json.dumps({"status":"NATIVE_FNS_SCHEMA_READY","crm_writes":0}))
            return 0
        except Exception:
            print(json.dumps({"status":"NATIVE_FNS_SCHEMA_BLOCKED","crm_writes":0}))
            return 2
    while True:
        try:
            with httpx.Client(trust_env=False, follow_redirects=False) as client:
                with db(env) as conn:
                    result = run_once(env, client=client, connection=conn)
            print(json.dumps(result, sort_keys=True), flush=True)
            if args.once:
                return 0
        except Exception:
            print(json.dumps({"status":"NATIVE_FNS_HOLD",
                              "crm_writes":0,"source_ingestion":0,
                              "formal_score_authorized":False}), flush=True)
            if args.once:
                return 2
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
