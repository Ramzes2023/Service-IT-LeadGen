#!/usr/bin/env python3
"""Read-only live VPS proof of free public source installation readiness.

Invoked by owner-only GitHub Actions via docker exec -i, with GitHub file
hashes as argv. No API calls, DB queries, source fetch or CRM side effects.
Only bounded fixed status tokens are printed; never prints key/path/URLs.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import sys

NAMES = (
    "serviceit_b2b_free_public_fallback.py",
    "serviceit_b2b_remote_public_fetch.py",
    "serviceit_b2b_public_fact_extractor.py",
    "serviceit_vps_b2b_deep_research.py",
)
BASE = Path("/app/leadpilot")


def code_state(expected: list[str]) -> str:
    if len(expected) != len(NAMES) or not all(
            re.fullmatch(r"[0-9a-f]{64}", x or "") for x in expected):
        return "INVALID_EXPECTED_SHA"
    observed = []
    for name in NAMES:
        path = BASE / name
        if path.is_symlink() or not path.is_file():
            return "NOT_INSTALLED"
        observed.append(hashlib.sha256(path.read_bytes()).hexdigest())
    return "EXACT" if observed == expected else "DIFFERENT_IMAGE"


def secret_state(env: dict) -> str:
    secret = env.get("SERVICEIT_TINYFISH_API_KEY_FILE", "")
    if not secret:
        return "NOT_CONFIGURED"
    if not secret.startswith("/run/secrets/"):
        return "UNSAFE_LOCATION"
    path = Path(secret)
    try:
        if path.is_symlink() or not path.is_file():
            return "NOT_MOUNTED"
        if not 16 <= path.stat().st_size <= 256:
            return "INVALID_SIZE"
    except (OSError, ValueError):
        return "NOT_MOUNTED"
    return "MOUNTED_SIZE_OK_NOT_VALIDATED"


def main(argv):
    env = os.environ
    statuses = {
        "CODE": code_state(argv),
        "KEY": secret_state(env),
        "ENABLED": "YES" if env.get(
            "SERVICEIT_VPS_B2B_FREE_PUBLIC_FETCH_ENABLED") == "YES" else "NO",
        "TENDEM_PERMISSION": "YES" if env.get(
            "SERVICEIT_B2B_TENDEM_PUBLIC_EXTRACTION_APPROVED") == "YES" else "NO",
        "B2B_PERMISSION": "YES" if env.get(
            "SERVICEIT_B2B_CENTER_PUBLIC_EXTRACTION_LICENSED") == "YES" else "NO",
        "WRITER": "NO",
    }
    for k, v in statuses.items():
        print("FREE_SOURCE_" + k + "=" + v, flush=True)
    return 0 if statuses["CODE"] != "INVALID_EXPECTED_SHA" else 3


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
