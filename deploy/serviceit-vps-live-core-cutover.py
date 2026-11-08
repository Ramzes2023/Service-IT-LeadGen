#!/usr/bin/env python3
"""Exact owner-only native VPS core activation AFTER GitHub cloud fence/drain.

Never run directly to bypass the owner-side GitHub variable and job checks.
At most one explicit new-card cutover: nine AI/HOLD fields, personal STOP,
visible CRM duplicates, 3-signal Kanban, Olga-605 tasks, preliminary Score.
No new lead sources, no old-card replay, no FNS-MSP download, payments,
outbound contacts, finance or postcall. On failure, restore READONLY config
and containers; leave the cloud fence ON for manual recovery.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time

APPROVAL = "ACTIVATE_NATIVE_CORE_1062_21_OLGA605_V1"
HOST = re.compile(r"(?:[a-z0-9-]{1,63}\.)+[a-z]{2,20}\Z")
SHA = re.compile(r"[a-f0-9]{40}\Z")
GATES = {
    "SERVICEIT_VPS_WRITER_MODE": "READONLY",
    "SERVICEIT_VPS_CUTOVER_AUTHORIZED": "NO",
    "SERVICEIT_VPS_POLL_ENABLED": "NO",
    "SERVICEIT_VPS_INTAKE_ENABLED": "NO",
}
EXPECTED = {
    "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED": "YES",
    "SERVICEIT_ROUTERAI_PRIVACY_APPROVED": "YES_1062_21",
    "SERVICEIT_VPS_TASK_ASSIGNEE_APPROVED": "YES_1062_21_INTERNAL_MANAGER",
    "SERVICEIT_VPS_TASK_ASSIGNEE_ID": "605",
}
FLAGS = {
    "SERVICEIT_VPS_WRITER_MODE": "LIVE",
    "SERVICEIT_VPS_CUTOVER_AUTHORIZED": "YES",
    "SERVICEIT_VPS_OLD_GH_WRITERS_DISABLED_ACK": "YES",
    "SERVICEIT_VPS_POLL_ENABLED": "YES",
    "SERVICEIT_VPS_INTAKE_ENABLED": "NO",
    "SERVICEIT_VPS_OWN_STOP_ENABLED": "YES",
    "SERVICEIT_VPS_CRM_DUPLICATE_ENABLED": "YES",
    "SERVICEIT_VPS_KANBAN_TITLE_ENABLED": "YES",
    "SERVICEIT_VPS_MANAGER_TASK_ENABLED": "YES",
    "SERVICEIT_VPS_PROVISIONAL_SCORE_ENABLED": "YES",
    "SERVICEIT_VPS_FREE_MSP_SCORE_ENABLED": "NO",
    "SERVICEIT_VPS_FNS_CRM_RESULT_ENABLED": "NO",
    "SERVICEIT_VPS_FNS_MSP_ENABLED": "NO",
    "SERVICEIT_VPS_POSTCALL_ENABLED": "NO",
    "SERVICEIT_VPS_FINANCE_ENABLED": "NO",
}
SERVICES = {
    "coordinator": "serviceit-leadgen-vps-coordinator-1",
    "auxiliaries": "serviceit-leadgen-vps-auxiliaries-1",
    "business": "serviceit-leadgen-vps-business-1",
    "observer": "serviceit-leadgen-vps-observer-1",
}
ENV_PATTERN = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")


class Blocked(ValueError):
    pass


def hosts(raw: str, *, required: bool) -> str:
    if not isinstance(raw, str) or len(raw) > 2400:
        raise Blocked("PUBLIC_SOURCE_HOSTS_INVALID")
    names = [h.strip().lower() for h in raw.split(",") if h.strip()]
    if ((required and not names) or len(names) > 20 or any(
        not HOST.fullmatch(h) or h.endswith(
            (".test", ".invalid", ".local", ".localhost"))
        for h in names
    )):
        raise Blocked("PUBLIC_SOURCE_HOSTS_INVALID_OR_NOT_APPROVED")
    return ",".join(dict.fromkeys(names))


def config_parse(raw: str) -> dict:
    result = {}
    for line in raw.splitlines():
        if not line or line.lstrip().startswith("#"):
            continue
        m = ENV_PATTERN.fullmatch(line)
        if m is None or m[1] in result:
            raise Blocked("CONFIG_FORMAT_OR_DUPLICATE_KEY")
        result[m[1]] = m[2]
    return result


def preflight(config: dict, now: datetime):
    for key, expected in {**GATES, **EXPECTED}.items():
        if config.get(key, "") != expected:
            raise Blocked("GATE_OR_OLGA_NOT_READY_" + key)
    if config.get("SERVICEIT_VPS_OLD_GH_WRITERS_DISABLED_ACK", "NO") != "NO":
        raise Blocked("ALREADY_SWITCHED_CUTOVER_ACK")
    if config.get("SERVICEIT_VPS_NATIVE_ACTIVATION_UTC", ""):
        raise Blocked("ACTIVATION_DATE_ALREADY_SET")
    for key in FLAGS:
        if key in GATES or key in EXPECTED or key == "SERVICEIT_VPS_OLD_GH_WRITERS_DISABLED_ACK":
            continue
        if config.get(key, "NO") != "NO":
            raise Blocked("NATIVE_WRITER_ALREADY_ENABLED_" + key)
    # Source host approval must be nonempty before claiming RouterAI is
    # ready to analyze PUBLIC TITLES on future cards.
    return True


def timestamp(raw: str, now: datetime) -> str:
    if not re.fullmatch(r"20\d{2}-\d\d-\d\dT\d\d:\d\d:\d\dZ", raw or ""):
        raise Blocked("CUTOVER_TIME_FORMAT_INVALID")
    try:
        value = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError:
        raise Blocked("CUTOVER_TIME_INVALID") from None
    if not now-timedelta(minutes=20) <= value <= now+timedelta(seconds=20):
        raise Blocked("CUTOVER_TIME_NOT_FRESH")
    return raw


def _private(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise Blocked("CONFIG_MISSING_OR_SYMLINK")
    st = path.stat()
    if st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) & 0o077:
        raise Blocked("CONFIG_OWNER_OR_PERMISSIONS_INVALID")
    return path.read_text("utf8")


def _write_atomic(path: Path, raw: str):
    fd, name = tempfile.mkstemp(prefix=".serviceit-core-cutover-", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf8", newline="") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _sub(command, *, check=True) -> subprocess.CompletedProcess:
    return subprocess.run(command, text=True, capture_output=True,
                          timeout=150, check=check)


def _compose(path: Path, config: Path, *cmd):
    return _sub(["docker", "compose", "--env-file", str(config),
                 "-f", str(path), *cmd])


def _active_containers():
    for key, name in SERVICES.items():
        proc = _sub(["docker", "inspect", "-f",
                     "{{.State.Running}}:{{.State.Health.Status}}", name])
        if proc.stdout.strip() != "true:healthy":
            raise Blocked("CONTAINER_UNHEALTHY_" + key)


def _env(container: str, key: str) -> str:
    return _sub(["docker", "exec", container, "printenv", key]).stdout.strip()


def _verified_readonly():
    _active_containers()
    for key, required in GATES.items():
        if _env(SERVICES["coordinator"], key) != required:
            raise Blocked("CONTAINER_NOT_READONLY_" + key)
    for key in EXPECTED:
        target = "auxiliaries" if "ASSIGNEE_" in key else "coordinator"
        if _env(SERVICES[target], key) != EXPECTED[key]:
            raise Blocked("CONTAINER_OWNER_PRIVACY_GATE_INVALID_" + key)
    if _env(SERVICES["auxiliaries"], "SERVICEIT_VPS_MANAGER_TASK_ENABLED") != "NO":
        raise Blocked("TASK_WRITER_ALREADY_ACTIVE")


def _health_after_live():
    for _ in range(20):
        try:
            _active_containers()
            for key in ("SERVICEIT_VPS_WRITER_MODE",
                        "SERVICEIT_VPS_CUTOVER_AUTHORIZED",
                        "SERVICEIT_VPS_POLL_ENABLED"):
                if _env(SERVICES["coordinator"], key) != FLAGS[key]:
                    raise Blocked("LIVE_GATE_NOT_RELOADED")
            if _env(SERVICES["auxiliaries"], "SERVICEIT_VPS_MANAGER_TASK_ENABLED") != "YES":
                raise Blocked("OLGA_TASK_MODULE_NOT_ENABLED")
            if _env(SERVICES["auxiliaries"], "SERVICEIT_VPS_TASK_ASSIGNEE_ID") != "605":
                raise Blocked("OLGA_TASK_ASSIGNEE_CHANGED")
            return
        except (Blocked, subprocess.SubprocessError):
            time.sleep(5)
    raise Blocked("LIVE_FOUR_HEALTHY_NOT_VERIFIED")


def _edit(raw: str, values: dict) -> str:
    out, seen = [], set()
    for line in raw.splitlines(keepends=True):
        m = ENV_PATTERN.fullmatch(line.rstrip("\r\n"))
        if m and m[1] in values:
            out.append(m[1] + "=" + values[m[1]] + "\n")
            seen.add(m[1])
        else:
            out.append(line)
    updated = "".join(out)
    if updated and not updated.endswith("\n"):
        updated += "\n"
    for key, value in values.items():
        if key not in seen:
            updated += key + "=" + value + "\n"
    if config_parse(updated) != {**config_parse(raw), **values}:
        raise Blocked("CONFIG_EDIT_VERIFICATION_FAILED")
    return updated


def run(args):
    if os.geteuid() == 0:
        raise Blocked("ROOT_NOT_PERMITTED")
    if not SHA.fullmatch(args.sha or ""):
        raise Blocked("RELEASE_SHA_INVALID")
    config = Path.home() / "serviceit/config/vps.env"
    release = Path.home() / "serviceit/releases" / args.sha
    compose = release / "deploy/serviceit-vps.compose.yml"
    if not compose.is_file():
        raise Blocked("PINNED_SHADOW_RELEASE_NOT_DEPLOYED")
    now = datetime.now(timezone.utc)
    source = hosts(args.source_hosts, required=True)
    company = hosts(args.company_hosts, required=False)
    if args.mode == "activate":
        if args.approval != APPROVAL or args.cloud_writers_fenced != "YES":
            raise Blocked("GH_FENCE_AND_OWNER_APPROVAL_REQUIRED")
        activated = timestamp(args.utc, now)
    lock = config.parent / ".serviceit-core-cutover.lock"
    with lock.open("a") as stream:
        os.chmod(lock, 0o600)
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = _private(config)
        preflight(config_parse(previous), now)
        _verified_readonly()
        # The source writers can keep creating cards, but we require the
        # immediate old-cloud handoff window to be QUIET before fencing.
        # A missing/unknown Bitrix page is never interpreted as zero.
        probe = _sub(["docker", "exec", SERVICES["coordinator"], "python",
                      "-m", "leadpilot.serviceit_vps_poll_readonly_probe"])
        observed = json.loads(probe.stdout)
        if (observed.get("status") != "BITRIX_1062_21_POLL_READONLY_COMPATIBLE"
                or observed.get("positive_control_card3") != "PASS"
                or observed.get("new_card_candidates") != 0):
            raise Blocked("RECENT_PRE_CUTOVER_CARDS_REQUIRE_OLD_CLOUD_DRAIN")
        queue = _sub(["docker", "exec", SERVICES["coordinator"], "python",
                      "-m", "leadpilot.serviceit_vps_worker", "--once"])
        summary = json.loads(queue.stdout)
        if (summary.get("status") != "READONLY_CRM_FIELD_METADATA_CONFIRMED"
                or summary.get("queue_counts") != {}
                or summary.get("migration_cutover") != "NOT_AUTHORIZED"):
            raise Blocked("NATIVE_QUEUE_OR_METADATA_NOT_CLEAN")
        rule_file = release / "config/serviceit_own_signal_stop.json"
        rules = json.loads(rule_file.read_text("utf8"))
        if rules.get("rules") != []:
            raise Blocked("OWNER_STOP_NOT_EMPTY")
        _compose(compose, config, "config", "--quiet")
        if args.mode == "preflight":
            print("NATIVE_CORE_PREP=PASS;SHADOW=READONLY;CUTOVER=NO")
            print("PUBLIC_APPROVED_SOURCE_HOSTS=" + str(len(source.split(","))))
            print("CRM_WRITES=0;TASKS_CREATED=0")
            return
        values = {
            **FLAGS,
            "SERVICEIT_VPS_NATIVE_ACTIVATION_UTC": activated,
            "SERVICEIT_PRELIMINARY_PUBLIC_HOSTS": source,
            "SERVICEIT_PUBLIC_RESEARCH_COMPANY_HOSTS": company,
        }
        candidate = _edit(previous, values)
        backup = config.parent / (
            "vps.env.bak-core-" +
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") +
            "-" + str(os.getpid()))
        with backup.open("xb") as saved:
            os.chmod(backup, 0o600)
            saved.write(previous.encode("utf8"))
            saved.flush()
            os.fsync(saved.fileno())
        try:
            _write_atomic(config, candidate)
            _compose(compose, config, "config", "--quiet")
            _compose(compose, config, "up", "-d", "--no-build", "--no-deps",
                     *SERVICES.keys())
            _health_after_live()
        except Exception:
            # Restore local READONLY first: a future restart cannot silently
            # activate a writer. GitHub CLOUD fence remains YES.
            _write_atomic(config, previous)
            try:
                _compose(compose, config, "up", "-d", "--no-build",
                         "--no-deps", *SERVICES.keys())
                _verified_readonly()
                print("VPS_CORE_CUTOVER=ROLLED_BACK_READONLY;GH_FENCE_STAYS_ON")
            except Exception:
                print("VPS_CORE_CUTOVER=ROLLBACK_UNVERIFIED;GH_FENCE_STAYS_ON")
            raise Blocked("LIVE_ACTIVATION_FAILED_HOLD_FOR_MANUAL_REVIEW") from None
        print("VPS_CORE_CUTOVER=LIVE_FOUR_HEALTHY")
        print("NATIVE_CORE_MODULES=AI_NINE,OWN_STOP,CRM_DUP,KANBAN,TASK_OLGA605,PROVISIONAL_SCORE")
        print("PUBLIC_SOURCE_HOSTS_APPROVED=" + str(len(source.split(","))))
        print("ACTIVATION_UTC=" + activated)
        print("SOURCES=UNCHANGED;FINANCE=OFF;POSTCALL=OFF;MSP_DOWNLOAD=OFF")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=("preflight","activate"), required=True)
    p.add_argument("--sha", required=True)
    p.add_argument("--source-hosts", required=True)
    p.add_argument("--company-hosts", default="")
    p.add_argument("--approval", default="")
    p.add_argument("--cloud-writers-fenced", default="NO")
    p.add_argument("--utc", default="")
    args = p.parse_args()
    try:
        run(args)
        return 0
    except Exception:
        # No raw external error bodies, environment values or secrets.
        print("VPS_CORE_CUTOVER=BLOCKED_OR_UNVERIFIED;CHECK_GITHUB_CLOUD_FENCE")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
