#!/usr/bin/env python3
"""Root-interactive, tightly-scoped Caddy route activation for B2B VPS only.

No CRM, Gmail, PostgreSQL or Docker changes. Never prints secrets. Requires:
the separate B2B receiver already running/healthy on localhost:18781 and a
secure 0400 HMAC file owned by UID/GID 10001. Fails closed / backup + restore.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from urllib.request import urlopen
from urllib.error import URLError

CADDYFILE = Path("/etc/caddy/Caddyfile")
SECRET = Path("/etc/serviceit/secrets/b2b_ingest_hmac")
UPSTREAM = "127.0.0.1:18781"
ROUTE = "/integrations/serviceit/b2b-center/notice"
FRAGMENT = """    @serviceit_b2b_direct {
        path /integrations/serviceit/b2b-center/notice /integrations/serviceit/b2b-center/probe /integrations/serviceit/b2b-center/health
    }
    handle @serviceit_b2b_direct {
        request_body {
            max_size 6KB
        }
        reverse_proxy 127.0.0.1:18781
    }
"""


class ActivationBlocked(ValueError):
    pass


def render(original: str) -> str:
    """Insert a fixed fragment ahead of catchall or at the end of target site."""
    if not isinstance(original, str) or len(original) > 2_000_000:
        raise ActivationBlocked("CADDYFILE_UNEXPECTED")
    if ROUTE in original:
        if UPSTREAM not in original:
            raise ActivationBlocked("CADDY_ROUTE_PRESENT_WITH_WRONG_UPSTREAM")
        return original
    candidates = list(re.finditer(
        r"(?m)^leadgen\.serviseit\.ru \{\s*$", original))
    if len(candidates) != 1:
        raise ActivationBlocked("CADDY_SITE_NOT_UNIQUE")
    match = candidates[0]
    # Find the matching site-closing brace, ignoring comment lines and quotes.
    # This fails on exotic multiline strings (which require explicit review).
    start = match.end()
    depth = 1
    in_quote = False
    escape = False
    end = -1
    for i in range(start, len(original)):
        c = original[i]
        if c == '"' and not escape:
            in_quote = not in_quote
        if not in_quote:
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        escape = c == "\\" and not escape
        if c != "\\":
            escape = False
    if end < 0 or in_quote:
        raise ActivationBlocked("CADDY_SITE_UNBALANCED")
    inside = original[start:end]
    if not ("/health" in inside and "/integrations/serviceit/bitrix24/new-card" in inside):
        raise ActivationBlocked("CADDY_EXISTING_ROUTES_UNVERIFIED")
    # Production can have a valid Caddyfile WITHOUT an explicit catch-all
    # 'handle {'; in that case insert before the site's closing brace.
    # A matcher-specific 'handle @serviceit_b2b_direct' is then sorted by
    # Caddy independently of the site's existing generic routes.
    # If the site has a single direct catch-all, preserve its original
    # ordering by inserting ahead of it as in the original installer.
    markers = list(re.finditer(r"(?m)^[ \\t]*handle[ \\t]*\\{[ \\t]*(?:#.*)?$", inside))
    if len(markers) > 1:
        raise ActivationBlocked("CADDY_MULTIPLE_CATCHALLS_REVIEW")
    pos = start + markers[0].start() if markers else end
    return original[:pos] + FRAGMENT + original[pos:]


def require_receiver():
    import json
    try:
        with urlopen("http://127.0.0.1:18781/health", timeout=3) as result:
            if result.status != 200:
                raise ActivationBlocked("B2B_RECEIVER_NOT_READY")
            payload = json.loads(result.read(600))
    except (OSError, URLError, ValueError):
        raise ActivationBlocked("B2B_RECEIVER_NOT_READY") from None
    if payload != {
        "status":"B2B_INGRESS_ALIVE",
        "source_enabled":True,
        "crm_writes":0,
    }:
        raise ActivationBlocked("B2B_RECEIVER_NOT_AUTHORIZED")


def require_secret():
    info = SECRET.lstat()
    if (not stat.S_ISREG(info.st_mode)
        or (info.st_uid, info.st_gid) != (10001, 10001)
        or stat.S_IMODE(info.st_mode) != 0o400
        or not 32 <= info.st_size <= 10000):
        raise ActivationBlocked("B2B_HMAC_FILE_PERMISSIONS_OR_SIZE_INVALID")


def run_checked(argv):
    p = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=15, check=False)
    if p.returncode != 0:
        raise ActivationBlocked("CADDY_VALIDATE_OR_RELOAD_FAILED")


def activate():
    if os.geteuid() != 0 or not (sys.stdin.isatty() and sys.stderr.isatty()):
        raise ActivationBlocked("ROOT_INTERACTIVE_TTY_REQUIRED")
    require_secret()
    require_receiver()
    info = CADDYFILE.lstat()
    if not stat.S_ISREG(info.st_mode) or CADDYFILE.is_symlink():
        raise ActivationBlocked("CADDYFILE_NOT_REGULAR")
    previous = CADDYFILE.read_text("utf-8")
    expected = render(previous)
    if previous == expected:
        print("B2B_CADDY_ROUTE_ALREADY_PRESENT=YES")
        return
    backup = CADDYFILE.with_name(
        "Caddyfile.b2b-backup-" +
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    if backup.exists():
        raise ActivationBlocked("CADDY_BACKUP_ALREADY_EXISTS")
    shutil.copy2(CADDYFILE, backup, follow_symlinks=False)
    os.chmod(backup, 0o600)
    replaced = False
    try:
        CADDYFILE.write_text(expected, "utf-8")
        os.chmod(CADDYFILE, stat.S_IMODE(info.st_mode))
        os.chown(CADDYFILE, info.st_uid, info.st_gid)
        replaced = True
        run_checked(["caddy","validate","--config",str(CADDYFILE),
                     "--adapter","caddyfile"])
        run_checked(["systemctl","reload","caddy"])
        run_checked(["systemctl","is-active","--quiet","caddy"])
    except Exception:
        if replaced:
            shutil.copy2(backup, CADDYFILE)
            try:
                run_checked(["caddy","validate","--config",str(CADDYFILE),
                             "--adapter","caddyfile"])
                run_checked(["systemctl","reload","caddy"])
            except Exception:
                print("B2B_CADDY_ROLLBACK_REQUIRES_OPERATOR")
                raise
        raise
    print("B2B_CADDY_ROUTE_VALIDATED_AND_RELOADED=YES")
    print("B2B_EXISTING_ROUTES_PRESERVED=YES")
    print("B2B_CRM_WRITES_BY_INSTALLER=ZERO")


if __name__ == "__main__":
    try:
        activate()
    except (ActivationBlocked, OSError, ValueError, subprocess.TimeoutExpired):
        print("B2B_CADDY_INSTALL_BLOCKED=YES")
        raise SystemExit(2)
