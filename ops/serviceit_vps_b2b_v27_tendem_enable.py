#!/usr/bin/env python3
"""V27: opt-in ONLY public Tendem free Fetch on existing VPS coordinator.

Only two documented flags move NO->YES. The B2B direct-source license stays
NO, and the old 300-second coordinator, V19/V20/V23, service identities, source
image, Caddy, PostgreSQL, other services, STOP, CRM and secret mount remain
exactly unchanged. No CRM or paid AI calls are made by this installer.
Owner-only GitHub workflow attests source script and current module SHAs.
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

ROOT=Path.home()/"serviceit"
CONFIG=ROOT/"config"
ENV=CONFIG/"vps.env"
PIN=CONFIG/"native-five-minute-core-image.compose.yml"
OVERLAY=CONFIG/"serviceit-vps-tinyfish-free.compose.yml"
LOCK=CONFIG/".serviceit-five-minute-activate.lock"
CORE="serviceit-leadgen-vps-coordinator-1"
FILES=("serviceit_b2b_free_public_fallback.py",
       "serviceit_b2b_remote_public_fetch.py",
       "serviceit_b2b_public_fact_extractor.py",
       "serviceit_vps_b2b_deep_research.py",
       "serviceit_vps_worker.py")
NEW_FLAGS={
 "SERVICEIT_VPS_B2B_FREE_PUBLIC_FETCH_ENABLED":"YES",
 "SERVICEIT_B2B_TENDEM_PUBLIC_EXTRACTION_APPROVED":"YES",
}
PRESERVED={
 "SERVICEIT_B2B_CENTER_PUBLIC_EXTRACTION_LICENSED":"NO",
 "SERVICEIT_VPS_B2B_V20_VERSIONED_ENABLED":"YES",
 "SERVICEIT_VPS_WRITER_MODE":"LIVE",
 "SERVICEIT_VPS_POLL_ENABLED":"YES",
 "SERVICEIT_VPS_INTAKE_ENABLED":"NO",
 "SERVICEIT_AI_PROVIDER":"routerai",
 "SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED":"YES",
 "SERVICEIT_VPS_BROWSER_PROXY_ISOLATION_VERIFIED":"YES",
}
NEIGHBORS=("serviceit-b2b-vps-writer-1","serviceit-b2b-vps-receiver-1",
           "serviceit-leadgen-vps-business-1","serviceit-leadgen-vps-observer-1",
           "serviceit-leadgen-vps-fns-msp-1",
           "serviceit-leadgen-vps-auxiliaries-1")

class Blocked(RuntimeError):pass
def need(ok,reason):
    if not ok:raise Blocked(reason)

def run_cmd(args,timeout=100,check=True):
    try:
        p=subprocess.run(args,capture_output=True,text=True,
                         stdin=subprocess.DEVNULL,timeout=timeout,check=False)
    except (OSError,subprocess.TimeoutExpired):
        raise Blocked("EXECUTION_UNAVAILABLE") from None
    if check and p.returncode!=0:
        raise Blocked("COMMAND_FAILED")
    return p

def output(args,timeout=100):
    return run_cmd(args,timeout).stdout.strip()

def env_of(name):
    x=json.loads(output(["docker","inspect","-f","{{json .Config.Env}}",name]))
    need(isinstance(x,list) and all(type(y) is str for y in x),"DOCKER_ENV_BAD")
    return dict(z.split("=",1) for z in x if "=" in z)

def health(name):
    z=run_cmd(["docker","inspect","-f",
               "{{.State.Running}}:{{.State.Health.Status}}",name],check=False)
    return z.returncode==0 and z.stdout.strip()=="true:healthy"

def image(name):
    x=output(["docker","inspect","-f","{{.Image}}",name])
    need(re.fullmatch(r"sha256:[0-9a-f]{64}",x),"DOCKER_IMAGE_ID_BAD")
    return x

def code_shas():
    code="""
import hashlib,sys
from pathlib import Path
for name in sys.argv[1:]:
    path=Path('/app/leadpilot')/name
    print(hashlib.sha256(path.read_bytes()).hexdigest()
          if path.is_file() and not path.is_symlink() else 'MISSING')
"""
    txt=output(["docker","exec",CORE,"python","-B","-c",code,*FILES])
    return txt.splitlines()

def compose_paths():
    labels=output(["docker","inspect","-f",
                   '{{index .Config.Labels "com.docker.compose.project.config_files"}}',
                   CORE])
    paths=[Path(p.strip()) for p in labels.split(",") if p.strip()]
    need(len(paths)==len(set(paths)) and 3<=len(paths)<=12
         and PIN in paths and OVERLAY in paths
         and all(p.is_file() and not p.is_symlink()
                 and p.resolve().is_relative_to(ROOT.resolve()) for p in paths),
         "EXACT_COMPOSE_PATHS_REQUIRED")
    return paths

def compose(paths,*args):
    cmd=["docker","compose","--env-file",str(ENV)]
    for p in paths:cmd.extend(("-f",str(p)))
    return cmd+list(args)

def compose_config(paths):
    return json.loads(output(compose(paths,"config","--format","json")))

def pending_writes():
    sql="""
import os
from leadpilot.serviceit_vps_pg_queue import db
with db(dict(os.environ)) as conn:
  with conn.cursor() as c:
    total=0
    c.execute("SELECT count(*) FROM serviceit_vps_native_receipts "
              "WHERE status IN ('RESERVED','WRITE_INTENT')")
    total+=int(c.fetchone()[0])
    for table in ('serviceit_vps_b2b_dossier_v11_receipts',
                  'serviceit_vps_b2b_v19_research_receipts',
                  'serviceit_vps_b2b_v20_revision_receipts',
                  'serviceit_vps_b2b_v23_primary_source_receipts',
                  'serviceit_vps_b2b_tender_doc_receipts'):
      c.execute('SELECT to_regclass(%s)',('public.'+table,))
      if c.fetchone()[0] is None:continue
      c.execute('SELECT count(*) FROM '+table+
                " WHERE status IN ('RESERVED','WRITE_INTENT')")
      total+=int(c.fetchone()[0])
    print(total)
"""
    x=output(["docker","exec",CORE,"python","-B","-c",sql])
    need(x.isdecimal() and int(x)<=1000000,"PENDING_COUNT_INVALID")
    return int(x)

def atomic_write(path,data):
    fd,temporary=tempfile.mkstemp(prefix=".v27-tendem-",dir=str(path.parent))
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            os.fchmod(f.fileno(),0o600)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary,path)
    finally:
        try:os.unlink(temporary)
        except FileNotFoundError:pass

def enabled_overlay(original):
    need(type(original) is str and
         original.count('SERVICEIT_VPS_B2B_FREE_PUBLIC_FETCH_ENABLED: "NO"')==1 and
         original.count('SERVICEIT_B2B_TENDEM_PUBLIC_EXTRACTION_APPROVED: "NO"')==1 and
         original.count('SERVICEIT_B2B_CENTER_PUBLIC_EXTRACTION_LICENSED: "NO"')==1,
         "TINYFISH_OVERLAY_PRESTATE_CHANGED")
    new=original
    for key in NEW_FLAGS:
        new=new.replace(key+': "NO"',key+': "YES"',1)
    need(new!=original,"TINYFISH_FLAGS_NOT_CHANGED")
    return new

def verify_diff(before,after):
    import copy
    b,a=copy.deepcopy(before),copy.deepcopy(after)
    need(b.get("name")==a.get("name") and
         set(b.get("services",{}))==set(a.get("services",{})),
         "COMPOSE_ID_OR_SERVICES_CHANGED")
    oldc,newc=b["services"]["coordinator"],a["services"]["coordinator"]
    bx,ax=oldc.get("environment"),newc.get("environment")
    need(type(bx) is dict and type(ax) is dict,"COMPOSE_ENV_BAD")
    need(ax=={**bx,**NEW_FLAGS},"MORE_THAN_TWO_ENV_FLAGS_CHANGED")
    newc["environment"]=bx
    need(a==b,"NONCOORDINATOR_COMPOSE_DRIFT")

def wait_healthy(expected_image,pins):
    for _ in range(35):
        if health(CORE):break
        time.sleep(3)
    need(health(CORE) and image(CORE)==expected_image
         and code_shas()==pins,"COORDINATOR_RESTART_DRIFT")

def main():
    need(len(sys.argv)==2+len(FILES) and
         re.fullmatch(r"[0-9]{7,16}",sys.argv[1]) is not None and
         all(re.fullmatch(r"[0-9a-f]{64}",x) for x in sys.argv[2:]),
         "EXACT_RUN_AND_SHAS_REQUIRED")
    pins=sys.argv[2:]
    need(os.geteuid()!=0 and LOCK.is_file() and not LOCK.is_symlink(),
         "UNTRUSTED_DEPLOY_USER_OR_LOCK")
    for path in (PIN,OVERLAY,ENV):
        need(path.is_file() and not path.is_symlink() and
             path.stat().st_mode & 0o077 == 0,"PRIVATE_CONFIG_MISSING")
    updated=False
    original=None
    paths=None
    original_env=None
    old_image=None
    with LOCK.open("a") as f:
        os.chmod(LOCK,0o600)
        fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            need(health(CORE),"EXISTING_COORDINATOR_UNHEALTHY")
            need(code_shas()==pins,"LIVE_SOURCE_SHA_MISMATCH")
            need(output(["docker","exec",CORE,"python","-B","-c",
                  "from leadpilot.serviceit_vps_worker import INTERVAL_SECONDS;"
                  "print(INTERVAL_SECONDS)"])=="300","FIVE_MINUTE_CADENCE_CHANGED")
            need(pending_writes()==0,"PENDING_WRITER_NO_RESTART")
            original_env=env_of(CORE)
            need(all(original_env.get(k)==v for k,v in PRESERVED.items()),
                 "EXISTING_SAFETY_GATES_CHANGED")
            need(all(original_env.get(k)=="NO" for k in NEW_FLAGS),
                 "TINYFISH_ALREADY_ENABLED_OR_DRIFT")
            need(original_env.get("SERVICEIT_TINYFISH_API_KEY_FILE")
                 =="/run/secrets/serviceit_tinyfish_key",
                 "TINYFISH_KEY_MOUNT_NOT_CONFIRMED")
            old_image=image(CORE)
            paths=compose_paths()
            original=OVERLAY.read_text("utf-8")
            before=compose_config(paths)
            need(before["services"]["coordinator"].get("image")==old_image,
                 "COORDINATOR_IMAGE_PIN_MISMATCH")
            neighbor={n:(image(n),health(n)) for n in NEIGHBORS
                      if run_cmd(["docker","inspect",n],check=False).returncode==0}
            desired=enabled_overlay(original)
            stage=ROOT/"staging"/("v27-tendem-enable-"+sys.argv[1])
            need(stage.is_dir() and not stage.is_symlink() and
                 stage.stat().st_uid==os.geteuid(),"OWNER_STAGE_MISSING")
            trial=stage/"trial-tinyfish.compose.yml"
            trial.write_text(desired,"utf-8")
            replaced=[trial if p==OVERLAY else p for p in paths]
            after=compose_config(replaced)
            verify_diff(before,after)
            print("V27_ENABLE_PREFLIGHT=YES",flush=True)
            atomic_write(OVERLAY,desired)
            updated=True
            run_cmd(compose(paths,"up","-d","--no-deps","--no-build",
                            "--pull","never","--force-recreate","coordinator"),
                    timeout=190)
            wait_healthy(old_image,pins)
            live_env=env_of(CORE)
            need(live_env=={**original_env,**NEW_FLAGS},
                 "NON_TARGET_ENV_CHANGED_AFTER_RESTART")
            need(all(image(n)==old and (not washealthy or health(n))
                     for n,(old,washealthy) in neighbor.items()),
                 "ADJACENT_CONTAINERS_CHANGED")
            print("V27_ENABLE_TENDEM_FREE_PUBLIC=YES",flush=True)
            print("V27_ENABLE_B2B_DIRECT_LICENSE=NO",flush=True)
            print("V27_ENABLE_CRM_WRITES=NO",flush=True)
            print("V27_ENABLE_ROLLBACK=NOT_NEEDED",flush=True)
            return 0
        except Exception as e:
            reason=str(e) if isinstance(e,Blocked) else "FAIL_CLOSED"
            if not re.fullmatch(r"[A-Z0-9_]{4,85}",reason):
                reason="FAIL_CLOSED"
            print("V27_ENABLE_REASON="+reason,flush=True)
            if updated and original is not None and paths:
                try:
                    atomic_write(OVERLAY,original)
                    run_cmd(compose(paths,"up","-d","--no-deps","--no-build",
                                    "--pull","never","--force-recreate","coordinator"),
                            timeout=190)
                    wait_healthy(old_image,pins)
                    need(env_of(CORE)==original_env,"ROLLBACK_ENV_DRIFT")
                    print("V27_ENABLE_ROLLBACK=YES",flush=True)
                except Exception:
                    print("V27_ENABLE_ROLLBACK=UNVERIFIED",flush=True)
            else:
                print("V27_ENABLE_ROLLBACK=NOT_NEEDED",flush=True)
            print("V27_ENABLE_TENDEM_FREE_PUBLIC=NO",flush=True)
            return 2

if __name__=="__main__":
    try:raise SystemExit(main())
    except Exception:
        print("V27_ENABLE_TENDEM_FREE_PUBLIC=NO",flush=True)
        raise SystemExit(2)
