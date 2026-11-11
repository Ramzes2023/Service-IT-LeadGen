#!/usr/bin/env python3
"""TinyFish V21+V22 free Fetch guarded code + fifth secret release; NO installer CRM writes.

Owner-only workflow stages 4 SHA-attested reviewed source-only modules. Existing live
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
FILES = ("serviceit_b2b_free_public_fallback.py",
         "serviceit_b2b_remote_public_fetch.py",
         "serviceit_b2b_public_fact_extractor.py",
         "serviceit_vps_b2b_deep_research.py")
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
}
TINYFISH_FLAGS = {
    "SERVICEIT_TINYFISH_API_KEY_FILE": "/run/secrets/serviceit_tinyfish_key",
    "SERVICEIT_VPS_B2B_FREE_PUBLIC_FETCH_ENABLED": "NO",
    "SERVICEIT_B2B_TENDEM_PUBLIC_EXTRACTION_APPROVED": "NO",
    "SERVICEIT_B2B_CENTER_PUBLIC_EXTRACTION_LICENSED": "NO",
}
TINYFISH_SECRET_SOURCE = "/etc/serviceit/secrets/tinyfish_api_key"
TINYFISH_SECRET_TARGET = "/run/secrets/serviceit_tinyfish_key"
OVERLAY = CONFIG / "serviceit-vps-tinyfish-free.compose.yml"

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
                      'serviceit_vps_b2b_v20_revision_receipts',
                      'serviceit_vps_b2b_v23_primary_source_receipts'):
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


def wait_healthy(expected_image, expected_hashes, *, new_secret=False):
    for _ in range(32):
        if healthy(CORE):
            break
        time.sleep(3)
    require(healthy(CORE), "CORE_NOT_HEALTHY_AFTER_UPGRADE")
    require(image(CORE) == expected_image, "CORE_IMAGE_MISMATCH")
    require(runtime_shas() == expected_hashes,
            "CORE_SOURCE_SHA_MISMATCH")
    actual = env_of(CORE)
    require(all(actual.get(k) == v for k, v in GATES.items()),
            "CORE_LIVE_GATES_CHANGED")
    if new_secret:
        require(all(actual.get(k) == v for k, v in TINYFISH_FLAGS.items()),
                "TINYFISH_DEFAULT_OFF_FLAGS_CHANGED")
        mounts = json.loads(output(["docker", "inspect", "-f",
                                    "{{json .Mounts}}", CORE]))
        matches = [m for m in mounts if m.get("Destination") == TINYFISH_SECRET_TARGET]
        require(len(matches) == 1 and matches[0].get("Source") == TINYFISH_SECRET_SOURCE
                and matches[0].get("RW") is False,
                "TINYFISH_SECRET_MOUNT_INVALID")
        proof = """from pathlib import Path
p=Path('/run/secrets/serviceit_tinyfish_key')
assert p.is_file() and 16 <= p.stat().st_size <= 256
print('SECRET_ACCESS_OK')"""
        require(output(["docker", "exec", CORE, "python", "-B", "-c", proof])
                == "SECRET_ACCESS_OK", "TINYFISH_SECRET_ACCESS_FAILED")


def verify_compose_only_changes(before, after, old_image, next_image):
    import copy
    b, a = copy.deepcopy(before), copy.deepcopy(after)
    require(a.get("name") == b.get("name")
            and set(a.get("services", {})) == set(b.get("services", {})),
            "COMPOSE_PROJECT_OR_SERVICE_DRIFT")
    ac, bc = a["services"]["coordinator"], b["services"]["coordinator"]
    require(ac.get("image") == next_image and bc.get("image") == old_image,
            "COORDINATOR_IMAGE_PIN_DRIFT")
    ac["image"] = old_image
    ae, be = ac.get("environment"), bc.get("environment")
    require(isinstance(ae, dict) and isinstance(be, dict)
            and all(k not in be and ae.get(k) == v
                    for k, v in TINYFISH_FLAGS.items()),
            "EXTRA_ENV_FLAGS_NOT_OFF")
    for k in TINYFISH_FLAGS:
        ae.pop(k)
    require(ae == be, "EXISTING_ENV_CHANGED")
    as_, bs_ = ac.get("secrets"), bc.get("secrets")
    require(isinstance(as_, list) and isinstance(bs_, list),
            "SECRETS_FORMAT_INVALID")
    new = [m for m in as_ if m.get("source") == "serviceit_tinyfish_key"]
    require(len(new) == 1 and new[0].get("target") in
            ("serviceit_tinyfish_key", TINYFISH_SECRET_TARGET),
            "NEW_SECRET_INVALID")
    ac["secrets"] = [m for m in as_ if m.get("source") != "serviceit_tinyfish_key"]
    require(ac["secrets"] == bs_, "OLD_SECRETS_CHANGED")
    require(a["services"] == b["services"], "ADJACENT_SERVICES_CHANGED")
    a_sec, b_sec = a.get("secrets"), b.get("secrets")
    require(isinstance(a_sec, dict) and isinstance(b_sec, dict)
            and set(a_sec) == set(b_sec) | {"serviceit_tinyfish_key"}
            and all(a_sec[k] == v for k, v in b_sec.items())
            and a_sec["serviceit_tinyfish_key"].get("file") ==
                TINYFISH_SECRET_SOURCE, "TOP_LEVEL_SECRETS_DRIFT")
    a["secrets"] = b_sec
    require(a == b, "GLOBAL_COMPOSE_DRIFT")


def run(run_id, new_hashes, old_hashes):
    global AUDITED_OLD_SHAS
    require(os.geteuid() != 0, "NONROOT_HOST_REQUIRED")
    require(re.fullmatch(r"\d{7,16}", run_id) is not None
            and type(new_hashes) is tuple and len(new_hashes) == len(FILES)
            and all(re.fullmatch(r"[0-9a-f]{64}", x) for x in new_hashes)
            and type(old_hashes) is tuple and len(old_hashes) == len(FILES)
            and all(x == "MISSING" or re.fullmatch(r"[0-9a-f]{64}", x) for x in old_hashes),
            "SOURCE_SHA_INPUT_INVALID")
    stage = ROOT / "staging" / ("tinyfish-v21-fourfile-release-" + run_id)
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
            baseline = runtime_shas()
            require(all(actual in (old, new) for actual, old, new
                        in zip(baseline, old_hashes, new_hashes)),
                    "EXISTING_CORE_SOURCE_SHA_UNRECOGNIZED")
            AUDITED_OLD_SHAS = baseline
            require(pending_source_writes() == 0,
                    "PENDING_AI_OR_CRM_WRITE_NO_RESTART")
            require(output(["docker", "exec", CORE, "python", "-B", "-c",
                "from leadpilot.serviceit_vps_worker import INTERVAL_SECONDS;print(INTERVAL_SECONDS)"])
                == "300", "FIVE_MINUTE_CADENCE_REQUIRED")
            old_image = image(CORE)
            actual_env = env_of(CORE)
            require(all(actual_env.get(k) == v for k, v in GATES.items()),
                    "TINYFISH_PRIVACY_OR_RESEARCH_GATES_MISSING")
            paths = inspect_compose_paths()
            old_pin = PIN.read_text("utf8")
            require(len(paths) == 3 and OVERLAY not in paths,
                    "COMPOSE_ALREADY_CHANGED_RECHECK_REQUIRED")
            require(OVERLAY.is_file() and not OVERLAY.is_symlink()
                    and OVERLAY.stat().st_mode & 0o077 == 0,
                    "STAGED_TINYFISH_OVERLAY_REQUIRED")
            require(OVERLAY.read_text("utf8").count('SERVICEIT_VPS_B2B_FREE_PUBLIC_FETCH_ENABLED: "NO"') == 1,
                    "OVERLAY_MUST_BE_OFF")
            # Only Docker daemon may read the owner-0400 key; no key bytes are
            # loaded or printed by this release. Prior independent Search=200.
            test_secret = """from pathlib import Path
p=Path('/run/secrets/serviceit_tinyfish_key')
assert p.is_file() and 16 <= p.stat().st_size <= 256
print('SECRET_ACCESS_OK')"""
            secret_probe = call(["docker", "run", "--rm", "--network", "none",
                "--pull", "never", "--read-only", "--user", "10001:10001",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--mount", "type=bind,src="+TINYFISH_SECRET_SOURCE+
                           ",dst="+TINYFISH_SECRET_TARGET+",readonly",
                "--entrypoint", "python", old_image, "-B", "-c", test_secret],
                timeout=35)
            require(secret_probe.stdout.strip() == "SECRET_ACCESS_OK",
                    "TINYFISH_SECRET_NOT_MOUNTABLE")
            before = compose_json(paths)
            core_before = before["services"]["coordinator"]
            require(core_before.get("image") == old_image,
                    "COMPOSE_OLD_IMAGE_DRIFT")
            for name in NEIGHBORS:
                if present_image(name):
                    neighbor_images[name] = image(name)
                    neighbor_healthy[name] = healthy(name)
            print("TINYFISH_INSTALL_PREFLIGHT=YES", flush=True)
            baseline = "serviceit-tinyfish-base:" + run_id
            target = "serviceit-tinyfish-off:" + run_id
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
files=sys.argv[1:5]; hashes=sys.argv[5:]
assert len(files)==len(hashes)==4
for file,digest in zip(files,hashes):
    assert hashlib.sha256((Path('/app/leadpilot')/file).read_bytes()).hexdigest()==digest
from leadpilot.serviceit_b2b_free_public_fallback import read_free_public_mirror
from leadpilot.serviceit_b2b_remote_public_fetch import approved_url
from leadpilot.serviceit_b2b_public_fact_extractor import valid_inn
from leadpilot.serviceit_vps_b2b_deep_research import source_findings
from leadpilot.serviceit_vps_worker import INTERVAL_SECONDS
assert INTERVAL_SECONDS==300
assert callable(source_findings) and callable(read_free_public_mirror)
assert read_free_public_mirror({}, {}, None) is None
assert approved_url('https://evil.invalid/malicious') is False
assert callable(valid_inn)
print('TINYFISH_OFFLINE_SMOKE_PASS')
"""
            res = call(["docker", "run", "--rm", "--network", "none",
                        "--pull", "never", "--read-only", "--user", "10001:10001",
                        "--cap-drop", "ALL", "--pids-limit", "64", "--memory", "512m",
                        "--entrypoint", "python", next_image, "-B", "-c", smoke,
                        *FILES, *new_hashes], timeout=110)
            require(res.stdout.strip() == "TINYFISH_OFFLINE_SMOKE_PASS",
                    "TINYFISH_OFFLINE_SMOKE_FAILED")
            print("TINYFISH_INSTALL_OFFLINE_SMOKE=YES", flush=True)
            next_pin = pin_image(old_pin, old_image, next_image)
            preflight = stage / "preflight.compose.yml"
            preflight.write_text(next_pin, "utf8")
            trial = compose_json([p for p in paths if p != PIN] +
                                 [preflight, OVERLAY])
            verify_compose_only_changes(before, trial, old_image, next_image)
            atomic_write(PIN, next_pin)
            altered = True
            call(compose([*paths, OVERLAY], "up", "-d", "--no-deps", "--no-build",
                         "--pull", "never", "--force-recreate", "coordinator"),
                 timeout=190)
            wait_healthy(next_image, new_hashes, new_secret=True)
            require(all(image(n) == before_id and
                        (not neighbor_healthy[n] or healthy(n))
                        for n, before_id in neighbor_images.items()),
                    "TINYFISH_ADJACENT_CONTAINER_CHANGED")
            print("TINYFISH_INSTALL_VPS_LIVE_ENABLED=YES", flush=True)
            print("TINYFISH_INSTALL_KEY_MOUNT=YES", flush=True)
            print("TINYFISH_INSTALL_FETCH_ENABLED=NO", flush=True)
            print("TINYFISH_INSTALL_CRM_WRITES_BY_INSTALLER=NO", flush=True)
            print("TINYFISH_INSTALL_ROLLBACK=NOT_NEEDED", flush=True)
            return 0
        except Exception as exc:
            code = str(exc) if isinstance(exc, ReleaseBlocked) else (
                "UNEXPECTED_FAIL_CLOSED")
            if not re.fullmatch(r"[A-Z0-9_]{4,90}", code):
                code = "UNEXPECTED_FAIL_CLOSED"
            print("TINYFISH_INSTALL_REASON=" + code, flush=True)
            if altered and old_pin and old_image and paths:
                try:
                    atomic_write(PIN, old_pin)
                    call(compose(paths, "up", "-d", "--no-deps", "--no-build",
                                 "--pull", "never", "--force-recreate",
                                 "coordinator"), timeout=180)
                    wait_healthy(old_image, AUDITED_OLD_SHAS)
                    print("TINYFISH_INSTALL_ROLLBACK=YES", flush=True)
                except Exception:
                    print("TINYFISH_INSTALL_ROLLBACK=UNVERIFIED", flush=True)
            else:
                print("TINYFISH_INSTALL_ROLLBACK=NOT_NEEDED", flush=True)
            print("TINYFISH_INSTALL_VPS_LIVE_ENABLED=NO", flush=True)
            return 2


def main():
    if len(sys.argv) != 2 + 2*len(FILES):
        print("TINYFISH_INSTALL_VPS_LIVE_ENABLED=NO", flush=True)
        return 2
    try:
        n=len(FILES)
        return run(sys.argv[1],tuple(sys.argv[2:2+n]),
                   tuple(sys.argv[2+n:]))
    except Exception:
        print("TINYFISH_INSTALL_VPS_LIVE_ENABLED=NO", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
