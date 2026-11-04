"""Corporate-free, public official FNS MSP ZIP availability/size HEAD probe.

Never downloads the archive, sends any company identifiers or calls the CRM.
The official public ZIP is *MSP membership* only, not legal EGRUL status.
"""
from __future__ import annotations

import json
from urllib.parse import urljoin, urlsplit

import httpx

SOURCE = ("https://www.nalog.gov.ru/opendata/7707329152-rsmp/"
          "data-10102026-structure-12052026.zip")
ALLOWED = {"www.nalog.gov.ru", "file.nalog.ru"}
EXPECTED_PATH = "/opendata/7707329152-rsmp/data-10102026-structure-12052026.zip"
MAX_ARCHIVE = 3 * 1024**3


def inspect(client, url=SOURCE):
    """HEAD only; HTTPS and exact official dataset identity on redirects."""
    if not isinstance(client, httpx.Client):
        raise ValueError("CLIENT_REQUIRED")
    visited = set()
    try:
        for _ in range(4):
            parsed = urlsplit(url)
            if (parsed.scheme != "https"
                    or parsed.netloc not in ALLOWED
                    or parsed.path != EXPECTED_PATH
                    or parsed.query or parsed.fragment
                    or url in visited):
                return {"status": "FNS_OFFICIAL_SOURCE_IDENTITY_BLOCKED",
                        "downloaded": False}
            visited.add(url)
            response = client.head(url, timeout=25,
                                   follow_redirects=False)
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location", "")
                if not location or len(location) > 500:
                    break
                url = urljoin(url, location)
                continue
            if response.status_code != 200:
                return {"status": "FNS_PUBLIC_HEAD_NOT_AVAILABLE",
                        "downloaded": False}
            raw = response.headers.get("content-length")
            content_type = response.headers.get("content-type", "").lower()
            if "text/html" in content_type or "text/plain" in content_type:
                return {"status": "FNS_HEAD_NOT_ZIP", "downloaded": False}
            if raw is None or not raw.isascii() or not raw.isdecimal():
                return {"status": "FNS_HEAD_LENGTH_UNKNOWN",
                        "downloaded": False}
            size = int(raw)
            if size < 22 or size > MAX_ARCHIVE:
                return {"status": "FNS_ZIP_OUTSIDE_RUNNER_BUDGET",
                        "downloaded": False,
                        "size_over_limit": size > MAX_ARCHIVE}
            return {"status": "FNS_ZIP_HEAD_READY_FOR_CONTROLLED_INGEST",
                    "downloaded": False,
                    "size_bytes": size,
                    "egrul_status_verified": False,
                    "signature_or_digest_verified": False}
    except (httpx.HTTPError, ValueError, TypeError, UnicodeError):
        pass
    return {"status": "FNS_PUBLIC_HEAD_UNVERIFIED", "downloaded": False}


def main():
    try:
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            result = inspect(client)
        print(json.dumps(result, sort_keys=True), flush=True)
        return 0
    except Exception:
        print(json.dumps({"status":"FNS_PUBLIC_HEAD_UNVERIFIED",
                          "downloaded":False}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
