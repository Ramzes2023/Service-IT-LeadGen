#!/usr/bin/env python3
"""Owner-gated rollback of ONLY the V37 supplemental consistency coordinator.

Restore the exact Docker image tagged by the successful 43103194380 rollout.
Never touch database rows, Bitrix, other images, secrets, other containers,
V36 research/docs, or source receipts. Fail closed on any state drift.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tempfile

RELEASE_RUN="43103194380"
FILES=("serviceit_vps_worker.py",
       "serviceit_b2b_dossier_consistency_v37.py",
       "serviceit_b2b_dossier_consistency_worker_v37.py")
BASELINE_TAG="serviceit-v37-baseline:"+RELEASE_RUN
TARGET_TAG="serviceit-v37-evidence:"+RELEASE_RUN
HEX=re.compile(r"[0-9a-f]{64}\Z")
ID=re.compile(r"sha256:[0-9a-f]{64}\Z")


def require(ok, code):
    if not ok:
        raise ValueError(code)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stage_helper(script_hash):
    path=Path.home()/"serviceit"/"staging"/(
        "b2b-v37-onefile-release-"+RELEASE_RUN)/"activate.py"
    require(path.is_file() and not path.is_symlink()
            and path.stat().st_uid==os.geteuid()
            and sha(path)==script_hash, "ORIGINAL_RELEASE_NOT_ATTESTED")
    return runpy.run_path(str(path))


def expected_sources(new_hashes, original_worker_hash):
    """Match the tuple representation returned by original runtime_shas()."""
    return tuple(new_hashes), (original_worker_hash, "MISSING", "MISSING")


def run(argv):
    require(os.geteuid()!=0, "HOST_NONROOT_REQUIRED")
    require(len(argv)==5 and all(HEX.fullmatch(v) for v in argv),
            "SOURCE_SHA_ARGS_INVALID")
    new_hashes=argv[:3]
    original_worker_hash=argv[3]
    original_release_hash=argv[4]
    h=stage_helper(original_release_hash)
    root=h["ROOT"]
    lock=h["LOCK"]
    pin=h["PIN"]
    core=h["CORE"]
    neighbors=h["NEIGHBORS"]
    healthy=h["healthy"]
    image=h["image"]
    output=h["output"]
    call=h["call"]
    runtime_shas=h["runtime_shas"]
    compose=h["compose"]
    compose_json=h["compose_json"]
    pin_image=h["pin_image"]
    atomic_write=h["atomic_write"]
    wait_healthy=h["wait_healthy"]
    pending=h["pending_source_writes"]
    image_exists=h["present_image"]
    # Existing V37 release runtime_shas() returns a positional tuple,
    # not a name-to-hash dictionary. Compare the exact same data type.
    expected_live, expected_base = expected_sources(
        new_hashes, original_worker_hash)
    # No source staging or rebuilt code: two exact OCI images already exist.
    require(lock.parent.is_dir() and not lock.is_symlink(),
            "HOST_ACTIVATION_LOCK_INVALID")
    with lock.open("a") as stream:
        os.chmod(lock,0o600)
        fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        changed=False
        old_pin=""
        current_id=""
        paths=[]
        neighbors_before={}
        try:
            require(healthy(core), "CURRENT_COORDINATOR_NOT_HEALTHY")
            require(runtime_shas()==expected_live,
                    "CURRENT_V37_SOURCE_DRIFT")
            current_id=image(core)
            original_target=output(
                ["docker","image","inspect","-f","{{.Id}}",TARGET_TAG])
            baseline_id=output(
                ["docker","image","inspect","-f","{{.Id}}",BASELINE_TAG])
            require(ID.fullmatch(original_target) and ID.fullmatch(baseline_id),
                    "ROLLBACK_IMAGE_ID_INVALID")
            require(current_id==original_target and baseline_id!=original_target,
                    "ROLLBACK_IMAGE_TARGET_CHANGED")
            require(pending()==0, "INFLIGHT_SOURCE_OR_CRM_WRITE")
            active_script="""
import os
from leadpilot.serviceit_vps_pg_queue import db
with db(dict(os.environ)) as conn:
 with conn.cursor() as cur:
  cur.execute("SELECT to_regclass('public.serviceit_vps_b2b_dossier_consistency_v37_receipts')")
  if cur.fetchone()[0] is None:
   print(0)
  else:
   cur.execute("SELECT count(*) FROM serviceit_vps_b2b_dossier_consistency_v37_receipts WHERE status IN ('RESERVED','WRITE_INTENT')")
   print(int(cur.fetchone()[0]))
"""
            count=output(["docker","exec",core,"python","-B","-c",active_script])
            require(count=="0", "V37_WRITE_INTENT_ACTIVE_NO_RESTART")
            smoke="""
import sys,hashlib
from pathlib import Path
names=sys.argv[1:4];expected=sys.argv[4:]
assert len(names)==len(expected)==3
for name,digest in zip(names,expected):
 p=Path('/app/leadpilot')/name
 actual=hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() and not p.is_symlink() else 'MISSING'
 assert actual==digest
from leadpilot.serviceit_vps_worker import INTERVAL_SECONDS
assert INTERVAL_SECONDS==300
print('BASELINE_SHA_OK')
"""
            result=call(["docker","run","--rm","--network","none",
                         "--pull","never","--read-only","--user","10001:10001",
                         "--cap-drop","ALL","--pids-limit","64",
                         "--memory","512m","--entrypoint","python",
                         BASELINE_TAG,"-B","-c",smoke,
                         *FILES,*expected_base],timeout=100)
            require(result.stdout.strip()=="BASELINE_SHA_OK",
                    "BASELINE_IMAGE_OFFLINE_SHA_INVALID")
            paths=h["inspect_compose_paths"]()
            old_pin=pin.read_text("utf8")
            before=compose_json(paths)
            require(before["services"]["coordinator"].get("image")==current_id,
                    "COMPOSE_IMAGE_DRIFT")
            for n in neighbors:
                if image_exists(n):
                    neighbors_before[n]=(image(n),healthy(n))
            restore_pin=pin_image(old_pin,current_id,baseline_id)
            with tempfile.NamedTemporaryFile(
                    mode="w",encoding="utf8",
                    dir=root/"staging",prefix="v37-rollback-compose-",
                    suffix=".yml",delete=False) as f:
                f.write(restore_pin)
                preflight=Path(f.name)
            try:
                trial=compose_json([p for p in paths if p!=pin]+[preflight])
                require(trial["services"]["coordinator"].get("image")==baseline_id,
                        "ROLLBACK_PREFLIGHT_IMAGE_INVALID")
                require(
                    {k:v for k,v in trial["services"]["coordinator"].items()
                     if k!="image"}==
                    {k:v for k,v in before["services"]["coordinator"].items()
                     if k!="image"},
                    "ROLLBACK_PREFLIGHT_CONFIG_DRIFT")
                require(all(trial["services"][n]==v for n,v in before["services"].items()
                            if n!="coordinator"),
                        "ROLLBACK_OTHER_SERVICES_DRIFT")
            finally:
                preflight.unlink(missing_ok=True)
            print("V37_SAFE_ROLLBACK_PREFLIGHT=YES",flush=True)
            atomic_write(pin,restore_pin)
            changed=True
            call(compose(paths,"up","-d","--no-deps","--no-build",
                         "--pull","never","--force-recreate","coordinator"),
                 timeout=190)
            wait_healthy(baseline_id,expected_base)
            require(all(image(n)==iid and (not prior_health or healthy(n))
                        for n,(iid,prior_health) in neighbors_before.items()),
                    "ROLLBACK_OTHER_CONTAINERS_CHANGED")
            print("V37_SAFE_ROLLBACK_PREVIOUS_IMAGE=YES",flush=True)
            print("V37_SAFE_ROLLBACK_V36_AND_PRIOR_PRESERVED=YES",flush=True)
            print("V37_SAFE_ROLLBACK_CRM_WRITES=NO",flush=True)
            return 0
        except Exception as exc:
            reason=str(exc) if isinstance(exc,ValueError) else "FAIL_CLOSED"
            if not re.fullmatch(r"[A-Z0-9_]{4,90}",reason):
                reason="FAIL_CLOSED"
            print("V37_SAFE_ROLLBACK_REASON="+reason,flush=True)
            if changed and old_pin and current_id and paths:
                try:
                    atomic_write(pin,old_pin)
                    call(compose(paths,"up","-d","--no-deps","--no-build",
                                 "--pull","never","--force-recreate",
                                 "coordinator"),timeout=190)
                    wait_healthy(current_id,expected_live)
                    print("V37_SAFE_ROLLBACK_RESTORE_CURRENT=YES",flush=True)
                except Exception:
                    print("V37_SAFE_ROLLBACK_RESTORE_CURRENT=UNVERIFIED",flush=True)
            print("V37_SAFE_ROLLBACK_PREVIOUS_IMAGE=NO",flush=True)
            print("V37_SAFE_ROLLBACK_CRM_WRITES=NO",flush=True)
            return 2


if __name__=="__main__":
    try:
        raise SystemExit(run(sys.argv[1:]))
    except Exception:
        print("V37_SAFE_ROLLBACK_PREVIOUS_IMAGE=NO",flush=True)
        print("V37_SAFE_ROLLBACK_CRM_WRITES=NO",flush=True)
        raise SystemExit(2)
