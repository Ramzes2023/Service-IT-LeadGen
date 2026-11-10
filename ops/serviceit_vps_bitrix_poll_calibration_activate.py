#!/usr/bin/env python3
"""Guarded two-module Bitrix poll calibration VPS release; no CRM writes by installer.

Owner-only workflow stages 2 Git-SHA-attested polling modules. Existing live
coordinator image and Compose pin are treated as immutable; old source SHA
must match actual independent VPS audit. Builds offline; replaces coordinator
only, retains other containers/images; restores old pin/image on failure.
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
ROOT = HOME / "serviceit"
CONFIG = ROOT / "config"
ENV = CONFIG / "vps.env"
PIN = CONFIG / "native-five-minute-core-image.compose.yml"
LOCK = CONFIG / ".serviceit-five-minute-activate.lock"
CORE = "serviceit-leadgen-vps-coordinator-1"
NEIGHBORS = (
    "serviceit-leadgen-vps-auxiliaries-1",
    "serviceit-leadgen-vps-business-1",
    "serviceit-leadgen-vps-observer-1",
    "serviceit-leadgen-vps-fns-msp-1",
    "serviceit-b2b-vps-receiver-1",
    "serviceit-b2b-vps-writer-1",
)
FILES = (
    "serviceit_bitrix_poll_fallback.py",
    "serviceit_bitrix_batch_worker.py",
)
# Independently audited on LIVE coordinator, owner-only readback
# GitHub Actions #42908064097, 2026-11-10. Refuse any source drift.
AUDITED_OLD_SHAS = (
    "ce7eb588686f28fea0dbc78f3f0c5a5a39e78cbfb6f6b392854e3de86ca950a3",
    "3317d7e946a8989e8314b97cb23b8f24f287ef5c46aa1b50d76aee16444c3df4",
)

GATES = {
    "SERVICEIT_VPS_WRITER_MODE": "LIVE",
    "SERVICEIT_VPS_CUTOVER_AUTHORIZED": "YES",
    "SERVICEIT_VPS_OLD_GH_WRITERS_DISABLED_ACK": "YES",
    "SERVICEIT_VPS_POLL_ENABLED": "YES",
    "SERVICEIT_VPS_INTAKE_ENABLED": "NO",
    "SERVICEIT_AI_PROVIDER": "routerai",
    "SERVICEIT_ROUTERAI_PRIVACY_APPROVED": "YES_1062_21",
    "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED": "YES",
    "SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED": "YES",
    "SERVICEIT_VPS_BROWSER_RESEARCH_ENABLED": "YES",
    "SERVICEIT_VPS_BROWSER_PROXY_ISOLATION_VERIFIED": "YES",
}
SHA_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")


class ReleaseBlocked(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise ReleaseBlocked(code)


def call(argv, timeout=100, check=True):
    try:
        p = subprocess.run(argv, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=timeout,
                           check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ReleaseBlocked("COMMAND_TIMEOUT_OR_MISSING") from None
    if check and p.returncode != 0:
        raise ReleaseBlocked("COMMAND_FAILED")
    return p


def output(argv, timeout=100):
    return call(argv, timeout=timeout).stdout.strip()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def image(name):
    value = output(["docker", "inspect", "-f", "{{.Image}}", name])
    require(bool(SHA_RE.fullmatch(value)), "DOCKER_IMAGE_ID_INVALID")
    return value


def healthy(name):
    p = call(["docker", "inspect", "-f",
              "{{.State.Running}}:{{.State.Health.Status}}", name],
             check=False)
    return p.returncode == 0 and p.stdout.strip() == "true:healthy"


def present_image(name):
    return call(["docker", "inspect", "-f", "{{.Image}}", name],
                check=False).returncode == 0


def env_of(name):
    raw = output(["docker", "inspect", "-f",
                  "{{json .Config.Env}}", name])
    values = json.loads(raw)
    require(type(values) is list and all(type(x) is str for x in values),
            "LIVE_CONTAINER_ENV_INVALID")
    return dict(x.split("=", 1) for x in values if "=" in x)


def runtime_shas():
    code = """
import hashlib,json
from pathlib import Path
files=json.loads(__import__('sys').argv[1])
out={}
for name in files:
    p=Path('/app/leadpilot')/name
    out[name]=(hashlib.sha256(p.read_bytes()).hexdigest()
               if p.is_file() and not p.is_symlink() else 'MISSING')
print(json.dumps(out,sort_keys=True))
"""
    raw = output(["docker", "exec", CORE, "python", "-B",
                  "-c", code, json.dumps(FILES)])
    result = json.loads(raw)
    require(type(result) is dict and set(result) == set(FILES),
            "LIVE_POLL_MODULE_SHA_READ_FAILED")
    return tuple(result[x] for x in FILES)


def inspect_compose_paths():
    raw = output(["docker", "inspect", "-f",
                  '{{index .Config.Labels "com.docker.compose.project.config_files"}}',
                  CORE])
    paths = [Path(x.strip()) for x in raw.split(",") if x.strip()]
    require(2 <= len(paths) <= 12 and PIN in paths
            and len(paths) == len(set(paths)), "COMPOSE_PIN_NOT_FOUND")
    require(all(p.is_file() and not p.is_symlink()
                and p.resolve().is_relative_to(ROOT.resolve()) for p in paths),
            "COMPOSE_UNTRUSTED_PATH")
    require(output(["docker", "inspect", "-f",
                    '{{index .Config.Labels "com.docker.compose.project"}}', CORE])
            == "serviceit-leadgen-vps", "COMPOSE_PROJECT_CHANGED")
    return paths


def compose(paths, *args):
    cmd = ["docker", "compose", "--env-file", str(ENV)]
    for path in paths:
        cmd += ["-f", str(path)]
    return cmd + list(args)


def compose_json(paths):
    return json.loads(output(compose(paths, "config", "--format", "json")))


def pin_image(content, expected_old, target):
    require(isinstance(content, str)
            and 'services:\n  coordinator:\n' in content
            and '    pull_policy: never\n' in content,
            "IMMUTABLE_PIN_FORMAT_CHANGED")
    pattern = r'(?m)^    image: "' + re.escape(expected_old) + r'"$'
    result, count = re.subn(pattern, '    image: "' + target + '"', content)
    require(count == 1 and result != content,
            "IMMUTABLE_PIN_IMAGE_CONFLICT")
    return result


def atomic_write(path, data):
    fd, tmp = tempfile.mkstemp(prefix=".pollcal-pinned-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf8") as stream:
            os.fchmod(fd, 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def pending_source_writes():
    code = """
import os
from leadpilot.serviceit_vps_pg_queue import db
names=('AI_NINE_V1','B2B_RESEARCH_V4','B2B_PUBLIC_INN_V1',
       'B2B_PUBLIC_INN_V2','FNS_CRM_V1','TASK_V1')
with db(dict(os.environ)) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM serviceit_vps_native_receipts "
                    "WHERE namespace=ANY(%s) AND status IN "
                    "('RESERVED','WRITE_INTENT')",(list(names),))
        print(int(cur.fetchone()[0]))
"""
    value = output(["docker", "exec", CORE, "python", "-B", "-c", code])
    require(value.isdigit(), "INFLIGHT_RESULT_UNVERIFIED")
    count = int(value)
    # Also fence the currently installed V10 one-use dossier writer.
    # Its own table is separate from the original native receipt table.
    v10 = """
import os
from leadpilot.serviceit_vps_pg_queue import db
with db(dict(os.environ)) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM serviceit_vps_b2b_dossier_v10_receipts WHERE status IN ('RESERVED','WRITE_INTENT')")
        print(int(cur.fetchone()[0]))
"""
    old_count = output(["docker","exec",CORE,"python","-B","-c",v10])
    require(old_count.isdigit(), "PREVIOUS_V10_INFLIGHT_UNVERIFIED")
    # V11 (current automatic coordinator writer) has a separate receipt table.
    # Do not interrupt its paid source investigation or CRM write intent.
    active = """
import os
from leadpilot.serviceit_vps_pg_queue import db
with db(dict(os.environ)) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM serviceit_vps_b2b_dossier_v11_receipts WHERE status IN ('RESERVED','WRITE_INTENT')")
        print(int(cur.fetchone()[0]))
"""
    pending = output(["docker","exec",CORE,"python","-B","-c",active])
    require(pending.isdigit(), "CURRENT_V11_INFLIGHT_UNVERIFIED")
    return count + int(old_count) + int(pending)


def wait_healthy(expected_image, expected_hashes):
    for _ in range(32):
        if healthy(CORE):
            break
        time.sleep(3)
    require(healthy(CORE), "CORE_NOT_HEALTHY_AFTER_UPGRADE")
    require(image(CORE) == expected_image, "CORE_IMAGE_MISMATCH")
    require(runtime_shas() == expected_hashes,
            "CORE_POLL_SOURCE_SHA_MISMATCH")
    actual = env_of(CORE)
    require(all(actual.get(k) == v for k, v in GATES.items()),
            "CORE_LIVE_GATES_CHANGED")


def run(run_id, new_hashes):
    require(os.geteuid() != 0, "NONROOT_HOST_REQUIRED")
    require(re.fullmatch(r"\d{7,16}", run_id) is not None
            and type(new_hashes) is tuple and len(new_hashes) == len(FILES)
            and all(re.fullmatch(r"[0-9a-f]{64}", x) for x in new_hashes),
            "SOURCE_SHA_INPUT_INVALID")
    stage = ROOT / "staging" / ("b2b-pollcal-release-" + run_id)
    require(stage.is_dir() and not stage.is_symlink()
            and stage.stat().st_uid == os.geteuid(), "STAGE_UNTRUSTED")
    for name, expected in zip(FILES, new_hashes):
        path = stage / name
        require(path.is_file() and not path.is_symlink()
                and path.stat().st_uid == os.geteuid()
                and 0 < path.stat().st_size <= 190000
                and sha(path) == expected, "STAGED_SOURCE_SHA_UNTRUSTED")
    for path in (PIN, ENV):
        require(path.is_file() and not path.is_symlink()
                and path.stat().st_mode & 0o077 == 0,
                "COMPOSE_PRIVATE_FILE_REQUIRED")
    require(LOCK.parent.is_dir() and not LOCK.is_symlink(),
            "HOST_ACTIVATION_LOCK_INVALID")
    with LOCK.open("a") as handle:
        os.chmod(LOCK, 0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        altered = False
        old_pin = None
        old_image = None
        paths = []
        neighbor_images = {}
        neighbor_healthy = {}
        try:
            require(healthy(CORE), "EXISTING_CORE_NOT_HEALTHY")
            require(runtime_shas() == AUDITED_OLD_SHAS,
                    "EXISTING_CORE_SHA_DRIFT_NO_DEPLOY")
            require(pending_source_writes() == 0,
                    "PENDING_AI_OR_CRM_WRITE_NO_RESTART")
            require(output(["docker", "exec", CORE, "python", "-B", "-c",
                "from leadpilot.serviceit_vps_worker import INTERVAL_SECONDS;print(INTERVAL_SECONDS)"])
                == "300", "FIVE_MINUTE_CADENCE_REQUIRED")
            old_image = image(CORE)
            actual_env = env_of(CORE)
            require(all(actual_env.get(k) == v for k, v in GATES.items()),
                    "POLL_PRIVACY_OR_RESEARCH_GATES_MISSING")
            paths = inspect_compose_paths()
            old_pin = PIN.read_text("utf8")
            before = compose_json(paths)
            core_before = before["services"]["coordinator"]
            require(core_before.get("image") == old_image,
                    "COMPOSE_OLD_IMAGE_DRIFT")
            for name in NEIGHBORS:
                if present_image(name):
                    neighbor_images[name] = image(name)
                    neighbor_healthy[name] = healthy(name)
            print("POLL_RELEASE_PREFLIGHT=YES", flush=True)
            baseline = "serviceit-pollcal-baseline:" + run_id
            target = "serviceit-pollcal-evidence:" + run_id
            require(call(["docker", "image", "inspect", target],
                         check=False).returncode != 0,
                    "IMAGE_TAG_ALREADY_EXISTS")
            call(["docker", "tag", old_image, baseline])
            build = stage / "build"
            build.mkdir(mode=0o700, exist_ok=False)
            dockerfile = "FROM " + baseline + "\n"
            for name in FILES:
                (build / name).write_bytes((stage / name).read_bytes())
                dockerfile += ('COPY --chmod=0444 ' + name
                               + ' /app/leadpilot/' + name + '\n')
            (build / "Dockerfile").write_text(dockerfile, "utf8")
            call(["docker", "build", "--network", "none", "--pull=false",
                  "--no-cache", "-t", target, "-f", str(build / "Dockerfile"),
                  str(build)], timeout=250)
            next_image = output(["docker", "image", "inspect", "-f",
                                 "{{.Id}}", target])
            require(bool(SHA_RE.fullmatch(next_image))
                    and next_image != old_image, "NEW_POLL_IMAGE_INVALID")
            smoke = """
import hashlib,sys
from pathlib import Path
files=sys.argv[1:3]; shas=sys.argv[3:]
assert len(files)==len(shas)==2
for name,value in zip(files,shas):
    assert hashlib.sha256((Path('/app/leadpilot')/name).read_bytes()).hexdigest()==value
from leadpilot.serviceit_vps_worker import INTERVAL_SECONDS
from leadpilot.serviceit_bitrix_poll_fallback import calibrate_known_card
from leadpilot.serviceit_bitrix_batch_worker import list_only
assert INTERVAL_SECONDS==300
control={"id":22,"categoryId":21,"createdTime":"2026-11-10T18:00:00+00:00"}
def test_readonly_rest(method,args):
    assert method=="crm.item.list"
    filt=args.get("filter")
    if filt=={"id":3}:
        return {"items":[],"total":0}
    if filt=={"categoryId":21}:
        return {"items":[control],"total":1}
    if filt=={"id":22}:
        return {"items":[control],"total":1}
    raise AssertionError("UNEXPECTED_LIST_SCOPE")
calibrate_known_card(test_readonly_rest)
assert callable(list_only)
print('POLL_OFFLINE_SMOKE_PASS')
"""
            res = call(["docker", "run", "--rm", "--network", "none",
                        "--pull", "never", "--read-only", "--user", "10001:10001",
                        "--cap-drop", "ALL", "--pids-limit", "64", "--memory", "512m",
                        "--entrypoint", "python", next_image, "-B", "-c", smoke,
                        *FILES, *new_hashes], timeout=110)
            require(res.stdout.strip() == "POLL_OFFLINE_SMOKE_PASS",
                    "POLL_OFFLINE_SMOKE_FAILED")
            print("POLL_RELEASE_OFFLINE_SMOKE=YES", flush=True)
            next_pin = pin_image(old_pin, old_image, next_image)
            preflight = stage / "preflight.compose.yml"
            preflight.write_text(next_pin, "utf8")
            trial = compose_json([p for p in paths if p != PIN] + [preflight])
            next_core = trial["services"]["coordinator"]
            require(next_core.get("image") == next_image
                    and {k:v for k,v in next_core.items() if k != "image"}
                    == {k:v for k,v in core_before.items() if k != "image"},
                    "POLL_COMPOSE_NON_IMAGE_DRIFT")
            require(all(trial["services"][name] == value
                        for name, value in before["services"].items()
                        if name != "coordinator"),
                    "POLL_ADJACENT_COMPOSE_DRIFT")
            atomic_write(PIN, next_pin)
            altered = True
            call(compose(paths, "up", "-d", "--no-deps", "--no-build",
                         "--pull", "never", "--force-recreate", "coordinator"),
                 timeout=190)
            wait_healthy(next_image, new_hashes)
            require(all(image(n) == before_id and
                        (not neighbor_healthy[n] or healthy(n))
                        for n, before_id in neighbor_images.items()),
                    "POLL_ADJACENT_CONTAINER_CHANGED")
            print("POLL_RELEASE_VPS_LIVE_ENABLED=YES", flush=True)
            print("POLL_RELEASE_CRM_WRITES_BY_INSTALLER=NO", flush=True)
            print("POLL_RELEASE_ROLLBACK=NOT_NEEDED", flush=True)
            return 0
        except Exception as exc:
            code = str(exc) if isinstance(exc, ReleaseBlocked) else (
                "UNEXPECTED_FAIL_CLOSED")
            if not re.fullmatch(r"[A-Z0-9_]{4,90}", code):
                code = "UNEXPECTED_FAIL_CLOSED"
            print("POLL_RELEASE_REASON=" + code, flush=True)
            if altered and old_pin and old_image and paths:
                try:
                    atomic_write(PIN, old_pin)
                    call(compose(paths, "up", "-d", "--no-deps", "--no-build",
                                 "--pull", "never", "--force-recreate",
                                 "coordinator"), timeout=180)
                    wait_healthy(old_image, AUDITED_OLD_SHAS)
                    print("POLL_RELEASE_ROLLBACK=YES", flush=True)
                except Exception:
                    print("POLL_RELEASE_ROLLBACK=UNVERIFIED", flush=True)
            else:
                print("POLL_RELEASE_ROLLBACK=NOT_NEEDED", flush=True)
            print("POLL_RELEASE_VPS_LIVE_ENABLED=NO", flush=True)
            return 2


def main():
    if not 2 <= len(sys.argv) == len(FILES) + 2:
        print("POLL_RELEASE_VPS_LIVE_ENABLED=NO", flush=True)
        return 2
    try:
        return run(sys.argv[1], tuple(sys.argv[2:]))
    except Exception:
        print("POLL_RELEASE_VPS_LIVE_ENABLED=NO", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
