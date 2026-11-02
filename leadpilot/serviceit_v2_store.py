"""PostgreSQL-backed, read-only procurement intake. NO CRM writes or outreach.

One Gmail message is committed atomically with all of its tender observations.
Two distinct mail messages may reference one opportunity; revisions are
retained. False positives and unverified buyers never become qualified leads.
"""
from __future__ import annotations

from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from hashlib import sha256
import json
import re

from .b2b_email_opportunity_bridge import InputRejected, parse_email

ORIGIN = "B2B-Center"
APPROVED_SENDER = "marketplace@b2b-center.ru"
STATES = ("OUT_OF_SCOPE", "TENDER_OPPORTUNITY_HOLD")
DDL = """
CREATE TABLE IF NOT EXISTS serviceit_v2_messages (
  gmail_message_id text PRIMARY KEY,
  mime_sha256 char(64) NOT NULL,
  received_at timestamptz NULL,
  first_seen timestamptz NOT NULL DEFAULT now(),
  observations integer NOT NULL CHECK (observations >= 0)
);
CREATE TABLE IF NOT EXISTS serviceit_v2_opportunities (
  source text NOT NULL,
  tender_id varchar(12) NOT NULL,
  canonical_url text NOT NULL,
  subject text NOT NULL,
  organizer text,
  deadline_unverified text,
  service_groups_json text NOT NULL,
  current_version char(64) NOT NULL,
  review_status text NOT NULL CHECK (review_status IN ('OUT_OF_SCOPE','TENDER_OPPORTUNITY_HOLD')),
  first_seen timestamptz NOT NULL DEFAULT now(),
  last_seen timestamptz NOT NULL DEFAULT now(),
  buyer_inn text NULL,
  fns_verification text NOT NULL DEFAULT 'NOT_CHECKED',
  qualified_lead boolean NOT NULL DEFAULT FALSE,
  PRIMARY KEY (source, tender_id),
  CONSTRAINT no_claim_of_qualification CHECK
    (buyer_inn IS NULL AND fns_verification = 'NOT_CHECKED' AND qualified_lead = FALSE)
);
CREATE TABLE IF NOT EXISTS serviceit_v2_versions (
  source text NOT NULL,
  tender_id varchar(12) NOT NULL,
  digest char(64) NOT NULL,
  gmail_message_id text NOT NULL REFERENCES serviceit_v2_messages(gmail_message_id),
  observed_at timestamptz NOT NULL DEFAULT now(),
  canonical_url text NOT NULL,
  subject text NOT NULL,
  organizer text,
  deadline_unverified text,
  service_groups_json text NOT NULL,
  PRIMARY KEY (source, tender_id, digest),
  FOREIGN KEY (source,tender_id) REFERENCES serviceit_v2_opportunities(source,tender_id)
);
CREATE TABLE IF NOT EXISTS serviceit_v2_crm_outbox (
  operation_key text PRIMARY KEY,
  source text NOT NULL,
  tender_id varchar(12) NOT NULL,
  state text NOT NULL CHECK (state IN
    ('PENDING','RESERVED','POST_UNCERTAIN','VERIFIED','BLOCKED')),
  bitrix_item_id bigint NULL,
  attempt_count integer NOT NULL DEFAULT 0,
  last_attempt_at timestamptz,
  FOREIGN KEY (source,tender_id) REFERENCES serviceit_v2_opportunities(source,tender_id)
);
CREATE TABLE IF NOT EXISTS serviceit_v2_runtime (
  name text PRIMARY KEY,
  value text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);
"""


class V2StoreBlocked(ValueError):
    pass


def _must(value, label):
    if not value:
        raise V2StoreBlocked(label)


def init_schema(conn):
    # No calls to CRM. Only isolated serviceit_v2_* tables.
    for statement in DDL.split(";"):
        if statement.strip():
            conn.execute(statement)


def contains_message(conn, mid: str) -> bool:
    _must(isinstance(mid, str) and re.fullmatch(r"[0-9a-f]{8,24}", mid),
          "BAD_GMAIL_MESSAGE_ID")
    return conn.execute(
        "SELECT 1 FROM serviceit_v2_messages WHERE gmail_message_id=%s",
        (mid,)).fetchone() is not None


def _normal(value, max_len):
    if value is None:
        return None
    _must(type(value) is str and len(value) <= max_len
          and not re.search(r"[\x00-\x08\x0b-\x1f\x7f]", value),
          "UNSAFE_NOTICE_TEXT")
    return value.strip()


def _observed(row):
    _must(row["source"] == ORIGIN and
          isinstance(row["tender_id"], str) and
          re.fullmatch(r"\d{5,12}", row["tender_id"]) is not None,
          "UNEXPECTED_SOURCE_OR_ID")
    # Parsed URL already canonical, no query params.
    from .b2b_email_opportunity_bridge import clean_tender_url
    _must(clean_tender_url(row["source_url"]) ==
          (row["source_url"], row["tender_id"]),
          "INVALID_ORIGINAL_URL")
    groups = row["source_service_groups"]
    _must(isinstance(groups, list) and
          len(groups) <= 6 and all(type(s) is str for s in groups),
          "SERVICE_GROUPS_INVALID")
    title = _normal(row["subject"], 320)
    _must(bool(title), "SUBJECT_MISSING")
    canonical = row["source_url"]
    organizer = _normal(row["organizer_as_named_in_email"], 320)
    deadline = _normal(row["deadline_as_stated_in_email"], 128)
    labels = json.dumps(sorted(set(groups)), ensure_ascii=False)
    state = "TENDER_OPPORTUNITY_HOLD" if groups else "OUT_OF_SCOPE"
    record = (canonical, title, organizer, deadline, labels, state)
    digest = sha256(json.dumps(record, ensure_ascii=False,
                               separators=(",", ":")).encode()).hexdigest()
    return (canonical, title, organizer, deadline, labels, state, digest)


def ingest_message(conn, gmail_message_id: str, mime: bytes) -> dict:
    _must(isinstance(gmail_message_id, str) and
          re.fullmatch(r"[0-9a-f]{8,24}", gmail_message_id),
          "INVALID_MESSAGE_ID")
    _must(isinstance(mime, bytes) and 0 < len(mime) <= 256_000,
          "INVALID_MESSAGE_BYTES")
    sender = parseaddr(str(BytesParser(policy=policy.default).
                            parsebytes(mime, headersonly=True).get("From", "")))[1].lower()
    _must(sender == APPROVED_SENDER, "UNAUTHORIZED_VENDOR_SENDER")
    parsed = parse_email(mime)
    digest = parsed["email_sha256"]
    _must(isinstance(digest, str) and len(digest) == 64, "MIME_DIGEST_INVALID")
    counts = {"new_messages": 0, "duplicate_messages": 0,
              "new_opportunities": 0, "revised_opportunities": 0,
              "it_opportunities_new_or_revised": 0, "out_of_scope": 0,
              "crm_writes": 0, "qualified_leads": 0}
    with conn.transaction():
        prior = conn.execute(
            "SELECT mime_sha256 FROM serviceit_v2_messages WHERE gmail_message_id=%s FOR UPDATE",
            (gmail_message_id,)).fetchone()
        if prior:
            _must(prior[0] == digest, "SAME_GMAIL_ID_DIFFERENT_MIME")
            counts["duplicate_messages"] = 1
            return counts
        received = parsed["email_received_at"]
        when = None
        if received:
            try:
                when = datetime.fromisoformat(received)
                if when.tzinfo is None:
                    when = None
            except ValueError:
                pass
        observations = parsed["observations"]
        _must(len(observations) <= 80, "MESSAGE_NOTICE_BUDGET")
        conn.execute(
            "INSERT INTO serviceit_v2_messages (gmail_message_id,mime_sha256,received_at,observations) VALUES (%s,%s,%s,%s)",
            (gmail_message_id, digest, when, len(observations)))
        counts["new_messages"] = 1
        for row in observations:
            canonical, title, organizer, deadline, labels, status, vd = _observed(row)
            source, tid = ORIGIN, row["tender_id"]
            existing = conn.execute(
                "SELECT current_version FROM serviceit_v2_opportunities WHERE source=%s AND tender_id=%s FOR UPDATE",
                (source, tid)).fetchone()
            if not existing:
                conn.execute("""
                    INSERT INTO serviceit_v2_opportunities
                    (source,tender_id,canonical_url,subject,organizer,deadline_unverified,
                     service_groups_json,current_version,review_status)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (source, tid, canonical, title, organizer, deadline, labels, vd, status))
                counts["new_opportunities"] += 1
            elif existing[0] != vd:
                conn.execute("""
                    UPDATE serviceit_v2_opportunities SET canonical_url=%s,subject=%s,
                    organizer=%s,deadline_unverified=%s,service_groups_json=%s,
                    current_version=%s,review_status=%s,last_seen=now()
                    WHERE source=%s AND tender_id=%s""",
                    (canonical, title, organizer, deadline, labels, vd, status, source, tid))
                counts["revised_opportunities"] += 1
            else:
                conn.execute(
                    "UPDATE serviceit_v2_opportunities SET last_seen=now() WHERE source=%s AND tender_id=%s",
                    (source, tid))
            conn.execute("""
                INSERT INTO serviceit_v2_versions
                (source,tender_id,digest,gmail_message_id,canonical_url,subject,
                 organizer,deadline_unverified,service_groups_json)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (source,tender_id,digest) DO NOTHING""",
                (source, tid, vd, gmail_message_id, canonical, title,
                 organizer, deadline, labels))
            if status == "OUT_OF_SCOPE":
                counts["out_of_scope"] += 1
            elif not existing or existing[0] != vd:
                counts["it_opportunities_new_or_revised"] += 1
    return counts


def read_status(conn):
    rows = {}
    for table, key in (
        ("serviceit_v2_messages", "total_mail_messages"),
        ("serviceit_v2_opportunities", "total_distinct_procurements"),
        ("serviceit_v2_versions", "total_notice_versions"),
        ("serviceit_v2_crm_outbox", "total_crm_outbox_events"),
    ):
        rows[key] = conn.execute("SELECT count(*) FROM " + table).fetchone()[0]
    rows["tender_hold"] = conn.execute(
        "SELECT count(*) FROM serviceit_v2_opportunities WHERE review_status=%s",
        ("TENDER_OPPORTUNITY_HOLD",)).fetchone()[0]
    rows["qualified_leads"] = 0
    rows["crm_writes"] = 0
    rows["mode"] = "READ_ONLY_SOURCE_CORE"
    return rows
