#!/usr/bin/env python3
"""Interactive, local-only installation of *available* VPS secrets.

Intended for an operator running on the VPS as root with an interactive TTY.
Never passes a secret on a command line, in environment variables, in GitHub
Actions logs, via a remote CI workflow, or through ChatGPT.

Does not fetch unreadable GitHub Actions secrets. Never overwrites existing
files. Never generates replacements for shared existing signing keys.
"""
from __future__ import annotations

import getpass
import os
from pathlib import Path
import secrets
import stat
import sys

DIRECTORY = Path("/etc/serviceit/secrets")
AUTHORIZED = (
    "bitrix_app_token",
    "event_bridge_key",
    "gh_ledger_key",
    "github_ledger_pat",
    "brave_api_key",
    "b2b_ingest_hmac",
    "routerai_key",
    "gigachat_credentials",
)
# Existing secrets are purposely excluded from all write operations.
EXISTING = ("pg_password", "bitrix_webhook")
# RouterAI-only polling: official outgoing Bitrix event token and GigaChat
# credentials are deliberately NOT required by the reviewed full VPS Compose.
POLL_ONLY_REQUIRED = (
    "pg_password", "bitrix_webhook", "event_bridge_key", "gh_ledger_key",
    "github_ledger_pat", "brave_api_key", "routerai_key",
)
UID = 10001
GID = 10001
MAX_BYTES = 10000


def secure_dir() -> int:
    info = DIRECTORY.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0:
        raise ValueError("SECRET_DIR_NOT_ROOT_OWNED")
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("SECRET_DIR_PERMISSIONS_NOT_0700")
    return os.open(DIRECTORY, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def inventory(dirfd: int, names: tuple[str, ...] | None = None) -> None:
    for name in (EXISTING + AUTHORIZED if names is None else names):
        try:
            fd = os.open(
                name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=dirfd,
            )
        except FileNotFoundError:
            print(f"{name}=MISSING")
            continue
        try:
            meta = os.fstat(fd)
            safe = (
                stat.S_ISREG(meta.st_mode)
                and meta.st_size > 0
                and meta.st_size <= MAX_BYTES
                and stat.S_IMODE(meta.st_mode) == 0o400
                and (meta.st_uid, meta.st_gid) == (UID, GID)
            )
        finally:
            os.close(fd)
        print(f"{name}={'PRESENT_SECURE' if safe else 'PRESENT_CHECK_PERMISSIONS'}")


def put(dirfd: int, name: str) -> None:
    if name not in AUTHORIZED:
        raise ValueError("SECRET_NAME_NOT_ALLOWED")
    try:
        os.stat(name, dir_fd=dirfd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        print(f"{name}=ALREADY_EXISTS_NO_OVERWRITE")
        return
    if not (sys.stdin.isatty() and sys.stderr.isatty()):
        raise ValueError("INTERACTIVE_TTY_REQUIRED")

    print(f"Enter the existing/issued value for {name} into this VPS terminal.")
    print("GitHub Actions secrets cannot be read back: obtain from issuer or")
    print("reissue at provider, then update the other party when applicable.")
    print("Shared keys MUST match their existing Apps Script / GitHub peer.")
    if name == "b2b_ingest_hmac":
        print("For B2B: use exactly SERVICEIT_PRIVATE_CONFIG.hmacSecret from the")
        print("already-authorized Gmail Apps Script secrets.gs; do not invent a new one.")
    value = getpass.getpass(f"{name} (hidden): ")
    raw = value.encode("utf-8")
    if not (1 <= len(raw) <= MAX_BYTES) or b"\x00" in raw or b"\n" in raw or b"\r" in raw:
        raise ValueError("EMPTY_OR_INVALID_SINGLE_LINE_SECRET")

    temp_name = ".secret-stage-" + secrets.token_hex(16)
    fd = -1
    try:
        fd = os.open(
            temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o400, dir_fd=dirfd,
        )
        os.fchown(fd, UID, GID)
        os.fchmod(fd, 0o400)
        with os.fdopen(fd, "wb", closefd=True) as dest:
            fd = -1
            dest.write(raw)
            dest.flush()
            os.fsync(dest.fileno())

        # Link only if the destination is still missing. No replacement.
        os.link(
            temp_name, name, src_dir_fd=dirfd, dst_dir_fd=dirfd,
            follow_symlinks=False,
        )
        os.fsync(dirfd)
        print(f"{name}=INSTALLED_SECURELY")
    finally:
        if fd != -1:
            os.close(fd)
        try:
            os.unlink(temp_name, dir_fd=dirfd)
        except FileNotFoundError:
            pass


def main() -> int:
    args = sys.argv[1:]
    if args not in (["inventory"], ["inventory-poll-only"]) and not (
        len(args) == 2 and args[0] == "put" and args[1] in AUTHORIZED
    ):
        print("Usage: python3 script.py inventory | inventory-poll-only | put <allowed-secret-name>")
        return 2
    if os.geteuid() != 0:
        print("ROOT_REQUIRED")
        return 2
    try:
        dirfd = secure_dir()
        try:
            if args[0] == "inventory":
                inventory(dirfd)
            elif args[0] == "inventory-poll-only":
                inventory(dirfd, POLL_ONLY_REQUIRED)
            else:
                put(dirfd, args[1])
        finally:
            os.close(dirfd)
    except (OSError, ValueError, EOFError, KeyboardInterrupt) as exc:
        # Fixed error type; no echoing secret values.
        print(f"SECRET_INSTALL_BLOCKED={type(exc).__name__}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
