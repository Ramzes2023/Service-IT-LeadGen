"""Optional READ-ONLY mail.ru notification transport for B2B-Center subscriptions.

Never accesses B2B-Center webpages. No mailbox credential or API secret in Git.
The mailbox owner must separately authorize limited ingestion and provision a
Mail.ru external-application password via local environment, never a chat.
All extracted procurements remain HOLD until source/buyer/FNS verification.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
import hashlib
import imaplib
import json
import os
from pathlib import Path
import ssl
import tempfile

from .procurement_notice_inbox import MAX_EMAIL_BYTES, NoticeError, _source, read_email

IMAP_HOST = "imap.mail.ru"
IMAP_PORT = 993
MAX_MESSAGES = 30
MAX_REPORT_ROWS = 100
STATE_FILE = "b2b_mailru_notice_seen.json"


class MailTransportBlocked(RuntimeError):
    """Redacted failure, without credentials, URLs or mailbox contents."""


def _gate(env):
    if env.get("SERVICEIT_MAILRU_READONLY_AUTHORIZED") != "YES":
        raise MailTransportBlocked("MAILBOX_OWNER_AUTHORIZATION_REQUIRED")
    user = env.get("SERVICEIT_MAILRU_USER")
    password = env.get("SERVICEIT_MAILRU_APP_PASSWORD")
    if not isinstance(user, str) or not user.endswith("@mail.ru") or len(user) > 254:
        raise MailTransportBlocked("MAILRU_ACCOUNT_UNCONFIGURED")
    if not isinstance(password, str) or len(password) < 8:
        raise MailTransportBlocked("MAILRU_EXTERNAL_APP_PASSWORD_REQUIRED")
    return user, password


def _data_bytes(result):
    for item in result or []:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    return None


def _state(path):
    if not path.is_file():
        return []
    if not 0 <= path.stat().st_size <= 100_000:
        raise MailTransportBlocked("LOCAL_STATE_UNREADABLE")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise MailTransportBlocked("LOCAL_STATE_UNREADABLE") from None
    if not isinstance(value, list) or len(value) > 1500 or not all(
        isinstance(x, str) and len(x) == 64 and all(ch in "0123456789abcdef" for ch in x)
        for x in value
    ):
        raise MailTransportBlocked("LOCAL_STATE_UNREADABLE")
    return value


def fetch_research(*, env=None, imap_factory=imaplib.IMAP4_SSL, now=None,
                   output_dir="exports"):
    env = os.environ if env is None else env
    username, app_password = _gate(env)
    target = Path(output_dir).resolve()
    if target != Path("exports").resolve():
        raise MailTransportBlocked("OUTPUT_MUST_BE_LOCAL_EXPORTS")
    target.mkdir(parents=True, exist_ok=True)
    seen_path = target / STATE_FILE
    prior = set(_state(seen_path))
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise MailTransportBlocked("CLOCK_MISSING_TIMEZONE")
    since = (current.astimezone(timezone.utc) - timedelta(days=3)).strftime("%d-%b-%Y")
    mail = None
    observations = []
    digests = set()
    fetched = 0
    try:
        mail = imap_factory(IMAP_HOST, IMAP_PORT, ssl_context=ssl.create_default_context(), timeout=15)
        status, _ = mail.login(username, app_password)
        if status != "OK":
            raise MailTransportBlocked("MAILRU_LOGIN_REJECTED")
        status, _ = mail.select("INBOX", readonly=True)
        if status != "OK":
            raise MailTransportBlocked("MAILRU_READONLY_INBOX_REJECTED")
        status, result = mail.uid("SEARCH", None, "SINCE", since)
        if status != "OK" or not result or not isinstance(result[0], bytes):
            raise MailTransportBlocked("MAILRU_SEARCH_FAILED")
        uids = [uid for uid in result[0].split() if uid.isascii() and uid.isdigit()]
        # Bound resource consumption; do not sweep a large mailbox.
        for uid in uids[-MAX_MESSAGES:]:
            status, data = mail.uid("FETCH", uid, "(BODY.PEEK[])")
            if status != "OK":
                raise MailTransportBlocked("MAILRU_FETCH_FAILED")
            payload = _data_bytes(data)
            if not payload or len(payload) > MAX_EMAIL_BYTES:
                continue
            fetched += 1
            message = BytesParser(policy=policy.default).parsebytes(payload)
            if not _source(message.get("From", "")):
                continue
            digest = hashlib.sha256(payload).hexdigest()
            if digest in prior or digest in digests:
                continue
            with tempfile.TemporaryDirectory(prefix="serviceit-notice-") as tmp:
                raw_file = Path(tmp) / "incoming.eml"
                raw_file.write_bytes(payload)
                try:
                    row = read_email(raw_file)
                except NoticeError:
                    continue
            row["content_digest"] = digest
            observations.append(row)
            digests.add(digest)
            if len(observations) == MAX_REPORT_ROWS:
                break
    except (imaplib.IMAP4.error, OSError, TimeoutError, ssl.SSLError):
        raise MailTransportBlocked("MAILRU_READONLY_TRANSPORT_FAILED") from None
    finally:
        if mail is not None:
            try:
                mail.logout()
            except (imaplib.IMAP4.error, OSError):
                pass

    # Do not store full messages, passwords or unrelated emails.
    report = {
        "source": "B2B_MAILRU_VENDOR_NOTIFICATIONS",
        "mailbox_transport": "READONLY_IMAP_RESPONDED",
        "first_party_vendor_data_verified": False,
        "message_count_checked": fetched,
        "new_candidate_observations": len(observations),
        "buyer_inn_verified": 0,
        "fns_micro_small_77_50": 0,
        "qualified_real_leads": 0,
        "crm_writes": "NO",
        "outreach": "NO",
        "observations": observations,
    }
    outfile = target / ("b2b-mail-notices-" + current.strftime("%Y%m%dT%H%M%SZ") + ".json")
    with outfile.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    all_seen = list(dict.fromkeys([*prior, *digests]))[-1500:]
    state_tmp = seen_path.with_suffix(".tmp")
    state_tmp.write_text(json.dumps(all_seen), encoding="utf-8")
    os.replace(state_tmp, seen_path)
    return report


def main():
    try:
        result = fetch_research()
    except MailTransportBlocked as exc:
        print("B2B_MAILRU_SOURCE=BLOCKED; REASON=" + str(exc) + "; QUALIFIED_REAL_LEADS=0")
        return 2
    print("B2B_MAILRU_SOURCE=MAILBOX_READONLY_OK; NEW_NOTICE_CANDIDATES="
          + str(result["new_candidate_observations"]) + "; QUALIFIED_REAL_LEADS=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
