"""Zero-identifier public FNS MSP passport preflight: only GET HTML + HEAD ZIP.

Runs independently of CRM; never sends an INN, never downloads a ZIP,
never authorizes an EGRUL status, qualified score, outreach or mutation.
The actual ZIP download remains strictly gated by fresh CRM card + valid INN.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json

import httpx

from .serviceit_fns_msp_official_live import (
    MAX_ZIP_BYTES, OfficialMspBlocked, dated_archive_url,
    discover_official_url, follow_official_redirect,
)

def inspect(client, *, now=None):
    now = now or datetime.now(timezone.utc)
    if (not isinstance(client, httpx.Client)
            or not isinstance(now, datetime)
            or now.tzinfo is None or now.utcoffset() is None):
        raise OfficialMspBlocked("FNS_PROBE_CLOCK_INVALID")
    today = now.astimezone(timezone.utc).date()
    url, published = discover_official_url(client, today=today)
    visited = set()
    for _ in range(5):
        if url in visited:
            raise OfficialMspBlocked("FNS_PROBE_REDIRECT_LOOP")
        visited.add(url)
        if dated_archive_url(url, today) != published:
            raise OfficialMspBlocked("FNS_PROBE_DATASET_MISMATCH")
        with client.stream("HEAD", url, timeout=30, follow_redirects=False) as response:
            if response.status_code in (301,302,303,307,308):
                url = follow_official_redirect(
                    url, response.headers.get("location"), today, published)
                continue
            if response.status_code != 200:
                raise OfficialMspBlocked("FNS_PROBE_ZIP_HEAD_UNAVAILABLE")
            raw = response.headers.get("content-length")
            mime = response.headers.get("content-type","").lower()
            if (type(raw) is not str or not raw.isascii() or not raw.isdecimal()
                    or not 22 <= int(raw) <= MAX_ZIP_BYTES
                    or "html" in mime or "plain" in mime):
                raise OfficialMspBlocked("FNS_PROBE_ZIP_HEADERS_INVALID")
            return {
                "status": "FNS_MSP_PUBLIC_PASSPORT_READY",
                "official_snapshot_date": published.isoformat(),
                "zip_size_bytes": int(raw),
                "passport_verified_by_https": True,
                "zip_downloaded": False,
                "independent_file_signature_verified": False,
                "egrul_egrip_verified": False,
                "formal_score_authorized": False,
                "crm_writes": 0,
                "new_sources_integrated": 0,
            }
    raise OfficialMspBlocked("FNS_PROBE_TOO_MANY_REDIRECTS")

def main():
    try:
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            result=inspect(client)
        print(json.dumps(result, sort_keys=True), flush=True)
        return 0
    except (OfficialMspBlocked, httpx.HTTPError, ValueError, TypeError):
        print(json.dumps({
            "status":"FNS_MSP_PUBLIC_PASSPORT_UNVERIFIED",
            "zip_downloaded":False,
            "egrul_egrip_verified":False,
            "formal_score_authorized":False,
            "crm_writes":0,
        },sort_keys=True),flush=True)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
