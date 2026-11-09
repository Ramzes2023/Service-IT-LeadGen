#!/usr/bin/env python3
"""Private, native ServiceIT VPS PostgreSQL backup and isolated restore drill.

No network except local DB for pg_dump. Does not write to the production DB,
does not contact Bitrix, does not send archives to GitHub or external servers.
Cron uses --backup-only; explicit owner acceptance uses --backup-and-drill.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid

CONTAINER = "serviceit-leadgen-vps-coordinator-1"
DB = "leadgen"
ROLE = "leadgen"
BACKUPS = Path.home() / "serviceit" / "backups"
OPS = Path.home() / "serviceit" / "ops"
BACKUP_FILE_RE = re.compile(r"svc-it-leadgen-\d{8}T\d{6}Z-[a-f0-9]{8}\.dump\Z")
KEEP = 14
MAX_BACKUP_BYTES = 1024 * 1024 * 1024
DRILL_PORT = "65417"


class Blocked(Exception):
    pass


def need(ok: bool, reason: str) -> None:
    if not ok:
        raise Blocked(reason)


def safe_run(args: list[str], *, timeout=180, env=None, stdout=subprocess.DEVNULL) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            args, stdin=subprocess.DEVNULL, stdout=stdout,
            stderr=subprocess.DEVNULL, timeout=timeout, env=env, check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        raise Blocked("LOCAL_EXEC_FAILED_OR_TIMEOUT") from None


def success(args: list[str], *, timeout=180, env=None) -> None:
    need(safe_run(args, timeout=timeout, env=env).returncode == 0,
         "BACKUP_OR_RESTORE_COMMAND_FAILED")


def owned_private_directory(path: Path, *, create=False) -> None:
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    need(path.is_dir() and not path.is_symlink(), "PRIVATE_DIRECTORY_INVALID")
    st = path.stat()
    need(st.st_uid == os.geteuid() and
         not (stat.S_IMODE(st.st_mode) & 0o077),
         "PRIVATE_DIRECTORY_PERMISSIONS_INVALID")


@contextmanager
def exclusive_backup_lock():
    owned_private_directory(OPS, create=True)
    lock = OPS / ".pg-backup.lock"
    need(not lock.is_symlink(), "BACKUP_LOCK_SYMLINK")
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Blocked("BACKUP_ALREADY_RUNNING") from None
        yield
    finally:
        os.close(descriptor)


def read_local_pg_password() -> str:
    # Read mounted service secret from existing live container into memory only.
    # Neither the password nor pg_dump arguments containing it are ever logged.
    script = (
        "import os;"
        "from leadpilot.serviceit_vps_pg_queue import secret_file;"
        "print(secret_file(dict(os.environ),'SERVICEIT_PG_PASSWORD'))"
    )
    try:
        result = subprocess.run(
            ["docker", "exec", CONTAINER, "python", "-c", script],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise Blocked("PG_CREDENTIAL_NOT_AVAILABLE") from None
    need(result.returncode == 0 and 1 <= len(result.stdout) <= 2048
         and result.stdout.endswith(b"\n") and result.stdout.count(b"\n") == 1,
         "PG_CREDENTIAL_NOT_VERIFIABLE")
    try:
        password = result.stdout[:-1].decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise Blocked("PG_CREDENTIAL_ENCODING_INVALID") from None
    need(bool(password) and "\x00" not in password and "\n" not in password,
         "PG_CREDENTIAL_UNSAFE")
    return password


def validate_dump(path: Path) -> None:
    need(path.is_file() and not path.is_symlink(), "DUMP_FILE_NOT_REGULAR")
    st = path.stat()
    need(st.st_uid == os.geteuid() and
         not (stat.S_IMODE(st.st_mode) & 0o077)
         and 5 <= st.st_size <= MAX_BACKUP_BYTES,
         "DUMP_OWNER_MODE_OR_SIZE_INVALID")
    with path.open("rb") as handle:
        need(handle.read(5) == b"PGDMP", "DUMP_MAGIC_MISMATCH")
    success(["pg_restore", "--list", str(path)], timeout=120)
    # Decodes every data block without executing any SQL or writing anywhere.
    success(["pg_restore", "--file", "/dev/null", str(path)], timeout=180)


def create_backup() -> Path:
    need(os.geteuid() != 0, "ROOT_BACKUP_EXECUTION_REFUSED")
    owned_private_directory(OPS, create=True)
    owned_private_directory(BACKUPS, create=True)
    need(shutil.which("pg_dump") is not None and
         shutil.which("pg_restore") is not None, "PG_TOOLS_NOT_INSTALLED")
    ready = safe_run(["pg_isready", "-h", "127.0.0.1", "-p", "5432",
                      "-d", DB], timeout=10)
    need(ready.returncode == 0, "LOCAL_PG_NOT_READY")
    need(shutil.disk_usage(BACKUPS).free >= 256 * 1024 * 1024,
         "BACKUP_DISK_SPACE_TOO_LOW")
    when = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    unique = uuid.uuid4().hex[:8]
    archive = BACKUPS / ("svc-it-leadgen-" + when + "-" + unique + ".dump")
    need(BACKUP_FILE_RE.fullmatch(archive.name) is not None
         and not archive.exists(), "BACKUP_FILENAME_UNSAFE")
    temp = BACKUPS / ("." + archive.name + ".tmp")
    need(not temp.exists(), "TEMP_BACKUP_ALREADY_EXISTS")
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.close(descriptor)
    try:
        password = read_local_pg_password()
        environment = os.environ.copy()
        environment.update({
            "PGPASSWORD": password,
            "PGCONNECT_TIMEOUT": "10",
            "PGSSLMODE": "disable",  # local 127.0.0.1 only
        })
        password = ""  # remove application-level reference
        try:
            success([
                "pg_dump", "--format=custom", "--compress=6",
                "--no-owner", "--no-privileges",
                "-h", "127.0.0.1", "-p", "5432", "-U", ROLE,
                "-d", DB, "-f", str(temp),
            ], timeout=240, env=environment)
        finally:
            environment.pop("PGPASSWORD", None)
        validate_dump(temp)
        # Add persistent file only after both verification passes.
        os.replace(temp, archive)
        validate_dump(archive)
        return archive
    finally:
        if temp.exists():
            temp.unlink()


def trim_successful_archives() -> None:
    files = [p for p in BACKUPS.iterdir()
             if BACKUP_FILE_RE.fullmatch(p.name) and
             p.is_file() and not p.is_symlink() and p.stat().st_uid == os.geteuid()]
    for path in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[KEEP:]:
        # Never delete unrelated files, the root-owned historical archive,
        # or the most recent 14 verified private backups.
        path.unlink()


def pg_binary(name: str) -> str:
    for major in ("16", "17", "18"):
        candidate = Path("/usr/lib/postgresql") / major / "bin" / name
        if candidate.is_file():
            return str(candidate)
    value = shutil.which(name)
    need(value is not None, "RESTORE_BINARY_MISSING")
    return str(value)


def restore_into_temporary_local_cluster(archive: Path) -> None:
    """Real restore into private non-networked disposable PostgreSQL instance."""
    validate_dump(archive)
    need(shutil.disk_usage(tempfile.gettempdir()).free >=
         max(384 * 1024 * 1024, archive.stat().st_size * 10),
         "RESTORE_TEMP_DISK_TOO_SMALL")
    initdb, pg_ctl = pg_binary("initdb"), pg_binary("pg_ctl")
    createdb, pg_restore = pg_binary("createdb"), pg_binary("pg_restore")
    psql = pg_binary("psql")
    with tempfile.TemporaryDirectory(prefix="svc-it-restore-") as folder:
        root = Path(folder)
        os.chmod(root, 0o700)
        data = root / "data"
        socket = root / "sock"
        socket.mkdir(mode=0o700)
        log = root / "pg.log"
        start_ok = False
        try:
            success([
                initdb, "--no-sync", "--no-instructions",
                "-A", "trust", "-U", "restore_checker", "-D", str(data)
            ], timeout=60)
            # This temp cluster has NO TCP listener and uses a private socket
            # and a different port. It cannot interact with production PG5432.
            options = (
                "-c listen_addresses= -c unix_socket_directories=" + str(socket) +
                " -c port=" + DRILL_PORT +
                " -c fsync=off -c shared_buffers=16MB -c max_connections=10"
            )
            success([pg_ctl, "-D", str(data), "-l", str(log),
                     "-o", options, "-w", "start"], timeout=70)
            start_ok = True
            prefix = ["-h", str(socket), "-p", DRILL_PORT,
                      "-U", "restore_checker"]
            success([createdb, *prefix, "serviceit_restore_drill"], timeout=20)
            success([pg_restore, "--exit-on-error", "--no-owner",
                     "--no-privileges", *prefix,
                     "-d", "serviceit_restore_drill", str(archive)],
                    timeout=180)
            for table in (
                "serviceit_vps_new_cards",
                "serviceit_vps_native_receipts",
                "serviceit_vps_native_actions",
                "serviceit_vps_poll_state",
            ):
                result = safe_run([
                    psql, *prefix, "-d", "serviceit_restore_drill",
                    "-X", "-t", "-A", "-c",
                    "SELECT to_regclass('public." + table + "') IS NOT NULL;",
                ], timeout=20, stdout=subprocess.PIPE)
                need(result.returncode == 0 and result.stdout.strip() == b"t",
                     "ISOLATED_RESTORE_SCHEMA_MISSING")
        finally:
            if start_ok:
                stopped = safe_run([pg_ctl, "-D", str(data),
                                    "-m", "immediate", "-w", "stop"], timeout=45)
                need(stopped.returncode == 0, "ISOLATED_POSTGRES_SHUTDOWN_FAILED")


def main() -> int:
    os.umask(0o077)
    mode = sys.argv[1:] if len(sys.argv) == 2 else []
    if mode not in (["--backup-only"], ["--backup-and-drill"]):
        print("SERVICEIT_BACKUP=BLOCKED_INVALID_MODE", flush=True)
        return 2
    try:
        with exclusive_backup_lock():
            archive = create_backup()
            print("SERVICEIT_BACKUP=SUCCESS", flush=True)
            print("SERVICEIT_BACKUP_CUSTOM_ARCHIVE_VALIDATED=YES", flush=True)
            if mode == ["--backup-and-drill"]:
                restore_into_temporary_local_cluster(archive)
                print("SERVICEIT_ISOLATED_FULL_RESTORE=PASS", flush=True)
                print("SERVICEIT_RESTORED_REQUIRED_SCHEMAS=YES", flush=True)
            trim_successful_archives()
            print("SERVICEIT_BACKUP_KEEP_LAST=14", flush=True)
            print("SERVICEIT_PRODUCTION_DATABASE_MUTATIONS=0", flush=True)
            print("SERVICEIT_SOURCE_CHANGES=0", flush=True)
        return 0
    except Exception as exc:
        reason = str(exc) if isinstance(exc, Blocked) else "UNEXPECTED_FAILURE"
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,80}", reason):
            reason = "UNEXPECTED_FAILURE"
        print("SERVICEIT_BACKUP=BLOCKED_" + reason, flush=True)
        print("SERVICEIT_PRODUCTION_DATABASE_MUTATIONS=0", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
