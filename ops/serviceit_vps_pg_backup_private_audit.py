#!/usr/bin/env python3
"""Read-only inventory of the actual native ServiceIT private PostgreSQL backups.

Never prints archive paths, filenames, data, SQL, credential values or cron text.
Never creates files, starts containers, alters crontabs or touches production DB.
"""
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

ARCHIVE_RE = re.compile(r"svc-it-leadgen-\d{8}T\d{6}Z-[a-f0-9]{8}\.dump\Z")
CRON_MARKER = "# SERVICEIT_NATIVE_PG_BACKUP_V1"


def probe(home=None, now=None, expected_sha=None, run=None):
    home = Path.home() if home is None else Path(home)
    now = time.time() if now is None else now
    run = subprocess.run if run is None else run
    root = home / "serviceit" / "backups"
    program = home / "serviceit" / "ops" / "serviceit_vps_pg_backup.py"
    log = home / "serviceit" / "ops" / "backup-scheduler.log"
    result = {
        "PRIVATE_DIR_EXISTS": "NO",
        "PRIVATE_DIR_OWNER_MODE_OK": "NO",
        "PRIVATE_ARCHIVE_COUNT": "UNVERIFIED",
        "PRIVATE_NEWEST_AGE_HOURS": "UNVERIFIED",
        "PRIVATE_NEWEST_SIZE_BYTES": "UNVERIFIED",
        "PRIVATE_NEWEST_PGDUMP_MAGIC": "UNVERIFIED",
        "PRIVATE_NEWEST_TOC_DECODED": "UNVERIFIED",
        "PRIVATE_NEWEST_FULL_DECODED": "UNVERIFIED",
        "PRIVATE_SCRIPT_EXACT_SHA": "UNVERIFIED",
        "PRIVATE_NATIVE_CRON_INSTALLED": "NO_OR_UNVERIFIED",
        "PRIVATE_NATIVE_CRON_ACTIVE": "NO_OR_UNVERIFIED",
        "PRIVATE_ISOLATED_RESTORE_CURRENT": "NOT_TESTED",
        "PRIVATE_CRM_WRITES": "ZERO",
        "PRIVATE_AUDIT_COMPLETE": "YES",
    }

    def safe(args, *, timeout=40, stdout=subprocess.DEVNULL):
        try:
            return run(args, stdout=stdout, stderr=subprocess.DEVNULL,
                       text=True, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None

    try:
        if expected_sha and re.fullmatch(r"[0-9a-f]{64}", expected_sha):
            if program.is_file() and not program.is_symlink():
                result["PRIVATE_SCRIPT_EXACT_SHA"] = (
                    "YES" if hashlib.sha256(program.read_bytes()).hexdigest() == expected_sha
                    else "NO")
    except OSError:
        pass

    try:
        if root.is_dir() and not root.is_symlink():
            result["PRIVATE_DIR_EXISTS"] = "YES"
            mode = root.stat()
            valid = (mode.st_uid == os.geteuid()
                     and not (stat.S_IMODE(mode.st_mode) & 0o077)
                     and os.access(root, os.R_OK | os.X_OK))
            if valid:
                result["PRIVATE_DIR_OWNER_MODE_OK"] = "YES"
                archives = []
                for path in root.iterdir():
                    if not ARCHIVE_RE.fullmatch(path.name):
                        continue
                    if path.is_symlink() or not path.is_file():
                        continue
                    info = path.stat()
                    if info.st_uid == os.geteuid() and not (
                            stat.S_IMODE(info.st_mode) & 0o077):
                        archives.append((path, info))
                result["PRIVATE_ARCHIVE_COUNT"] = str(len(archives))
                if archives:
                    newest, info = max(archives, key=lambda row: row[1].st_mtime)
                    result["PRIVATE_NEWEST_AGE_HOURS"] = str(
                        max(0, int((now - info.st_mtime) // 3600)))
                    result["PRIVATE_NEWEST_SIZE_BYTES"] = str(info.st_size)
                    with newest.open("rb") as f:
                        result["PRIVATE_NEWEST_PGDUMP_MAGIC"] = (
                            "YES" if f.read(5) == b"PGDMP" else "NO")
                    if result["PRIVATE_NEWEST_PGDUMP_MAGIC"] == "YES":
                        toc = safe(["pg_restore", "--list", str(newest)], timeout=75)
                        full = safe(["pg_restore", "--file", "/dev/null", str(newest)],
                                    timeout=120)
                        result["PRIVATE_NEWEST_TOC_DECODED"] = (
                            "YES" if toc and toc.returncode == 0 else "NO_OR_UNVERIFIED")
                        result["PRIVATE_NEWEST_FULL_DECODED"] = (
                            "YES" if full and full.returncode == 0 else "NO_OR_UNVERIFIED")
    except (OSError, OverflowError, ValueError):
        pass

    expected_cron = (
        "17 2 * * * /usr/bin/python3 " + str(program)
        + " --backup-only >> " + str(log) + " 2>&1 " + CRON_MARKER
    )
    cron = safe(["crontab", "-l"], timeout=10, stdout=subprocess.PIPE)
    if cron and cron.returncode == 0:
        lines = cron.stdout.splitlines()
        result["PRIVATE_NATIVE_CRON_INSTALLED"] = (
            "YES" if lines.count(expected_cron) == 1 and
            sum(CRON_MARKER in row for row in lines) == 1 else "NO")
    active = safe(["systemctl", "is-active", "cron"], timeout=10,
                  stdout=subprocess.PIPE)
    if active and active.returncode == 0:
        result["PRIVATE_NATIVE_CRON_ACTIVE"] = (
            "YES" if active.stdout.strip() == "active" else "NO")
    return result


def main():
    expected = sys.argv[1] if len(sys.argv) == 2 else None
    data = probe(expected_sha=expected)
    for key, val in data.items():
        # All printed values are strictly booleans, short enums or bounded counts.
        value = str(val)
        if re.fullmatch(r"(?:YES|NO|ZERO|UNVERIFIED|NO_OR_UNVERIFIED|NOT_TESTED|[0-9]{1,12})", value):
            print("BACKUP_" + key + "=" + value, flush=True)
        else:
            print("BACKUP_" + key + "=UNVERIFIED", flush=True)


if __name__ == "__main__":
    main()
