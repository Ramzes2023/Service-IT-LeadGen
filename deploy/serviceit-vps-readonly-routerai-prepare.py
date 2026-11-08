#!/usr/bin/env python3
"""Prepare the two already-approved RouterAI privacy flags on a READONLY VPS.

This script is deliberately NOT a production cutover tool. It never talks to
Bitrix, RouterAI, PostgreSQL, Docker, GitHub, or any external network. It only
updates exactly two non-secret settings in a private local vps.env after hard
fail-closed validation. The existing shadow installer must be rerun separately.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import stat
import tempfile
from datetime import datetime, timezone

APPROVAL = "PREPARE_ROUTERAI_1062_21_READONLY"
PRIVACY = {
    "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED": "YES",
    "SERVICEIT_ROUTERAI_PRIVACY_APPROVED": "YES_1062_21",
}
HARD_GATES = {
    "SERVICEIT_VPS_WRITER_MODE": "READONLY",
    "SERVICEIT_VPS_CUTOVER_AUTHORIZED": "NO",
    "SERVICEIT_VPS_POLL_ENABLED": "NO",
    "SERVICEIT_VPS_INTAKE_ENABLED": "NO",
}
OPTIONAL_DISABLED = (
    "SERVICEIT_VPS_OWN_STOP_ENABLED",
    "SERVICEIT_VPS_CRM_DUPLICATE_ENABLED",
    "SERVICEIT_VPS_KANBAN_TITLE_ENABLED",
    "SERVICEIT_VPS_MANAGER_TASK_ENABLED",
    "SERVICEIT_VPS_FNS_CRM_RESULT_ENABLED",
    "SERVICEIT_VPS_PROVISIONAL_SCORE_ENABLED",
    "SERVICEIT_VPS_FREE_MSP_SCORE_ENABLED",
    "SERVICEIT_VPS_POSTCALL_ENABLED",
    "SERVICEIT_VPS_FINANCE_ENABLED",
    "SERVICEIT_VPS_FNS_MSP_ENABLED",
)
KEY_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")


class Blocked(Exception):
    pass


def read_private_config(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise Blocked("CONFIG_NOT_A_REGULAR_FILE")
    metadata = path.stat()
    if metadata.st_uid != os.geteuid():
        raise Blocked("CONFIG_NOT_OWNED_BY_DEPLOY_USER")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise Blocked("CONFIG_NOT_PRIVATE")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise Blocked("CONFIG_NOT_UTF8") from exc


def parse(raw: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in raw.splitlines():
        if not line or line.lstrip().startswith("#"):
            continue
        match = KEY_LINE.fullmatch(line)
        if not match:
            raise Blocked("UNSUPPORTED_CONFIG_LINE")
        key, value = match.groups()
        if key in values:
            raise Blocked("DUPLICATE_CONFIG_KEY")
        values[key] = value
    return values


def validate_safety(values: dict[str, str]) -> None:
    for key, required in HARD_GATES.items():
        if values.get(key) != required:
            raise Blocked("READONLY_GATE_INVALID_" + key)
    if values.get("SERVICEIT_VPS_NATIVE_ACTIVATION_UTC", ""):
        raise Blocked("NATIVE_ACTIVATION_MUST_BE_EMPTY")
    # The old-GitHub acknowledgement defaults to NO in Docker Compose.
    # It may be omitted from the host vps.env but can never be YES here.
    if values.get("SERVICEIT_VPS_OLD_GH_WRITERS_DISABLED_ACK", "NO") != "NO":
        raise Blocked("OLD_GH_WRITER_ACK_MUST_REMAIN_NO")
    for key in OPTIONAL_DISABLED:
        if values.get(key, "NO") != "NO":
            raise Blocked("AUX_WRITER_MUST_REMAIN_OFF_" + key)
    for key in PRIVACY:
        if values.get(key, "NO") not in ("NO", PRIVACY[key]):
            raise Blocked("UNRECOGNIZED_PRIVACY_FLAG_" + key)


def prepared_content(raw: str) -> str:
    values = parse(raw)
    validate_safety(values)
    newline = "\r\n" if "\r\n" in raw else "\n"
    output = []
    seen = set()
    for line in raw.splitlines(keepends=True):
        match = KEY_LINE.fullmatch(line.rstrip("\r\n"))
        if match and match.group(1) in PRIVACY:
            key = match.group(1)
            output.append(key + "=" + PRIVACY[key] + newline)
            seen.add(key)
        else:
            output.append(line)
    result = "".join(output)
    if result and not result.endswith(("\n", "\r")):
        result += newline
    for key, value in PRIVACY.items():
        if key not in seen:
            result += key + "=" + value + newline
    validate_safety(parse(result))
    return result


def apply_readonly_privacy(path: Path) -> str:
    raw = read_private_config(path)
    next_raw = prepared_content(raw)
    if next_raw == raw:
        return "ALREADY_PREPARED"
    parent = path.parent
    if parent.is_symlink() or parent.stat().st_uid != os.geteuid():
        raise Blocked("CONFIG_DIRECTORY_NOT_TRUSTED")
    # Backup is private, created before updating. No secret values are logged.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = parent / (path.name + ".bak-routerai-readonly-" + stamp + "-" + str(os.getpid()))
    temp_path = None
    try:
        with backup.open("xb") as saved:
            os.chmod(backup, 0o600)
            saved.write(raw.encode("utf-8"))
            saved.flush()
            os.fsync(saved.fileno())
        fd, temporary = tempfile.mkstemp(prefix=".routerai-readonly-", dir=str(parent))
        temp_path = Path(temporary)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as candidate:
            os.fchmod(candidate.fileno(), 0o600)
            candidate.write(next_raw)
            candidate.flush()
            os.fsync(candidate.fileno())
        # Re-check the original file just before replacing to avoid overwriting
        # another operator's concurrent safety change.
        if read_private_config(path) != raw:
            raise Blocked("CONFIG_CHANGED_CONCURRENTLY")
        os.replace(temp_path, path)
        temp_path = None
        if read_private_config(path) != next_raw:
            raise Blocked("CONFIG_READBACK_FAILED")
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return "PREPARED"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approval", required=True)
    args = parser.parse_args()
    if args.approval != APPROVAL or os.geteuid() == 0:
        print("VPS_READONLY_PRIVACY_PREP=BLOCKED")
        return 2
    config = Path.home() / "serviceit" / "config" / "vps.env"
    try:
        outcome = apply_readonly_privacy(config)
    except (Blocked, OSError) as exc:
        reason = str(exc) if isinstance(exc, Blocked) else "LOCAL_FILE_ACCESS_FAILED"
        print("VPS_READONLY_PRIVACY_PREP=BLOCKED;REASON=" + reason)
        return 2
    print("VPS_READONLY_PRIVACY_PREP=" + outcome)
    print("VPS_ROUTERAI_SCOPE=YES_1062_21")
    print("VPS_MODEL_PRIVACY=YES")
    print("VPS_WRITER_MODE=READONLY;CUTOVER=NO;POLL=NO;INTAKE=NO")
    print("VPS_CONTAINERS_NOT_RESTARTED=YES;RUN_READONLY_SHADOW_DEPLOY_NEXT")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
