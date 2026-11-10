#!/usr/bin/env python3
"""Add ONLY opt-in public-company research to existing healthy VPS coordinator.

This changes one nonsecret Compose feature toggle, reuses the exact current
image and retains all existing TDI, B2B-V4 and INN flags. No CRM writes or
external Brave/RouterAI requests by installer. Fail closed + rollback.
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
ENV_FILE = CONFIG / "vps.env"
PIN = CONFIG / "native-five-minute-core-image.compose.yml"
LOCK = CONFIG / ".serviceit-five-minute-activate.lock"
CORE = "serviceit-leadgen-vps-coordinator-1"
FLAG = "SERVICEIT_DEEP_COMPANY_SEARCH_ENABLED"
EXISTING_FLAGS = (
    "SERVICEIT_PUBLIC_COMPANY_REQUISITES_EXTRA_SEARCH_ENABLED",
    "SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED",
    "SERVICEIT_VPS_B2B_PUBLIC_INN_ENABLED",
    "SERVICEIT_VPS_TENDER_DOC_INTEL_ENABLED",
)
EXPECTED_SOURCE_SHAS = {
    "serviceit_vps_b2b_deep_research.py":
        "5cf0ff1bfed170cc208bd4adc6e29d748513392f9e852478bf94fd3cd03198ca",
    "serviceit_b2b_public_mirrors.py":
        "e11fb8f76618371e80938516c569fd5aea8757baac8254e02ffa3ec9716ab593",
    "serviceit_b2b_dossier_v11.py":
        "233c211f75e75d4bfeddbedd9e3b163ddf1e05ee39372bb6010b119f8a88009a",
}
GATES = {
    "SERVICEIT_VPS_WRITER_MODE": "LIVE",
    "SERVICEIT_VPS_CUTOVER_AUTHORIZED": "YES",
    "SERVICEIT_VPS_OLD_GH_WRITERS_DISABLED_ACK": "YES",
    "SERVICEIT_VPS_POLL_ENABLED": "YES",
    "SERVICEIT_VPS_INTAKE_ENABLED": "NO",
    "SERVICEIT_AI_PROVIDER": "routerai",
    "SERVICEIT_ROUTERAI_PRIVACY_APPROVED": "YES_1062_21",
    "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED": "YES",
    "SERVICEIT_VPS_BROWSER_RESEARCH_ENABLED": "YES",
    "SERVICEIT_VPS_BROWSER_PROXY_ISOLATION_VERIFIED": "YES",
}
NEIGHBORS = (
    "serviceit-leadgen-vps-auxiliaries-1",
    "serviceit-leadgen-vps-business-1",
    "serviceit-leadgen-vps-observer-1",
    "serviceit-leadgen-vps-fns-msp-1",
    "serviceit-b2b-vps-receiver-1",
    "serviceit-b2b-vps-writer-1",
)


class FlagBlocked(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise FlagBlocked(code)


def call(argv, *, timeout=110, check=True):
    try:
        p = subprocess.run(argv, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True,
                           timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise FlagBlocked("COMMAND_OR_TIMEOUT") from None
    if check and p.returncode:
        raise FlagBlocked("COMMAND_FAILED")
    return p


def out(argv, *, timeout=110):
    return call(argv, timeout=timeout).stdout.strip()


def healthy(name):
    p = call(["docker", "inspect", "-f",
              "{{.State.Running}}:{{.State.Health.Status}}", name],
             check=False)
    return p.returncode == 0 and p.stdout.strip() == "true:healthy"


def image(name):
    value = out(["docker", "inspect", "-f", "{{.Image}}", name])
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None,
            "IMAGE_ID_INVALID")
    return value


def present(name):
    return call(["docker", "inspect", "-f", "{{.Image}}", name],
                check=False).returncode == 0


def env_of(name):
    values = json.loads(out(["docker", "inspect", "-f",
                             "{{json .Config.Env}}", name]))
    require(type(values) is list and all(type(x) is str for x in values),
            "LIVE_ENV_SHAPE_UNVERIFIED")
    return dict(x.split("=", 1) for x in values if "=" in x)


def current_source_shas():
    code = """
from pathlib import Path
import hashlib,json
names=json.loads(__import__('sys').argv[1])
value={}
for name in names:
    p=Path('/app/leadpilot')/name
    value[name]=(hashlib.sha256(p.read_bytes()).hexdigest()
                 if p.is_file() and not p.is_symlink() else 'MISSING')
print(json.dumps(value,sort_keys=True))
"""
    return json.loads(out(["docker", "exec", CORE, "python", "-B", "-c",
                           code, json.dumps(list(EXPECTED_SOURCE_SHAS))]))


def compose_paths():
    raw = out(["docker", "inspect", "-f",
               '{{index .Config.Labels "com.docker.compose.project.config_files"}}',
               CORE])
    paths = [Path(p.strip()) for p in raw.split(",") if p.strip()]
    require(2 <= len(paths) <= 12 and PIN in paths
            and len(paths) == len(set(paths)),
            "COMPOSE_PIN_NOT_FOUND")
    require(all(p.is_file() and not p.is_symlink()
                and p.resolve().is_relative_to(ROOT.resolve())
                for p in paths), "COMPOSE_PATH_UNTRUSTED")
    require(out(["docker", "inspect", "-f",
                 '{{index .Config.Labels "com.docker.compose.project"}}', CORE])
            == "serviceit-leadgen-vps", "COMPOSE_PROJECT_CHANGED")
    return paths


def compose(paths, *args):
    argv = ["docker", "compose", "--env-file", str(ENV_FILE)]
    for p in paths:
        argv += ["-f", str(p)]
    return argv + list(args)


def compose_json(paths):
    return json.loads(out(compose(paths, "config", "--format", "json")))


def pinned_add_only_one_flag(old_pin, image_digest):
    """Canonical public feature pin with exact 4 existing toggles, no secrets."""
    if type(old_pin) is not str:
        raise FlagBlocked("PIN_NOT_TEXT")
    pattern = (
        r'services:\n  coordinator:\n    image: "'
        + re.escape(image_digest)
        + r'"\n    pull_policy: never\n    environment:\n'
        + r'((?:      SERVICEIT_[A-Z0-9_]+: "(?:YES|NO)"\n){4})\Z'
    )
    m = re.fullmatch(pattern, old_pin)
    require(m is not None, "CANONICAL_PIN_FORMAT_CHANGED")
    pairs = re.findall(
        r'(?m)^      (SERVICEIT_[A-Z0-9_]+): "(YES|NO)"$', m.group(1))
    existing = dict(pairs)
    require(len(pairs) == len(existing) == len(EXISTING_FLAGS)
            and set(existing) == set(EXISTING_FLAGS)
            and existing["SERVICEIT_PUBLIC_COMPANY_REQUISITES_EXTRA_SEARCH_ENABLED"] == "YES"
            and existing["SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED"] == "YES"
            and existing["SERVICEIT_VPS_TENDER_DOC_INTEL_ENABLED"] == "YES",
            "EXISTING_RESEARCH_TDI_FLAGS_NOT_PRESERVED")
    require(FLAG not in old_pin, "COMPANY_FLAG_ALREADY_IN_PIN")
    return old_pin + '      ' + FLAG + ': "YES"\n'


def atomic_write(path, data):
    fd, temp = tempfile.mkstemp(prefix=".buyer-opt-in-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf8") as handle:
            os.fchmod(fd, 0o600)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass


def pending_write_count():
    code = """
import os
from leadpilot.serviceit_vps_pg_queue import db
with db(dict(os.environ)) as conn:
    with conn.cursor() as cur:
        cur.execute(
          "SELECT count(*) FROM serviceit_vps_native_receipts "
          "WHERE status IN ('RESERVED','WRITE_INTENT') "
          "AND namespace IN ('AI_NINE_V1','B2B_RESEARCH_V4',"
          "'B2B_PUBLIC_INN_V1','B2B_PUBLIC_INN_V2','FNS_CRM_V1','TASK_V1')")
        total=int(cur.fetchone()[0])
        for table in (
            'serviceit_vps_b2b_dossier_v10_receipts',
            'serviceit_vps_b2b_dossier_v11_receipts',
            'serviceit_vps_b2b_tender_doc_receipts',
        ):
            cur.execute('SELECT to_regclass(%s)',('public.'+table,))
            if cur.fetchone()[0] is None:
                continue
            statuses=('RESERVED','WRITE_INTENT','AI_INTENT')
            cur.execute('SELECT count(*) FROM '+table+' WHERE status=ANY(%s)',
                        (list(statuses),))
            total+=int(cur.fetchone()[0])
        print(total)
"""
    value = out(["docker", "exec", CORE, "python", "-B", "-c", code])
    require(value.isdecimal(), "INFLIGHT_QUERY_INVALID")
    return int(value)


def wait_healthy(expected_image, expected_env):
    for _ in range(32):
        if healthy(CORE):
            break
        time.sleep(3)
    require(healthy(CORE) and image(CORE) == expected_image,
            "NEW_COORDINATOR_UNHEALTHY")
    require(current_source_shas() == EXPECTED_SOURCE_SHAS,
            "SOURCE_SHA_CHANGED_AFTER_UPDATE")
    require(env_of(CORE) == expected_env, "UNRELATED_ENV_CHANGE_AFTER_UPDATE")
    require(out(["docker", "exec", CORE, "python", "-B", "-c",
                 "from leadpilot.serviceit_vps_worker import INTERVAL_SECONDS;print(INTERVAL_SECONDS)"])
            == "300", "FIVE_MINUTE_CADENCE_CHANGED")


def run(run_id):
    require(os.geteuid() != 0, "NONROOT_HOST_REQUIRED")
    require(re.fullmatch(r"[0-9]{7,16}", run_id) is not None,
            "RUN_ID_INVALID")
    for p in (ENV_FILE, PIN):
        require(p.is_file() and not p.is_symlink()
                and p.stat().st_mode & 0o077 == 0, "PRIVATE_PIN_REQUIRED")
    require(LOCK.parent.is_dir() and not LOCK.is_symlink(),
            "ACTIVATION_LOCK_UNSAFE")
    with LOCK.open("a") as lock:
        os.chmod(LOCK, 0o600)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        changed = False
        old_pin, old_image, old_env, paths = None, None, None, []
        neighbors = {}
        try:
            require(healthy(CORE), "PREVIOUS_COORDINATOR_UNHEALTHY")
            require(pending_write_count() == 0,
                    "PENDING_AI_DOCUMENT_OR_CRM_WRITE_NO_RESTART")
            require(current_source_shas() == EXPECTED_SOURCE_SHAS,
                    "REVIEWED_PUBLIC_RESEARCH_CODE_DRIFT")
            old_env = env_of(CORE)
            require(all(old_env.get(k) == v for k,v in GATES.items()),
                    "EXISTING_PRIVACY_AND_POLL_GATES_MISSING")
            # Secrets live in /run/secrets, not plaintext Docker env values.
            # Require an actual mounted Brave token without ever logging it.
            secrets_check = """
import os
from leadpilot.serviceit_vps_pg_queue import secret_file
value=secret_file(dict(os.environ),
                  'SERVICEIT_BRAVE_SEARCH_API_KEY',optional=True)
print('YES' if isinstance(value,str) and 12 <= len(value) <= 200 else 'NO')
"""
            require(out(["docker","exec",CORE,"python","-B","-c",
                         secrets_check]) == "YES",
                    "BRAVE_MOUNTED_SEARCH_TOKEN_REQUIRED")
            require(old_env.get(FLAG) not in ("YES", "NO"),
                    "FEATURE_FLAG_ALREADY_CONFIGURED")
            old_image = image(CORE)
            neighbors = {
                name: (image(name), healthy(name))
                for name in NEIGHBORS if present(name)
            }
            paths = compose_paths()
            old_pin = PIN.read_text("utf8")
            new_pin = pinned_add_only_one_flag(old_pin, old_image)
            before = compose_json(paths)
            base = before["services"]["coordinator"]
            require(base.get("image") == old_image
                    and type(base.get("environment")) is dict
                    and FLAG not in base["environment"],
                    "OLD_COMPOSE_NOT_AS_EXPECTED")
            require(all(str(v) == old_env.get(k)
                        for k,v in base["environment"].items() if v is not None),
                    "CURRENT_COMPOSE_RUNTIME_ENV_DRIFT")
            # Compare the entire resolved stack: not just coordinator.
            path_trial = CONFIG / (".buyer-flag-trial-" + run_id + ".yml")
            atomic_write(path_trial, new_pin)
            try:
                trial = compose_json(
                    [p for p in paths if p != PIN] + [path_trial])
            finally:
                path_trial.unlink(missing_ok=True)
            after = trial["services"]["coordinator"]
            expected = dict(base)
            expected["environment"] = dict(
                base["environment"], **{FLAG: "YES"})
            require(after == expected
                    and {k:v for k,v in trial["services"].items()
                         if k != "coordinator"} ==
                        {k:v for k,v in before["services"].items()
                         if k != "coordinator"},
                    "COMPOSE_DIFF_NOT_EXACT_ONE_FLAG")
            print("BUYER_FLAG_PREFLIGHT=YES",flush=True)
            atomic_write(PIN, new_pin)
            changed = True
            call(compose(paths, "up", "-d", "--no-deps", "--no-build",
                         "--pull", "never", "--force-recreate", "coordinator"),
                 timeout=190)
            wait_healthy(old_image, dict(old_env, **{FLAG: "YES"}))
            require(all(image(name) == digest and
                        (not healthy_before or healthy(name))
                        for name, (digest, healthy_before) in neighbors.items()),
                    "NEIGHBOR_CHANGED")
            print("BUYER_FLAG_ACTIVE=YES",flush=True)
            print("BUYER_FLAG_REUSED_IMAGE=YES",flush=True)
            print("BUYER_FLAG_INSTALLER_CRM_WRITES=NO",flush=True)
            print("BUYER_FLAG_ROLLBACK=NOT_NEEDED",flush=True)
            return 0
        except Exception as exc:
            code = str(exc) if isinstance(exc,FlagBlocked) else "UNEXPECTED_FAIL_CLOSED"
            if not re.fullmatch(r"[A-Z0-9_]{4,95}",code):
                code = "UNEXPECTED_FAIL_CLOSED"
            print("BUYER_FLAG_REASON="+code,flush=True)
            if changed and old_pin and old_image and paths:
                try:
                    atomic_write(PIN, old_pin)
                    call(compose(paths,"up","-d","--no-deps","--no-build",
                                 "--pull","never","--force-recreate",
                                 "coordinator"),timeout=180)
                    require(healthy(CORE) and image(CORE)==old_image
                            and env_of(CORE)==old_env,
                            "ROLLBACK_NOT_PROVEN")
                    print("BUYER_FLAG_ROLLBACK=YES",flush=True)
                except Exception:
                    print("BUYER_FLAG_ROLLBACK=UNVERIFIED",flush=True)
            else:
                print("BUYER_FLAG_ROLLBACK=NOT_NEEDED",flush=True)
            print("BUYER_FLAG_ACTIVE=NO",flush=True)
            return 2


def main():
    if len(sys.argv)!=2:
        print("BUYER_FLAG_ACTIVE=NO",flush=True)
        return 2
    try:
        return run(sys.argv[1])
    except Exception:
        print("BUYER_FLAG_ACTIVE=NO",flush=True)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
