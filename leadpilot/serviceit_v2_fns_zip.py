"""Read-only targeted INN lookup in an official-layout FNS MSP ZIP snapshot.

A full snapshot can be ~2GB compressed and >20GB expanded. No global
extraction to disk; read each bounded member from a local archive and search
only explicitly supplied checksum-valid INNs. A reported hash is not an
official signature, and absence is reliable only after a complete scan.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from pathlib import Path
import re
import stat
from zipfile import ZipFile, BadZipFile

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

from .serviceit_v2_fns_qualification import (
    MSPRecord, EvidenceRejected, checked_inn, extract_msp_record,
)
from .serviceit_v2_fns_release import Release, FNSDownloadBlocked, require

MAX_ZIP_SIZE = 5_000_000_000
MAX_MEMBERS = 25000
MAX_MEMBER_BYTES = 16_000_000
MAX_TOTAL_UNCOMPRESSED = 32_000_000_000
NAME_RE = re.compile(r"VO_RRMSPSV_[A-Za-z0-9_-]{8,160}\.xml", re.I)


class ArchiveRejected(ValueError):
    pass


def _must(ok, label):
    if not ok:
        raise ArchiveRejected(label)


def sha256_file(filename: Path):
    digest = sha256()
    with filename.open("rb") as raw:
        for chunk in iter(lambda: raw.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ScanResult:
    sha256_zip: str
    snapshot: str
    member_count: int
    records_scanned: int
    matched: dict[str, MSPRecord]
    missing_inns: tuple[str, ...]
    status: str
    verified_official_signature: bool = False
    crm_writes: int = 0
    qualified_leads: int = 0


def inspect_local_snapshot(archive: Path, *, release: Release,
                           target_inns: list[str],
                           expected_archive_sha256: str) -> ScanResult:
    _must(isinstance(archive, Path) and archive.is_file()
          and archive.suffix.lower() == ".zip"
          and 100 <= archive.stat().st_size <= MAX_ZIP_SIZE,
          "ZIP_LOCAL_FILE_MISSING_OR_OVER_BUDGET")
    _must(isinstance(release, Release), "FNS_PASSPORT_RELEASE_REQUIRED")
    _must(isinstance(target_inns, list) and
          1 <= len(target_inns) <= 100 and
          len(set(target_inns)) == len(target_inns), "TARGET_INN_LIST_INVALID")
    for inn in target_inns:
        checked_inn(inn)
    _must(isinstance(expected_archive_sha256, str)
          and re.fullmatch(r"[0-9a-f]{64}", expected_archive_sha256),
          "ARCHIVE_SHA256_REQUIRED")
    actual_digest = sha256_file(archive)
    _must(actual_digest == expected_archive_sha256,
          "ZIP_ARCHIVE_DIGEST_MISMATCH")
    wanted = set(target_inns)
    matched = {}
    count = 0
    member_count = 0
    try:
        with ZipFile(archive) as zip_file:
            entries = zip_file.infolist()
            _must(0 < len(entries) <= MAX_MEMBERS, "ZIP_MEMBER_COUNT_INVALID")
            _must(len({z.filename for z in entries}) == len(entries),
                  "DUPLICATE_ARCHIVE_MEMBER")
            total = 0
            for entry in entries:
                member_count += 1
                _must(bool(NAME_RE.fullmatch(entry.filename))
                      and not entry.is_dir()
                      and not (entry.flag_bits & 0x1)
                      and entry.file_size > 0
                      and entry.file_size <= MAX_MEMBER_BYTES,
                      "UNSAFE_OR_OVERSIZE_ZIP_MEMBER")
                # A ZIP Unix external attribute may mark a symlink.
                mode = (entry.external_attr >> 16) & 0xFFFF
                _must(not stat.S_ISLNK(mode), "SYMLINK_MEMBER_FORBIDDEN")
                total += entry.file_size
                _must(total <= MAX_TOTAL_UNCOMPRESSED,
                      "ZIP_TOTAL_EXPANDED_TOO_LARGE")
            for entry in entries:
                with zip_file.open(entry) as src:
                    data = src.read(MAX_MEMBER_BYTES + 1)
                _must(len(data) == entry.file_size
                      and len(data) <= MAX_MEMBER_BYTES,
                      "ZIP_MEMBER_SIZE_MISMATCH")
                try:
                    root = SafeET.fromstring(data)
                except (SafeET.ParseError, DefusedXmlException,
                        ValueError, TypeError):
                    raise ArchiveRejected("ZIP_MEMBER_XML_UNSAFE") from None
                _must(root.tag == "Файл"
                      and root.get("ТипИнф") == "РЕЕСТРМСП"
                      and root.get("ВерсФорм") == "4.06"
                      and root.get("ИдФайл") == entry.filename[:-4],
                      "ZIP_MEMBER_NOT_OFFICIAL_SCHEMA")
                documents = root.findall("Документ")
                _must(len(documents) <= 1000
                      and len(documents) > 0
                      and root.get("КолДок") == str(len(documents)),
                      "ZIP_MEMBER_RECORD_COUNT_MISMATCH")
                count += len(documents)
                for doc in documents:
                    _must(doc.get("ДатаСост")
                          == release.snapshot_date.strftime("%d.%m.%Y"),
                          "FNS_SNAPSHOT_DATE_DISAGREEMENT")
                    org = doc.find("ОргВклМСП")
                    ip = doc.find("ИПВклМСП")
                    _must((org is None) != (ip is None),
                          "AMBIGUOUS_LEGAL_ENTITY_TYPE")
                    entity = org if org is not None else ip
                    inn = entity.get("ИННЮЛ") if org is not None else entity.get("ИННФЛ")
                    if inn not in wanted:
                        continue
                    _must(inn not in matched, "DUPLICATE_INN_ACROSS_XML_FILES")
                    rec = extract_msp_record(
                        data, target_inn=inn,
                        expected_xml_sha256=sha256(data).hexdigest())
                    _must(rec.snapshot_date == release.snapshot_date,
                          "RECORD_NOT_IN_RELEASE_SNAPSHOT")
                    matched[inn] = rec
    except (BadZipFile, OSError, RuntimeError, EOFError):
        raise ArchiveRejected("FNS_ZIP_INVALID_OR_TRUNCATED") from None
    return ScanResult(
        sha256_zip=actual_digest,
        snapshot=release.snapshot_date.isoformat(),
        member_count=member_count,
        records_scanned=count,
        matched=matched,
        missing_inns=tuple(sorted(wanted-set(matched))),
        status="LOCAL_FNS_ARCHIVE_SCAN_COMPLETE_NOT_EGRUL_OR_LEAD",
    )
