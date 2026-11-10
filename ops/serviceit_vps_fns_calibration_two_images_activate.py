#!/usr/bin/env python3
"""Only FNS MSP and Bitrix native AUX real-card calibration image patch.

AUX may be crash-looping; use Docker archive copy for exact old-source
verification. Build separate immutable images without network, dry-run
Compose and replace only two named containers with full image rollback.
Never update Bitrix cards, queues, scores, API keys or source collectors.
"""
from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime,timezone

HOME=Path.home()
CONFIG=HOME/"serviceit/config"
ENV=CONFIG/"vps.env"
LOCK=CONFIG/".serviceit-five-minute-activate.lock"
RELEASES=(HOME/"serviceit/releases").resolve()
SERVICES={
    "auxiliaries": ("serviceit-leadgen-vps-auxiliaries-1",
                    "serviceit_vps_native_aux.py"),
    "fns-msp": ("serviceit-leadgen-vps-fns-msp-1",
                "serviceit_vps_native_msp.py"),
}
CORE="serviceit-leadgen-vps-coordinator-1"

class Blocked(RuntimeError):
    pass

def need(ok,code):
    if not ok:raise Blocked(code)

def run(argv,timeout=90,check=True):
    try:
        p=subprocess.run(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL,timeout=timeout,check=False)
    except (OSError,subprocess.TimeoutExpired):
        raise Blocked("REMOTE_COMMAND_TIMEOUT")
    if check:need(p.returncode==0,"REMOTE_COMMAND_FAILED")
    return p

def output(argv,timeout=90):
    return run(argv,timeout).stdout.decode("utf8").strip()

def inspect(name,fmt):
    return output(["docker","inspect","-f",fmt,name])

def image(name):
    v=inspect(name,"{{.Image}}")
    need(re.fullmatch(r"sha256:[a-f0-9]{64}",v),"IMAGE_ID_INVALID")
    return v

def health(name):
    try:
        return inspect(name,"{{.State.Running}}:{{.State.Health.Status}}")=="true:healthy"
    except Blocked:
        return False

def cp_sha(container,module):
    p=run(["docker","cp",container+":/app/leadpilot/"+module,"-"],timeout=35)
    try:
        with tarfile.open(fileobj=io.BytesIO(p.stdout),mode="r:") as archive:
            files=[m for m in archive.getmembers()
                   if m.isfile() and m.name.split("/")[-1]==module]
            need(len(files)==1,"SOURCE_DOCKER_ARCHIVE_INVALID")
            stream=archive.extractfile(files[0])
            need(stream is not None,"SOURCE_DOCKER_ARCHIVE_EMPTY")
            raw=stream.read()
    except tarfile.TarError:
        raise Blocked("SOURCE_DOCKER_ARCHIVE_INVALID") from None
    need(0<len(raw)<200000,"SOURCE_DOCKER_MODULE_INVALID")
    return hashlib.sha256(raw).hexdigest()

def compose_paths(container):
    value=inspect(container,
        '{{index .Config.Labels "com.docker.compose.project.config_files"}}')
    paths=[Path(x.strip()) for x in value.split(",") if x.strip()]
    need(1<=len(paths)<=12 and len(set(paths))==len(paths),
         "COMPOSE_PATH_COUNT_INVALID")
    need(all(p.is_file() and not p.is_symlink() for p in paths),
         "COMPOSE_PATH_MISSING_OR_LINK")
    need(sum(bool(re.search(
         r"/releases/[a-f0-9]{40}/deploy/serviceit-vps.compose.yml$",
         str(p.resolve()))) for p in paths)==1,"COMPOSE_RELEASE_NOT_PINNED")
    need(all(p.resolve().is_relative_to(RELEASES)
             or p.resolve().is_relative_to(CONFIG) for p in paths),
         "COMPOSE_PATH_NOT_APPROVED")
    return paths

def compose_cmd(paths,service,*args):
    cmd=["docker","compose","--env-file",str(ENV)]
    if service=="fns-msp":cmd.extend(["--profile","msp"])
    for p in paths:cmd.extend(["-f",str(p)])
    return cmd+list(args)

def model(paths,service):
    data=json.loads(output(compose_cmd(paths,service,"config","--format","json")))
    need(isinstance(data.get("services"),dict)
         and service in data["services"],"COMPOSE_MODEL_INVALID")
    return data["services"]

def private_pin(path,service,img):
    need(not path.is_symlink(),"PIN_SYMLINK_FORBIDDEN")
    text="services:\n  "+service+":\n    image: \""+img+"\"\n    pull_policy: never\n"
    fd,temp=tempfile.mkstemp(prefix=".fns-native-",dir=CONFIG)
    try:
        with os.fdopen(fd,"w",encoding="utf8") as handle:
            os.fchmod(handle.fileno(),0o600)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp,path)
    finally:
        try:os.unlink(temp)
        except FileNotFoundError:pass

def check_container_flags(container,service):
    items=json.loads(inspect(container,"{{json .Config.Env}}"))
    need(isinstance(items,list),"RUNTIME_FLAGS_UNREADABLE")
    env=dict(row.split("=",1) for row in items
             if isinstance(row,str) and "=" in row)
    for k,v in (
        ("SERVICEIT_VPS_WRITER_MODE","LIVE"),
        ("SERVICEIT_VPS_CUTOVER_AUTHORIZED","YES"),
        ("SERVICEIT_VPS_POLL_ENABLED","YES"),
        ("SERVICEIT_VPS_OLD_GH_WRITERS_DISABLED_ACK","YES"),
        ("SERVICEIT_VPS_INTAKE_ENABLED","NO"),
    ):
        need(env.get(k,v if k.endswith("_INTAKE_ENABLED") else None)==v,
             "LIVE_FLAG_UNEXPECTED")
    if service=="auxiliaries":
        need(env.get("SERVICEIT_VPS_FNS_CRM_RESULT_ENABLED","NO")=="NO",
             "FNS_WRITER_ALREADY_ENABLED")
    if service=="fns-msp":
        need(env.get("SERVICEIT_VPS_FNS_MSP_ENABLED")=="YES",
             "FNS_COLLECTOR_NOT_ENABLED")
    return env

def wait_image(service,img,expected_sha,started):
    container,module=SERVICES[service]
    for _ in range(24):
        if health(container):break
        time.sleep(4)
    need(health(container),"SERVICE_HEALTH_FAILED")
    need(image(container)==img and cp_sha(container,module)==expected_sha,
         "RUNTIME_IMAGE_SOURCE_MISMATCH")
    first=inspect(container,"{{.RestartCount}}")
    time.sleep(12)
    need(health(container) and inspect(container,"{{.RestartCount}}")==first,
         "SERVICE_RESTART_LOOP_PERSISTS")
    # A healthy config-only check can miss an unhealthy main AUX process.
    # Require at least one complete live cycle after deployment; never
    # accept merely an import/flags-only healthcheck.
    if service=="auxiliaries":
        logs=run(["docker","logs","--since",started,container],timeout=15,check=False)
        statuses=set()
        if logs.returncode==0:
            for line in logs.stdout.decode("utf8","replace").splitlines():
                try:datum=json.loads(line)
                except (ValueError,TypeError):continue
                if isinstance(datum,dict) and isinstance(datum.get("status"),str):
                    statuses.add(datum["status"])
        need("NATIVE_AUX_CYCLE" in statuses and "NATIVE_AUX_BLOCKED" not in statuses,
             "AUX_NATIVE_FIRST_CYCLE_FAILED")

def main():
    need(os.geteuid()!=0,"NONROOT_REQUIRED")
    need(len(sys.argv)==6,"ARGS_INVALID")
    runid,aux_old,msp_old,aux_new,msp_new=sys.argv[1:]
    need(re.fullmatch(r"[0-9]{7,16}",runid),"RUN_ID_INVALID")
    for value in (aux_old,msp_old,aux_new,msp_new):
        need(re.fullmatch(r"[a-f0-9]{64}",value),"SOURCE_SHA_INPUT_INVALID")
    stage=HOME/"serviceit/staging"/("fns-native-calibration-"+runid)
    need(stage.is_dir() and not stage.is_symlink() and stage.stat().st_uid==os.geteuid(),
         "STAGE_NOT_PRIVATE")
    need(ENV.is_file() and not ENV.is_symlink()
         and not (stat.S_IMODE(ENV.stat().st_mode)&0o077),"PRIVATE_CONFIG_MISSING")
    need(CONFIG.is_dir() and not LOCK.is_symlink(),"LOCK_INVALID")
    approved={"auxiliaries":(aux_old,aux_new),"fns-msp":(msp_old,msp_new)}
    for service,(old_sha,new_sha) in approved.items():
        module=SERVICES[service][1]
        f=stage/module
        need(f.is_file() and not f.is_symlink() and f.stat().st_uid==os.geteuid()
             and hashlib.sha256(f.read_bytes()).hexdigest()==new_sha,
             "STAGED_REVIEWED_SOURCE_INVALID")
    pins={}
    paths={}
    old_images={}
    new_images={}
    untouched={}
    modified=[]
    with LOCK.open("a") as lock:
        os.chmod(LOCK,0o600)
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            need(health(CORE),"CORE_UNHEALTHY")
            for service,(container,module) in SERVICES.items():
                old_images[service]=image(container)
                need(cp_sha(container,module)==approved[service][0],
                     "LIVE_"+service.upper().replace("-","_")+"_SHA_DRIFT")
                need(inspect(container,
                       '{{index .Config.Labels "com.docker.compose.project"}}')
                     =="serviceit-leadgen-vps","COMPOSE_PROJECT_WRONG")
                paths[service]=compose_paths(container)
                cfg=model(paths[service],service)
                runtime=check_container_flags(container,service)
                declared=cfg[service].get("environment",{})
                need(isinstance(declared,dict)
                     and all(str(v)==runtime.get(k) for k,v in declared.items()),
                     "COMPOSE_LIVE_ENV_DRIFT")
            untouched={name:image(name) for name in [
                CORE,"serviceit-leadgen-vps-business-1",
                "serviceit-leadgen-vps-observer-1"]}
            print("FNS_ONLY_TWO_IMAGES_PREFLIGHT=YES",flush=True)
            # Build BOTH independent images before changing either service.
            for service,(container,module) in SERVICES.items():
                old=old_images[service]
                oldtag="serviceit-fns-fix-base-"+service+":"+runid
                newtag="serviceit-fns-fix-"+service+":"+runid
                need(run(["docker","image","inspect",newtag],check=False).returncode!=0,
                     "RELEASE_TAG_ALREADY_EXISTS")
                run(["docker","tag",old,oldtag])
                build=stage/("build-"+service)
                build.mkdir(mode=0o700,exist_ok=False)
                (build/module).write_bytes((stage/module).read_bytes())
                (build/"Dockerfile").write_text(
                    "FROM "+oldtag+"\nCOPY --chmod=0444 "+module+
                    " /app/leadpilot/"+module+"\n",encoding="utf8")
                run(["docker","build","--network","none","--pull=false",
                     "--no-cache","-t",newtag,"-f",str(build/"Dockerfile"),str(build)],
                    timeout=250)
                new=output(["docker","image","inspect","-f","{{.Id}}",newtag])
                need(re.fullmatch(r"sha256:[a-f0-9]{64}",new)
                     and new!=old,"NEW_IMAGE_INVALID")
                smoke=("import hashlib,importlib.util,sys;"
                       "p=importlib.util.find_spec('leadpilot.'+sys.argv[1][:-3]);"
                       "assert p and p.origin.startswith('/app/leadpilot/');"
                       "assert hashlib.sha256(open(p.origin,'rb').read()).hexdigest()==sys.argv[2];"
                       "import leadpilot.serviceit_vps_native_aux;"
                       "import leadpilot.serviceit_vps_native_msp;"
                       "print('FNS_SMOKE_OK')")
                checked=run(["docker","run","--rm","--network","none","--read-only",
                    "--user","10001:10001","--cap-drop","ALL","--pids-limit","64",
                    "--memory","256m","--entrypoint","python",new,
                    "-B","-c",smoke,module,approved[service][1]],timeout=100)
                need(checked.stdout.decode("utf8").strip()=="FNS_SMOKE_OK",
                     "FNS_IMAGE_SMOKE_FAILED")
                new_images[service]=new
                pin=CONFIG/("fns-calibration-"+service+"-"+runid+".compose.yml")
                need(not pin.exists() and not pin.is_symlink(),"PIN_EXISTS")
                private_pin(pin,service,new)
                pins[service]=pin
                before=model(paths[service],service)
                after=model(paths[service]+[pin],service)
                need(after[service].get("image")==new,
                     "COMPOSE_IMAGE_NOT_PINNED")
                old_cfg=before[service].copy()
                next_cfg=after[service].copy()
                for key in ("image","pull_policy"):
                    old_cfg.pop(key,None);next_cfg.pop(key,None)
                need(old_cfg==next_cfg,"COMPOSE_NON_IMAGE_SETTINGS_CHANGED")
                for other in before:
                    if other!=service:
                        need(before[other]==after.get(other),"OTHER_COMPOSE_MODEL_CHANGED")
            print("FNS_ONLY_BOTH_IMAGES_VERIFIED=YES",flush=True)
            for service in ("auxiliaries","fns-msp"):
                container,module=SERVICES[service]
                cmd=compose_cmd(paths[service]+[pins[service]],service,
                    "up","-d","--no-deps","--no-build","--pull","never",
                    "--force-recreate",service)
                # Snapshot start time before action, used to verify first cycle.
                started=datetime.now(timezone.utc).isoformat(timespec="seconds")
                modified.append(service)
                run(cmd,timeout=175)
                wait_image(service,new_images[service],approved[service][1],started)
                print("FNS_ONLY_"+service.upper().replace("-","_")+"_PATCH=YES",flush=True)
            need(all(image(name)==want for name,want in untouched.items()),
                 "UNRELATED_IMAGE_CHANGED")
            need(health(CORE),"CORE_HEALTH_CHANGED")
            print("FNS_ONLY_CRM_WRITES_BY_INSTALLER=NO",flush=True)
            print("FNS_ONLY_OTHER_IMAGES_UNCHANGED=YES",flush=True)
            print("FNS_ONLY_ROLLBACK=NOT_NEEDED",flush=True)
            return 0
        except Exception as error:
            reason=str(error) if isinstance(error,Blocked) else "UNEXPECTED"
            if not re.fullmatch(r"[A-Z0-9_]{3,110}",reason):reason="UNEXPECTED"
            print("FNS_ONLY_BLOCKED_"+reason+"=YES",flush=True)
            rollback="NOT_REQUIRED"
            for service in reversed(modified):
                try:
                    private_pin(pins[service],service,old_images[service])
                    run(compose_cmd(paths[service]+[pins[service]],service,
                        "up","-d","--no-deps","--no-build","--pull","never",
                        "--force-recreate",service),timeout=160)
                    need(image(SERVICES[service][0])==old_images[service],
                         "ROLLBACK_IMAGE_INCORRECT")
                    rollback="YES"
                except Exception:
                    rollback="FAILED"
            print("FNS_ONLY_ROLLBACK="+rollback,flush=True)
            return 2

if __name__=="__main__":
    try:raise SystemExit(main())
    except Exception:
        print("FNS_ONLY_RELEASE_OUTER_BLOCKED=YES",flush=True)
        raise SystemExit(2)
