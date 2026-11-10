#!/usr/bin/env python3
"""Owner-gated B2B V6 public legal buyer, INN and contacts deployment; no GitHub CRM writer.

Only rebuild exact live coordinator FROM its pinned image, overlay reviewed
Python source, prove dependencies and privacy mounts without reading values,
retain existing feature flag and replace coordinator. All other Docker
services, PostgreSQL records, CRM fields and existing tasks untouched by the
activator. Any CRM writes occur later via the existing five-minute worker,
under a per-card one-use PostgreSQL receipt and exact readback.
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
CORE = "serviceit-leadgen-vps-coordinator-1"
OTHER = (
    "serviceit-leadgen-vps-auxiliaries-1",
    "serviceit-leadgen-vps-business-1",
    "serviceit-leadgen-vps-observer-1",
    "serviceit-leadgen-vps-fns-msp-1",
    "serviceit-b2b-vps-receiver-1",
    "serviceit-b2b-vps-writer-1",
)
FILES = ("serviceit_vps_b2b_deep_research.py",)
# Enforce pinned previous SHA passed by GitHub owner workflow.
FLAG = "SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED"
EXTRA_FLAG = "SERVICEIT_PUBLIC_COMPANY_REQUISITES_EXTRA_SEARCH_ENABLED"
GATES = {
    "SERVICEIT_VPS_WRITER_MODE": "LIVE",
    "SERVICEIT_VPS_CUTOVER_AUTHORIZED": "YES",
    "SERVICEIT_VPS_OLD_GH_WRITERS_DISABLED_ACK": "YES",
    "SERVICEIT_VPS_POLL_ENABLED": "YES",
    "SERVICEIT_VPS_INTAKE_ENABLED": "NO",
    "SERVICEIT_AI_PROVIDER": "routerai",
    "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED": "YES",
    "SERVICEIT_ROUTERAI_PRIVACY_APPROVED": "YES_1062_21",
    "SERVICEIT_VPS_BROWSER_RESEARCH_ENABLED": "YES",
    "SERVICEIT_VPS_BROWSER_PROXY_ISOLATION_VERIFIED": "YES",
}
MAX_RUN_SECONDS = 220


class DeploymentBlocked(RuntimeError):
    pass


def require(ok, code):
    if not ok:
        raise DeploymentBlocked(code)


def call(argv, *, timeout=70, check=True):
    try:
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise DeploymentBlocked("LOCAL_TOOL_OR_TIMEOUT") from None
    if check and proc.returncode != 0:
        raise DeploymentBlocked("COMMAND_FAILED")
    return proc


def out(argv, *, timeout=70):
    return call(argv, timeout=timeout).stdout.strip()


def sha_bytes(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def image(name):
    result = out(["docker", "inspect", "-f", "{{.Image}}", name])
    require(re.fullmatch(r"sha256:[a-f0-9]{64}", result) is not None,
            "LIVE_IMAGE_ID_INVALID")
    return result


def running(name):
    try:
        return out(["docker", "inspect", "-f", "{{.State.Running}}", name]) == "true"
    except DeploymentBlocked:
        return False


def healthy(name):
    try:
        return out(["docker", "inspect", "-f",
                    "{{.State.Running}}:{{.State.Health.Status}}",
                    name]) == "true:healthy"
    except DeploymentBlocked:
        return False


def runtime_shas():
    safe_script = """
import hashlib, json
from pathlib import Path
paths = [str(Path('/app/leadpilot') / x) for x in json.loads(__import__('sys').argv[1])]
out = {}
for path in paths:
    p = Path(path)
    out[p.name] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() and not p.is_symlink() else 'MISSING'
print(json.dumps(out, sort_keys=True))
"""
    raw = out(["docker", "exec", CORE, "python", "-B", "-c",
               safe_script, json.dumps(FILES)])
    rows = json.loads(raw)
    require(isinstance(rows, dict) and set(rows) == set(FILES),
            "LIVE_PYTHON_SOURCE_UNVERIFIED")
    return rows


def secret_readiness():
    code = """
from pathlib import Path
import os, json
names = ('SERVICEIT_BITRIX_WEBHOOK','SERVICEIT_ROUTERAI_API_KEY',
         'SERVICEIT_BRAVE_SEARCH_API_KEY','SERVICEIT_PG_PASSWORD')
def present(name):
    value=os.environ.get(name+'_FILE','')
    if not value.startswith('/run/secrets/'):
        return False
    p=Path(value)
    return p.is_file() and not p.is_symlink() and 0 < p.stat().st_size < 10000
hosts = os.environ.get('SERVICEIT_PRELIMINARY_PUBLIC_HOSTS','')
print(json.dumps({'secrets':all(present(n) for n in names),
    'host': 'b2b-center.ru' in set(x.strip() for x in hosts.split(','))}))
"""
    checks = json.loads(out(["docker", "exec", CORE, "python", "-B", "-c", code]))
    require(checks == {"secrets": True, "host": True},
            "PRIVACY_SECRET_OR_PUBLIC_HOST_NOT_READY")


def env_of(name):
    raw = json.loads(out(["docker", "inspect", "-f",
                          "{{json .Config.Env}}", name]))
    require(type(raw) is list and all(type(x) is str for x in raw),
            "LIVE_ENV_INVALID")
    return dict(value.split("=", 1) for value in raw if "=" in value)


def current_paths():
    raw = out(["docker", "inspect", "-f",
               '{{index .Config.Labels "com.docker.compose.project.config_files"}}', CORE])
    paths = [Path(v.strip()) for v in raw.split(",") if v.strip()]
    require(2 <= len(paths) <= 9 and PIN in paths and
            len(set(paths)) == len(paths), "COMPOSE_PINS_NOT_AS_EXPECTED")
    root = (HOME / "serviceit").resolve()
    require(all(p.is_file() and not p.is_symlink()
                and p.resolve().is_relative_to(root)
                for p in paths), "COMPOSE_FILES_UNTRUSTED")
    require(out(["docker", "inspect", "-f",
                 '{{index .Config.Labels "com.docker.compose.project"}}', CORE])
            == "serviceit-leadgen-vps", "COMPOSE_PROJECT_MISMATCH")
    return paths


def compose(paths, *args):
    cmd = ["docker", "compose", "--env-file", str(ENV)]
    for path in paths:
        cmd.extend(["-f", str(path)])
    return cmd + list(args)


def compose_json(paths):
    return json.loads(out(compose(paths, "config", "--format", "json")))


def make_pin(image_id, *, active, extra=False):
    text = ('services:\n  coordinator:\n    image: "' + image_id +
            '"\n    pull_policy: never\n')
    if active:
        text += ('    environment:\n      ' + FLAG + ': "YES"\n')
        if extra:
            text += ('      ' + EXTRA_FLAG + ': "YES"\n')
    return text


def atomic_replace(path, text):
    fd, tmp = tempfile.mkstemp(prefix=".b2b-contacts-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf8") as sink:
            os.fchmod(fd, 0o600)
            sink.write(text)
            sink.flush()
            os.fsync(sink.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def wait_healthy(expected_image, expected_shas):
    for _ in range(20):
        if healthy(CORE):
            break
        time.sleep(3)
    require(healthy(CORE) and image(CORE) == expected_image,
            "NEW_COORDINATOR_NOT_HEALTHY")
    require(runtime_shas() == expected_shas,
            "COORDINATOR_PYTHON_SHA_MISMATCH")
    data = env_of(CORE)
    require(data.get(FLAG) == "YES" and data.get(EXTRA_FLAG) == "YES",
            "B2B_BUYER_V6_FEATURES_NOT_ENABLED")


def main():
    require(os.geteuid() != 0, "NONROOT_HOST_REQUIRED")
    require(len(sys.argv) == 3 + len(FILES),
            "OWNER_RELEASE_SHA_ARGUMENTS_REQUIRED")
    runid, old_sha, *new_shas = sys.argv[1:]
    require(re.fullmatch(r"[0-9]{7,16}", runid) is not None
            and re.fullmatch(r"[a-f0-9]{64}", old_sha) is not None
            and all(re.fullmatch(r"[a-f0-9]{64}", value)
                    for value in new_shas), "SOURCE_SHA_INPUT_INVALID")
    stage = HOME / "serviceit/staging" / ("b2b-contacts-" + runid)
    files = dict(zip(FILES, new_shas))
    require(stage.is_dir() and not stage.is_symlink()
            and stage.stat().st_uid == os.geteuid(), "STAGE_NOT_OWNED")
    for name, expected in files.items():
        path = stage / name
        require(path.is_file() and not path.is_symlink()
                and path.stat().st_uid == os.geteuid()
                and 0 < path.stat().st_size < 180000
                and sha_bytes(path) == expected,
                "STAGED_MODULE_SHA_UNVERIFIED")
    for f in (ENV, PIN):
        require(f.is_file() and not f.is_symlink()
                and f.stat().st_mode & 0o077 == 0,
                "PRIVATE_COMPOSE_FILE_REQUIRED")
    require(LOCK.parent.is_dir() and not LOCK.is_symlink(), "LOCK_UNSAFE")
    with LOCK.open("a") as handle:
        os.chmod(LOCK, 0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        changed = False
        old_image = None
        old_pin = None
        paths = []
        old_health = {}
        old_digests = {}
        try:
            require(healthy(CORE), "CURRENT_COORDINATOR_NOT_HEALTHY")
            require(healthy("serviceit-public-browser-vps-browser-1")
                    and running("serviceit-public-browser-vps-egress-1"),
                    "ISOLATED_BROWSER_NOT_READY")
            # Do not interrupt an in-flight V1 CRM write or paid model call.
            # All known cards had verified V1 receipts before this release.
            pending_code = """
import os
from leadpilot.serviceit_vps_pg_queue import db
with db(dict(os.environ)) as c:
    with c.cursor() as cur:
        cur.execute("SELECT count(*) FROM serviceit_vps_native_receipts WHERE namespace IN ('B2B_RESEARCH_V4','AI_NINE_V1') AND status IN ('RESERVED','WRITE_INTENT')")
        print(int(cur.fetchone()[0]))
"""
            require(out(["docker", "exec", CORE, "python", "-B",
                         "-c", pending_code]) == "0",
                    "V4_OR_AI_INFLIGHT_NOT_SAFE_TO_RESTART")
            runtime = runtime_shas()
            require(runtime == {FILES[0]: old_sha}, "LIVE_B2B_PREVIOUS_SHA_DRIFT")
            require(out(["docker", "exec", CORE, "python", "-B", "-c",
                         "from leadpilot.serviceit_vps_worker import INTERVAL_SECONDS;print(INTERVAL_SECONDS)"])
                    == "300", "EXACT_FIVE_MINUTE_CADENCE_REQUIRED")
            secret_readiness()
            old_image = image(CORE)
            old_env = env_of(CORE)
            require(old_env.get(FLAG) == "YES" and old_env.get(EXTRA_FLAG, "NO") == "NO",
                    "CURRENT_V5_ACTIVE_EXTRA_SEARCH_NOT_ALREADY_ENABLED")
            require(all(old_env.get(k, "NO") == v for k,v in GATES.items()),
                    "CURRENT_APPROVED_LIVE_GATES_MISSING")
            old_health = {n: healthy(n) for n in OTHER if running(n)}
            old_digests = {n: image(n) for n in old_health}
            paths = current_paths()
            old_pin = PIN.read_text("utf8")
            require(old_pin == make_pin(old_image, active=True, extra=False),
                    "CURRENT_IMMUTABLE_PIN_DRIFT")
            before = compose_json(paths)["services"]["coordinator"]
            require(isinstance(before.get("environment"), dict)
                    and before["environment"].get(FLAG) == "YES",
                    "CURRENT_COMPOSE_FLAG_NOT_ACTIVE")
            require(all(str(v) == old_env.get(k)
                        for k,v in before["environment"].items()
                        if v is not None), "COMPOSE_RUNTIME_ENV_DRIFT")
            print("B2B_BUYER_V6_PREFLIGHT=YES", flush=True)
            # Tag exact running digest. No registry, pip, apt or network.
            baseline = "serviceit-b2b-contacts-base:" + runid
            target = "serviceit-b2b-contacts-live:" + runid
            require(call(["docker", "image", "inspect", target],
                         check=False).returncode != 0,
                    "DEPLOY_TAG_REUSE_FORBIDDEN")
            call(["docker", "tag", old_image, baseline])
            build = stage / "build"
            build.mkdir(mode=0o700, exist_ok=False)
            for name in FILES:
                (build / name).write_bytes((stage / name).read_bytes())
            dockerfile = ("FROM " + baseline + "\n" +
                          "".join("COPY --chmod=0444 " + name +
                                  " /app/leadpilot/" + name + "\n"
                                  for name in FILES))
            (build / "Dockerfile").write_text(dockerfile, encoding="utf8")
            call(["docker", "build", "--network", "none", "--pull=false",
                  "--no-cache", "-t", target, "-f", str(build / "Dockerfile"),
                  str(build)], timeout=MAX_RUN_SECONDS)
            new_image = out(["docker", "image", "inspect", "-f", "{{.Id}}", target])
            require(re.fullmatch(r"sha256:[a-f0-9]{64}", new_image) is not None
                    and new_image != old_image, "NEW_IMAGE_DIGEST_UNVERIFIED")
            smoke = """
import hashlib,sys
from pathlib import Path
names=sys.argv[1:2]
digests=sys.argv[2:]
assert len(names)==len(digests)==1
for name,expected in zip(names,digests):
    p=Path('/app/leadpilot')/name
    assert p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest()==expected
from leadpilot.serviceit_vps_b2b_deep_research import run_once
from leadpilot.serviceit_vps_b2b_deep_research import public_buyer_candidate,published_buyer_inn_evidence,cited_manager_fields,_public_company_self_declaration
from leadpilot.serviceit_b2b_routerai_deep import public_documents,cited_fields
from leadpilot.serviceit_vps_native_fence import NATIVE_NAMESPACES
from leadpilot.serviceit_vps_worker import INTERVAL_SECONDS
from leadpilot.serviceit_b2b_public_mirrors import matched_excerpt
assert callable(matched_excerpt)
assert all(callable(f) for f in (run_once, public_documents, cited_fields, public_buyer_candidate,published_buyer_inn_evidence,cited_manager_fields,_public_company_self_declaration))
assert 'B2B_RESEARCH_V4' in NATIVE_NAMESPACES and INTERVAL_SECONDS==300
print('B2B_BUYER_V6_OFFLINE_SMOKE=YES')
"""
            checked = call(["docker", "run", "--rm", "--network", "none",
                            "--read-only", "--user", "10001:10001",
                            "--pids-limit", "64", "--memory", "384m",
                            "--cap-drop", "ALL", "--entrypoint", "python",
                            new_image, "-B", "-c", smoke, *FILES, *new_shas],
                           timeout=100)
            require(checked.stdout.strip() == "B2B_BUYER_V6_OFFLINE_SMOKE=YES",
                    "B2B_BUYER_V6_IMAGE_SMOKE_FAILED")
            print("B2B_BUYER_V6_IMAGE_VERIFIED=YES", flush=True)
            # Preflight full merged Compose BEFORE altering the live pin.
            next_pin = make_pin(new_image, active=True, extra=True)
            preflight_pin = stage / "preflight.compose.yml"
            preflight_pin.write_text(next_pin, encoding="utf8")
            trial = compose_json([p for p in paths if p != PIN] + [preflight_pin])
            future = trial["services"]["coordinator"]
            same_before = {k:v for k,v in before.items()
                           if k not in ("image","pull_policy","environment")}
            same_future = {k:v for k,v in future.items()
                           if k not in ("image","pull_policy","environment")}
            require(same_before == same_future and future.get("image") == new_image,
                    "UNRELATED_COMPOSE_SETTINGS_CHANGED")
            needed = dict(before["environment"], **{FLAG:"YES", EXTRA_FLAG:"YES"})
            require(future.get("environment") == needed,
                    "ONLY_ONE_NEW_PUBLIC_BUYER_SEARCH_FLAG_REQUIRED")
            atomic_replace(PIN, next_pin)
            changed = True
            call(compose(paths, "up", "-d", "--no-deps", "--no-build",
                         "--pull", "never", "--force-recreate", "coordinator"),
                 timeout=180)
            wait_healthy(new_image, files)
            require(all(image(name) == digest and
                        (not old_health[name] or healthy(name))
                        for name, digest in old_digests.items()),
                    "OTHER_CONTAINERS_CHANGED")
            print("B2B_BUYER_V6_VPS_LIVE_ENABLED=YES", flush=True)
            print("B2B_BUYER_V6_RESEARCH_V4=READY_FOR_FIVE_MINUTE_WORKER", flush=True)
            print("B2B_BUYER_V6_CRM_WRITES_BY_ACTIVATOR=NO", flush=True)
            print("B2B_BUYER_V6_ROLLBACK=NOT_NEEDED", flush=True)
            return 0
        except Exception as exc:
            code = str(exc) if isinstance(exc, DeploymentBlocked) else "UNEXPECTED_EXCEPTION"
            if re.fullmatch(r"[A-Z0-9_]{3,100}", code) is None:
                code = "UNEXPECTED_EXCEPTION"
            print("B2B_BUYER_V6_BLOCKED_" + code + "=YES", flush=True)
            if changed and old_pin and paths and old_image:
                try:
                    atomic_replace(PIN, old_pin)
                    call(compose(paths, "up", "-d", "--no-deps",
                                 "--no-build", "--pull", "never",
                                 "--force-recreate", "coordinator"), timeout=180)
                    require(healthy(CORE) and image(CORE)==old_image,
                            "ROLLBACK_CORE_NOT_HEALTHY")
                    print("B2B_BUYER_V6_ROLLBACK=YES", flush=True)
                except Exception:
                    print("B2B_BUYER_V6_ROLLBACK=FAILED", flush=True)
            else:
                print("B2B_BUYER_V6_NO_LIVE_CHANGE=YES", flush=True)
            return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("B2B_BUYER_V6_ACTIVATOR_OUTER_BLOCKED=YES", flush=True)
        raise SystemExit(2)
