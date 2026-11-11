#!/usr/bin/env python3
"""V20 OFF-by-default versioned evidence guarded source installation; no installer CRM writes.

Owner-only workflow stages 2 Git-SHA-attested research modules. Existing live
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
FILES = ("serviceit_vps_worker.py", "serviceit_b2b_v20_versioned.py")
# Filled after validating exact previous reviewed worker source at deploy time.
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
    # V19 can have a durable CRM intent in its own receipt table.
    v19 = """
import os
from leadpilot.serviceit_vps_pg_queue import db
with db(dict(os.environ)) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.serviceit_vps_b2b_v19_research_receipts')")
        if cur.fetchone()[0] is None: print(0)
        else:
            cur.execute("SELECT count(*) FROM serviceit_vps_b2b_v19_research_receipts "
                        "WHERE status IN ('RESERVED','WRITE_INTENT')")
            print(int(cur.fetchone()[0]))
"""
    v19_count = output(["docker","exec",CORE,"python","-B","-c",v19])
    require(v19_count.isdigit(), "V19_PENDING_UNVERIFIED")
    return count + int(old_count) + int(pending) + int(tdi_count) + int(v19_count)


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


FLAG = "SERVICEIT_VPS_B2B_V20_VERSIONED_ENABLED"

def pin_flag(original):
    """Insert only one fresh V20 flag in existing coordinator environment."""
    require(type(original) is str and
            original.startswith("services:\n  coordinator:\n") and
            original.count("    environment:\n") == 1 and
            FLAG not in original, "V20_PIN_STRUCTURE_UNEXPECTED")
    result=original.replace("    environment:\n",
         '    environment:\n      '+FLAG+': "YES"\n',1)
    require(result.count(FLAG)==1 and result!=original,"V20_FLAG_PIN_DRIFT")
    return result


def pending_v20_intents():
    """Read only: never restart amid uncertain V20 Bitrix write intent."""
    script = """
import os
from leadpilot.serviceit_vps_pg_queue import db
with db(dict(os.environ)) as conn:
  with conn.cursor() as cur:
    cur.execute("SELECT to_regclass('public.serviceit_vps_b2b_v20_revision_receipts')")
    if cur.fetchone()[0] is None:
      print(0)
    else:
      cur.execute("SELECT count(*) FROM serviceit_vps_b2b_v20_revision_receipts "
                  "WHERE status IN ('RESERVED','WRITE_INTENT')")
      print(int(cur.fetchone()[0]))
"""
    value=output(["docker","exec",CORE,"python","-B","-c",script])
    require(value.isdigit(),"V20_INTENT_COUNT_INVALID")
    return int(value)


def run(run_id, expected_hashes):
    global AUDITED_OLD_SHAS
    require(os.geteuid()!=0 and re.fullmatch(r"\d{7,16}",run_id) is not None,
            "V20_OWNER_RUN_INVALID")
    require(type(expected_hashes) is tuple and len(expected_hashes)==2
            and all(re.fullmatch(r"[0-9a-f]{64}",h) for h in expected_hashes),
            "V20_PINNED_SOURCE_SHAS_REQUIRED")
    AUDITED_OLD_SHAS=expected_hashes
    stage=ROOT/"staging"/("b2b-v20-enable-"+run_id)
    require(stage.is_dir() and not stage.is_symlink()
            and stage.stat().st_uid==os.geteuid(),"V20_STAGE_UNTRUSTED")
    for path in (PIN,ENV):
        require(path.is_file() and not path.is_symlink()
                and path.stat().st_mode & 0o077 == 0,
                "V20_PRIVATE_COMPOSE_REQUIRED")
    require(LOCK.parent.is_dir() and not LOCK.is_symlink(),"V20_ACTIVATION_LOCK_INVALID")
    with LOCK.open("a") as lock:
      os.chmod(LOCK,0o600)
      fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
      old_pin=None
      original_env=None
      paths=[]
      changed=False
      old_image=None
      adjacent={}
      try:
        require(healthy(CORE),"V20_CORE_NOT_HEALTHY")
        require(runtime_shas()==expected_hashes,"V20_INSTALLED_MODULE_SHA_DRIFT")
        require(output(["docker","exec",CORE,"python","-B","-c",
            "from leadpilot.serviceit_vps_worker import INTERVAL_SECONDS;"
            "print(INTERVAL_SECONDS)"])=="300","V20_CYCLE_NOT_300S")
        require(pending_source_writes()==0 and pending_v20_intents()==0,
                "V20_ACTIVE_WRITE_NO_RESTART")
        old_image=image(CORE)
        original_env=env_of(CORE)
        require(all(original_env.get(k)==v for k,v in GATES.items()),
                "V20_EXISTING_RESEARCH_GATES_CHANGED")
        require(original_env.get(FLAG) in (None,"NO"),
                "V20_ALREADY_ENABLED_OR_CONFLICT")
        require(output(["docker","exec",CORE,"python","-B","-c",
            "import os;from leadpilot.serviceit_vps_worker import exact_environment;"
            "print(exact_environment(dict(os.environ)).get("
            "'SERVICEIT_VPS_B2B_V19_BACKFILL_ENABLED'))"])=="YES",
            "V20_PRIOR_V19C_NOT_RUNNING")
        paths=inspect_compose_paths()
        old_pin=PIN.read_text("utf8")
        before=compose_json(paths)
        old_core=before["services"]["coordinator"]
        require(old_core.get("image")==old_image,
                "V20_EXISTING_IMAGE_PIN_DRIFT")
        env_before=old_core.get("environment",{})
        require(type(env_before) is dict and FLAG not in env_before,
                "V20_COMPOSE_ALREADY_HAS_FLAG")
        adjacent={name:(image(name),healthy(name)) for name in NEIGHBORS
                  if present_image(name)}
        new_pin=pin_flag(old_pin)
        trial=stage/"trial.compose.yml"
        trial.write_text(new_pin,"utf8")
        desired=compose_json([p for p in paths if p!=PIN]+[trial])
        expected_core=dict(old_core)
        expected_core["environment"]={**env_before,FLAG:"YES"}
        require(desired["services"]["coordinator"]==expected_core,
                "V20_ONLY_ONE_ENV_CHANGE_ALLOWED")
        require(all(desired["services"][name]==value
                for name,value in before["services"].items()
                if name!="coordinator"),"V20_OTHER_COMPOSE_SERVICES_CHANGED")
        print("V20_ENABLE_PREFLIGHT=YES",flush=True)
        atomic_write(PIN,new_pin)
        changed=True
        call(compose(paths,"up","-d","--no-deps","--no-build",
                     "--pull","never","--force-recreate","coordinator"),timeout=190)
        wait_healthy(old_image,expected_hashes)
        after_env=env_of(CORE)
        require(after_env=={**original_env,FLAG:"YES"},
                "V20_LIVE_NONFLAG_ENV_CHANGED")
        require(all(image(name)==old_sha and
                    (not old_health or healthy(name))
                    for name,(old_sha,old_health) in adjacent.items()),
                "V20_ADJACENT_CONTAINER_CHANGED")
        print("V20_ENABLE_VPS_ACTIVE=YES",flush=True)
        print("V20_ENABLE_INSTALLER_CRM_WRITES=NO",flush=True)
        print("V20_ENABLE_ROLLBACK=NOT_NEEDED",flush=True)
        return 0
      except Exception as exc:
        why=str(exc) if isinstance(exc,ReleaseBlocked) else "UNEXPECTED_FAIL_CLOSED"
        if not re.fullmatch(r"[A-Z0-9_]{4,90}",why):
            why="UNEXPECTED_FAIL_CLOSED"
        print("V20_ENABLE_REASON="+why,flush=True)
        if changed and old_pin and paths:
          try:
            atomic_write(PIN,old_pin)
            call(compose(paths,"up","-d","--no-deps","--no-build",
                         "--pull","never","--force-recreate","coordinator"),timeout=190)
            wait_healthy(old_image,expected_hashes)
            require(env_of(CORE)==original_env,"V20_ROLLBACK_ENV_NOT_RESTORED")
            print("V20_ENABLE_ROLLBACK=YES",flush=True)
          except Exception:
            print("V20_ENABLE_ROLLBACK=UNVERIFIED",flush=True)
        else:
          print("V20_ENABLE_ROLLBACK=NOT_NEEDED",flush=True)
        print("V20_ENABLE_VPS_ACTIVE=NO",flush=True)
        return 2


def main():
    if len(sys.argv)!=4:
        print("V20_ENABLE_VPS_ACTIVE=NO",flush=True)
        return 2
    try:
        return run(sys.argv[1],tuple(sys.argv[2:]))
    except Exception:
        print("V20_ENABLE_VPS_ACTIVE=NO",flush=True)
        return 2

if __name__=="__main__":
    raise SystemExit(main())
