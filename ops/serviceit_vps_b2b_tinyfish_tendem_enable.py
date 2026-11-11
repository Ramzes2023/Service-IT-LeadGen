#!/usr/bin/env python3
"""One-use, roll-backable OFF->Tendem-ONLY TinyFish source gate on live coordinator.

Owner-triggered, SHA-attested. Existing Bitrix writers, queue, PostgreSQL,
model, all 5 secrets, current Docker image and all adjacent containers are
preserved. No CRM requests. If current code/config differs, FAIL CLOSED.
Do not activate B2B-Center scraping, paid agents or inaccessible documents.
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

ROOT = Path.home() / "serviceit"
CONFIG = ROOT / "config"
ENV = CONFIG / "vps.env"
PIN = CONFIG / "native-five-minute-core-image.compose.yml"
OVERLAY = CONFIG / "serviceit-vps-tinyfish-free.compose.yml"
LOCK = CONFIG / ".serviceit-five-minute-activate.lock"
CORE = "serviceit-leadgen-vps-coordinator-1"
FILES = (
    "serviceit_b2b_free_public_fallback.py",
    "serviceit_b2b_remote_public_fetch.py",
    "serviceit_b2b_public_fact_extractor.py",
    "serviceit_vps_b2b_deep_research.py",
)
NEIGHBORS = (
    "serviceit-leadgen-vps-auxiliaries-1",
    "serviceit-leadgen-vps-business-1",
    "serviceit-leadgen-vps-observer-1",
    "serviceit-leadgen-vps-fns-msp-1",
    "serviceit-b2b-vps-receiver-1",
    "serviceit-b2b-vps-writer-1",
)
FEATURE = "SERVICEIT_VPS_B2B_FREE_PUBLIC_FETCH_ENABLED"
TENDEM = "SERVICEIT_B2B_TENDEM_PUBLIC_EXTRACTION_APPROVED"
B2B = "SERVICEIT_B2B_CENTER_PUBLIC_EXTRACTION_LICENSED"
KEY = "SERVICEIT_TINYFISH_API_KEY_FILE"
MOUNT = "/run/secrets/serviceit_tinyfish_key"
SOURCE = "/etc/serviceit/secrets/tinyfish_api_key"
FLAGS_OFF = {FEATURE: "NO", TENDEM: "NO", B2B: "NO", KEY: MOUNT}
FLAGS_ON = {FEATURE: "YES", TENDEM: "YES", B2B: "NO", KEY: MOUNT}
PREFIX = "TINYFISH_TENDEM_ENABLE"


class ActivationBlocked(RuntimeError):
    pass


def need(value, code):
    if not value:
        raise ActivationBlocked(code)


def output(argv, *, timeout=90, check=True):
    try:
        p = subprocess.run(argv, stdin=subprocess.DEVNULL, text=True,
                           capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ActivationBlocked("COMMAND_TIMEOUT_OR_MISSING") from None
    if check and p.returncode != 0:
        raise ActivationBlocked("COMMAND_FAILED")
    return p.stdout.strip() if check else (p.returncode, p.stdout.strip())


def hash_bytes(data: bytes):
    return hashlib.sha256(data).hexdigest()


def container_image(name):
    image = output(["docker", "inspect", "-f", "{{.Image}}", name])
    need(re.fullmatch(r"sha256:[a-f0-9]{64}", image), "DOCKER_IMAGE_INVALID")
    return image


def health(name):
    code, text = output(["docker", "inspect", "-f",
       "{{.State.Running}}:{{if .State.Health}}{{.State.Health.Status}}{{end}}",
       name], check=False)
    return code == 0 and text == "true:healthy"


def env_of_core():
    data = json.loads(output(["docker", "inspect", "-f",
                              "{{json .Config.Env}}", CORE]))
    need(isinstance(data, list), "LIVE_ENV_INVALID")
    return dict(x.split("=", 1) for x in data if isinstance(x, str) and "=" in x)


def paths_of_core():
    data = output(["docker", "inspect", "-f",
                    '{{index .Config.Labels "com.docker.compose.project.config_files"}}',
                    CORE])
    paths = tuple(Path(p.strip()) for p in data.split(",") if p.strip())
    need(len(paths) == 4 and paths[-2:] == (PIN, OVERLAY)
         and paths[0].name == "serviceit-vps.compose.yml"
         and paths[1].name == "serviceit-vps-brave.compose.yml"
         and len(set(paths)) == 4, "COMPOSE_FOUR_PATHS_REQUIRED")
    need(all(p.is_file() and not p.is_symlink()
             and p.resolve().is_relative_to(ROOT.resolve()) for p in paths),
         "COMPOSE_PATH_UNTRUSTED")
    need(output(["docker","inspect","-f",
                  '{{index .Config.Labels "com.docker.compose.project"}}',
                  CORE]) == "serviceit-leadgen-vps", "COMPOSE_PROJECT_CHANGED")
    return paths


def compose(paths, *args):
    command = ["docker", "compose", "--env-file", str(ENV)]
    for p in paths:
        command += ["-f", str(p)]
    return command + list(args)


def effective(paths):
    d = json.loads(output(compose(paths, "config", "--format", "json")))
    need(isinstance(d, dict) and isinstance(d.get("services"), dict)
         and "coordinator" in d["services"], "COMPOSE_CONFIG_INVALID")
    return d


def diff_only_two_flags(before, after):
    import copy
    b, a = copy.deepcopy(before), copy.deepcopy(after)
    need(a.get("name") == b.get("name")
         and a.keys() == b.keys()
         and a["services"].keys() == b["services"].keys(),
         "COMPOSE_SERVICE_OR_GLOBAL_DIFF")
    ac = a["services"]["coordinator"]
    bc = b["services"]["coordinator"]
    ae, be = ac.get("environment"), bc.get("environment")
    need(isinstance(ae, dict) and isinstance(be, dict)
         and all(be.get(k) == FLAGS_OFF[k] and ae.get(k) == FLAGS_ON[k]
                 for k in (FEATURE, TENDEM))
         and all(be.get(k) == FLAGS_OFF[k] and ae.get(k) == FLAGS_ON[k]
                 for k in (B2B, KEY)),
         "PUBLIC_SOURCE_FLAGS_INVALID")
    for k in (FEATURE, TENDEM):
        ae[k] = be[k]
    need(a == b, "COMPOSE_OTHER_CONFIG_CHANGED")


def exact_source_shas(expected):
    code = """
from pathlib import Path
import hashlib,json,sys
names=json.loads(sys.argv[1])
for name in names:
    p=Path('/app/leadpilot')/name
    print(hashlib.sha256(p.read_bytes()).hexdigest()
          if p.is_file() and not p.is_symlink() else 'MISSING')
"""
    out = output(["docker", "exec", CORE, "python", "-B", "-c",
                  code, json.dumps(FILES)])
    need(out.splitlines() == list(expected), "EXACT_SOURCE_CODE_SHA_REQUIRED")


def mounted_key():
    arr = json.loads(output(["docker", "inspect", "-f", "{{json .Mounts}}", CORE]))
    matches = [m for m in arr if m.get("Destination") == MOUNT]
    need(len(matches) == 1
         and matches[0].get("Source") == SOURCE
         and matches[0].get("RW") is False, "TINYFISH_MOUNT_REQUIRED")
    proof = """
from pathlib import Path
p=Path('/run/secrets/serviceit_tinyfish_key')
assert p.is_file() and 16 <= p.stat().st_size <= 256
print('SECRET_MOUNT_READY')"""
    need(output(["docker", "exec", CORE, "python", "-B", "-c", proof])
         == "SECRET_MOUNT_READY", "TINYFISH_MOUNT_UNREADABLE")


def pending_writes():
    code = """
import os
from leadpilot.serviceit_vps_pg_queue import db
rows=[
('serviceit_vps_native_receipts',"status IN ('RESERVED','WRITE_INTENT')"),
('serviceit_vps_b2b_dossier_v10_receipts',"status IN ('RESERVED','WRITE_INTENT')"),
('serviceit_vps_b2b_dossier_v11_receipts',"status IN ('RESERVED','WRITE_INTENT')"),
('serviceit_vps_b2b_tender_doc_receipts',"status IN ('RESERVED','AI_INTENT','WRITE_INTENT')"),
('serviceit_vps_b2b_v19_research_receipts',"status IN ('RESERVED','WRITE_INTENT')"),
('serviceit_vps_b2b_v20_revision_receipts',"status IN ('RESERVED','WRITE_INTENT')"),
('serviceit_vps_b2b_v23_primary_source_receipts',"status IN ('RESERVED','WRITE_INTENT')"),
]
total=0
with db(dict(os.environ)) as conn:
  with conn.cursor() as cur:
    for name,where in rows:
      cur.execute("SELECT to_regclass(%s)",('public.'+name,))
      if cur.fetchone()[0] is None: continue
      cur.execute("SELECT count(*) FROM "+name+" WHERE "+where)
      total+=int(cur.fetchone()[0])
print(total)
"""
    value = output(["docker", "exec", CORE, "python", "-B", "-c", code])
    need(value.isdigit(), "PENDING_RECEIPT_AUDIT_FAILED")
    return int(value)


def atomic_overlay_write(content: bytes):
    fd, temp = tempfile.mkstemp(prefix=".tinyfish-switch-", dir=str(CONFIG))
    try:
        with os.fdopen(fd, "wb") as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, OVERLAY)
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass


def wait_live(image, source_shas, enabled):
    for _ in range(35):
        if health(CORE):
            break
        time.sleep(3)
    need(health(CORE), "COORDINATOR_NOT_HEALTHY")
    need(container_image(CORE) == image, "COORDINATOR_IMAGE_CHANGED")
    exact_source_shas(source_shas)
    mounted_key()
    actual = env_of_core()
    flags = FLAGS_ON if enabled else FLAGS_OFF
    need(all(actual.get(k) == v for k,v in flags.items()),
         "COORDINATOR_FLAGS_NOT_MATCHING")


def enable(run_id, script_sha, old_overlay_sha, source_shas):
    need(os.geteuid() != 0, "NONROOT_HOST_REQUIRED")
    need(re.fullmatch(r"[0-9]{7,16}", run_id)
         and all(re.fullmatch(r"[a-f0-9]{64}", sha)
                 for sha in (script_sha, old_overlay_sha, *source_shas))
         and len(source_shas) == len(FILES), "ATTESTED_INPUT_REQUIRED")
    stage = ROOT / "staging" / ("tinyfish-tendem-enable-" + run_id)
    active_script = stage / "activate.py"
    need(active_script.is_file() and not active_script.is_symlink()
         and active_script.stat().st_uid == os.geteuid()
         and hash_bytes(active_script.read_bytes()) == script_sha,
         "SCRIPT_SHA_MISMATCH")
    need(ENV.is_file() and not ENV.is_symlink()
         and ENV.stat().st_mode & 0o077 == 0
         and OVERLAY.is_file() and not OVERLAY.is_symlink()
         and OVERLAY.stat().st_mode & 0o077 == 0,
         "PRIVATE_CONFIG_REQUIRED")
    need(LOCK.parent.is_dir() and not LOCK.is_symlink(),
         "LOCK_PATH_INVALID")
    with LOCK.open("a") as handle:
        os.chmod(LOCK, 0o600)
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ActivationBlocked("PARALLEL_DEPLOYMENT_BLOCKED") from None
        changed = False
        old_overlay = None
        old_image = None
        paths = None
        neighbors = {}
        try:
            need(health(CORE), "COORDINATOR_NOT_HEALTHY")
            need(output(["docker", "exec", CORE, "python", "-B", "-c",
                 "from leadpilot.serviceit_vps_worker import INTERVAL_SECONDS;print(INTERVAL_SECONDS)"])
                 == "300", "FIVE_MINUTE_CADENCE_REQUIRED")
            old_image = container_image(CORE)
            exact_source_shas(source_shas)
            mounted_key()
            old_env = env_of_core()
            need(all(old_env.get(k) == v for k,v in FLAGS_OFF.items()),
                 "EXISTING_FLAGS_NOT_OFF")
            need(pending_writes() == 0, "PENDING_AI_OR_CRM_WRITE_NO_RESTART")
            paths = paths_of_core()
            old_overlay = OVERLAY.read_bytes()
            need(hash_bytes(old_overlay) == old_overlay_sha,
                 "OLD_COMPOSE_SHA_DRIFT")
            text = old_overlay.decode("utf8")
            new_text = text
            for name in (FEATURE, TENDEM):
                old_line = name + ': "NO"'
                new_line = name + ': "YES"'
                need(new_text.count(old_line) == 1, "EXACT_OFF_GATE_REQUIRED")
                new_text = new_text.replace(old_line, new_line, 1)
            need(new_text != text
                 and new_text.count(B2B + ': "NO"') == 1,
                 "B2B_UNLICENSED_MUST_STAY_OFF")
            before = effective(paths)
            candidate = stage / "candidate.compose.yml"
            candidate.write_text(new_text, "utf8")
            trial = effective([*paths[:-1], candidate])
            diff_only_two_flags(before, trial)
            for name in NEIGHBORS:
                code, value = output(["docker", "inspect", "-f", "{{.Image}}", name],
                                     check=False)
                if code == 0:
                    neighbors[name] = (value, health(name))
            print(PREFIX + "_PREFLIGHT=YES", flush=True)
            atomic_overlay_write(new_text.encode("utf8"))
            changed = True
            output(compose(paths, "up", "-d", "--no-deps", "--no-build",
                           "--pull", "never", "--force-recreate", "coordinator"),
                   timeout=200)
            wait_live(old_image, source_shas, True)
            need(paths_of_core() == paths, "COMPOSE_PATHS_CHANGED_POST_APPLY")
            after = effective(paths)
            diff_only_two_flags(before, after)
            need(all(container_image(name) == img and
                     (not was_healthy or health(name))
                     for name, (img, was_healthy) in neighbors.items()),
                 "ADJACENT_CONTAINER_DRIFT")
            print(PREFIX + "_LIVE=YES", flush=True)
            print(PREFIX + "_CODE=EXACT", flush=True)
            print(PREFIX + "_TENDEM=YES", flush=True)
            print(PREFIX + "_B2B_CENTER=NO", flush=True)
            print(PREFIX + "_CRM_WRITES_BY_INSTALLER=NO", flush=True)
            print(PREFIX + "_ROLLBACK=NOT_NEEDED", flush=True)
            return 0
        except Exception as exc:
            reason = str(exc) if isinstance(exc, ActivationBlocked) else "UNEXPECTED_FAILURE"
            if re.fullmatch(r"[A-Z0-9_]{4,90}", reason) is None:
                reason = "UNEXPECTED_FAILURE"
            print(PREFIX + "_REASON=" + reason, flush=True)
            if changed and old_overlay is not None and paths and old_image:
                try:
                    atomic_overlay_write(old_overlay)
                    output(compose(paths, "up", "-d", "--no-deps",
                                   "--no-build", "--pull", "never",
                                   "--force-recreate", "coordinator"),
                           timeout=180)
                    wait_live(old_image, source_shas, False)
                    print(PREFIX + "_ROLLBACK=YES", flush=True)
                except Exception:
                    print(PREFIX + "_ROLLBACK=UNVERIFIED", flush=True)
            else:
                print(PREFIX + "_ROLLBACK=NOT_NEEDED", flush=True)
            print(PREFIX + "_LIVE=NO", flush=True)
            return 2


if __name__ == "__main__":
    try:
        need(len(sys.argv) == 4 + len(FILES), "INPUT_COUNT_INVALID")
        raise SystemExit(enable(sys.argv[1], sys.argv[2], sys.argv[3],
                                tuple(sys.argv[4:])))
    except Exception:
        print(PREFIX + "_LIVE=NO", flush=True)
        raise SystemExit(2)
