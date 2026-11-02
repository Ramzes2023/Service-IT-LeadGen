"""FNS MSP official passport discovery + bounded ZIP acquisition (NO CRM).

The ZIP is multi-gigabyte; NEVER download it during module import or tests.
No arbitrary URLs, redirects or paths, no silent fallback to mirrors.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
import os
import re
from urllib.parse import urljoin, urlsplit

import httpx

PASSPORT = "https://www.nalog.gov.ru/opendata/7707329152-rsmp/"
HOSTS = frozenset({"www.nalog.gov.ru", "file.nalog.ru"})
DATASET_PATH = "/opendata/7707329152-rsmp/"
ZIP_RE = re.compile(r"data-(\d{8})-structure-(\d{8})\.zip", re.I)
XSD_RE = re.compile(r"structure-(\d{8})\.xsd", re.I)
MAX_PASSPORT_BYTES = 2_000_000
MAX_DOWNLOAD_BYTES = 5_000_000_000


class FNSDownloadBlocked(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise FNSDownloadBlocked(code)


def release_date_ddmmyyyy(s):
    try:
        require(isinstance(s, str) and re.fullmatch(r"\d{8}", s),
                "FNS_DATE_FORMAT")
        return date(int(s[4:]), int(s[2:4]), int(s[:2]))
    except (ValueError, TypeError):
        raise FNSDownloadBlocked("FNS_DATE_INVALID") from None


def _path(value, pattern):
    try:
        u = urlsplit(value)
    except (ValueError, TypeError):
        raise FNSDownloadBlocked("FNS_URL_INVALID") from None
    require(u.scheme == "https" and u.hostname in HOSTS
            and u.port in (None, 443) and not (u.username or u.password)
            and not u.query and not u.fragment
            and u.path.startswith(DATASET_PATH), "FNS_HOST_OR_PATH_REJECTED")
    path = u.path[len(DATASET_PATH):]
    m = pattern.fullmatch(path)
    require(m is not None, "FNS_ARTIFACT_PATH_REJECTED")
    return m


@dataclass(frozen=True)
class Release:
    zip_url: str
    xsd_url: str
    snapshot_date: date
    schema_date: date
    passport_sha256: str
    latest_change_date: date


class _Table(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.row = None
        self.cell = None
        self.cell_links = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []
            self.cell_links = []
        elif tag == "a" and self.cell is not None:
            href = dict(attrs).get("href")
            if isinstance(href, str):
                self.cell_links.append(href)

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            text = " ".join(" ".join(self.cell).split())
            self.row.append((text, self.cell_links))
            self.cell = None
            self.cell_links = None
        if tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def _find_field(rows, num):
    found = []
    for row in rows:
        if len(row) >= 3 and row[0][0] == str(num):
            found.append(row[-1])
    require(len(found) == 1, "FNS_PASSPORT_FIELD_AMBIGUOUS")
    return found[0]


def parse_official_passport(page: bytes) -> Release:
    require(isinstance(page, bytes) and 0 < len(page) <= MAX_PASSPORT_BYTES,
            "FNS_PASSPORT_PAGE_SIZE")
    try:
        parser = _Table()
        parser.feed(page.decode("utf-8"))
    except (UnicodeError, ValueError):
        raise FNSDownloadBlocked("FNS_PASSPORT_HTML_INVALID") from None
    def link(num, pattern):
        text, links = _find_field(parser.rows, num)
        candidates = []
        for item in links + re.findall(r"https://[^\s<>\"']+", text):
            u = urljoin(PASSPORT, item.strip())
            try:
                _path(u, pattern)
                candidates.append(u)
            except FNSDownloadBlocked:
                continue
        candidates = set(candidates)
        require(len(candidates) == 1, "FNS_PASSPORT_LINK_AMBIGUOUS")
        return candidates.pop()
    zip_url = link(8, ZIP_RE)
    xsd_url = link(10, XSD_RE)
    zip_match = _path(zip_url, ZIP_RE)
    xsd_match = _path(xsd_url, XSD_RE)
    snapshot = release_date_ddmmyyyy(zip_match.group(1))
    schema = release_date_ddmmyyyy(zip_match.group(2))
    require(schema == release_date_ddmmyyyy(xsd_match.group(1)),
            "FNS_RELEASE_SCHEMA_MISMATCH")
    modified_text, _ = _find_field(parser.rows, 12)
    require(re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", modified_text),
            "FNS_PASSPORT_CHANGED_DATE_MISSING")
    try:
        changed = date(int(modified_text[6:]), int(modified_text[3:5]),
                       int(modified_text[:2]))
    except ValueError:
        raise FNSDownloadBlocked("FNS_PASSPORT_CHANGED_DATE_INVALID") from None
    require(changed == snapshot, "FNS_RELEASE_DATE_DISAGREES_WITH_PASSPORT")
    return Release(zip_url=zip_url, xsd_url=xsd_url,
                   snapshot_date=snapshot, schema_date=schema,
                   passport_sha256=sha256(page).hexdigest(),
                   latest_change_date=changed)


def discover_release(client: httpx.Client) -> Release:
    """Read only official dataset passport over HTTPS."""
    require(isinstance(client, httpx.Client), "HTTP_CLIENT_REQUIRED")
    try:
        response = client.get(PASSPORT, timeout=25, follow_redirects=False)
        require(response.status_code == 200
                and 0 < len(response.content) <= MAX_PASSPORT_BYTES,
                "FNS_PASSPORT_HTTP_UNAVAILABLE")
        return parse_official_passport(response.content)
    except httpx.HTTPError:
        raise FNSDownloadBlocked("FNS_PASSPORT_NETWORK_UNAVAILABLE") from None


def download_release(client: httpx.Client, release: Release, destination: Path,
                     *, explicitly_approved: bool,
                     max_download_bytes: int = MAX_DOWNLOAD_BYTES) -> dict:
    """Download a large ZIP to NEW local file, bounded and without redirects to outsiders.

    Caller is responsible for enough storage, lawful data handling and
    independent approval. The reported SHA is a checksum, NOT a signature.
    """
    require(explicitly_approved is True, "FNS_DOWNLOAD_NOT_APPROVED")
    require(isinstance(client, httpx.Client) and isinstance(release, Release),
            "FNS_DOWNLOAD_ARGS_INVALID")
    require(type(max_download_bytes) is int and
            1024 <= max_download_bytes <= MAX_DOWNLOAD_BYTES,
            "FNS_DOWNLOAD_SIZE_LIMIT")
    _path(release.zip_url, ZIP_RE)
    require(isinstance(destination, Path) and
            destination.suffix.lower() == ".zip" and not destination.exists()
            and destination.parent.is_dir(), "FNS_DOWNLOAD_DESTINATION_INVALID")
    part = destination.with_name(destination.name + ".part")
    require(not part.exists(), "FNS_PARTIAL_FILE_EXISTS")
    total = 0
    digest = sha256()
    url = release.zip_url
    try:
        with part.open("xb") as out:
            for hop in range(4):
                _path(url, ZIP_RE)
                with client.stream("GET", url, timeout=60,
                                   follow_redirects=False) as response:
                    if response.status_code in (301, 302, 307, 308):
                        target = response.headers.get("location", "")
                        url = urljoin(url, target)
                        _path(url, ZIP_RE)
                        continue
                    require(response.status_code == 200,
                            "FNS_ZIP_DOWNLOAD_HTTP_INVALID")
                    length = response.headers.get("content-length")
                    if length:
                        require(length.isdigit()
                                and int(length) <= max_download_bytes,
                                "FNS_ZIP_CONTENT_LENGTH_INVALID")
                    for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                        total += len(chunk)
                        require(total <= max_download_bytes,
                                "FNS_ZIP_EXCEEDS_DISK_BUDGET")
                        out.write(chunk)
                        digest.update(chunk)
                    break
            else:
                raise FNSDownloadBlocked("FNS_ZIP_REDIRECT_LOOP")
            out.flush()
            os.fsync(out.fileno())
        require(total >= 1024, "FNS_ZIP_TOO_SMALL")
        with part.open("rb") as inp:
            require(inp.read(4) == b"PK\x03\x04", "FNS_ARCHIVE_NOT_ZIP")
        part.rename(destination)
    except (OSError, httpx.HTTPError, FNSDownloadBlocked):
        part.unlink(missing_ok=True)
        raise FNSDownloadBlocked("FNS_DOWNLOAD_BLOCKED_NO_PARTIAL") from None
    return {"status": "FNS_OFFICIAL_TLS_ZIP_DOWNLOADED_NOT_YET_VERIFIED",
            "snapshot": release.snapshot_date.isoformat(),
            "path": str(destination), "bytes": total,
            "zip_sha256": digest.hexdigest(),
            "official_zip_url": release.zip_url,
            "crm_writes": 0, "qualified_leads": 0}
