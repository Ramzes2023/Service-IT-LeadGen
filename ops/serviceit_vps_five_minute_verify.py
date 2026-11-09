#!/usr/bin/env python3
"""Read-only acceptance: two actual 5-minute cycles and Bitrix metadata."""

from datetime import datetime, timezone
import json
import re
import subprocess
import sys

BASE = "serviceit-leadgen-vps-"
WATCHED = {
    "CORE": ("coordinator", "VPS_LIVE_CYCLE_COMPLETE",
             "VPS_CYCLE_BLOCKED_OR_UNVERIFIED",
             "leadpilot.serviceit_vps_worker"),
    "AUX": ("auxiliaries", "NATIVE_AUX_CYCLE", "NATIVE_AUX_BLOCKED",
            "leadpilot.serviceit_vps_native_aux"),
    "BUSINESS": ("business", "NATIVE_BUSINESS_CYCLE", "NATIVE_BUSINESS_HOLD",
                 "leadpilot.serviceit_vps_native_business"),
}


def command(argv, timeout=70):
    try:
        result = subprocess.run(argv, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, check=False,
                                text=True, timeout=timeout)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def emit(label, value):
    label = str(label)
    value = str(value)
    if (re.fullmatch(r"[A-Z0-9_]{3,65}", label) and
            re.fullmatch(r"[A-Z0-9_.+-]{1,90}", value)):
        print("FIVE_MIN_" + label + "=" + value, flush=True)


def as_utc(raw):
    # Docker timestamps are RFC3339 with optional nanoseconds.
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def inspect(container, expr):
    return command(["docker", "inspect", "-f", expr, container])


def docker_exec(container, args, timeout=65):
    return command(["docker", "exec", container, *args], timeout)


def verify_container(label, group, success, blocked, module):
    name = BASE + group + "-1"
    healthy = inspect(name, "{{.State.Running}}:{{.State.Health.Status}}") == "true:healthy"
    emit(label + "_HEALTH", "YES" if healthy else "NO")
    tested = docker_exec(name, ["python", "-B", "-c",
                         "import importlib,sys;print(importlib.import_module(sys.argv[1]).INTERVAL_SECONDS)",
                          module])
    emit(label + "_RUNTIME_INTERVAL",
         "300" if tested == "300" else "UNVERIFIED")
    started = inspect(name, "{{.State.StartedAt}}")
    if not started or as_utc(started) is None:
        emit(label + "_OBSERVED_CADENCE", "UNVERIFIED")
        return False
    raw = command(["docker", "logs", "--since", started, "--timestamps",
                   "--tail", "150", name], timeout=30)
    times = []
    bad = 0
    for line in (raw or "").splitlines():
        parts = line.split(" ", 1)
        if len(parts) != 2:
            continue
        moment = as_utc(parts[0])
        if moment is None:
            continue
        try:
            obj = json.loads(parts[1])
        except (ValueError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("status") == success:
            times.append(moment)
        elif obj.get("status") == blocked:
            bad += 1
    emit(label + "_SUCCESSFUL_CYCLES", min(999, len(times)))
    emit(label + "_BLOCKED_CYCLES", min(999, bad))
    deltas = [(b - a).total_seconds() for a, b in zip(times, times[1:])]
    good = len(deltas) >= 1 and all(260 <= x <= 365 for x in deltas[-2:])
    emit(label + "_OBSERVED_CADENCE",
         "MATCH_5MIN" if good else "NOT_YET_VERIFIED")
    if deltas:
        emit(label + "_LATEST_CYCLE_GAP_SEC", round(deltas[-1]))
    return bool(healthy and tested == "300" and good and bad == 0)


def main():
    emit("AUDIT_START", "YES")
    results = {}
    for label, (group, good, bad, module) in WATCHED.items():
        results[label] = verify_container(label, group, good, bad, module)
    core = BASE + "coordinator-1"
    aux = BASE + "auxiliaries-1"
    business = BASE + "business-1"
    expected = (
        (core, "SERVICEIT_VPS_POLL_ENABLED", "YES"),
        (core, "SERVICEIT_VPS_WRITER_MODE", "LIVE"),
        (core, "SERVICEIT_VPS_INTAKE_ENABLED", "NO"),
        (aux, "SERVICEIT_VPS_FREE_MSP_SCORE_ENABLED", "YES"),
        (aux, "SERVICEIT_VPS_MANAGER_TASK_ENABLED", "YES"),
        (business, "SERVICEIT_VPS_POSTCALL_ENABLED", "YES"),
        (business, "SERVICEIT_VPS_FINANCE_ENABLED", "YES"),
    )
    gates = all(docker_exec(name, ["printenv", key]) == want
                for name, key, want in expected)
    emit("BITRIX_NATIVE_GATES", "PASS" if gates else "BLOCKED")
    metadata = """
import httpx,os
from leadpilot.serviceit_vps_worker import readonly_preflight
try:
    with httpx.Client(trust_env=False,follow_redirects=False,timeout=25) as c:
        result=readonly_preflight(dict(os.environ),client=c)
        print('PASS' if result.get('status')=='READONLY_CRM_FIELD_METADATA_CONFIRMED' else 'BLOCKED')
except Exception:
    print('UNVERIFIED')
"""
    value = docker_exec(core, ["python", "-c", metadata], timeout=90)
    emit("BITRIX_NINE_FIELDS_METADATA",
         "PASS" if value == "PASS" else "UNVERIFIED")
    pg = """
import json,os
from leadpilot.serviceit_vps_pg_queue import db
value={'ready':False}
try:
    with db(dict(os.environ)) as con:
        with con.transaction():
            with con.cursor() as cur:
                cur.execute('SET TRANSACTION READ ONLY')
                cur.execute("SELECT EXTRACT(EPOCH FROM(now()-last_success_at))::bigint FROM serviceit_vps_poll_state WHERE singleton=1")
                row=cur.fetchone()
                value['age']=int(row[0]) if row and row[0] is not None else None
                cur.execute("SELECT status,count(*) FROM serviceit_vps_new_cards WHERE status IN ('PENDING','REVIEW','UNCERTAIN') GROUP BY status")
                rows=dict(cur.fetchall())
                value['pending']=int(rows.get('PENDING',0))
                value['review']=int(rows.get('REVIEW',0))
                value['uncertain']=int(rows.get('UNCERTAIN',0))
        value['ready']=True
except Exception:
    pass
print(json.dumps(value))
"""
    raw = docker_exec(core, ["python", "-c", pg], timeout=65)
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        data = {}
    pg_ok = data.get("ready") is True
    age = data.get("age")
    freshness = bool(type(age) is int and 0 <= age <= 420)
    emit("PG_READONLY", "PASS" if pg_ok else "UNVERIFIED")
    emit("POLL_AGE_SEC", age if freshness else "UNVERIFIED")
    for name in ("pending", "review", "uncertain"):
        count = data.get(name)
        emit("PG_" + name.upper(),
             count if type(count) is int and 0 <= count <= 1000000
             else "UNVERIFIED")
    # Use exact production selected flags. Does not enable sources.
    emit("SOURCE_INTAKE", "OFF" if docker_exec(
        core, ["printenv", "SERVICEIT_VPS_INTAKE_ENABLED"]) == "NO" else "UNVERIFIED")
    valid = all(results.values()) and gates and value == "PASS" and pg_ok and freshness
    emit("OVERALL_5MIN_ACCEPTANCE", "PASS" if valid else "BLOCKED_OR_UNVERIFIED")
    emit("CRM_WRITES_BY_AUDIT", "ZERO")
    emit("SOURCE_SWITCHES_CHANGED", "NO")
    emit("AUDIT_END", "YES")


if __name__ == "__main__":
    main()
