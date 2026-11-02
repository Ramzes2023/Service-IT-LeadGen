"""Independent FNS MSP operator: official passport, opt-in download, ZIP scan.

NOT a commercial lead service, and does not call CRM, Gmail or GigaChat.
Never executes an automated download unless the human enables a local gate.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import httpx

from .serviceit_v2_fns_qualification import EvidenceRejected, checked_inn
from .serviceit_v2_fns_release import (
    FNSDownloadBlocked, discover_release, download_release,
)
from .serviceit_v2_fns_zip import ArchiveRejected, inspect_local_snapshot


def run(argv=None):
    p = argparse.ArgumentParser(description="Service IT FNS MSP official ZIP research")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--manifest", action="store_true")
    mode.add_argument("--download", action="store_true")
    mode.add_argument("--scan", action="store_true")
    p.add_argument("--destination")
    p.add_argument("--archive")
    p.add_argument("--expected-zip-sha256")
    p.add_argument("--inn-file")
    args = p.parse_args(argv)
    try:
        with httpx.Client(trust_env=False, follow_redirects=False) as http:
            release = discover_release(http)
            if args.manifest:
                result = {
                    "status": "OFFICIAL_FNS_PASSPORT_READ_ONLY",
                    "release_zip_url": release.zip_url,
                    "schema_url": release.xsd_url,
                    "snapshot_date": release.snapshot_date.isoformat(),
                    "passport_sha256": release.passport_sha256,
                    "crm_writes": 0,
                }
            elif args.download:
                if os.environ.get("SERVICEIT_V2_FNS_DOWNLOAD_APPROVED") != "YES":
                    raise FNSDownloadBlocked("OWNER_APPROVAL_REQUIRED")
                if not args.destination:
                    raise FNSDownloadBlocked("DESTINATION_REQUIRED")
                dst = Path(args.destination).expanduser().resolve()
                if dst.name != release.zip_url.split("/")[-1]:
                    raise FNSDownloadBlocked("DESTINATION_MUST_MATCH_FNS_RELEASE")
                result = download_release(http, release, dst,
                                          explicitly_approved=True)
            elif args.scan:
                if not (args.archive and args.inn_file
                        and args.expected_zip_sha256):
                    raise ArchiveRejected("ARCHIVE_SHA_AND_TARGETS_REQUIRED")
                filename = Path(args.archive).expanduser().resolve()
                if filename.name != release.zip_url.split("/")[-1]:
                    raise ArchiveRejected("SCANNED_ZIP_NOT_CURRENT_RELEASE")
                targets_file = Path(args.inn_file).expanduser().resolve()
                if not targets_file.is_file() or targets_file.stat().st_size > 4096:
                    raise ArchiveRejected("TARGET_INNS_FILE_MISSING_OR_OVERSIZE")
                targets = [s.strip() for s in
                           targets_file.read_text("utf-8-sig").splitlines()
                           if s.strip()]
                if not (1 <= len(targets) <= 100
                        and len(set(targets)) == len(targets)):
                    raise ArchiveRejected("TARGET_INN_LIST_INVALID")
                for x in targets:
                    checked_inn(x)
                scanned = inspect_local_snapshot(
                    filename, release=release,
                    target_inns=targets,
                    expected_archive_sha256=args.expected_zip_sha256)
                result = {
                    "status": scanned.status,
                    "snapshot_date": scanned.snapshot,
                    "zip_sha256": scanned.sha256_zip,
                    "xml_members_checked": scanned.member_count,
                    "total_msp_records_checked": scanned.records_scanned,
                    "exact_inns_found": len(scanned.matched),
                    "exact_inns_missing": len(scanned.missing_inns),
                    "source_provenance_manually_verified": False,
                    "egrul_verified": False,
                    "qualified_leads": 0,
                    "crm_writes": 0,
                }
            else:
                raise ArchiveRejected("UNSUPPORTED_MODE")
        print(json.dumps(result,ensure_ascii=False),flush=True)
        return 0
    except (FNSDownloadBlocked, ArchiveRejected, EvidenceRejected,
            OSError, ValueError, httpx.HTTPError):
        print(json.dumps({"status": "FNS_RESEARCH_BLOCKED",
                          "secrets_exposed": False, "crm_writes": 0,
                          "qualified_leads": 0}),flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(run())
