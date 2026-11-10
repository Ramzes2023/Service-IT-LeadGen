#!/usr/bin/env python3
"""Guarded B2B-only production container promotion with preserved rollback containers.

Operates through existing Docker permissions; never reads or logs secret values.
Only receiver/writer are touched. New image must independently pass the live
read-only Bitrix source identity check before old containers are stopped.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.request import urlopen

RECEIVER = "serviceit-b2b-vps-receiver-1"
WRITER = "serviceit-b2b-vps-writer-1"
CORE = "serviceit-leadgen-vps-coordinator-1"
IMAGE = "serviceit-vps-b2b-direct:20261109-pilot"
MOUNTS = {
    RECEIVER: {
        "/run/secrets/serviceit_pg_password",
        "/run/secrets/serviceit_b2b_hmac",
    },
    WRITER: {
        "/run/secrets/serviceit_pg_password",
        "/run/secrets/serviceit_bitrix_webhook",
    },
}
ALLOWED_ENV = {
    "SERVICEIT_PG_HOST", "SERVICEIT_PG_PORT",
    "SERVICEIT_PG_DB", "SERVICEIT_PG_USER", "SERVICEIT_PG_PASSWORD_FILE",
    "SERVICEIT_VPS_B2B_RECEIVER_ENABLED",
    "SERVICEIT_VPS_B2B_GH_WRITERS_DISABLED_ACK",
    "SERVICEIT_VPS_INTAKE_ENABLED", "SERVICEIT_VPS_B2B_HMAC_KEY_FILE",
    "SERVICEIT_BITRIX_WEBHOOK_FILE",
    "SERVICEIT_VPS_B2B_WRITER_ENABLED", "SERVICEIT_VPS_WRITER_MODE",
    "SERVICEIT_VPS_CUTOVER_AUTHORIZED",
    "SERVICEIT_VPS_OLD_GH_WRITERS_DISABLED_ACK",
    "SERVICEIT_VPS_POLL_ENABLED", "SERVICEIT_AI_PROVIDER",
    "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED",
    "SERVICEIT_ROUTERAI_PRIVACY_APPROVED",
    "SERVICEIT_VPS_NATIVE_ACTIVATION_UTC",
}
REQUIRED_ENV = {
    RECEIVER: {
        "SERVICEIT_VPS_B2B_RECEIVER_ENABLED": "YES",
        "SERVICEIT_VPS_B2B_GH_WRITERS_DISABLED_ACK": "YES",
        "SERVICEIT_VPS_INTAKE_ENABLED": "NO",
        "SERVICEIT_PG_PASSWORD_FILE": "/run/secrets/serviceit_pg_password",
        "SERVICEIT_VPS_B2B_HMAC_KEY_FILE": "/run/secrets/serviceit_b2b_hmac",
    },
    WRITER: {
        "SERVICEIT_VPS_B2B_RECEIVER_ENABLED": "YES",
        "SERVICEIT_VPS_B2B_WRITER_ENABLED": "YES",
        "SERVICEIT_VPS_B2B_GH_WRITERS_DISABLED_ACK": "YES",
        "SERVICEIT_VPS_INTAKE_ENABLED": "NO",
        "SERVICEIT_VPS_WRITER_MODE": "LIVE",
        "SERVICEIT_VPS_CUTOVER_AUTHORIZED": "YES",
        "SERVICEIT_VPS_OLD_GH_WRITERS_DISABLED_ACK": "YES",
        "SERVICEIT_VPS_POLL_ENABLED": "YES",
        "SERVICEIT_AI_PROVIDER": "routerai",
        "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED": "YES",
        "SERVICEIT_ROUTERAI_PRIVACY_APPROVED": "YES_1062_21",
        "SERVICEIT_PG_PASSWORD_FILE": "/run/secrets/serviceit_pg_password",
        "SERVICEIT_BITRIX_WEBHOOK_FILE": "/run/secrets/serviceit_bitrix_webhook",
    },
}

class Blocked(Exception):
    pass

def need(value: bool, code: str):
    if not value:
        raise Blocked(code)

def run(argv, *, timeout=30, env=None, check=True):
    try:
        p = subprocess.run(
            argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, timeout=timeout, env=env, text=True)
    except (OSError, subprocess.TimeoutExpired):
        raise Blocked("DOCKER_COMMAND_TIMEOUT_OR_UNAVAILABLE") from None
    if check and p.returncode != 0:
        raise Blocked("DOCKER_COMMAND_FAILED")
    return p

def inspect(name: str) -> dict:
    p = run(["docker", "inspect", "--type", "container", name])
    doc = json.loads(p.stdout)
    need(isinstance(doc, list) and len(doc) == 1
         and isinstance(doc[0], dict), "CONTAINER_INSPECT_INVALID")
    return doc[0]

def environment(container: dict, name: str) -> dict:
    items = container.get("Config", {}).get("Env", [])
    need(isinstance(items, list), "CONTAINER_ENV_INVALID")
    source = dict(entry.split("=", 1) for entry in items
                  if isinstance(entry, str) and "=" in entry)
    for key, expected in REQUIRED_ENV[name].items():
        need(source.get(key, "NO" if key == "SERVICEIT_VPS_INTAKE_ENABLED" else None)
             == expected, "LIVE_FLAG_OR_SECRET_PATH_MISSING")
    selected = {key: source[key] for key in ALLOWED_ENV if key in source}
    # Inherit only fixed allowlisted non-secret fields. Path-only credentials.
    for key, value in selected.items():
        need(len(value) <= 250 and "\n" not in value and "\r" not in value,
             "CONTAINER_ENV_UNSAFE")
        if any(word in key for word in ("KEY_FILE", "PASSWORD_FILE", "WEBHOOK_FILE")):
            need(value.startswith("/run/secrets/serviceit_"),
                 "SECRET_FILE_PATH_UNEXPECTED")
    return selected

def validate_existing(name: str):
    doc = inspect(name)
    need(doc.get("State", {}).get("Running") is True,
         "EXISTING_CONTAINER_NOT_RUNNING")
    config = doc.get("Config", {})
    host = doc.get("HostConfig", {})
    need(config.get("User") == "10001:10001",
         "EXISTING_USER_DRIFT")
    need(host.get("NetworkMode") == "host",
         "EXISTING_NETWORK_DRIFT")
    need(host.get("ReadonlyRootfs") is True,
         "EXISTING_ROOTFS_DRIFT")
    need(set(m.get("Destination") for m in doc.get("Mounts", []))
         >= MOUNTS[name], "EXISTING_SECRET_MOUNTS_MISSING")
    return doc, environment(doc, name)

def source_probe():
    code = "\n".join((
        "import os,httpx",
        "from leadpilot.serviceit_b2b_positive_control import positive_duplicate_control",
        "from leadpilot.serviceit_vps_pg_queue import secret_file",
        "from leadpilot.bitrix_metadata_audit import validate_webhook",
        "base=validate_webhook(secret_file(dict(os.environ),'SERVICEIT_BITRIX_WEBHOOK'))",
        "with httpx.Client(trust_env=False,follow_redirects=False,timeout=20) as client:",
        "    assert positive_duplicate_control(client,base)",
        "print('PREWRITE_OK')",
    ))
    r = run([
        "docker", "run", "--rm", "--network", "host",
        "--user", "10001:10001", "--read-only",
        "--security-opt", "no-new-privileges", "--cap-drop", "ALL",
        "--volumes-from", WRITER,
        "--env", "SERVICEIT_BITRIX_WEBHOOK_FILE=/run/secrets/serviceit_bitrix_webhook",
        "--entrypoint", "python", IMAGE, "-c", code,
    ], timeout=90)
    need(r.stdout.strip() == "PREWRITE_OK", "PREWRITE_SOURCE_ID_UNVERIFIED")

def start_new(name: str, former: str, variables: dict):
    receiver = name == RECEIVER
    args = [
        "docker", "run", "-d", "--name", name, "--network", "host",
        "--restart", "unless-stopped",
        "--user", "10001:10001", "--read-only",
        "--init", "--tmpfs", "/tmp:size=32m,mode=1777",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--pids-limit", "128", "--memory", "400m",
        "--volumes-from", former,
        "--label", "serviceit.b2b.safe_cutover=20261110",
    ]
    for key in sorted(variables):
        args.extend(["--env", key + "=" + variables[key]])
    args.extend(["--entrypoint", "python", IMAGE])
    args += (["-m", "uvicorn", "leadpilot.serviceit_vps_b2b_ingress:app",
              "--host", "127.0.0.1", "--port", "18781",
              "--workers", "1", "--no-access-log"]
             if receiver else ["-m", "leadpilot.serviceit_vps_b2b_bridge"])
    run(args, timeout=50)
    need(inspect(name).get("State", {}).get("Running") is True,
         "NEW_CONTAINER_NOT_RUNNING")

def check_receiver():
    for _ in range(12):
        try:
            with urlopen("http://127.0.0.1:18781/health", timeout=2) as resp:
                data = json.loads(resp.read(900))
            if (resp.status == 200 and data.get("status") == "B2B_INGRESS_ALIVE"
                    and data.get("source_enabled") is True
                    and data.get("crm_writes") == 0):
                return
        except Exception:
            time.sleep(2)
    raise Blocked("NEW_RECEIVER_HEALTH_FAILED")

def rollback(backups):
    ok = True
    for name in (WRITER, RECEIVER):
        if name in backups:
            if run(["docker", "inspect", name], check=False).returncode == 0:
                run(["docker", "rm", "-f", name], check=False)
    for name in (RECEIVER, WRITER):
        backup = backups.get(name)
        if backup:
            a = run(["docker", "rename", backup, name], check=False)
            b = run(["docker", "start", name], check=False) if a.returncode == 0 else a
            ok = ok and a.returncode == 0 and b.returncode == 0
    return ok

def main():
    ref = os.environ.get("SERVICEIT_B2B_DEPLOY_RUN_ID", "")
    need(re.fullmatch(r"[0-9]{7,16}", ref) is not None, "RUN_ID_INVALID")
    need(os.geteuid() != 0, "NONROOT_REQUIRED")
    source = Path.home() / "serviceit/staging/b2b-direct-image-42791235235/source"
    need((source/"leadpilot/serviceit_b2b_positive_control.py").is_file(),
         "STAGED_RELEASE_MISSING")
    core = inspect(CORE)
    need(core.get("State", {}).get("Running") is True, "CORE_UNHEALTHY")
    original = {}
    variables = {}
    for name in (RECEIVER, WRITER):
        original[name], variables[name] = validate_existing(name)
    image_id = run(["docker", "image", "inspect", "-f", "{{.Id}}", IMAGE]).stdout.strip()
    need(re.fullmatch(r"sha256:[a-f0-9]{64}", image_id) is not None,
         "NEW_IMAGE_NOT_VERIFIED")
    need(all(doc.get("Image") != image_id for doc in original.values()),
         "IMAGE_ALREADY_RUNNING")
    source_probe()
    print("B2BLIVE_PREWRITE=YES", flush=True)
    # Preserve both old containers and independent snapshots before mutation.
    for name, role in ((RECEIVER, "receiver"), (WRITER, "writer")):
        print("B2BLIVE_SNAPSHOT_"+role.upper()+"_STARTED=YES", flush=True)
        run(["docker", "commit", "--pause=false", name,
             "serviceit-b2b-rollback:" + role + "-" + ref], timeout=100)
        print("B2BLIVE_SNAPSHOT_"+role.upper()+"_COMPLETED=YES", flush=True)
    print("B2BLIVE_OLD_SNAPSHOTS=YES", flush=True)
    backups = {}
    try:
        for name in (WRITER, RECEIVER):
            backup = name.replace("-1", "-rollback-" + ref)
            run(["docker", "rename", name, backup])
            backups[name] = backup
            run(["docker", "stop", "--time", "20", backup], timeout=40)
        start_new(RECEIVER, backups[RECEIVER], variables[RECEIVER])
        check_receiver()
        start_new(WRITER, backups[WRITER], variables[WRITER])
        time.sleep(5)
        need(inspect(WRITER).get("State",{}).get("Running") is True,
             "NEW_WRITER_EXITED")
        need(inspect(CORE).get("Id") == core.get("Id")
             and inspect(CORE).get("State",{}).get("Running") is True,
             "CORE_STATE_CHANGED")
        print("B2BLIVE_NEW_CONTAINERS=YES",flush=True)
        print("B2BLIVE_BACKUP_CONTAINERS_PRESERVED=YES",flush=True)
        print("B2BLIVE_ROLLBACK=NOT_NEEDED",flush=True)
        return 0
    except Exception:
        good = rollback(backups)
        print("B2BLIVE_NEW_CONTAINERS=NO",flush=True)
        print("B2BLIVE_ROLLBACK="+("YES" if good else "FAILED"),flush=True)
        return 2

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("B2BLIVE_DEPLOY_BLOCKED=YES",flush=True)
        raise SystemExit(2)
