#!/usr/bin/env python3
"""Stage TinyFish OFF-only Compose overlay on the *existing* VPS, no restarts.

Run exclusively from the owner-triggered, Git-SHA-attested SSH workflow.
Checks exact current three Compose files, live healthy coordinator, offline
before/after effective Compose equality except a single secret+OFF flags.
No Docker up/restart, no database access, no model calls and no CRM writes.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path.home() / "serviceit"
CONFIG = ROOT / "config"
ENV = CONFIG / "vps.env"
PIN = CONFIG / "native-five-minute-core-image.compose.yml"
LOCK = CONFIG / ".serviceit-five-minute-activate.lock"
DEST = CONFIG / "serviceit-vps-tinyfish-free.compose.yml"
CORE = "serviceit-leadgen-vps-coordinator-1"
SECRET = "serviceit_tinyfish_key"
MOUNT = "/run/secrets/serviceit_tinyfish_key"
SOURCE = "/etc/serviceit/secrets/tinyfish_api_key"
FLAGS = {
    "SERVICEIT_TINYFISH_API_KEY_FILE": MOUNT,
    "SERVICEIT_VPS_B2B_FREE_PUBLIC_FETCH_ENABLED": "NO",
    "SERVICEIT_B2B_TENDEM_PUBLIC_EXTRACTION_APPROVED": "NO",
    "SERVICEIT_B2B_CENTER_PUBLIC_EXTRACTION_LICENSED": "NO",
}


class StageBlocked(ValueError):
    pass


def need(ok: bool, code: str) -> None:
    if not ok:
        raise StageBlocked(code)


def command(args, *, timeout=35):
    try:
        p = subprocess.run(args, check=False, capture_output=True,
                           text=True, stdin=subprocess.DEVNULL,
                           timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise StageBlocked("COMMAND_UNAVAILABLE") from None
    need(p.returncode == 0, "COMMAND_FAILED")
    return p.stdout.strip()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def baseline_paths():
    raw = command(["docker", "inspect", "-f",
        '{{index .Config.Labels "com.docker.compose.project.config_files"}}',
        CORE])
    paths = tuple(Path(x) for x in raw.split(",") if x)
    need(len(paths) == 3 and len(set(paths)) == 3
         and PIN in paths
         and paths[0].name == "serviceit-vps.compose.yml"
         and paths[1].name == "serviceit-vps-brave.compose.yml"
         and paths[2] == PIN, "COMPOSE_LIVE_PATHS_CHANGED")
    for p in paths:
        need(p.is_absolute() and p.is_file() and not p.is_symlink()
             and p.resolve().is_relative_to(ROOT.resolve()),
             "COMPOSE_UNTRUSTED_PATH")
    image = command(["docker", "inspect", "-f", "{{.Image}}", CORE])
    need(re.fullmatch(r"sha256:[a-f0-9]{64}", image) is not None,
         "LIVE_IMAGE_INVALID")
    health = command(["docker", "inspect", "-f",
                      "{{.State.Health.Status}}", CORE])
    need(health == "healthy", "COORDINATOR_NOT_HEALTHY")
    return paths, image


def effective(files):
    args = ["docker", "compose", "--env-file", str(ENV)]
    for f in files:
        args += ["-f", str(f)]
    args += ["config", "--format", "json"]
    try:
        data = json.loads(command(args, timeout=60))
    except (json.JSONDecodeError, UnicodeError):
        raise StageBlocked("COMPOSE_JSON_UNREADABLE") from None
    need(type(data) is dict and type(data.get("services")) is dict,
         "COMPOSE_JSON_INVALID")
    return data


def verify_overlay(before, after):
    """Fail closed if ANY existing service, mount, image or setting drifts."""
    import copy
    need(type(before) is dict and type(after) is dict,
         "COMPOSE_COMPARE_INVALID")
    b, a = copy.deepcopy(before), copy.deepcopy(after)
    need(a.get("name") == b.get("name"), "COMPOSE_PROJECT_DRIFT")
    need(set(a.get("services", {})) == set(b.get("services", {}))
         and "coordinator" in b["services"], "COMPOSE_SERVICE_SET_DRIFT")
    bc = b["services"]["coordinator"]
    ac = a["services"]["coordinator"]
    need(type(bc.get("environment")) is dict
         and type(ac.get("environment")) is dict
         and type(bc.get("secrets")) is list
         and type(ac.get("secrets")) is list, "COMPOSE_COORDINATOR_INVALID")
    new_env = ac["environment"]
    need(all(new_env.get(k) == v and k not in bc["environment"]
             for k, v in FLAGS.items()), "COMPOSE_NEW_FLAGS_NOT_STRICTLY_OFF")
    for k in FLAGS:
        new_env.pop(k)
    need(new_env == bc["environment"], "COMPOSE_EXISTING_ENV_DRIFT")
    ac["environment"] = bc["environment"]

    def names(rows):
        need(all(type(x) is dict and type(x.get("source")) is str
                 and type(x.get("target")) is str for x in rows),
             "COMPOSE_SECRET_FORMAT_CHANGED")
        return {x["source"]: x for x in rows}
    before_secrets = names(bc["secrets"])
    after_secrets = names(ac["secrets"])
    need(len(before_secrets) == len(bc["secrets"])
         and len(after_secrets) == len(ac["secrets"])
         and set(after_secrets) == set(before_secrets) | {SECRET},
         "COMPOSE_SECRET_SET_DRIFT")
    need(after_secrets[SECRET].get("target") in (SECRET, MOUNT)
         and all(after_secrets[name] == old for name, old in before_secrets.items()),
         "COMPOSE_EXISTING_SECRETS_DRIFT")
    ac["secrets"] = bc["secrets"]
    need(a["services"] == b["services"], "COMPOSE_OTHER_SERVICE_DRIFT")
    b_sec, a_sec = b.get("secrets", {}), a.get("secrets", {})
    need(type(b_sec) is dict and type(a_sec) is dict
         and set(a_sec) == set(b_sec) | {SECRET}
         and all(a_sec[k] == v for k, v in b_sec.items())
         and type(a_sec[SECRET]) is dict
         and a_sec[SECRET].get("file") == SOURCE, "COMPOSE_SECRET_DEFINITION_DRIFT")
    a["secrets"] = b_sec
    need(a == b, "COMPOSE_GLOBAL_CONFIG_DRIFT")
    return True


def stage(run_id, expected_sha):
    need(os.geteuid() != 0, "ROOT_USER_FORBIDDEN")
    need(re.fullmatch(r"\d{7,16}", run_id) is not None
         and re.fullmatch(r"[a-f0-9]{64}", expected_sha) is not None,
         "INPUT_INVALID")
    src = ROOT / "staging" / ("tinyfish-compose-" + run_id) / "overlay.yml"
    need(src.is_file() and not src.is_symlink()
         and src.stat().st_uid == os.geteuid()
         and 100 < src.stat().st_size < 6000
         and sha(src.read_bytes()) == expected_sha, "OVERLAY_SOURCE_UNVERIFIED")
    need(ENV.is_file() and not ENV.is_symlink()
         and ENV.stat().st_mode & 0o077 == 0
         and CONFIG.is_dir() and not CONFIG.is_symlink(),
         "PRIVATE_CONFIG_REQUIRED")
    need(LOCK.parent == CONFIG and not LOCK.is_symlink(),
         "LOCK_PATH_INVALID")
    with LOCK.open("a") as handle:
        os.chmod(LOCK, 0o600)
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise StageBlocked("OTHER_DEPLOYMENT_IN_PROGRESS") from None
        paths, before_image = baseline_paths()
        before = effective(paths)
        candidate = src
        after = effective([*paths, candidate])
        verify_overlay(before, after)
        # Verify no other chat or operator has created a conflicting override.
        if DEST.exists() or DEST.is_symlink():
            need(DEST.is_file() and not DEST.is_symlink()
                 and sha(DEST.read_bytes()) == expected_sha,
                 "STAGED_OVERLAY_ALREADY_DIFFERENT")
            print("TINYFISH_STAGE_RESULT=ALREADY_STAGED", flush=True)
        else:
            fd, temp = tempfile.mkstemp(prefix=".tinyfish-verified-",
                                         dir=str(CONFIG))
            try:
                with os.fdopen(fd, "wb") as f:
                    os.fchmod(f.fileno(), 0o600)
                    f.write(src.read_bytes())
                    f.flush()
                    os.fsync(f.fileno())
                need(sha(Path(temp).read_bytes()) == expected_sha,
                     "COPIED_SHA_MISMATCH")
                os.replace(temp, DEST)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
            print("TINYFISH_STAGE_RESULT=CONFIG_ONLY_STAGED", flush=True)
        need(effective([*paths, DEST]) == after,
             "STAGED_COMPOSE_DIFFERS")
        paths2, image2 = baseline_paths()
        need(paths2 == paths and image2 == before_image,
             "RUNTIME_CHANGED_DURING_STAGE")
        print("TINYFISH_STAGE_COMPOSE_PREVIEW=PASS", flush=True)
        print("TINYFISH_STAGE_COORDINATOR_RESTARTED=NO", flush=True)
        print("TINYFISH_STAGE_CRM_WRITES=NO", flush=True)
        print("TINYFISH_STAGE_FETCH_ENABLED=NO", flush=True)
        return 0


def main(argv):
    try:
        need(len(argv) == 2, "INPUT_COUNT_INVALID")
        return stage(argv[0], argv[1])
    except Exception as exc:
        reason = str(exc) if isinstance(exc, StageBlocked) else "STAGE_UNEXPECTED_FAILURE"
        if re.fullmatch(r"[A-Z0-9_]{4,80}", reason) is None:
            reason = "STAGE_UNEXPECTED_FAILURE"
        print("TINYFISH_STAGE_REASON=" + reason, flush=True)
        print("TINYFISH_STAGE_RESULT=NO_DEPLOYMENT", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
