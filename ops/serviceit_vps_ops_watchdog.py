#!/usr/bin/env python3
"""Read-only production VPS state for external GitHub health monitor.

Runs over pinned nonroot SSH, not in the production processing loop.
Outputs only allowlisted Boolean and bounded numeric data. No CRM HTTP,
secret reads, source ingestion, DB write, client data, or customer IDs.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess

CONTAINERS = {
    "CORE": "serviceit-leadgen-vps-coordinator-1",
    "AUX": "serviceit-leadgen-vps-auxiliaries-1",
    "BUSINESS": "serviceit-leadgen-vps-business-1",
    "OBSERVER": "serviceit-leadgen-vps-observer-1",
    "FNS": "serviceit-leadgen-vps-fns-msp-1",
    "BROWSER": "serviceit-public-browser-vps-browser-1",
    "EGRESS": "serviceit-public-browser-vps-egress-1",
}
LOG_STATUSES = {
    "CORE": ("VPS_LIVE_CYCLE_COMPLETE", "VPS_CYCLE_BLOCKED_OR_UNVERIFIED"),
    "AUX": ("NATIVE_AUX_CYCLE", "NATIVE_AUX_BLOCKED"),
    "BUSINESS": ("NATIVE_BUSINESS_CYCLE", "NATIVE_BUSINESS_HOLD"),
    "OBSERVER": ("NATIVE_OBSERVER_READONLY", "NATIVE_OBSERVER_UNVERIFIED"),
}
FLAGS = {
    "CORE": {
        "LIVE": ("SERVICEIT_VPS_WRITER_MODE", "LIVE"),
        "CUTOVER": ("SERVICEIT_VPS_CUTOVER_AUTHORIZED", "YES"),
        "POLL": ("SERVICEIT_VPS_POLL_ENABLED", "YES"),
        "LEGACY_WRITERS_FENCED": ("SERVICEIT_VPS_OLD_GH_WRITERS_DISABLED_ACK", "YES"),
        "ROUTERAI": ("SERVICEIT_AI_PROVIDER", "routerai"),
        "BROWSER_ENABLED": ("SERVICEIT_VPS_BROWSER_RESEARCH_ENABLED", "YES"),
        "BROWSER_ISOLATED": ("SERVICEIT_VPS_BROWSER_PROXY_ISOLATION_VERIFIED", "YES"),
        "INTAKE_OFF": ("SERVICEIT_VPS_INTAKE_ENABLED", "NO"),
    },
    "AUX": {
        "STOP": ("SERVICEIT_VPS_OWN_STOP_ENABLED", "YES"),
        "DUP": ("SERVICEIT_VPS_CRM_DUPLICATE_ENABLED", "YES"),
        "KANBAN": ("SERVICEIT_VPS_KANBAN_TITLE_ENABLED", "YES"),
        "TASKS": ("SERVICEIT_VPS_MANAGER_TASK_ENABLED", "YES"),
        "MSP_FIELD": ("SERVICEIT_VPS_FNS_CRM_RESULT_ENABLED", "YES"),
        "FINAL_SCORE": ("SERVICEIT_VPS_FREE_MSP_SCORE_ENABLED", "YES"),
        "PROVISIONAL_OFF": ("SERVICEIT_VPS_PROVISIONAL_SCORE_ENABLED", "NO"),
    },
    "BUSINESS": {
        "POSTCALL": ("SERVICEIT_VPS_POSTCALL_ENABLED", "YES"),
        "FINANCE": ("SERVICEIT_VPS_FINANCE_ENABLED", "YES"),
    },
    "FNS": {
        "MSP_COLLECTOR": ("SERVICEIT_VPS_FNS_MSP_ENABLED", "YES"),
    },
}


def run(args, *, seconds=20):
    try:
        result = subprocess.run(args, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL,
                                timeout=seconds, text=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def safe_count(value):
    return value if type(value) is int and 0 <= value <= 1_000_000 else None


def health(name):
    result = run(["docker", "inspect", "-f",
                  "{{.State.Running}}:{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
                  CONTAINERS[name]])
    return (result == "true:healthy"
            or name == "EGRESS" and result == "true:none")


def snapshot():
    result = {
        "health": {k: health(k) for k in CONTAINERS},
        "flags": {},
        "cycles": {},
        "pg": {"ready": False},
        "backup": {"ready": False},
    }
    for kind, flags in FLAGS.items():
        for key, (name, expected) in flags.items():
            actual = run(["docker", "exec", CONTAINERS[kind], "printenv", name])
            result["flags"][key] = actual == expected

    for role, (good, bad) in LOG_STATUSES.items():
        text = run(["docker", "logs", "--since", "85m",
                    "--tail", "300", CONTAINERS[role]], seconds=30) or ""
        ok = blocked = 0
        for line in text.splitlines()[-300:]:
            if len(line) > 3000:
                continue
            try:
                item = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(item, dict):
                continue
            status = item.get("status")
            ok += int(status == good)
            blocked += int(status == bad)
        result["cycles"][role] = {
            "ok": safe_count(ok),
            "blocked": safe_count(blocked),
        }

    pg_script = """
import json,os
from leadpilot.serviceit_vps_pg_queue import db
value={'ready':False}
try:
    with db(dict(os.environ)) as con:
        with con.transaction():
            with con.cursor() as c:
                c.execute('SET TRANSACTION READ ONLY')
                c.execute("SELECT EXTRACT(EPOCH FROM(now()-last_success_at))::bigint "
                          "FROM serviceit_vps_poll_state WHERE singleton=1")
                row=c.fetchone()
                value['poll_age_sec']=int(row[0]) if row and row[0] is not None else None
                c.execute("SELECT status,count(*) FROM serviceit_vps_new_cards "
                          "WHERE status IN ('PENDING','REVIEW','UNCERTAIN') GROUP BY status")
                rows=dict(c.fetchall())
                for key in ('PENDING','REVIEW','UNCERTAIN'):
                    value[key.lower()]=int(rows.get(key,0))
        value['ready']=True
except Exception:
    pass
print(json.dumps(value))
"""
    text = run(["docker", "exec", CONTAINERS["CORE"],
                "python", "-c", pg_script], seconds=50)
    try:
        pg = json.loads(text or "")
        if isinstance(pg, dict) and pg.get("ready") is True:
            result["pg"] = {
                "ready": True,
                "poll_age_sec": safe_count(pg.get("poll_age_sec")),
                "pending": safe_count(pg.get("pending")),
                "review": safe_count(pg.get("review")),
                "uncertain": safe_count(pg.get("uncertain")),
            }
    except (TypeError, ValueError):
        pass

    backups = Path.home() / "serviceit" / "backups"
    cron = run(["crontab", "-l"], seconds=15)
    active_cron = run(["systemctl", "is-active", "cron"], seconds=15) == "active"
    exact = "SERVICEIT_NATIVE_PG_BACKUP_V1"
    cron_ok = (cron is not None and sum(exact in line for line in cron.splitlines()) == 1
               and any("--backup-only" in line and exact in line
                       for line in cron.splitlines()))
    try:
        valid = backups.is_dir() and not backups.is_symlink()
        files = [path for path in backups.iterdir()
                 if path.is_file() and not path.is_symlink()
                 and re.fullmatch(
                     r"svc-it-leadgen-\d{8}T\d{6}Z-[a-f0-9]{8}\.dump",
                     path.name)] if valid else []
        age = int((datetime.now(timezone.utc).timestamp() -
                   max(path.stat().st_mtime for path in files)) // 3600) if files else None
        result["backup"] = {
            "ready": bool(active_cron and cron_ok and files and age is not None),
            "count": safe_count(len(files)),
            "newest_age_hours": safe_count(max(0, age)) if age is not None else None,
            "cron": bool(cron_ok and active_cron),
        }
    except (OSError, ValueError, PermissionError):
        pass
    return result


def main():
    print(json.dumps(snapshot(), sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
