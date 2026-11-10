#!/usr/bin/env python3
"""Guarded isolated Tender Document Intelligence VPS release; no installer CRM writes.

Owner-only workflow stages three SHA-attested source files and pinned pypdf wheel. Existing live
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
    "serviceit_vps_worker.py",
    "serviceit_b2b_tender_documents.py",
    "serviceit_vps_b2b_document_worker.py",
)
# Exact live coordinator SHAs from owner-gated READONLY #42901141789.
# Fail closed on concurrent V11/AI work drift; never silently overlay it.
AUDITED_OLD_SHAS = (
    "9ff0cd40266b23bc2bfb8da14ed1198054e8a118be94c5624255bc22a7fb8ea8",
    "MISSING",
    "MISSING",
)
WHEEL = "pypdf-6.21.4-py3-none-any.whl"
WHEEL_SHA256 = "33e4fb41ca352810eb4e9838aa0d82d6888a5f8bf682f43e99cce5faa666255c"
TDI_FLAG = "SERVICEIT_VPS_TENDER_DOC_INTEL_ENABLED"

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
            "LIVE_TDI_MODULE_SHA_READ_FAILED")
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
    """Canonical self-contained Compose override; never append to old content.

    The previous appended environment stanza failed live docker compose
    config parsing (#42908404218); the canonical minimal variant passed
    independently (#42908770020). Existing live settings are compared
    structurally before the pin is ever changed, and the original pin is
    archived for rollback.
    """
    require(isinstance(content, str)
            and 'services:\\n  coordinator:\\n' in content
            and '    pull_policy: never\\n' in content,
            "IMMUTABLE_PIN_FORMAT_CHANGED")
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", expected_old)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", target)
            and expected_old != target,
            "IMMUTABLE_PIN_IMAGE_INVALID")
    pattern = r'(?m)^    image: "' + re.escape(expected_old) + r'"$'
    _, count = re.subn(pattern, '', content)
    require(count == 1, "IMMUTABLE_PIN_IMAGE_CONFLICT")
    require(TDI_FLAG not in content, "TDI_FLAG_ALREADY_IN_PIN")
    # Preserve old Compose runtime semantics via the separate model-diff
    # preflight, not by concatenating YAML to an unknown trailing section.
    return (
        'services:\\n'
        '  coordinator:\\n'
        '    image: "' + target + '"\\n'
        '    pull_policy: never\\n'
        '    environment:\\n'
        '      ' + TDI_FLAG + ': "YES"\\n'
    )


def atomic_write(path, data):
    fd, tmp = tempfile.mkstemp(prefix=".tdi-pinned-", dir=str(path.parent))
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
    v11 = """
import os
from leadpilot.serviceit_vps_pg_queue import db
with db(dict(os.environ)) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM serviceit_vps_b2b_dossier_v11_receipts WHERE status IN ('RESERVED','WRITE_INTENT')")
        print(int(cur.fetchone()[0]))
"""
    v11_count = output(["docker","exec",CORE,"python","-B","-c",v11])
    require(v11_count.isdigit(), "TDI_V11_INFLIGHT_UNVERIFIED")
    return count + int(old_count) + int(v11_count)


def wait_healthy(expected_image, expected_hashes, *, tdi_expected=True):
    for _ in range(32):
        if healthy(CORE):
            break
        time.sleep(3)
    require(healthy(CORE), "CORE_NOT_HEALTHY_AFTER_UPGRADE")
    require(image(CORE) == expected_image, "CORE_IMAGE_MISMATCH")
    require(runtime_shas() == expected_hashes,
            "CORE_TDI_SOURCE_SHA_MISMATCH")
    actual = env_of(CORE)
    require(all(actual.get(k) == v for k, v in GATES.items()),
            "CORE_LIVE_GATES_CHANGED")
    # Bitrix comment writer must run ONLY in explicitly approved new pin.
    if tdi_expected:
        require(actual.get(TDI_FLAG) == "YES",
                "TDI_OPT_IN_NOT_VISIBLE_IN_CONTAINER")
    else:
        require(TDI_FLAG not in actual, "TDI_ROLLBACK_FLAG_STILL_PRESENT")


def run(run_id, new_hashes):
    require(os.geteuid() != 0, "NONROOT_HOST_REQUIRED")
    require(re.fullmatch(r"\d{7,16}", run_id) is not None
            and type(new_hashes) is tuple and len(new_hashes) == len(FILES)
            and all(re.fullmatch(r"[0-9a-f]{64}", x) for x in new_hashes),
            "SOURCE_SHA_INPUT_INVALID")
    stage = ROOT / "staging" / ("b2b-tdi-release-" + run_id)
    require(stage.is_dir() and not stage.is_symlink()
            and stage.stat().st_uid == os.geteuid(), "STAGE_UNTRUSTED")
    for name, expected in zip(FILES, new_hashes):
        path = stage / name
        require(path.is_file() and not path.is_symlink()
                and path.stat().st_uid == os.geteuid()
                and 0 < path.stat().st_size <= 190000
                and sha(path) == expected, "STAGED_SOURCE_SHA_UNTRUSTED")
    wheel = stage / WHEEL
    require(wheel.is_file() and not wheel.is_symlink()
            and wheel.stat().st_uid == os.geteuid()
            and 10000 <= wheel.stat().st_size <= 2500000
            and sha(wheel) == WHEEL_SHA256,
            "TDI_WHEEL_PIN_CHECK_FAILED")
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
                    "TDI_PRIVACY_OR_RESEARCH_GATES_MISSING")
            paths = inspect_compose_paths()
            old_pin = PIN.read_text("utf8")
            require(TDI_FLAG not in old_pin,
                    "TDI_ALREADY_INSTALLED_NO_UNSAFE_REPLAY")
            before = compose_json(paths)
            core_before = before["services"]["coordinator"]
            require(TDI_FLAG not in core_before.get("environment", {}),
                    "TDI_ALREADY_PRESENT_IN_ENV")
            require(core_before.get("image") == old_image,
                    "COMPOSE_OLD_IMAGE_DRIFT")
            for name in NEIGHBORS:
                if present_image(name):
                    neighbor_images[name] = image(name)
                    neighbor_healthy[name] = healthy(name)
            print("TDI_RELEASE_PREFLIGHT=YES", flush=True)
            baseline = "serviceit-tdi-baseline:" + run_id
            target = "serviceit-tdi-evidence:" + run_id
            require(call(["docker", "image", "inspect", target],
                         check=False).returncode != 0,
                    "IMAGE_TAG_ALREADY_EXISTS")
            tagged = call(["docker", "tag", old_image, baseline],
                          check=False)
            require(tagged.returncode == 0, "TDI_TAG_BASELINE_FAILED")
            build = stage / "build"
            build.mkdir(mode=0o700, exist_ok=False)
            dockerfile = "FROM " + baseline + "\n"
            for name in FILES:
                (build / name).write_bytes((stage / name).read_bytes())
                dockerfile += ('COPY --chmod=0444 ' + name
                               + ' /app/leadpilot/' + name + '\n')
            (build / WHEEL).write_bytes(wheel.read_bytes())
            dockerfile += (
                'USER root\n'
                'COPY --chmod=0444 ' + WHEEL + ' /tmp/' + WHEEL + '\n'
                'RUN python -m pip install --no-index --no-deps /tmp/' + WHEEL +
                ' && rm /tmp/' + WHEEL + '\n'
                'USER 10001:10001\n'
            )
            (build / "Dockerfile").write_text(dockerfile, "utf8")
            built = call(["docker", "build", "--network", "none", "--pull=false",
                  "--no-cache", "-t", target, "-f", str(build / "Dockerfile"),
                  str(build)], timeout=250, check=False)
            require(built.returncode == 0, "TDI_OFFLINE_DOCKER_BUILD_FAILED")
            image_query = call(["docker", "image", "inspect", "-f",
                                 "{{.Id}}", target], check=False)
            require(image_query.returncode == 0, "TDI_IMAGE_INSPECT_FAILED")
            next_image = image_query.stdout.strip()
            require(bool(SHA_RE.fullmatch(next_image))
                    and next_image != old_image, "NEW_TDI_IMAGE_INVALID")
            smoke = """
import hashlib,sys
from pathlib import Path
files=sys.argv[1:4]; shas=sys.argv[4:]
assert len(files)==len(shas)==3
for name,value in zip(files,shas):
    assert hashlib.sha256((Path('/app/leadpilot')/name).read_bytes()).hexdigest()==value
from pypdf import PdfReader
from leadpilot.serviceit_vps_worker import INTERVAL_SECONDS
from leadpilot.serviceit_b2b_tender_documents import inspect_public_documents
from leadpilot.serviceit_vps_b2b_document_worker import run_once, build_report
from leadpilot.serviceit_vps_b2b_dossier_v11_worker import verify_all
assert INTERVAL_SECONDS==300
assert all(callable(x) for x in
    (PdfReader,inspect_public_documents,run_once,build_report,verify_all))
print('TDI_OFFLINE_SMOKE_PASS')
"""
            res = call(["docker", "run", "--rm", "--network", "none",
                        "--pull", "never", "--read-only", "--user", "10001:10001",
                        "--cap-drop", "ALL", "--pids-limit", "64", "--memory", "512m",
                        "--entrypoint", "python", next_image, "-B", "-c", smoke,
                        *FILES, *new_hashes], timeout=110, check=False)
            require(res.returncode == 0, "TDI_OFFLINE_DOCKER_RUN_FAILED")
            require(res.stdout.strip() == "TDI_OFFLINE_SMOKE_PASS",
                    "TDI_OFFLINE_SMOKE_OUTPUT_FAILED")
            print("TDI_RELEASE_OFFLINE_SMOKE=YES", flush=True)
            next_pin = pin_image(old_pin, old_image, next_image)
            preflight = stage / "preflight.compose.yml"
            preflight.write_text(next_pin, "utf8")
            trial = compose_json([p for p in paths if p != PIN] + [preflight])
            next_core = trial["services"]["coordinator"]
            expected_env = dict(core_before.get("environment", {}))
            expected_env[TDI_FLAG] = "YES"
            require(next_core.get("image") == next_image
                    and next_core.get("environment") == expected_env
                    and {k:v for k,v in next_core.items()
                         if k not in ("image", "environment")}
                    == {k:v for k,v in core_before.items()
                         if k not in ("image", "environment")},
                    "TDI_COMPOSE_UNAPPROVED_DRIFT")
            require(all(trial["services"][name] == value
                        for name, value in before["services"].items()
                        if name != "coordinator"),
                    "TDI_ADJACENT_COMPOSE_DRIFT")
            atomic_write(PIN, next_pin)
            altered = True
            call(compose(paths, "up", "-d", "--no-deps", "--no-build",
                         "--pull", "never", "--force-recreate", "coordinator"),
                 timeout=190)
            wait_healthy(next_image, new_hashes)
            require(all(image(n) == before_id and
                        (not neighbor_healthy[n] or healthy(n))
                        for n, before_id in neighbor_images.items()),
                    "TDI_ADJACENT_CONTAINER_CHANGED")
            print("TDI_RELEASE_VPS_LIVE_ENABLED=YES", flush=True)
            print("TDI_RELEASE_CRM_WRITES_BY_INSTALLER=NO", flush=True)
            print("TDI_RELEASE_ROLLBACK=NOT_NEEDED", flush=True)
            return 0
        except Exception as exc:
            code = str(exc) if isinstance(exc, ReleaseBlocked) else (
                "UNEXPECTED_FAIL_CLOSED")
            if not re.fullmatch(r"[A-Z0-9_]{4,90}", code):
                code = "UNEXPECTED_FAIL_CLOSED"
            print("TDI_RELEASE_REASON=" + code, flush=True)
            if altered and old_pin and old_image and paths:
                try:
                    atomic_write(PIN, old_pin)
                    call(compose(paths, "up", "-d", "--no-deps", "--no-build",
                                 "--pull", "never", "--force-recreate",
                                 "coordinator"), timeout=180)
                    wait_healthy(old_image, AUDITED_OLD_SHAS, tdi_expected=False)
                    print("TDI_RELEASE_ROLLBACK=YES", flush=True)
                except Exception:
                    print("TDI_RELEASE_ROLLBACK=UNVERIFIED", flush=True)
            else:
                print("TDI_RELEASE_ROLLBACK=NOT_NEEDED", flush=True)
            print("TDI_RELEASE_VPS_LIVE_ENABLED=NO", flush=True)
            return 2


def main():
    if len(sys.argv) != len(FILES) + 2:
        print("TDI_RELEASE_VPS_LIVE_ENABLED=NO", flush=True)
        return 2
    try:
        return run(sys.argv[1], tuple(sys.argv[2:]))
    except Exception:
        print("TDI_RELEASE_VPS_LIVE_ENABLED=NO", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
