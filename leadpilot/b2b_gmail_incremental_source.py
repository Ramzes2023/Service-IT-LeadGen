"""Incremental read-only Gmail IMAP source for verified B2B-Center email notices.

Reads mailbox with BODY.PEEK[] and READ-ONLY SELECT; persists only sanitized
observations and digest/version history locally. NEVER modifies Gmail, Bitrix24,
a customer, a prospect, a bid or a contact. Does NOT claim FNS-qualified leads.
A user-approved Gmail app password or OAuth2 IMAP access token is supplied in
environment at process start; neither credential is stored or printed here.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import imaplib
import json
import os
from pathlib import Path
import re
import sqlite3
import ssl
import sys

from .b2b_email_opportunity_bridge import InputRejected, parse_email

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
SENDER = "marketplace@b2b-center.ru"
MAX_UIDS = 100
MAX_MESSAGE_BYTES = 256_000


class SourceBlocked(Exception):
    pass


def _require(ok: bool, reason: str):
    if not ok:
        raise SourceBlocked(reason)


def _uid_bytes(value):
    _require(isinstance(value, (bytes, bytearray)) and re.fullmatch(rb"[0-9]{1,20}", value),
             "INVALID_IMAP_UID")
    return bytes(value)


def _read_only_mail(imap, *, days: int = 14, max_messages: int = 60):
    _require(isinstance(days, int) and 1 <= days <= 30, "LOOKBACK_INVALID")
    _require(isinstance(max_messages, int) and 1 <= max_messages <= MAX_UIDS, "MESSAGE_BUDGET_INVALID")
    status, _ = imap.select("INBOX", readonly=True)
    _require(status == "OK", "MAILBOX_READ_ONLY_SELECT_FAILED")
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d-%b-%Y")
    status, data = imap.uid("SEARCH", None, "FROM", f'"{SENDER}"', "SINCE", since)
    _require(status == "OK" and isinstance(data, list) and len(data) == 1,
             "IMAP_SEARCH_FAILED")
    listing = data[0] or b""
    _require(isinstance(listing, bytes), "IMAP_SEARCH_FORMAT_INVALID")
    uids = listing.split()
    _require(len(uids) <= 5000, "MAILBOX_SEARCH_TOO_LARGE")
    unique = []
    for uid in uids:
        unique.append(_uid_bytes(uid))
    _require(len(unique) == len(set(unique)), "IMAP_DUPLICATE_UID")
    # Most recent first; no "mark as read": PEEK prevents setting Seen.
    for uid in reversed(unique[-max_messages:]):
        status, parts = imap.uid("FETCH", uid, "(BODY.PEEK[])")
        _require(status == "OK" and isinstance(parts, list), "IMAP_FETCH_FAILED")
        raw = [p[1] for p in parts if isinstance(p, tuple)
               and len(p) == 2 and isinstance(p[1], bytes)]
        _require(len(raw) == 1 and 0 < len(raw[0]) <= MAX_MESSAGE_BYTES,
                 "IMAP_MESSAGE_INVALID_OR_OVERSIZE")
        yield uid.decode("ascii"), raw[0]


def _schema(db: sqlite3.Connection):
    db.execute("PRAGMA busy_timeout=4000")
    db.execute("""CREATE TABLE IF NOT EXISTS notice_versions (
        mime_sha256 TEXT PRIMARY KEY,
        email_subject TEXT NOT NULL,
        first_received TEXT,
        first_seen TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS tender_observations (
        source_key TEXT NOT NULL,
        mime_sha256 TEXT NOT NULL REFERENCES notice_versions(mime_sha256),
        tender_id TEXT NOT NULL,
        subject TEXT NOT NULL,
        organizer TEXT,
        canonical_url TEXT NOT NULL,
        deadline_unverified TEXT,
        service_groups_json TEXT NOT NULL,
        PRIMARY KEY (source_key, mime_sha256)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS tender_index (
        source_key TEXT PRIMARY KEY,
        tender_id TEXT NOT NULL,
        current_subject TEXT NOT NULL,
        organizer TEXT,
        canonical_url TEXT NOT NULL,
        current_deadline_unverified TEXT,
        current_service_groups_json TEXT NOT NULL,
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL
    )""")


def ingest_one(db: sqlite3.Connection, raw: bytes, *, timestamp: str | None = None):
    """Transactional per-email ingestion with original message version retention."""
    parsed = parse_email(raw)
    digest = parsed["email_sha256"]
    observed_at = timestamp or datetime.now(timezone.utc).isoformat()
    row = db.execute("SELECT 1 FROM notice_versions WHERE mime_sha256=?", (digest,)).fetchone()
    if row:
        return {"new_email": 0, "new_tenders": 0, "updated_tenders": 0,
                "it_tenders": 0, "duplicate_email": 1}
    new_count = changed_count = it_count = 0
    with db:
        db.execute("INSERT INTO notice_versions VALUES (?,?,?,?)",
                   (digest, parsed["subject"], parsed["email_received_at"], observed_at))
        for item in parsed["observations"]:
            _require(item["source"] == "B2B-Center"
                     and re.fullmatch(r"\d{5,12}", item["tender_id"]) is not None,
                     "SOURCE_TENDER_ID_INVALID")
            key = "B2B-Center:" + item["tender_id"]
            labels = json.dumps(item["source_service_groups"], ensure_ascii=False)
            db.execute("INSERT INTO tender_observations VALUES (?,?,?,?,?,?,?,?)",
                       (key, digest, item["tender_id"], item["subject"],
                        item["organizer_as_named_in_email"], item["source_url"],
                        item["deadline_as_stated_in_email"], labels))
            existing = db.execute("""SELECT current_subject, organizer, canonical_url,
                current_deadline_unverified, current_service_groups_json
                FROM tender_index WHERE source_key=?""", (key,)).fetchone()
            current = (item["subject"], item["organizer_as_named_in_email"],
                       item["source_url"], item["deadline_as_stated_in_email"], labels)
            if existing is None:
                new_count += 1
                db.execute("INSERT INTO tender_index VALUES (?,?,?,?,?,?,?,?,?)",
                           (key, item["tender_id"], *current, observed_at, observed_at))
            else:
                if tuple(existing) != current:
                    changed_count += 1
                    db.execute("""UPDATE tender_index SET current_subject=?, organizer=?,
                        canonical_url=?, current_deadline_unverified=?,
                        current_service_groups_json=?, last_seen=? WHERE source_key=?""",
                               (*current, observed_at, key))
                else:
                    db.execute("UPDATE tender_index SET last_seen=? WHERE source_key=?",
                               (observed_at, key))
            if item["source_service_groups"]:
                it_count += 1
    return {"new_email": 1, "new_tenders": new_count,
            "updated_tenders": changed_count, "it_tenders": it_count,
            "duplicate_email": 0}


def review_queue(db: sqlite3.Connection, *, limit=100):
    _require(1 <= limit <= 100, "REVIEW_LIMIT_INVALID")
    rows = db.execute("""SELECT tender_id, current_subject, organizer, canonical_url,
        current_deadline_unverified, current_service_groups_json,
        first_seen, last_seen
        FROM tender_index ORDER BY last_seen DESC, tender_id DESC LIMIT ?""",
        (limit,)).fetchall()
    items = []
    for n, title, org, url, deadline, groups_json, first, last in rows:
        groups = json.loads(groups_json)
        if not groups:
            continue
        # Research queue prioritizes possible private 1C buyers, but the
        # company name and SME status remain UNVERIFIED.
        private_form_unverified = bool(
            re.match(r'^\s*ООО(?:\s|[«"])', org or "", re.I))
        lead_research_candidate = private_form_unverified and "1C" in groups
        items.append({
            "source": "B2B-Center", "tender_id": n,
            "subject": title, "organizer_as_named_in_email": org,
            "source_url": url, "deadline_as_stated_in_email_unverified": deadline,
            "source_service_groups": groups, "first_observed_at": first,
            "last_observed_at": last, "published_at": None,
            "buyer_inn": None, "fns_msp": "NOT_VERIFIED",
            "buyer_region": "NOT_VERIFIED", "source_status": "NOT_VERIFIED",
            "corporate_contact": "NOT_VERIFIED",
            "classification": "TENDER_OPPORTUNITY_HOLD",
            "lead_research_candidate": bool(lead_research_candidate),
            "review_priority": ("P1_1C_PRIVATE_BUYER_RESEARCH" if lead_research_candidate
                                else "P2_TENDER_RESEARCH"),
            "next_gate": "VERIFY_BUYER_INN_REGION_FNS_DEMAND_CONTACT_DUPLICATES_STOP",
            "qualified_lead": False,
            "crm_upload_authorized": False,
        })
    return sorted(items, key=lambda item: (
        not item["lead_research_candidate"], item["tender_id"]))


def run_poll(user: str, password: str, state_dir: Path, *, client_factory=None,
             days=14, max_messages=60):
    _require(isinstance(user, str) and re.fullmatch(r"[^@\s]+@gmail\.com", user, re.I),
             "GMAIL_ACCOUNT_INVALID")
    _require(isinstance(password, str) and 12 <= len(password) < 4096,
             "GMAIL_AUTH_SECRET_NOT_CONFIGURED")
    state_dir = Path(state_dir).resolve()
    _require(state_dir.is_absolute() and state_dir.name == "B2B-Gmail-Source"
             and "ServiceIT" in state_dir.parts, "STATE_DIR_NOT_APPROVED")
    state_dir.mkdir(parents=True, exist_ok=True)
    database = state_dir / "source-observations.sqlite3"
    summary = {"status": "B2B_GMAIL_LOCAL_INGESTION",
               "new_emails": 0, "duplicate_emails": 0,
               "new_tenders": 0, "updated_tenders": 0,
               "new_it_observations": 0, "rejected_messages": 0,
               "qualified_leads": 0, "crm_writes": 0,
               "gmail_modifications": 0, "outreach": 0}
    factory = client_factory or (lambda: imaplib.IMAP4_SSL(
        IMAP_HOST, IMAP_PORT, ssl_context=ssl.create_default_context(), timeout=20))
    with sqlite3.connect(database, timeout=5) as db:
        _schema(db)
        with db:
            db.execute("BEGIN IMMEDIATE")
        conn = factory()
        try:
            status, _ = conn.login(user, password)
            _require(status == "OK", "GMAIL_AUTH_DENIED")
            for uid, raw in _read_only_mail(conn, days=days, max_messages=max_messages):
                try:
                    result = ingest_one(db, raw)
                except (InputRejected, ValueError):
                    summary["rejected_messages"] += 1
                    continue
                summary["new_emails"] += result["new_email"]
                summary["duplicate_emails"] += result["duplicate_email"]
                summary["new_tenders"] += result["new_tenders"]
                summary["updated_tenders"] += result["updated_tenders"]
                summary["new_it_observations"] += (
                    result["it_tenders"] if result["new_email"] else 0)
        finally:
            try:
                conn.logout()
            except (imaplib.IMAP4.error, OSError):
                pass
        queue = review_queue(db)
        # Atomic overwrite, local-only. Never includes MIME body, tracking URL or secret.
        target = state_dir / "b2b-review-queue.json"
        temporary = state_dir / "b2b-review-queue.pending"
        with temporary.open("w", encoding="utf-8") as output:
            json.dump({"status": "REVIEW_ONLY_NOT_VERIFIED_LEADS",
                       "total_review_it": len(queue), "qualified_leads": 0,
                       "crm_writes": 0, "items": queue}, output,
                      ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    summary["total_it_review_queue"] = len(queue)
    summary["output_file"] = "LOCALAPPDATA/ServiceIT/B2B-Gmail-Source/b2b-review-queue.json"
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only source: B2B-Center Gmail IMAP")
    parser.add_argument("--poll", action="store_true", required=True)
    parser.add_argument("--lookback-days", type=int, default=14)
    parser.add_argument("--max-messages", type=int, default=60)
    args = parser.parse_args(argv)
    try:
        user = os.environ.pop("SERVICEIT_B2B_GMAIL_ADDRESS", "")
        password = os.environ.pop("SERVICEIT_B2B_GMAIL_APP_PASSWORD", "")
        base = os.environ.get("LOCALAPPDATA", "")
        _require(bool(base), "WINDOWS_LOCALAPPDATA_REQUIRED")
        out = run_poll(user, password, Path(base) / "ServiceIT" / "B2B-Gmail-Source",
                       days=args.lookback_days, max_messages=args.max_messages)
        print(json.dumps(out, ensure_ascii=False))
        return 0
    except (SourceBlocked, imaplib.IMAP4.error, TimeoutError, OSError,
            sqlite3.Error, ValueError, ssl.SSLError):
        # No details (credentials and local email headers must never leak).
        print(json.dumps({"status": "B2B_GMAIL_SOURCE_BLOCKED",
                          "crm_writes": 0, "outreach": 0,
                          "secret_exposed": False, "do_not_assume_ingested": True}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
