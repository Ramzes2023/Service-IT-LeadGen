#!/usr/bin/env python3
"""V44 code-only guarded source discovery rollout; no CRM/provider calls during installer."""
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
FILES = ('serviceit_vps_b2b_deep_research.py',
         'serviceit_b2b_v19_backfill.py',
         'serviceit_b2b_v20_versioned.py',
         'serviceit_b2b_tavily_fallback_v44.py')
# Exact pre-V21 old module hashes are independently derived from reviewed git
# baseline by the owner-only workflow and checked against the LIVE container.
AUDITED_OLD_SHAS = ()

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
    "SERVICEIT_DEEP_COMPANY_SEARCH_ENABLED": "YES",
    "SERVICEIT_PUBLIC_COMPANY_REQUISITES_EXTRA_SEARCH_ENABLED": "YES",
    "SERVICEIT_VPS_TENDER_DOC_INTEL_ENABLED": "YES",
    "SERVICEIT_VPS_BROWSER_RESEARCH_ENABLED": "YES",
    "SERVICEIT_VPS_BROWSER_PROXY_ISOLATION_VERIFIED": "YES",
    "SERVICEIT_VPS_B2B_V20_VERSIONED_ENABLED": "YES",
    "SERVICEIT_TINYFISH_API_KEY_FILE": "/run/secrets/serviceit_tinyfish_key",
    "SERVICEIT_VPS_B2B_FREE_PUBLIC_FETCH_ENABLED": "YES",
    "SERVICEIT_B2B_TENDEM_PUBLIC_EXTRACTION_APPROVED": "YES",
    "SERVICEIT_B2B_CENTER_PUBLIC_EXTRACTION_LICENSED": "NO",
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
            "LIVE_MIRROR_MODULE_SHA_READ_FAILED")
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
    fd, tmp = tempfile.mkstemp(prefix=".crossportal-pinned-", dir=str(path.parent))
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
    # TDI may be activated by a DIFFERENT project chat. Never restart
    # the coordinator during its source download, model intent or CRM note.
    tdi = """
import os
from leadpilot.serviceit_vps_pg_queue import db
with db(dict(os.environ)) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.serviceit_vps_b2b_tender_doc_receipts')")
        if cur.fetchone()[0] is None:
            print(0)
        else:
            cur.execute("SELECT count(*) FROM serviceit_vps_b2b_tender_doc_receipts "
                        "WHERE status IN ('RESERVED','AI_INTENT','WRITE_INTENT')")
            print(int(cur.fetchone()[0]))
"""
    tdi_count = output(["docker","exec",CORE,"python","-B","-c",tdi])
    require(tdi_count.isdigit(), "TDI_INFLIGHT_UNVERIFIED")
    # V19 and V20 have separate write-intent receipts. Never rebuild while
    # their exact Bitrix readback is uncertain or an update is in flight.
    active = """
import os
from leadpilot.serviceit_vps_pg_queue import db
with db(dict(os.environ)) as conn:
    with conn.cursor() as cur:
        pending=0
        for table in ('serviceit_vps_b2b_v19_research_receipts',
                      'serviceit_vps_b2b_v20_revision_receipts'):
            cur.execute("SELECT to_regclass(%s)",('public.'+table,))
            if cur.fetchone()[0] is None: continue
            cur.execute('SELECT count(*) FROM '+table+
                        " WHERE status IN ('RESERVED','WRITE_INTENT')")
            pending+=int(cur.fetchone()[0])
        print(pending)
"""
    new_count=output(["docker","exec",CORE,"python","-B","-c",active])
    require(new_count.isdigit(),"V19_V20_PENDING_UNVERIFIED")
    return count + int(old_count) + int(pending) + int(tdi_count) + int(new_count)


def wait_healthy(expected_image, expected_hashes):
    for _ in range(32):
        if healthy(CORE):
            break
        time.sleep(3)
    require(healthy(CORE), "CORE_NOT_HEALTHY_AFTER_UPGRADE")
    require(image(CORE) == expected_image, "CORE_IMAGE_MISMATCH")
    require(runtime_shas() == expected_hashes,
            "CORE_MIRROR_SOURCE_SHA_MISMATCH")
    actual = env_of(CORE)
    require(all(actual.get(k) == v for k, v in GATES.items()),
            "CORE_LIVE_GATES_CHANGED")


def run(run_id, new_hashes, old_hashes):
    global AUDITED_OLD_SHAS
    require(os.geteuid() != 0, "NONROOT_HOST_REQUIRED")
    require(re.fullmatch(r"\d{7,16}", run_id) is not None
            and type(new_hashes) is tuple and len(new_hashes) == len(FILES)
            and all(re.fullmatch(r"[0-9a-f]{64}", x) for x in new_hashes)
            and type(old_hashes) is tuple and len(old_hashes) == len(FILES)
            and all(re.fullmatch(r"[0-9a-f]{64}", h) for h in old_hashes[:3])
            and old_hashes[3] == "MISSING",
            "SOURCE_SHA_INPUT_INVALID")
    AUDITED_OLD_SHAS = old_hashes
    stage = ROOT / "staging" / ("b2b-v44-onefile-release-" + run_id)
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
                    "MIRROR_PRIVACY_OR_RESEARCH_GATES_MISSING")
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
            print("V44_RELEASE_PREFLIGHT=YES", flush=True)
            baseline = "serviceit-v44-baseline:" + run_id
            target = "serviceit-v44-evidence:" + run_id
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
                    and next_image != old_image, "NEW_MIRROR_IMAGE_INVALID")
            smoke = """
import hashlib,sys
from pathlib import Path
names=sys.argv[1:5];hashes=sys.argv[5:]
assert len(names)==len(hashes)==4
for name,digest in zip(names,hashes):
 assert hashlib.sha256((Path('/app/leadpilot')/name).read_bytes()).hexdigest()==digest
from leadpilot.serviceit_vps_worker import INTERVAL_SECONDS,exact_environment
from leadpilot.serviceit_b2b_v19_backfill import run_once as v19
from leadpilot.serviceit_b2b_v20_versioned import run_once as v20
from leadpilot.serviceit_vps_b2b_deep_research import source_findings
from leadpilot.serviceit_b2b_tavily_fallback_v44 import MAX_MONTHLY_REQUESTS,permitted
assert INTERVAL_SECONDS==300
assert MAX_MONTHLY_REQUESTS==300
assert callable(source_findings) and callable(v19) and callable(v20)
assert not permitted({})
assert exact_environment
print('V44_OFFLINE_SMOKE_PASS')
"""
            res = call(["docker", "run", "--rm", "--network", "none",
                        "--pull", "never", "--read-only", "--user", "10001:10001",
                        "--cap-drop", "ALL", "--pids-limit", "64", "--memory", "512m",
                        "--entrypoint", "python", next_image, "-B", "-c", smoke,
                        *FILES, *new_hashes], timeout=110)
            require(res.stdout.strip() == "V44_OFFLINE_SMOKE_PASS",
                    "MIRROR_OFFLINE_SMOKE_FAILED")
            print("V44_RELEASE_OFFLINE_SMOKE=YES", flush=True)
            next_pin = pin_image(old_pin, old_image, next_image)
            preflight = stage / "preflight.compose.yml"
            preflight.write_text(next_pin, "utf8")
            trial = compose_json([p for p in paths if p != PIN] + [preflight])
            next_core = trial["services"]["coordinator"]
            require(next_core.get("image") == next_image
                    and {k:v for k,v in next_core.items() if k != "image"}
                    == {k:v for k,v in core_before.items() if k != "image"},
                    "MIRROR_COMPOSE_NON_IMAGE_DRIFT")
            require(all(trial["services"][name] == value
                        for name, value in before["services"].items()
                        if name != "coordinator"),
                    "MIRROR_ADJACENT_COMPOSE_DRIFT")
            atomic_write(PIN, next_pin)
            altered = True
            call(compose(paths, "up", "-d", "--no-deps", "--no-build",
                         "--pull", "never", "--force-recreate", "coordinator"),
                 timeout=190)
            wait_healthy(next_image, new_hashes)
            require(all(image(n) == before_id and
                        (not neighbor_healthy[n] or healthy(n))
                        for n, before_id in neighbor_images.items()),
                    "MIRROR_ADJACENT_CONTAINER_CHANGED")
            print("V44_RELEASE_VPS_LIVE_ENABLED=YES", flush=True)
            print("V44_RELEASE_CRM_WRITES_BY_INSTALLER=NO", flush=True)
            print("V44_RELEASE_ROLLBACK=NOT_NEEDED", flush=True)
            return 0
        except Exception as exc:
            code = str(exc) if isinstance(exc, ReleaseBlocked) else (
                "UNEXPECTED_FAIL_CLOSED")
            if not re.fullmatch(r"[A-Z0-9_]{4,90}", code):
                code = "UNEXPECTED_FAIL_CLOSED"
            print("V44_RELEASE_REASON=" + code, flush=True)
            if altered and old_pin and old_image and paths:
                try:
                    atomic_write(PIN, old_pin)
                    call(compose(paths, "up", "-d", "--no-deps", "--no-build",
                                 "--pull", "never", "--force-recreate",
                                 "coordinator"), timeout=180)
                    wait_healthy(old_image, AUDITED_OLD_SHAS)
                    print("V44_RELEASE_ROLLBACK=YES", flush=True)
                except Exception:
                    print("V44_RELEASE_ROLLBACK=UNVERIFIED", flush=True)
            else:
                print("V44_RELEASE_ROLLBACK=NOT_NEEDED", flush=True)
            print("V44_RELEASE_VPS_LIVE_ENABLED=NO", flush=True)
            return 2


def main():
    if len(sys.argv) != 2 + 2*len(FILES):
        print("V44_RELEASE_VPS_LIVE_ENABLED=NO", flush=True)
        return 2
    try:
        n=len(FILES)
        return run(sys.argv[1],tuple(sys.argv[2:2+n]),
                   tuple(sys.argv[2+n:]))
    except Exception:
        print("V44_RELEASE_VPS_LIVE_ENABLED=NO", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
