#!/usr/bin/env python3
"""Exact one-module VPS AUX color score release; disable FNS scoring dependency.

No changes to INN worker, public research, CRM source cards, LLM, other
services or private secrets. Source hash must match known live baseline.
Single existing AUX service only, Compose image+three flags with rollback.
"""
from __future__ import annotations
import fcntl,hashlib,io,json,os,re,stat,subprocess,sys,tarfile,tempfile,time
from pathlib import Path

HOME=Path.home()
CONFIG=HOME/"serviceit/config"
ENV=CONFIG/"vps.env"
LOCK=CONFIG/".serviceit-five-minute-activate.lock"
RELEASES=(HOME/"serviceit/releases").resolve()
AUX="serviceit-leadgen-vps-auxiliaries-1"
CORE="serviceit-leadgen-vps-coordinator-1"
FNS="serviceit-leadgen-vps-fns-msp-1"
OTHER=("serviceit-leadgen-vps-coordinator-1",
       "serviceit-leadgen-vps-fns-msp-1",
       "serviceit-leadgen-vps-business-1",
       "serviceit-leadgen-vps-observer-1")
MODULE="serviceit_preliminary_score_markers.py"
NEW_FLAGS={
    "SERVICEIT_VPS_FNS_CRM_RESULT_ENABLED":"NO",
    "SERVICEIT_VPS_FREE_MSP_SCORE_ENABLED":"NO",
    "SERVICEIT_VPS_PROVISIONAL_SCORE_ENABLED":"YES",
}
OLD_FLAGS={
    "SERVICEIT_VPS_FNS_CRM_RESULT_ENABLED":"YES",
    "SERVICEIT_VPS_FREE_MSP_SCORE_ENABLED":"YES",
    "SERVICEIT_VPS_PROVISIONAL_SCORE_ENABLED":"NO",
}
class Blocked(RuntimeError):pass
def need(ok,reason):
    if not ok:raise Blocked(reason)
def cmd(args,timeout=85,check=True):
    try:
        p=subprocess.run(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
             stderr=subprocess.DEVNULL,timeout=timeout,check=False)
    except (OSError,subprocess.TimeoutExpired):
        raise Blocked("COMMAND_UNAVAILABLE_OR_TIMED_OUT") from None
    if check:need(p.returncode==0,"COMMAND_FAILED")
    return p
def out(args,timeout=85):
    return cmd(args,timeout).stdout.decode("utf8").strip()
def inspect(name,field):
    return out(["docker","inspect","-f",field,name])
def image(name):
    value=inspect(name,"{{.Image}}")
    need(re.fullmatch(r"sha256:[a-f0-9]{64}",value),"IMAGE_ID_INVALID")
    return value
def healthy(name):
    try:return inspect(name,"{{.State.Running}}:{{.State.Health.Status}}")=="true:healthy"
    except Blocked:return False
def runtime_sha(container,module):
    raw=cmd(["docker","cp",container+":/app/leadpilot/"+module,"-"],
            timeout=35).stdout
    try:
        with tarfile.open(fileobj=io.BytesIO(raw),mode="r:") as archive:
            match=[m for m in archive.getmembers()
                   if m.isfile() and m.name.split("/")[-1]==module]
            need(len(match)==1,"MODULE_ARCHIVE_NOT_EXACT")
            stream=archive.extractfile(match[0])
            need(stream is not None,"MODULE_ARCHIVE_INVALID")
            binary=stream.read()
    except tarfile.TarError:
        raise Blocked("MODULE_ARCHIVE_INVALID") from None
    need(0<len(binary)<100000,"MODULE_LENGTH_UNTRUSTED")
    return hashlib.sha256(binary).hexdigest()
def compose_paths(container):
    raw=inspect(container,
        '{{index .Config.Labels "com.docker.compose.project.config_files"}}')
    paths=[Path(x.strip()) for x in raw.split(",") if x.strip()]
    need(1<=len(paths)<=14 and len(paths)==len(set(paths)),
         "LIVE_COMPOSE_PATHS_UNVERIFIED")
    need(all(p.is_file() and not p.is_symlink() for p in paths),
         "LIVE_COMPOSE_PATH_NOT_REGULAR")
    need(sum(bool(re.search(r"/releases/[a-f0-9]{40}/deploy/serviceit-vps.compose.yml$",
                            str(p.resolve()))) for p in paths)==1,
         "LIVE_RELEASE_NOT_PINNED")
    need(all(p.resolve().is_relative_to(RELEASES)
             or p.resolve().is_relative_to(CONFIG) for p in paths),
         "LIVE_COMPOSE_PATH_UNAUTHORIZED")
    need(inspect(container,
         '{{index .Config.Labels "com.docker.compose.project"}}')
         =="serviceit-leadgen-vps","COMPOSE_PROJECT_UNVERIFIED")
    return paths
def compose_cmd(paths,*args):
    return ["docker","compose","--env-file",str(ENV)]+[
        x for p in paths for x in ("-f",str(p))]+list(args)
def model(paths):
    data=json.loads(out(compose_cmd(paths,"config","--format","json")))
    need(isinstance(data.get("services"),dict)
         and isinstance(data["services"].get("auxiliaries"),dict),
         "COMPOSE_MODEL_UNVERIFIED")
    return data["services"]
def flags(container):
    raw=json.loads(inspect(container,"{{json .Config.Env}}"))
    need(isinstance(raw,list),"LIVE_FLAGS_INVALID")
    return dict(x.split("=",1) for x in raw
                if isinstance(x,str) and "=" in x)
def pin(path,img,assign):
    need(not path.is_symlink(),"PIN_SYMLINK")
    need(set(assign)==set(NEW_FLAGS)
         and all(assign[k] in ("YES","NO") for k in assign),
         "PIN_FLAGS_INVALID")
    body="services:\n  auxiliaries:\n    image: \""+img+"\"\n    pull_policy: never\n    environment:\n"
    body+="".join("      "+k+": \""+assign[k]+"\"\n" for k in sorted(assign))
    fd,tmp=tempfile.mkstemp(prefix=".score-no-fns-",dir=CONFIG)
    try:
        with os.fdopen(fd,"w",encoding="utf8") as dest:
            os.fchmod(dest.fileno(),0o600)
            dest.write(body);dest.flush();os.fsync(dest.fileno())
        os.replace(tmp,path)
    finally:
        try:os.unlink(tmp)
        except FileNotFoundError:pass
def compare(before,after,new_image):
    need(after["auxiliaries"].get("image")==new_image,"PIN_NEW_IMAGE_MISSING")
    old=dict(before["auxiliaries"]);fresh=dict(after["auxiliaries"])
    for k in ("image","pull_policy"):
        old.pop(k,None);fresh.pop(k,None)
    oldenv=old.pop("environment",{})
    newenv=fresh.pop("environment",{})
    need(old==fresh,"NON_FLAG_AUX_COMPOSE_CHANGED")
    need(isinstance(oldenv,dict) and isinstance(newenv,dict)
         and set(oldenv)==set(newenv),"COMPOSE_ENV_KEYS_CHANGED")
    need(all(oldenv[k]==newenv[k] for k in oldenv if k not in NEW_FLAGS),
         "UNRELATED_AUX_ENV_CHANGED")
    need(all(newenv.get(k)==v for k,v in NEW_FLAGS.items()),
         "EXPECTED_SCORE_FLAGS_MISSING")
    need(all(before[k]==after.get(k) for k in before if k!="auxiliaries"),
         "OTHER_SERVICES_COMPOSE_CHANGED")
def wait_new(expected_img,expected_sha):
    for i in range(30):
        if healthy(AUX):break
        time.sleep(4)
    need(healthy(AUX) and image(AUX)==expected_img,
         "AUX_NEW_IMAGE_NOT_HEALTHY")
    need(runtime_sha(AUX,MODULE)==expected_sha,
         "AUX_COLOR_MARKER_NOT_APPLIED")
    live=flags(AUX)
    need(all(live.get(k)==v for k,v in NEW_FLAGS.items()),
         "AUX_NEW_FLAGS_NOT_APPLIED")
    rc=inspect(AUX,"{{.RestartCount}}")
    time.sleep(12)
    need(healthy(AUX) and inspect(AUX,"{{.RestartCount}}")==rc,
         "AUX_SCORE_RESTART_LOOP")
def main():
    need(os.geteuid()!=0,"NONROOT_REQUIRED")
    need(len(sys.argv)==4,"INPUT_ARGS_INVALID")
    runid,oldsha,newsha=sys.argv[1:]
    need(re.fullmatch(r"[0-9]{7,16}",runid)
         and all(re.fullmatch(r"[a-f0-9]{64}",x) for x in (oldsha,newsha)),
         "INPUT_SHA_INVALID")
    need(oldsha!=newsha,"ALREADY_SAME_SOURCE")
    stage=HOME/"serviceit/staging"/("score-no-fns-"+runid)
    source=stage/MODULE
    need(source.is_file() and not source.is_symlink()
         and source.stat().st_uid==os.geteuid()
         and 0<source.stat().st_size<100000
         and hashlib.sha256(source.read_bytes()).hexdigest()==newsha,
         "STAGED_MARKER_NOT_REVIEWED")
    need(ENV.is_file() and not ENV.is_symlink()
         and not(stat.S_IMODE(ENV.stat().st_mode)&0o077),
         "PRIVATE_ENV_NOT_VERIFIED")
    need(CONFIG.is_dir() and not LOCK.is_symlink(),
         "CONFIG_OR_LOCK_UNVERIFIED")
    changed=False
    oldimage=None
    paths=[]
    override=None
    other_ids={}
    with LOCK.open("a") as lock:
        os.chmod(LOCK,0o600)
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            need(healthy(AUX) and healthy(CORE) and healthy(FNS),
                 "CRITICAL_SERVICE_HEALTH_NOT_READY")
            need(runtime_sha(AUX,MODULE)==oldsha,
                 "LIVE_COLOR_SCORE_SHA_DRIFT")
            oldimage=image(AUX)
            other_ids={s:image(s) for s in OTHER}
            existing=flags(AUX)
            need(all(existing.get(k)==v for k,v in OLD_FLAGS.items()),
                 "EXISTING_FNS_SCORE_FLAGS_CHANGED")
            need(existing.get("SERVICEIT_VPS_WRITER_MODE")=="LIVE"
                 and existing.get("SERVICEIT_VPS_CUTOVER_AUTHORIZED")=="YES",
                 "AUX_GLOBAL_LIVE_NOT_APPROVED")
            paths=compose_paths(AUX)
            before=model(paths)
            environment=before["auxiliaries"].get("environment")
            need(isinstance(environment,dict)
                 and all(str(v)==existing.get(k) for k,v in environment.items()),
                 "LIVE_AUX_ENV_DRIFT")
            need(out(["docker","image","inspect","-f","{{.Id}}",oldimage])
                 ==oldimage,"OLD_IMAGE_NOT_PINNED")
            print("SCORE_NO_FNS_PREFLIGHT=YES",flush=True)
            oldtag="serviceit-score-old:"+runid
            newtag="serviceit-score-no-fns:"+runid
            need(cmd(["docker","image","inspect",newtag],check=False).returncode!=0,
                 "NEW_TAG_EXISTS")
            cmd(["docker","tag",oldimage,oldtag])
            build=stage/"build"
            build.mkdir(mode=0o700,exist_ok=False)
            (build/MODULE).write_bytes(source.read_bytes())
            (build/"Dockerfile").write_text(
                "FROM "+oldtag+"\nCOPY --chmod=0444 "+MODULE+
                " /app/leadpilot/"+MODULE+"\n",encoding="utf8")
            cmd(["docker","build","--network","none","--pull=false",
                 "--no-cache","-t",newtag,"-f",str(build/"Dockerfile"),
                 str(build)],timeout=250)
            newimage=out(["docker","image","inspect","-f","{{.Id}}",newtag])
            need(re.fullmatch(r"sha256:[a-f0-9]{64}",newimage)
                 and newimage!=oldimage,"NEW_IMAGE_UNVERIFIED")
            smoke = """
import hashlib,importlib.util,sys
from leadpilot.serviceit_preliminary_score_markers import provisional_payload
from leadpilot.serviceit_kanban_presentation import BADGE_FIELD
m=importlib.util.find_spec('leadpilot.serviceit_preliminary_score_markers')
assert m and m.origin.startswith('/app/leadpilot/')
assert hashlib.sha256(open(m.origin,'rb').read()).hexdigest()==sys.argv[1]
for score,color in ((0,'🔴'),(65,'🟡'),(90,'🟢')):
    assert provisional_payload(score,'ИНН НЕТ')[BADGE_FIELD].startswith(color)
print('SCORE_COLOR_SMOKE=YES')
"""
            checked=cmd(["docker","run","--rm","--network","none",
                 "--read-only","--user","10001:10001","--cap-drop","ALL",
                 "--pids-limit","64","--memory","256m","--entrypoint","python",
                 newimage,"-B","-c",smoke,newsha],timeout=90)
            need(checked.stdout.decode("utf8").strip()=="SCORE_COLOR_SMOKE=YES",
                 "NEW_SCORE_IMAGE_SMOKE_FAILED")
            override=CONFIG/("score-no-fns-"+runid+".compose.yml")
            need(not override.exists() and not override.is_symlink(),
                 "OVERRIDE_EXISTS")
            pin(override,newimage,NEW_FLAGS)
            after=model(paths+[override])
            compare(before,after,newimage)
            print("SCORE_NO_FNS_IMAGE_VERIFIED=YES",flush=True)
            changed=True
            cmd(compose_cmd(paths+[override],"up","-d","--no-deps",
                  "--no-build","--pull","never","--force-recreate",
                  "auxiliaries"),timeout=180)
            wait_new(newimage,newsha)
            need(all(image(s)==digest for s,digest in other_ids.items()),
                 "UNRELATED_SERVICE_IMAGE_CHANGED")
            need(healthy(CORE) and healthy(FNS),
                 "CRITICAL_SERVICE_HEALTH_CHANGED")
            print("SCORE_NO_FNS_AUX_ACTIVATED=YES",flush=True)
            print("SCORE_NO_FNS_FNS_WRITER=NO",flush=True)
            print("SCORE_NO_FNS_FNS_REQUIRED=NO",flush=True)
            print("SCORE_NO_FNS_FREE_MSP_SCORE=NO",flush=True)
            print("SCORE_NO_FNS_PROVISIONAL_SCORE=YES",flush=True)
            print("SCORE_NO_FNS_INN_WORKER_UNCHANGED=YES",flush=True)
            print("SCORE_NO_FNS_OTHER_IMAGES_UNCHANGED=YES",flush=True)
            print("SCORE_NO_FNS_ROLLBACK=NOT_NEEDED",flush=True)
            return 0
        except Exception as error:
            code=str(error) if isinstance(error,Blocked) else "UNEXPECTED"
            if not re.fullmatch(r"[A-Z0-9_]{3,96}",code):
                code="UNEXPECTED"
            print("SCORE_NO_FNS_BLOCKED_"+code+"=YES",flush=True)
            if changed and override is not None and oldimage:
                try:
                    pin(override,oldimage,OLD_FLAGS)
                    cmd(compose_cmd(paths+[override],"up","-d","--no-deps",
                        "--no-build","--pull","never","--force-recreate",
                        "auxiliaries"),timeout=180)
                    need(image(AUX)==oldimage,"ROLLBACK_IMAGE_CHANGED")
                    print("SCORE_NO_FNS_ROLLBACK=YES",flush=True)
                except Exception:
                    print("SCORE_NO_FNS_ROLLBACK=FAILED",flush=True)
            else:
                print("SCORE_NO_FNS_NO_LIVE_CHANGE=YES",flush=True)
            return 2
if __name__=="__main__":
    try:raise SystemExit(main())
    except Exception:
        print("SCORE_NO_FNS_OUTER_BLOCKED=YES",flush=True)
        raise SystemExit(2)
