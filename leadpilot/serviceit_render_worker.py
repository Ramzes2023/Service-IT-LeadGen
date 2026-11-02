"""Opt-in 24/7 Render background-worker supervisor for ServiceIT CRM 1062/21.

No Bitrix/GigaChat calls without explicit cloud LIVE flag, secrets, privacy
decision and a mounted persistent disk. Uses the existing guarded worker.
No historic item reprocessing and no independent verification claims.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from threading import Event

LIVE_FLAG = "YES_1062_21_CLOUD"
INTERVAL_SECONDS = 300
CYCLE_TIMEOUT_SECONDS = 240
ROOT = Path("/opt/render/project/src/serviceit-data")
STOP = Event()


def config_state(env, *, root=ROOT, mounted=None):
    """Preflight only; never prints secrets or performs external operations."""
    if env.get("SERVICEIT_CLOUD_LIVE_ENABLED") != LIVE_FLAG:
        return "CLOUD_NOT_ENABLED"
    if not env.get("SERVICEIT_BITRIX_WEBHOOK", "").strip():
        return "CLOUD_WEBHOOK_MISSING"
    if env.get("SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED") not in ("YES", "NO"):
        return "CLOUD_PRIVACY_DECISION_REQUIRED"
    if env["SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED"] == "YES":
        if not env.get("GIGACHAT_CREDENTIALS", "").strip():
            return "CLOUD_GIGACHAT_KEY_MISSING"
        if env.get("GIGACHAT_SCOPE") not in (
            "GIGACHAT_API_PERS", "GIGACHAT_API_B2B", "GIGACHAT_API_CORP"
        ):
            return "CLOUD_GIGACHAT_SCOPE_INVALID"
    # Explicit mount guard protects the durable CRM-write and AI-cost ledgers.
    if not root.is_dir() or not (root.is_mount() if mounted is None else mounted):
        return "CLOUD_PERSISTENT_DISK_NOT_MOUNTED"
    return "CLOUD_CONFIG_READY"


def child_environment(env, *, root=ROOT):
    result = dict(env)
    result["SERVICEIT_WINDOWS_AUTOFILL_ENABLED"] = "YES_1062_21"
    result["SERVICEIT_AUTOFILL_STATE_DIR"] = str(root / "state")
    result["SERVICEIT_APPROVAL_DIR"] = str(root / "approvals")
    result["AI_PROVIDER"] = "gigachat"
    result["AI_ENABLE"] = (
        "1" if env["SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED"] == "YES" else "0"
    )
    return result


def run_once(env=None, *, root=ROOT, mounted=None, executor=subprocess.run):
    env = dict(os.environ if env is None else env)
    status = config_state(env, root=root, mounted=mounted)
    if status != "CLOUD_CONFIG_READY":
        return {"status": status, "crm_writes": 0, "gigachat_calls": 0}
    (root / "state").mkdir(parents=True, exist_ok=True)
    (root / "approvals").mkdir(parents=True, exist_ok=True)
    try:
        completed = executor(
            [sys.executable, "-m", "leadpilot.serviceit_windows_autofill", "--once"],
            env=child_environment(env, root=root),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=CYCLE_TIMEOUT_SECONDS,
            check=False,
        )
        # Child output consists of fixed statuses and IDs, never secret values.
        # Nevertheless do NOT relay child stdout to hosting logs: only a fixed
        # coarse status here, to avoid unforeseen data leakage on future edits.
        return {"status": ("CLOUD_CYCLE_OK" if completed.returncode == 0
                            else "CLOUD_CYCLE_BLOCKED"),
                "exit_code": 0 if completed.returncode == 0 else 2}
    except (subprocess.TimeoutExpired, OSError):
        return {"status": "CLOUD_CYCLE_UNVERIFIED", "exit_code": 2}


def _terminate(*_):
    STOP.set()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="Only report configuration. Never access CRM.")
    parser.add_argument("--once", action="store_true",
                        help="One guarded cycle; fail closed by default.")
    args = parser.parse_args(argv)
    if args.check:
        status = config_state(os.environ)
        print(json.dumps({"status": status, "crm_writes": 0,
                          "gigachat_calls": 0}), flush=True)
        return 0
    if args.once:
        result = run_once()
        print(json.dumps(result), flush=True)
        return 0 if result["status"] == "CLOUD_CYCLE_OK" else 2

    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGINT, _terminate)
    while not STOP.is_set():
        result = run_once()
        print(json.dumps(result), flush=True)
        STOP.wait(INTERVAL_SECONDS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
