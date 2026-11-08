#!/usr/bin/env python3
"""Owner-approved Olga (Bitrix user 605) VPS-native internal tasks, no CRM writes.

--probe  runs inside the existing READONLY container with the existing
         mounted Bitrix webhook; only user.get and task metadata GET-style
         Bitrix REST methods, never tasks.task.add or crm.item.update.
--apply  runs on the nonroot VPS host and atomically prepares TWO nonsecret
         config keys while requiring all production write gates remain OFF.
Only a separate, future approved live cutover can create actual tasks.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import stat
import tempfile

APPROVAL = "PREPARE_OLGA_605_READONLY"
UID = "605"
EXPECTED_NAME = "ольга"
EXPECTED_LAST = "торгашина"
TARGET = {
    "SERVICEIT_VPS_TASK_ASSIGNEE_APPROVED": "YES_1062_21_INTERNAL_MANAGER",
    "SERVICEIT_VPS_TASK_ASSIGNEE_ID": UID,
}
GATES = {
    "SERVICEIT_VPS_WRITER_MODE": "READONLY",
    "SERVICEIT_VPS_CUTOVER_AUTHORIZED": "NO",
    "SERVICEIT_VPS_POLL_ENABLED": "NO",
    "SERVICEIT_VPS_INTAKE_ENABLED": "NO",
}
OFF_FLAGS = (
    "SERVICEIT_VPS_MANAGER_TASK_ENABLED",
    "SERVICEIT_VPS_OWN_STOP_ENABLED",
    "SERVICEIT_VPS_CRM_DUPLICATE_ENABLED",
    "SERVICEIT_VPS_KANBAN_TITLE_ENABLED",
    "SERVICEIT_VPS_FNS_CRM_RESULT_ENABLED",
    "SERVICEIT_VPS_PROVISIONAL_SCORE_ENABLED",
    "SERVICEIT_VPS_FREE_MSP_SCORE_ENABLED",
    "SERVICEIT_VPS_POSTCALL_ENABLED",
    "SERVICEIT_VPS_FINANCE_ENABLED",
    "SERVICEIT_VPS_FNS_MSP_ENABLED",
)
LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")


class Blocked(Exception):
    pass


def parse(raw: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for text in raw.splitlines():
        if not text or text.lstrip().startswith("#"):
            continue
        match = LINE.fullmatch(text)
        if not match:
            raise Blocked("INVALID_CONFIG_LINE")
        key, value = match.groups()
        if key in found:
            raise Blocked("DUPLICATE_CONFIG_KEY")
        found[key] = value
    return found


def safety(values: dict[str, str]) -> None:
    for key, required in GATES.items():
        if values.get(key) != required:
            raise Blocked("READONLY_GATE_REQUIRED_" + key)
    if values.get("SERVICEIT_VPS_OLD_GH_WRITERS_DISABLED_ACK", "NO") != "NO":
        raise Blocked("OLD_GH_ACK_MUST_BE_NO")
    if values.get("SERVICEIT_VPS_NATIVE_ACTIVATION_UTC", ""):
        raise Blocked("NATIVE_ACTIVATION_ALREADY_SET")
    for key in OFF_FLAGS:
        if values.get(key, "NO") != "NO":
            raise Blocked("WRITER_MODULE_NOT_OFF_" + key)
    if values.get("SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED") != "YES":
        raise Blocked("AI_PRIVACY_NOT_PREPARED")
    if values.get("SERVICEIT_ROUTERAI_PRIVACY_APPROVED") != "YES_1062_21":
        raise Blocked("ROUTERAI_PRIVACY_NOT_PREPARED")
    for key, target in TARGET.items():
        if values.get(key, "") not in ("", "NO", target):
            raise Blocked("EXISTING_ASSIGNEE_DIFFERENT_" + key)


def desired(raw: str) -> str:
    safety(parse(raw))
    newline = "\r\n" if "\r\n" in raw else "\n"
    seen = set()
    result = []
    for line in raw.splitlines(keepends=True):
        match = LINE.fullmatch(line.rstrip("\r\n"))
        if match and match.group(1) in TARGET:
            key = match.group(1)
            result.append(key + "=" + TARGET[key] + newline)
            seen.add(key)
        else:
            result.append(line)
    updated = "".join(result)
    if updated and not updated.endswith(("\r", "\n")):
        updated += newline
    for key, val in TARGET.items():
        if key not in seen:
            updated += key + "=" + val + newline
    safety(parse(updated))
    return updated


def private_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise Blocked("CONFIG_MISSING_OR_SYMLINK")
    data = path.stat()
    if data.st_uid != os.geteuid() or stat.S_IMODE(data.st_mode) & 0o077:
        raise Blocked("CONFIG_OWNERSHIP_OR_MODE_INVALID")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise Blocked("CONFIG_NOT_UTF8") from exc


def apply(path: Path) -> str:
    old = private_file(path)
    new = desired(old)
    if old == new:
        return "ALREADY_PREPARED"
    parent = path.parent
    if parent.is_symlink() or parent.stat().st_uid != os.geteuid():
        raise Blocked("CONFIG_PARENT_NOT_TRUSTED")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = parent / (path.name + ".bak-olga605-" + stamp + "-" + str(os.getpid()))
    temporary = None
    try:
        with backup.open("xb") as saved:
            os.chmod(backup, 0o600)
            saved.write(old.encode("utf-8"))
            saved.flush()
            os.fsync(saved.fileno())
        descriptor, filename = tempfile.mkstemp(prefix=".olga605-", dir=parent)
        temporary = Path(filename)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(new)
            output.flush()
            os.fsync(output.fileno())
        if private_file(path) != old:
            raise Blocked("CONFIG_CHANGED_DURING_PREP")
        os.replace(temporary, path)
        temporary = None
        if private_file(path) != new:
            raise Blocked("CONFIG_READBACK_MISMATCH")
        return "PREPARED"
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def probe(env: dict[str, str]) -> tuple[str, str]:
    """Real user/identity check; no customer cards and zero CRM mutations."""
    for key, expected in GATES.items():
        if env.get(key) != expected:
            raise Blocked("CONTAINER_NOT_READONLY_" + key)
    if env.get("SERVICEIT_VPS_OLD_GH_WRITERS_DISABLED_ACK") != "NO":
        raise Blocked("CUTOVER_ALREADY_ACKNOWLEDGED")
    if env.get("SERVICEIT_VPS_MANAGER_TASK_ENABLED", "NO") != "NO":
        raise Blocked("TASK_WRITES_ALREADY_ENABLED")
    import httpx
    from leadpilot.serviceit_vps_pg_queue import secret_file
    from leadpilot.bitrix_metadata_audit import validate_webhook
    from leadpilot.serviceit_stage34_signed_readonly_sidecar import request
    from leadpilot.serviceit_stage3_all_new_task_automation import check_task_binding

    base = validate_webhook(secret_file(env, "SERVICEIT_BITRIX_WEBHOOK"))
    if not base.startswith("https://serviseit.bitrix24.ru/rest/"):
        raise Blocked("WRONG_BITRIX_PORTAL")
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        data = request(client, base, "user.get", {
            "FILTER": {"ID": int(UID), "ACTIVE": True},
            "select": ["ID", "NAME", "LAST_NAME", "ACTIVE", "USER_TYPE"],
            "start": 0,
        })
        users = data.get("result")
        if (not isinstance(users, list) or len(users) != 1 or
                data.get("next") is not None or data.get("total") != 1):
            raise Blocked("USER_RESULT_NOT_UNIQUE")
        person = users[0]
        if (not isinstance(person, dict) or
                str(person.get("ID")) != UID or
                person.get("ACTIVE") not in (True, "Y") or
                person.get("USER_TYPE") != "employee" or
                str(person.get("NAME", "")).strip().casefold() != EXPECTED_NAME or
                str(person.get("LAST_NAME", "")).strip().casefold() != EXPECTED_LAST):
            raise Blocked("USER_605_NOT_CONFIRMED_AS_OLGA_TORGASHINA")
        # The recipient can be recorded even if task binding is not enabled
        # in Bitrix yet. That condition must be fixed before live activation.
        try:
            check_task_binding(client, base)
            scope = "VERIFIED"
        except Exception:
            scope = "UNVERIFIED"
        return "USER_605_VERIFIED", scope


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--probe", action="store_true")
    group.add_argument("--apply", action="store_true")
    p.add_argument("--approval", required=True)
    opts = p.parse_args()
    if opts.approval != APPROVAL:
        print("VPS_TASK_MANAGER_PREP=BLOCKED;REASON=EXPLICIT_APPROVAL_REQUIRED")
        return 2
    try:
        if opts.probe:
            status, task_scope = probe(dict(os.environ))
            print("VPS_OLGA_605_IDENTITY=" + status)
            print("VPS_TASK_LINK_AND_RIGHTS=" + task_scope)
            print("VPS_TASKS_CREATED=0;CRM_WRITES=0")
        else:
            if os.geteuid() == 0:
                raise Blocked("ROOT_NOT_ALLOWED")
            path = Path.home() / "serviceit" / "config" / "vps.env"
            result = apply(path)
            print("VPS_OLGA_605_TASK_ASSIGNEE_PREP=" + result)
            print("VPS_TASK_MANAGER=605;VPS_MANAGER_TASK_ENABLED=NO")
            print("VPS_WRITER_MODE=READONLY;CUTOVER=NO;POLL=NO")
            print("VPS_TASKS_CREATED=0;CRM_WRITES=0")
        return 0
    except Blocked as exc:
        print("VPS_TASK_MANAGER_PREP=BLOCKED;REASON=" + str(exc))
        return 2
    except Exception:
        print("VPS_TASK_MANAGER_PREP=BLOCKED;REASON=READONLY_PROBE_OR_FILE_UNVERIFIED")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
