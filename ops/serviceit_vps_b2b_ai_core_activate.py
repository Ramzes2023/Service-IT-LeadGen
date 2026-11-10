#!/usr/bin/env python3
"""Owner-gated, offline, coordinator-only 9-AI-field metadata fix rollout.

Makes a new image FROM the exact running image. Changes only one reviewed
Python module. Preserves image digest + Compose env and rolls back on failure.
No Bitrix/PG modifications in this script; no passwords or customer data printed.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

HOME = Path.home()
CONFIG = HOME / "serviceit/config"
ENV = CONFIG / "vps.env"
PIN = CONFIG / "native-five-minute-core-image.compose.yml"
LOCK = CONFIG / ".serviceit-five-minute-activate.lock"
BASE = "serviceit-leadgen-vps"
CORE = BASE + "-coordinator-1"
OTHER = [BASE + "-" + x + "-1" for x in
         ("auxiliaries", "business", "observer", "fns-msp")]
SOURCE_NAME = "serviceit_windows_autofill.py"
MODULE = "leadpilot.serviceit_windows_autofill"
MODULE_DIR = "/app/leadpilot/"
RELEASES = (HOME / "serviceit/releases").resolve()


class Blocked(Exception):
    pass


def need(ok, code):
    if not ok:
        raise Blocked(code)


def run(args, timeout=70, check=True):
    try:
        p = subprocess.run(args, text=True, stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise Blocked("COMMAND_TIMEOUT_OR_UNAVAILABLE") from None
    if check:
        need(p.returncode == 0, "COMMAND_FAILED")
    return p


def output(args, timeout=70):
    return run(args, timeout=timeout).stdout.strip()


def inspected(name, template):
    return output(["docker", "inspect", "-f", template, name])


def digest_id(name):
    value = inspected(name, "{{.Image}}")
    need(re.fullmatch(r"sha256:[a-f0-9]{64}", value) is not None,
         "IMAGE_ID_INVALID")
    return value


def healthy(name):
    try:
        return inspected(name,
             "{{.State.Running}}:{{.State.Health.Status}}") == "true:healthy"
    except Blocked:
        return False


def runtime_sha():
    code = """
import hashlib,importlib.util
m=importlib.util.find_spec('leadpilot.serviceit_windows_autofill')
assert m and m.origin and m.origin.startswith('/app/leadpilot/')
print(hashlib.sha256(open(m.origin,'rb').read()).hexdigest())
"""
    result = output(["docker", "exec", CORE, "python", "-B", "-c", code])
    need(re.fullmatch(r"[a-f0-9]{64}", result) is not None,
         "RUNTIME_SOURCE_SHA_INVALID")
    return result


def core_cadence():
    return output(["docker", "exec", CORE, "python", "-B", "-c",
        "from leadpilot.serviceit_vps_worker import INTERVAL_SECONDS; print(INTERVAL_SECONDS)"]) == "300"


def env_flag(name, key):
    return output(["docker", "exec", name, "printenv", key])


def safe_write(path, content):
    need(path.is_file() and not path.is_symlink(),
         "EXISTING_IMAGE_PIN_NOT_FILE")
    handle, tmp = tempfile.mkstemp(prefix=".b2b-ai-core-", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf8") as out:
            os.fchmod(out.fileno(), 0o600)
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def compose_cmd(paths, *rest):
    args = ["docker", "compose", "--env-file", str(ENV)]
    for file in paths:
        args.extend(["-f", str(file)])
    return args + list(rest)


def image_pin(image):
    return ('services:\n  coordinator:\n    image: "' + image +
            '"\n    pull_policy: never\n')


def preflight(paths, former_id):
    need(len(paths) == 2 and PIN in paths, "COMPOSE_PATH_DRIFT")
    need(any(p.resolve().is_relative_to(RELEASES)
             and re.search(r"/releases/[a-f0-9]{40}/deploy/serviceit-vps\.compose\.yml$",
                           str(p.resolve())) for p in paths),
         "RELEASE_PATH_UNVERIFIED")
    need(all(p.is_file() and not p.is_symlink() for p in paths),
         "COMPOSE_FILE_UNTRUSTED")
    need(PIN.read_text("utf8") == image_pin(former_id),
         "PREVIOUS_PIN_DRIFT")
    labels = inspected(CORE, '{{index .Config.Labels "com.docker.compose.project"}}')
    need(labels == BASE, "COMPOSE_PROJECT_DRIFT")
    original = json.loads(output(compose_cmd(paths, "config", "--format", "json")))
    service = original["services"]["coordinator"]
    actual_env = dict(s.split("=", 1) for s in json.loads(
        inspected(CORE, "{{json .Config.Env}}")))
    need(isinstance(service.get("environment"), dict)
         and all(str(value) == actual_env.get(key)
                 for key, value in service["environment"].items()),
         "COMPOSE_ENV_DRIFT")
    for key, value in (
        ("SERVICEIT_VPS_WRITER_MODE", "LIVE"),
        ("SERVICEIT_VPS_CUTOVER_AUTHORIZED", "YES"),
        ("SERVICEIT_VPS_OLD_GH_WRITERS_DISABLED_ACK", "YES"),
        ("SERVICEIT_VPS_POLL_ENABLED", "YES"),
        ("SERVICEIT_VPS_INTAKE_ENABLED", "NO"),
        ("SERVICEIT_AI_PROVIDER", "routerai"),
        ("SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED", "YES"),
        ("SERVICEIT_ROUTERAI_PRIVACY_APPROVED", "YES_1062_21"),
    ):
        need(actual_env.get(key, "NO") == value, "NATIVE_GATES_UNVERIFIED")
    return service["environment"]


def wait_coordinator(expected_id, expected_sha):
    for _ in range(20):
        if healthy(CORE):
            break
        time.sleep(4)
    need(healthy(CORE) and digest_id(CORE) == expected_id,
         "CORE_HEALTH_OR_IMAGE_MISMATCH")
    need(runtime_sha() == expected_sha and core_cadence(),
         "NEW_RUNTIME_MODULE_OR_INTERVAL_MISMATCH")


def main():
    need(os.geteuid() != 0, "NONROOT_REQUIRED")
    need(len(sys.argv) == 4, "ARGS_REQUIRED")
    runid, oldsha, newsha = sys.argv[1:]
    need(re.fullmatch(r"[0-9]{7,16}", runid) is not None
         and re.fullmatch(r"[a-f0-9]{64}", oldsha) is not None
         and re.fullmatch(r"[a-f0-9]{64}", newsha) is not None
         and oldsha != newsha, "SOURCE_SHA_INVALID")
    stage = HOME / "serviceit/staging" / ("b2b-ai-core-" + runid)
    new_file = stage / SOURCE_NAME
    need(new_file.is_file() and not new_file.is_symlink()
         and new_file.stat().st_uid == os.geteuid()
         and 0 < new_file.stat().st_size < 150000
         and hashlib.sha256(new_file.read_bytes()).hexdigest() == newsha,
         "STAGED_SOURCE_MISMATCH")
    need(ENV.is_file() and not ENV.is_symlink()
         and (ENV.stat().st_mode & 0o077) == 0,
         "ENV_FILE_NOT_PROTECTED")
    need(PIN.is_file() and not PIN.is_symlink()
         and (PIN.stat().st_mode & 0o077) == 0,
         "CORE_IMAGE_PIN_UNVERIFIED")
    need(LOCK.parent.is_dir() and not LOCK.is_symlink(), "LOCK_INVALID")
    with LOCK.open("a") as locked:
        os.chmod(LOCK, 0o600)
        fcntl.flock(locked.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        former_id = None
        paths = []
        preserved = {}
        pin_modified = False
        try:
            need(healthy(CORE) and all(healthy(x) for x in OTHER),
                 "BASELINE_HEALTH_UNVERIFIED")
            preserved = {name: digest_id(name) for name in OTHER}
            former_id = digest_id(CORE)
            need(runtime_sha() == oldsha and core_cadence(),
                 "RUNNING_SOURCE_NOT_REVIEWED_BASELINE")
            labels = inspected(CORE,
                '{{index .Config.Labels "com.docker.compose.project.config_files"}}')
            paths = [Path(p.strip()) for p in labels.split(",") if p.strip()]
            need(len(paths) == len(set(paths)), "DUPLICATE_COMPOSE_FILES")
            expected_env = preflight(paths, former_id)
            print("B2B_AI_CORE_PREFLIGHT=YES", flush=True)
            baseline = "serviceit-b2b-ai-baseline:" + runid
            target = "serviceit-b2b-ai-core:" + runid
            need(run(["docker", "image", "inspect", target],
                     check=False).returncode != 0, "NEW_TAG_ALREADY_EXISTS")
            run(["docker", "tag", former_id, baseline])
            build = stage / "overlay"
            build.mkdir(mode=0o700, exist_ok=False)
            (build / SOURCE_NAME).write_bytes(new_file.read_bytes())
            (build / "Dockerfile").write_text(
                "FROM " + baseline + "\nCOPY --chmod=0444 " +
                SOURCE_NAME + " " + MODULE_DIR + SOURCE_NAME + "\n",
                encoding="utf8")
            run(["docker", "build", "--network", "none", "--pull=false",
                 "--no-cache", "-t", target, "-f", str(build / "Dockerfile"),
                 str(build)], timeout=210)
            new_id = output(["docker", "image", "inspect", "-f", "{{.Id}}", target])
            need(re.fullmatch(r"sha256:[a-f0-9]{64}", new_id) is not None
                 and new_id != former_id, "BUILT_IMAGE_INVALID")
            smoke = """
import hashlib,importlib.util
s=importlib.util.find_spec('leadpilot.serviceit_windows_autofill')
assert s and s.origin and s.origin.startswith('/app/leadpilot/')
assert hashlib.sha256(open(s.origin,'rb').read()).hexdigest()==__import__('sys').argv[1]
from leadpilot.serviceit_windows_autofill import preliminary_schema_limits
from leadpilot.serviceit_vps_worker import INTERVAL_SECONDS
assert callable(preliminary_schema_limits) and INTERVAL_SECONDS == 300
print('CORE_SMOKE_OK')
"""
            proof = run(["docker", "run", "--rm", "--network", "none",
                "--read-only", "--user", "10001:10001", "--pids-limit", "64",
                "--memory", "256m", "--cap-drop", "ALL",
                "--entrypoint", "python", new_id,
                "-B", "-c", smoke, newsha], timeout=95)
            need(proof.stdout.strip() == "CORE_SMOKE_OK",
                 "OFFLINE_IMAGE_SMOKE_FAILED")
            print("B2B_AI_CORE_IMAGE_VERIFIED=YES", flush=True)
            safe_write(PIN, image_pin(new_id))
            pin_modified = True
            model = json.loads(output(compose_cmd(paths, "config",
                                                  "--format", "json")))
            next_service = model["services"]["coordinator"]
            need(next_service.get("image") == new_id
                 and next_service.get("environment") == expected_env,
                 "COMPOSE_NEW_IMAGE_OR_ENV_MISMATCH")
            run(compose_cmd(paths, "up", "-d", "--no-deps", "--no-build",
                            "--pull", "never", "--force-recreate", "coordinator"),
                timeout=160)
            wait_coordinator(new_id, newsha)
            need(all(healthy(name) and digest_id(name)==expected
                     for name, expected in preserved.items()),
                 "UNRELATED_CONTAINER_CHANGED")
            print("B2B_AI_CORE_ACTIVATED=YES", flush=True)
            print("B2B_AI_CORE_ROLLBACK=NOT_NEEDED", flush=True)
            return 0
        except Exception as error:
            code = str(error) if isinstance(error, Blocked) else "UNEXPECTED_EXCEPTION"
            if re.fullmatch(r"[A-Z0-9_]{3,100}", code) is None:
                code = "UNEXPECTED_EXCEPTION"
            print("B2B_AI_CORE_BLOCKED_" + code + "=YES", flush=True)
            if pin_modified and former_id is not None:
                try:
                    safe_write(PIN, image_pin(former_id))
                    run(compose_cmd(paths, "up", "-d", "--no-deps",
                                    "--no-build", "--pull", "never",
                                    "--force-recreate", "coordinator"), timeout=160)
                    wait_coordinator(former_id, oldsha)
                    print("B2B_AI_CORE_ROLLBACK=YES", flush=True)
                except Exception:
                    print("B2B_AI_CORE_ROLLBACK=FAILED", flush=True)
            print("B2B_AI_CORE_BLOCKED=YES", flush=True)
            return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("B2B_AI_CORE_PREFLIGHT_BLOCKED=YES", flush=True)
        raise SystemExit(2)
