"""On-demand official FNS MSP monthly ZIP -> encrypted per-card HOLD receipts.

The data source is NOT a tender/source ingestion. Never sends customer INNs
to the FNS HTTP endpoint: only the public XML ZIP is fetched. No CRM writes,
no contact authorization, no formal Score, no EGRUL status claims.

Fetch only if eligible genuinely new 1062/21 cards have valid INNs not already
checked against this month's exact snapshot. ZIP is streamed into ephemeral
runner storage, parsed ONCE for all candidate INNs, and deleted after the job.
No original public dataset or raw INNs are ever committed to GitHub.
"""
from __future__ import annotations

import base64
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import hmac
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import urljoin, urlsplit
import zipfile

from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException
import httpx

from .bitrix_1062_mapping import _is_valid_inn
from .bitrix_metadata_audit import validate_webhook
from .serviceit_bitrix_batch_worker import list_only
from .serviceit_bitrix_poll_fallback import after_activation, calibrate_known_card, discover
from .serviceit_bitrix_dispatch_worker import EncryptedEventFence, REPOSITORY, BRANCH, PORTAL
from .serviceit_stage4_fns_dedupe_watch import read_item
from .serviceit_enrichment_worker import PREFIX

PUBLIC_PAGE = "https://www.nalog.gov.ru/opendata/7707329152-rsmp/"
DATASET_PATH = "/opendata/7707329152-rsmp/"
OFFICIAL_HOSTS = frozenset(("www.nalog.gov.ru", "file.nalog.ru"))
ARCHIVE_NAME = re.compile(r"data-(\d{8})-structure-(\d{8})\.zip\Z")
MAX_ZIP_BYTES = 3 * 1024**3
MAX_TOTAL_XML = 16 * 1024**3
MAX_MEMBERS = 100000
MAX_MEMBER_BYTES = 512 * 1024**2
MAX_CARD_BATCH = 20
OWNER_MARKER = "SERVICEIT_FNS_MSP_OFFICIAL_LIVE_ONCE_V1"
ENABLED = "YES_1062_21_OFFICIAL_MSP_READONLY"
FNS_RECEIPT_FOLDER = ".serviceit/official-msp-snapshot-by-card"
NO_SIDE_EFFECTS = {
    "crm_writes": 0, "score_writes": 0, "customer_contacts": 0,
    "source_ingestion": 0, "egrul_active_verified": False,
    "formal_score_authorized": False, "corporate_stop_verified": False,
    "independent_fns_signature_verified": False,
}


class OfficialMspBlocked(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise OfficialMspBlocked(code)


def dated_archive_url(url, today):
    """Require exact dated official MSP dataset path; never allow arbitrary URLs."""
    p = urlsplit(url)
    require(type(today) is date and p.scheme == "https"
            and p.netloc in OFFICIAL_HOSTS and not p.query and not p.fragment
            and p.username is None and p.password is None
            and p.path.startswith(DATASET_PATH), "FNS_SOURCE_IDENTITY_UNVERIFIED")
    m = ARCHIVE_NAME.fullmatch(p.path[len(DATASET_PATH):])
    require(m is not None, "FNS_DATASET_PATH_UNVERIFIED")
    try:
        published = datetime.strptime(m.group(1), "%d%m%Y").date()
        structured = datetime.strptime(m.group(2), "%d%m%Y").date()
    except ValueError:
        raise OfficialMspBlocked("FNS_SOURCE_DATE_INVALID") from None
    require(structured <= published <= today
            and timedelta(0) <= today - published <= timedelta(days=45),
            "FNS_MSP_SOURCE_STALE")
    return published


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []
    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            for key, value in attrs:
                if key.lower() == "href" and isinstance(value, str):
                    self.urls.append(value)


def discover_official_url(client, *, today):
    """Official HTML dataset passport is the only source of archive links."""
    response = client.get(PUBLIC_PAGE, timeout=35, follow_redirects=False)
    require(response.status_code == 200
            and 0 < len(response.content) <= 512000,
            "FNS_PUBLIC_PASSPORT_UNAVAILABLE")
    links = Links()
    links.feed(response.text)
    dates = []
    for raw in links.urls:
        url = urljoin(PUBLIC_PAGE, raw)
        try:
            published = dated_archive_url(url, today)
        except OfficialMspBlocked:
            continue
        dates.append((published, url))
    require(bool(dates), "FNS_PASSPORT_NO_CURRENT_OFFICIAL_ZIP")
    latest = max(date for date, _ in dates)
    matching = {url for publication, url in dates if publication == latest}
    require(len(matching) == 1, "FNS_PASSPORT_LATEST_AMBIGUOUS")
    return matching.pop(), latest


def follow_official_redirect(url, location, today, publication):
    require(isinstance(location, str) and 0 < len(location) < 500,
            "FNS_REDIRECT_UNVERIFIED")
    next_url = urljoin(url, location)
    require(dated_archive_url(next_url, today) == publication
            and urlsplit(next_url).path == urlsplit(url).path,
            "FNS_REDIRECT_NOT_SAME_OFFICIAL_DATASET")
    return next_url


def fetch_archive(client, url, publication, today, destination):
    """Bounded streaming official HTTPS. No client identifiers in URL/body."""
    require(isinstance(destination, Path) and not destination.exists(),
            "FNS_TEMP_PATH_INVALID")
    declared = None
    for method in ("HEAD", "GET"):
        visited = set()
        current = url
        for _ in range(5):
            require(current not in visited, "FNS_REDIRECT_LOOP")
            visited.add(current)
            require(dated_archive_url(current, today) == publication,
                    "FNS_ARCHIVE_IDENTITY_CHANGED")
            with client.stream(method, current, timeout=60,
                               follow_redirects=False) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    current = follow_official_redirect(
                        current, response.headers.get("location"), today, publication)
                    continue
                require(response.status_code == 200, "FNS_REMOTE_ZIP_NOT_AVAILABLE")
                content_type = response.headers.get("content-type", "").lower()
                require("html" not in content_type and "plain" not in content_type,
                        "FNS_ZIP_RESPONSE_NOT_BINARY")
                raw = response.headers.get("content-length")
                require(type(raw) is str and raw.isascii() and raw.isdecimal(),
                        "FNS_ZIP_SIZE_UNVERIFIED")
                size = int(raw)
                require(22 <= size <= MAX_ZIP_BYTES, "FNS_ZIP_OUTSIDE_DISK_BUDGET")
                if method == "HEAD":
                    declared = size
                else:
                    require(size == declared, "FNS_HEAD_GET_LENGTH_CHANGED")
                    free = shutil.disk_usage(destination.parent).free
                    require(free >= size + 256*1024**2,
                            "FNS_RUNNER_DISK_INSUFFICIENT")
                    digest = sha256()
                    received = 0
                    with destination.open("xb") as out:
                        for chunk in response.iter_bytes(chunk_size=4*1024**2):
                            received += len(chunk)
                            require(received <= declared, "FNS_ZIP_OVERSIZED_STREAM")
                            digest.update(chunk)
                            out.write(chunk)
                    require(received == declared,
                            "FNS_ZIP_INCOMPLETE")
                    return {"size_bytes": received, "sha256": digest.hexdigest(),
                            "official_tls_url": True}
                break
        else:
            raise OfficialMspBlocked("FNS_REDIRECT_CHAIN_EXCEEDED")
    raise OfficialMspBlocked("FNS_DOWNLOAD_INCOMPLETE")


def scan_zip_for_inns(archive, target_inns):
    """Single bounded streaming XML pass for all requested valid INNs."""
    require(isinstance(archive, Path) and archive.is_file()
            and 22 <= archive.stat().st_size <= MAX_ZIP_BYTES,
            "FNS_ARCHIVE_FILE_INVALID")
    require(type(target_inns) is set and 1 <= len(target_inns) <= MAX_CARD_BATCH
            and all(type(x) is str and _is_valid_inn(x) for x in target_inns),
            "FNS_TARGET_SCOPE_INVALID")
    seen = {}
    total = 0
    try:
        with zipfile.ZipFile(archive, "r") as z:
            members = [m for m in z.infolist() if not m.is_dir()]
            require(0 < len(members) <= MAX_MEMBERS
                    and len({m.filename for m in members}) == len(members)
                    and sum(m.file_size for m in members) <= MAX_TOTAL_XML,
                    "FNS_ZIP_MEMBER_BUDGET")
            for member in members:
                require(member.filename.lower().endswith(".xml")
                        and member.file_size <= MAX_MEMBER_BYTES
                        and not member.filename.startswith("/")
                        and ".." not in Path(member.filename).parts,
                        "FNS_XML_MEMBER_UNSAFE")
                with z.open(member) as f:
                    events = ET.iterparse(
                        f, events=("start", "end"),
                        forbid_dtd=True, forbid_entities=True,
                        forbid_external=True,
                    )
                    _, root = next(events)
                    require(root.tag == "Файл"
                            and root.get("ТипИнф") == "РЕЕСТРМСП"
                            and root.get("ВерсФорм") == "4.06",
                            "FNS_MSP_DOCUMENT_FORMAT_INVALID")
                    for event, doc in events:
                        if event == "end" and doc.tag == "Документ":
                            total += 1
                            require(total <= 50_000_000,
                                    "FNS_XML_RECORD_LIMIT")
                            org, ip = doc.find("ОргВклМСП"), doc.find("ИПВклМСП")
                            require(not (org is not None and ip is not None),
                                    "FNS_MSP_ENTITY_AMBIGUOUS")
                            inn = (org.get("ИННЮЛ") if org is not None else
                                   ip.get("ИННФЛ") if ip is not None else None)
                            if inn in target_inns:
                                kind = doc.get("КатСубМСП")
                                require(kind in ("1", "2", "3")
                                        and (inn not in seen or seen[inn] == kind),
                                        "FNS_MSP_CONFLICTING_CATEGORY")
                                seen[inn] = kind
                            root.clear()
        require(total > 0, "FNS_MSP_SNAPSHOT_EMPTY")
    except (zipfile.BadZipFile, ET.ParseError, DefusedXmlException,
            EOFError, StopIteration):
        raise OfficialMspBlocked("FNS_ZIP_OR_XML_INVALID") from None
    return seen, total


class OfficialMspFence(EncryptedEventFence):
    def __init__(self, client, token, key, publication):
        super().__init__(client, token, key)
        require(type(publication) is date, "FNS_RECEIPT_DATE_INVALID")
        self.publication = publication

    def url(self, card):
        require(type(card) is int and 8 <= card <= 2_000_000_000
                and card not in (3, 7), "FNS_RECEIPT_CARD_INVALID")
        payload = (f"official-fns-msp-v1:1062:21:"
                   f"{self.publication.isoformat()}:{card}")
        digest = hmac.new(
            self.path_key, payload.encode("ascii"), sha256).hexdigest()
        return (f"https://api.github.com/repos/{REPOSITORY}/contents/"
                f"{FNS_RECEIPT_FOLDER}/{digest}.enc")

    def inn_digest(self, inn):
        return hmac.new(self.path_key,
                        ("fns-msp-card-inn-v1:" + inn).encode(),
                        sha256).hexdigest()

    def already_checked(self, card, inn):
        """Only complete matching encrypted receipts qualify as cached."""
        try:
            response = self.client.get(
                self.url(card), params={"ref": BRANCH}, headers=self.headers,
                timeout=20, follow_redirects=False)
            if response.status_code == 404:
                return False
            require(response.status_code == 200,
                    "FNS_EXISTING_RECEIPT_NOT_READABLE")
            body = response.json()
            require(type(body) is dict and type(body.get("content")) is str,
                    "FNS_EXISTING_RECEIPT_SHAPE")
            record = json.loads(self.cipher.decrypt(
                base64.b64decode(body["content"])))
            expected = record.get("expected")
            require(record.get("card") == card
                    and type(expected) is dict
                    and expected.get("publication") == self.publication.isoformat()
                    and expected.get("inn_hmac") == self.inn_digest(inn),
                    "FNS_RECEIPT_CARD_CHANGED_OR_INVALID")
            # A previously reserved/unverified operation is not silently retried.
            require(record.get("status") == "HOLD"
                    and expected.get("fns_egrul_verified") is False
                    and expected.get("formal_score_authorized") is False,
                    "FNS_RECEIPT_NOT_COMPLETELY_COMMITTED")
            return True
        except OfficialMspBlocked:
            raise
        except Exception:
            raise OfficialMspBlocked("FNS_RECEIPT_UNVERIFIED") from None

    def save_holding_result(self, card, inn, category, publication, checksum):
        require(category in ("1", "2", "3", None)
                and publication == self.publication
                and type(checksum) is str
                and re.fullmatch("[a-f0-9]{64}", checksum),
                "FNS_RESULT_UNTRUSTED")
        state = self.reserve(card)
        if state != "RESERVED":
            return "FNS_ALREADY_FENCED_REVIEW_REQUIRED"
        self.receipt = {"expected": {
            "publication": publication.isoformat(),
            "inn_hmac": self.inn_digest(inn),
            "msp_category": {"1": "MICRO", "2": "SMALL",
                             "3": "MEDIUM"}.get(category),
            "archive_sha256": checksum,
            "fns_egrul_verified": False,
            "independent_signature_verified": False,
            "corporate_stop_verified": False,
            "formal_score_authorized": False,
        }}
        return ("FNS_MSP_SNAPSHOT_RECEIPT_SAVED"
                if self.finalize(card, "HOLD")
                else "FNS_RECEIPT_FINALIZE_UNCERTAIN")


def run(client, env, *, now=None):
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    require(env.get("GITHUB_REPOSITORY") == REPOSITORY
            and env.get("GITHUB_REF") == "refs/heads/main"
            and env.get("GITHUB_EVENT_NAME") in ("schedule", "issue_comment")
            and env.get("SERVICEIT_FNS_MSP_AUTO_ENABLED") == ENABLED,
            "FNS_OWNER_AND_CLOUD_GATE_MISSING")
    if env.get("GITHUB_EVENT_NAME") == "issue_comment":
        try:
            path = Path(env.get("GITHUB_EVENT_PATH", ""))
            require(path.is_file() and 0 < path.stat().st_size < 250000,
                    "FNS_OWNER_EVENT_MISSING")
            event = json.loads(path.read_text("utf-8"))
            require(type(event) is dict and event.get("action") == "created"
                    and (event.get("repository") or {}).get("full_name") == REPOSITORY
                    and (event.get("issue") or {}).get("number") == 148
                    and (event.get("issue") or {}).get("pull_request") is None
                    and (event.get("sender") or {}).get("login") == "Ramzes2023"
                    and (event.get("comment") or {}).get("body") == OWNER_MARKER
                    and ((event.get("comment") or {}).get("user") or {}).get("login")
                    == "Ramzes2023", "FNS_OWNER_EVENT_INVALID")
        except (OSError, ValueError, TypeError, AttributeError):
            raise OfficialMspBlocked("FNS_OWNER_EVENT_UNVERIFIED") from None
    base = validate_webhook(env.get("SERVICEIT_BITRIX_WEBHOOK", ""))
    require(base.startswith(f"https://{PORTAL}/rest/"),
            "FNS_CORPORATE_PORTAL_INVALID")
    list_fn = lambda method, args: list_only(client, base, method, args)
    calibrate_known_card(list_fn)
    cards = discover(list_fn, after_activation(env, now), now)
    require(len(cards) <= 200, "FNS_NEW_CARD_DISCOVERY_OVER_BUDGET")
    out = {"status": "FNS_NO_NEW_CARDS", "new_cards_visible": len(cards),
           "pending_checked": 0, "cached": 0, "invalid_inn": 0,
           "official_archive_downloaded": False,
           "verified_local_snapshot_digest": False,
           "msp_matches": 0, "msp_absent_from_snapshot": 0,
           "encrypted_holding_receipts_saved": 0, **NO_SIDE_EFFECTS}
    if not cards:
        return out
    url, publication = discover_official_url(client, today=now.date())
    fence = OfficialMspFence(
        client, env.get("GITHUB_TOKEN", ""),
        env.get("SERVICEIT_GH_LEDGER_KEY", ""), publication)
    pending = []
    for card in cards:
        if len(pending) >= MAX_CARD_BATCH:
            break
        row = read_item(client, base, card)
        inn = row.get(PREFIX + "COMPANY_INN")
        if type(inn) is not str or not _is_valid_inn(inn):
            out["invalid_inn"] += 1
            continue
        if fence.already_checked(card, inn):
            out["cached"] += 1
            continue
        pending.append((card, inn))
    if not pending:
        out["status"] = "FNS_NO_PENDING_VALID_INNS"
        return out
    with tempfile.TemporaryDirectory(prefix="serviceit-public-fns-") as temp:
        target = Path(temp) / "official-msp.zip"
        download = fetch_archive(client, url, publication, now.date(), target)
        matches, examined = scan_zip_for_inns(
            target, {inn for _, inn in pending})
        out["official_archive_downloaded"] = True
        out["verified_local_snapshot_digest"] = True
        # Delete the full dataset before any GitHub receipt writes.
    require(examined > 0, "FNS_OFFICIAL_SNAPSHOT_UNVERIFIED")
    out["pending_checked"] = len(pending)
    out["msp_matches"] = sum(inn in matches for _, inn in pending)
    out["msp_absent_from_snapshot"] = len(pending) - out["msp_matches"]
    problems = 0
    for card, inn in pending:
        result = fence.save_holding_result(
            card, inn, matches.get(inn), publication, download["sha256"])
        if result == "FNS_MSP_SNAPSHOT_RECEIPT_SAVED":
            out["encrypted_holding_receipts_saved"] += 1
        else:
            problems += 1
    out["status"] = ("FNS_MSP_OFFICIAL_SNAPSHOT_PROCESSED"
                     if not problems else "FNS_RECEIPT_SOME_UNCERTAIN")
    out["publication"] = publication.isoformat()
    # Hash belongs to public FNS ZIP and may be logged, unlike corporate INNs.
    out["source_zip_sha256"] = download["sha256"]
    return out


def main():
    env = dict(os.environ)
    try:
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            result = run(client, env)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["status"] not in ("FNS_RECEIPT_SOME_UNCERTAIN",) else 2
    except Exception:
        print(json.dumps({
            "status": "FNS_OFFICIAL_MSP_HOLD_NEEDS_REVIEW",
            "reason": "SOURCE_CRM_NETWORK_OR_LEDGER_UNVERIFIED",
            **NO_SIDE_EFFECTS}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
