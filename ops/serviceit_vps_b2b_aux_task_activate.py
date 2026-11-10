#!/usr/bin/env python3
"""Guarded two-module AUXILIARIES-only B2B internal task update.

Existing Docker image and Compose/environment are preserved; never touch
coordinator, B2B receiver/writer, business, Caddy, PG data or any CRM item.
Only offline image build, exact-sha tested aux image promotion, safe rollback.
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
CONFIG = HOME/"serviceit/config"
ENV = CONFIG/"vps.env"
LOCK = CONFIG/".serviceit-five-minute-activate.lock"
RELEASES = (HOME/"serviceit/releases").resolve()
AUX = "serviceit-leadgen-vps-auxiliaries-1"
OTHER = [
    "serviceit-leadgen-vps-coordinator-1",
    "serviceit-leadgen-vps-business-1",
    "serviceit-leadgen-vps-observer-1",
    "serviceit-leadgen-vps-fns-msp-1",
    "serviceit-b2b-vps-receiver-1",
    "serviceit-b2b-vps-writer-1",
]
FILES = ("serviceit_vps_native_aux.py",
         "serviceit_stage3_all_new_task_automation.py")


class Blocked(Exception):
    pass


def must(value, code):
    if not value:
        raise Blocked(code)


def call(args, *, timeout=80, check=True):
    try:
        r = subprocess.run(args, stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise Blocked("COMMAND_UNAVAILABLE_OR_TIMEOUT") from None
    if check:
        must(r.returncode == 0, "COMMAND_FAILED")
    return r


def out(args, timeout=80):
    return call(args,timeout=timeout).stdout.strip()


def inspect(name, pattern):
    return out(["docker","inspect","-f",pattern,name])


def image_id(name):
    value = inspect(name,"{{.Image}}")
    must(re.fullmatch(r"sha256:[a-f0-9]{64}",value) is not None,
         "IMAGE_DIGEST_UNVERIFIED")
    return value


def healthy(name):
    try:
        return inspect(name,"{{.State.Running}}:{{.State.Health.Status}}") == "true:healthy"
    except Blocked:
        return False


def runtime_shas(container):
    code = """
import hashlib,importlib.util
for name in ('serviceit_vps_native_aux','serviceit_stage3_all_new_task_automation'):
    m=importlib.util.find_spec('leadpilot.'+name)
    assert m and m.origin and m.origin.startswith('/app/leadpilot/')
    print(hashlib.sha256(open(m.origin,'rb').read()).hexdigest())
"""
    lines = out(["docker","exec",container,"python","-B","-c",code]).splitlines()
    must(len(lines)==2 and all(re.fullmatch(r"[a-f0-9]{64}",x) for x in lines),
         "MODULE_SHA_UNVERIFIED")
    return lines


def compose_cmd(paths,*cmd):
    args=["docker","compose","--env-file",str(ENV)]
    for p in paths:
        args.extend(["-f",str(p)])
    return args+list(cmd)


def read_compose(paths):
    data=json.loads(out(compose_cmd(paths,"config","--format","json")))
    service=data["services"]["auxiliaries"]
    must(isinstance(service,dict) and isinstance(service.get("environment"),dict),
         "AUX_COMPOSE_STRUCTURE_UNVERIFIED")
    return service


def pin_value(image):
    return "services:\n  auxiliaries:\n    image: \""+image+"\"\n    pull_policy: never\n"


def write_pin(path,value):
    tmp_fd,tmp=tempfile.mkstemp(prefix=".b2b-aux-",dir=str(CONFIG))
    try:
        with os.fdopen(tmp_fd,"w",encoding="utf8") as file:
            os.fchmod(file.fileno(),0o600)
            file.write(value)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp,path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def check_existing_env(service):
    current=json.loads(inspect(AUX,"{{json .Config.Env}}"))
    actual=dict(item.split("=",1) for item in current if "=" in item)
    must(all(str(v)==actual.get(k)
             for k,v in service["environment"].items()),
         "AUX_COMPOSE_ENV_CHANGED")
    for k,v in (
        ("SERVICEIT_VPS_MANAGER_TASK_ENABLED","YES"),
        ("SERVICEIT_VPS_TASK_ASSIGNEE_APPROVED",
         "YES_1062_21_INTERNAL_MANAGER"),
        ("SERVICEIT_VPS_TASK_ASSIGNEE_ID","605"),
        ("SERVICEIT_VPS_CUTOVER_AUTHORIZED","YES"),
        ("SERVICEIT_VPS_OLD_GH_WRITERS_DISABLED_ACK","YES"),
        ("SERVICEIT_VPS_POLL_ENABLED","YES"),
    ):
        must(actual.get(k)==v,"REQUIRED_APPROVED_TASK_FLAGS_MISSING")
    return service["environment"]


def inspect_paths():
    label=inspect(AUX,
        '{{index .Config.Labels "com.docker.compose.project.config_files"}}')
    paths=[Path(x.strip()) for x in label.split(",") if x.strip()]
    must(1<=len(paths)<=5 and len(paths)==len(set(paths)),
         "AUX_COMPOSE_LABELS_INVALID")
    must(all(p.is_file() and not p.is_symlink() for p in paths),
         "AUX_COMPOSE_PATHS_UNTRUSTED")
    valid_release=[
        p for p in paths if p.resolve().is_relative_to(RELEASES)
        and re.search(r"/releases/[a-f0-9]{40}/deploy/serviceit-vps\.compose\.yml$",
                      str(p.resolve()))
    ]
    must(len(valid_release)==1,"PINNED_RELEASE_UNVERIFIED")
    must(all(p.resolve().is_relative_to(RELEASES)
             or p.resolve().is_relative_to(CONFIG)
             for p in paths),
         "AUX_COMPOSE_PATH_OUTSIDE_APPROVED_ROOT")
    must(inspect(AUX,'{{index .Config.Labels "com.docker.compose.project"}}')==
         "serviceit-leadgen-vps", "COMPOSE_PROJECT_UNVERIFIED")
    return paths


def wait_new(expected_image, shas):
    for _ in range(22):
        if healthy(AUX):
            break
        time.sleep(4)
    must(healthy(AUX) and image_id(AUX)==expected_image,
         "NEW_AUX_HEALTH_OR_DIGEST_MISMATCH")
    must(runtime_shas(AUX)==shas,"NEW_AUX_MODULES_NOT_APPLIED")


def main():
    must(os.geteuid()!=0,"HOST_MUST_BE_NONROOT")
    must(len(sys.argv)==6,"REQUIRED_ARGS_MISSING")
    runid,*hashes=sys.argv[1:]
    must(re.fullmatch(r"[0-9]{7,16}",runid) and len(hashes)==4
         and all(re.fullmatch(r"[a-f0-9]{64}",x) for x in hashes),
         "SOURCE_HASH_ARGUMENTS_INVALID")
    old_hashes=[hashes[0],hashes[1]]
    new_hashes=[hashes[2],hashes[3]]
    must(old_hashes!=new_hashes,"ALREADY_CURRENT_VERSION")
    stage=HOME/"serviceit/staging"/("b2b-aux-task-"+runid)
    for name,expected in zip(FILES,new_hashes):
        file=stage/name
        must(file.is_file() and not file.is_symlink()
             and file.stat().st_uid==os.geteuid()
             and 0<file.stat().st_size<160000
             and hashlib.sha256(file.read_bytes()).hexdigest()==expected,
             "STAGED_REVIEWED_MODULE_UNVERIFIED")
    must(ENV.is_file() and not ENV.is_symlink()
         and ENV.stat().st_uid==os.geteuid()
         and ENV.stat().st_mode & 0o077==0,"PRIVATE_ENV_UNVERIFIED")
    must(CONFIG.is_dir() and not LOCK.is_symlink(),"CONFIG_OR_LOCK_UNTRUSTED")
    with LOCK.open("a") as lk:
        os.chmod(LOCK,0o600)
        fcntl.flock(lk.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        old_image=None
        pin=None
        paths=[]
        changed=False
        try:
            must(healthy(AUX) and all(healthy(x) for x in OTHER),
                 "BASELINE_CONTAINER_UNHEALTHY")
            preserved={name:image_id(name) for name in OTHER}
            old_image=image_id(AUX)
            must(runtime_shas(AUX)==old_hashes,
                 "LIVE_AUX_NOT_REVIEWED_BASELINE")
            paths=inspect_paths()
            original_service=read_compose(paths)
            configured=original_service.get("image")
            must(isinstance(configured,str) and configured
                 and out(["docker","image","inspect","-f","{{.Id}}",
                          configured])==old_image,
                 "AUX_EXISTING_IMAGE_DRIFT")
            original_env=check_existing_env(original_service)
            print("B2B_TASK_AUX_PREFLIGHT=YES",flush=True)
            baseline="serviceit-b2b-task-aux-base:"+runid
            tag="serviceit-b2b-task-aux:"+runid
            must(call(["docker","image","inspect",tag],check=False).returncode!=0,
                 "PATCH_TAG_ALREADY_EXISTS")
            call(["docker","tag",old_image,baseline])
            overlay=stage/"build"
            overlay.mkdir(mode=0o700,exist_ok=False)
            for name in FILES:
                (overlay/name).write_bytes((stage/name).read_bytes())
            (overlay/"Dockerfile").write_text(
                "FROM "+baseline+"\n"+
                "".join("COPY --chmod=0444 "+f+" /app/leadpilot/"+f+"\n"
                        for f in FILES),encoding="utf8")
            call(["docker","build","--network","none","--pull=false","--no-cache",
                  "-t",tag,"-f",str(overlay/"Dockerfile"),str(overlay)],
                 timeout=230)
            new_image=out(["docker","image","inspect","-f","{{.Id}}",tag])
            must(re.fullmatch(r"sha256:[a-f0-9]{64}",new_image)
                 and new_image!=old_image,"NEW_IMAGE_ID_INVALID")
            smoke = """
import hashlib,importlib.util,sys
for name,sha in zip(
    ('serviceit_vps_native_aux','serviceit_stage3_all_new_task_automation'),
    sys.argv[1:]):
    m=importlib.util.find_spec('leadpilot.'+name)
    assert m and m.origin.startswith('/app/leadpilot/')
    assert hashlib.sha256(open(m.origin,'rb').read()).hexdigest()==sha
from leadpilot.serviceit_vps_native_aux import ai_receipt_allows_task,INTERVAL_SECONDS
from leadpilot.serviceit_stage3_all_new_task_automation import b2b_preliminary_hold_allows_internal_review
assert INTERVAL_SECONDS==300
assert callable(ai_receipt_allows_task)
assert callable(b2b_preliminary_hold_allows_internal_review)
print('B2B_TASK_AUX_OFFLINE_SMOKE=YES')
"""
            check=call(["docker","run","--rm","--network","none","--read-only",
                        "--user","10001:10001","--cap-drop","ALL",
                        "--pids-limit","64","--memory","256m",
                        "--entrypoint","python",new_image,
                        "-B","-c",smoke,*new_hashes],timeout=110)
            must(check.stdout.strip()=="B2B_TASK_AUX_OFFLINE_SMOKE=YES",
                 "NEW_AUX_IMAGE_SMOKE_FAILED")
            print("B2B_TASK_AUX_IMAGE_VERIFIED=YES",flush=True)
            pin=CONFIG/("b2b-task-aux-"+runid+".compose.yml")
            must(not pin.exists() and not pin.is_symlink(),
                 "AUX_PATCH_PIN_CONFLICT")
            write_pin(pin,pin_value(new_image))
            next_service=read_compose(paths+[pin])
            must(next_service.get("image")==new_image
                 and next_service.get("environment")==original_env
                 and all(next_service.get(k)==v
                         for k,v in original_service.items()
                         if k not in ("image","pull_policy")),
                 "NEW_AUX_COMPOSE_CHANGES_UNAPPROVED")
            changed=True
            call(compose_cmd(paths+[pin],"up","-d","--no-deps","--no-build",
                             "--pull","never","--force-recreate","auxiliaries"),
                 timeout=170)
            wait_new(new_image,new_hashes)
            must(all(healthy(name) and image_id(name)==expected
                     for name,expected in preserved.items()),
                 "OTHER_CONTAINERS_CHANGED")
            print("B2B_TASK_AUX_ACTIVATED=YES",flush=True)
            print("B2B_TASK_AUX_ROLLBACK=NOT_NEEDED",flush=True)
            return 0
        except Exception as error:
            code=str(error) if isinstance(error,Blocked) else "UNEXPECTED_EXCEPTION"
            if re.fullmatch(r"[A-Z0-9_]{3,100}",code) is None:
                code="UNEXPECTED_EXCEPTION"
            print("B2B_TASK_AUX_BLOCKED_"+code+"=YES",flush=True)
            if changed and old_image and pin and paths:
                try:
                    write_pin(pin,pin_value(old_image))
                    call(compose_cmd(paths+[pin],"up","-d","--no-deps","--no-build",
                                     "--pull","never","--force-recreate","auxiliaries"),
                         timeout=170)
                    wait_new(old_image,old_hashes)
                    print("B2B_TASK_AUX_ROLLBACK=YES",flush=True)
                except Exception:
                    print("B2B_TASK_AUX_ROLLBACK=FAILED",flush=True)
            print("B2B_TASK_AUX_ACTIVATED=NO",flush=True)
            return 2


if __name__=="__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("B2B_TASK_AUX_PREFLIGHT_BLOCKED=YES",flush=True)
        raise SystemExit(2)
