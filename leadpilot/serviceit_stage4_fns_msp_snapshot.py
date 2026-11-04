"""Read-only FNS MSP XML ZIP parser. No CRM writes or automatic VERIFIED."""
import hashlib
import re
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET
from .bitrix_1062_mapping import _is_valid_inn

class SnapshotBlocked(ValueError):
    pass

def need(ok, code):
    if not ok:
        raise SnapshotBlocked(code)

def verify(archive: Path, inn: str, url: str, trusted_digest: str, *, today: date):
    need(type(inn) is str and _is_valid_inn(inn), "INN_CHECKSUM_INVALID")
    need(type(url) is str and type(today) is date and len(url) < 300,
         "OFFICIAL_SOURCE_UNVERIFIED")
    src = urlsplit(url)
    prefix = "/opendata/7707329152-rsmp/"
    name = src.path.removeprefix(prefix)
    m = re.fullmatch(r"data-(\d{8})-structure-(\d{8})\.zip", name)
    need(src.scheme == "https" and src.netloc == "www.nalog.gov.ru"
         and src.path.startswith(prefix) and not src.query
         and not src.fragment and m is not None, "OFFICIAL_SOURCE_UNVERIFIED")
    try:
        publication = datetime.strptime(m.group(1), "%d%m%Y").date()
        structure = datetime.strptime(m.group(2), "%d%m%Y").date()
    except ValueError:
        raise SnapshotBlocked("PUBLICATION_DATE_INVALID") from None
    need(structure <= publication <= today
         and today-publication <= timedelta(days=45), "MSP_SNAPSHOT_STALE")
    need(type(archive) is Path and archive.is_file() and not archive.is_symlink()
         and 0 < archive.stat().st_size <= 3*1024**3, "SNAPSHOT_FILE_INVALID")
    need(type(trusted_digest) is str and re.fullmatch("[0-9a-f]{64}", trusted_digest),
         "SNAPSHOT_DIGEST_INVALID")
    h = hashlib.sha256()
    with archive.open("rb") as f:
        for chunk in iter(lambda: f.read(4*1024**2), b""):
            h.update(chunk)
    need(h.hexdigest() == trusted_digest, "SNAPSHOT_HASH_MISMATCH")
    total, category = 0, None
    try:
        with zipfile.ZipFile(archive) as z:
            members = [x for x in z.infolist() if not x.is_dir()]
            need(0 < len(members) <= 100000
                 and len({m.filename for m in members}) == len(members)
                 and sum(m.file_size for m in members) <= 16*1024**3,
                 "SNAPSHOT_ARCHIVE_INVALID")
            for member in members:
                need(member.filename.lower().endswith(".xml")
                     and not member.filename.startswith("/")
                     and ".." not in Path(member.filename).parts
                     and member.file_size <= 512*1024**2,
                     "SNAPSHOT_MEMBER_UNSAFE")
                with z.open(member) as f:
                    events = ET.iterparse(f, events=("start", "end"))
                    _, root = next(events)
                    need(root.tag == "Файл" and root.get("ТипИнф") == "РЕЕСТРМСП"
                         and root.get("ВерсФорм") == "4.06", "MSP_FORMAT_UNVERIFIED")
                    for event, doc in events:
                        if event == "end" and doc.tag == "Документ":
                            total += 1
                            need(total <= 50000000, "SNAPSHOT_TOO_MANY_RECORDS")
                            org, ip = doc.find("ОргВклМСП"), doc.find("ИПВклМСП")
                            need(not (org is not None and ip is not None),
                                 "MSP_ENTITY_AMBIGUOUS")
                            if (org is not None and org.get("ИННЮЛ") == inn) or (
                                    ip is not None and ip.get("ИННФЛ") == inn):
                                found = doc.get("КатСубМСП")
                                need(found in ("1", "2", "3")
                                     and category in (None, found),
                                     "MSP_CATEGORY_AMBIGUOUS")
                                category = found
                            root.clear()
            need(total > 0, "SNAPSHOT_EMPTY")
    except (zipfile.BadZipFile, ET.ParseError, StopIteration, EOFError):
        raise SnapshotBlocked("SNAPSHOT_INVALID_XML_OR_ZIP") from None
    return {
        "status": "MATCH_IN_SUPPLIED_MSP_SNAPSHOT" if category else
                  "NO_MATCH_IN_SUPPLIED_SNAPSHOT",
        "msp_category": {"1": "MICRO", "2": "SMALL", "3": "MEDIUM"}.get(category),
        "published": publication.isoformat(),
        "source_file": name,
        "snapshot_sha256": h.hexdigest(),
        "origin_independently_verified": False,
        "egrul_active_verified": False,
        "region_verified": False,
        "corporate_stop_and_contact_verified": False,
        "formal_score_authorized": False,
        "crm_writes": 0,
    }
