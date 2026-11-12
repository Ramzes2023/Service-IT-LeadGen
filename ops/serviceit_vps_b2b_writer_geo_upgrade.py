#!/usr/bin/env python3
"""B2B writer-only overlay upgrade (five reviewed Python modules, PG/CRM safe).

The running receiver, core, Google Apps Script, Bitrix CRM and all secrets stay
unchanged. Read-only PUBLIC geo canary decides whether to enable the Moscow-only
gate: no affirmative verified Moscow tender => preserve existing live behavior.
Old writer container is kept stopped as rollback snapshot after successful swap.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

WRITER = "serviceit-b2b-vps-writer-1"
RECEIVER = "serviceit-b2b-vps-receiver-1"
CORE = "serviceit-leadgen-vps-coordinator-1"
NAMES = (
    "serviceit_b2b_moscow_geo.py",
    "serviceit_b2b_public_mirrors.py",
    "serviceit_public_evidence_research.py",
    "serviceit_vps_b2b_bridge.py",
    "b2b_email_opportunity_bridge.py",
)
ALLOWED = {
    "SERVICEIT_PG_HOST", "SERVICEIT_PG_PORT", "SERVICEIT_PG_DB",
    "SERVICEIT_PG_USER", "SERVICEIT_PG_PASSWORD_FILE",
    "SERVICEIT_VPS_B2B_RECEIVER_ENABLED", "SERVICEIT_VPS_B2B_WRITER_ENABLED",
    "SERVICEIT_VPS_B2B_GH_WRITERS_DISABLED_ACK",
    "SERVICEIT_VPS_INTAKE_ENABLED", "SERVICEIT_BITRIX_WEBHOOK_FILE",
    "SERVICEIT_VPS_WRITER_MODE", "SERVICEIT_VPS_CUTOVER_AUTHORIZED",
    "SERVICEIT_VPS_OLD_GH_WRITERS_DISABLED_ACK",
    "SERVICEIT_VPS_POLL_ENABLED", "SERVICEIT_AI_PROVIDER",
    "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED",
    "SERVICEIT_ROUTERAI_PRIVACY_APPROVED",
    "SERVICEIT_VPS_NATIVE_ACTIVATION_UTC",
    "SERVICEIT_B2B_MOSCOW_ONLY_ENABLED",
}
EXPECTED = {
    "SERVICEIT_VPS_B2B_RECEIVER_ENABLED": "YES",
    "SERVICEIT_VPS_B2B_WRITER_ENABLED": "YES",
    "SERVICEIT_VPS_B2B_GH_WRITERS_DISABLED_ACK": "YES",
    "SERVICEIT_VPS_WRITER_MODE": "LIVE",
    "SERVICEIT_VPS_CUTOVER_AUTHORIZED": "YES",
    "SERVICEIT_VPS_POLL_ENABLED": "YES",
    "SERVICEIT_AI_PROVIDER": "routerai",
    "SERVICEIT_PG_PASSWORD_FILE": "/run/secrets/serviceit_pg_password",
    "SERVICEIT_BITRIX_WEBHOOK_FILE": "/run/secrets/serviceit_bitrix_webhook",
}
SAMPLES = (4677160, 4678279, 4677404, 4676643, 4675475)

class Blocked(Exception):
    pass

def need(ok, code):
    if not ok:
        raise Blocked(code)

def run(argv, *, timeout=180, check=True, cwd=None):
    try:
        p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           stdin=subprocess.DEVNULL, timeout=timeout, text=True,
                           cwd=cwd, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise Blocked("COMMAND_UNAVAILABLE_OR_TIMEOUT") from None
    if check and p.returncode != 0:
        raise Blocked("COMMAND_FAILED")
    return p

def output(argv):
    return run(argv).stdout.strip()

def inspect(name):
    info = json.loads(output(["docker", "inspect", "--type", "container", name]))
    need(isinstance(info, list) and len(info) == 1, "CONTAINER_INSPECT_UNVERIFIED")
    return info[0]

def running(name):
    info = inspect(name)
    need(info["State"]["Running"] is True, "CORE_OR_SOURCE_NOT_RUNNING")
    return info

def env_of(info):
    rows = info.get("Config", {}).get("Env", [])
    need(isinstance(rows, list), "B2B_ENV_INVALID")
    env = dict(x.split("=", 1) for x in rows if isinstance(x, str) and "=" in x)
    for key, value in EXPECTED.items():
        need(env.get(key) == value, "B2B_PRODUCTION_GATES_DRIFT")
    safe = {key:env[key] for key in ALLOWED if key in env}
    for key, value in safe.items():
        need(len(value) < 256 and "\n" not in value and "\r" not in value,
             "B2B_ENV_UNSAFE")
        if key.endswith("_FILE"):
            need(value.startswith("/run/secrets/serviceit_"),
                 "B2B_SECRET_PATH_UNEXPECTED")
    return safe

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def smoke(image):
    code = (
        "from leadpilot.serviceit_vps_b2b_bridge import INTERVAL_SECONDS;"
        "from leadpilot.serviceit_b2b_moscow_geo import classify_location_lines;"
        "from leadpilot.b2b_email_opportunity_bridge import classify_services;"
        "assert INTERVAL_SECONDS==300;"
        "assert classify_location_lines(['Место поставки: Москва'])=='MOSCOW';"
        "assert 'LICENSES' in classify_services('Приобретение лицензий Teams (продление)');"
        "assert 'CLOUD' in classify_services('Доступ к облачным сервисам');"
        "print('B2B_WRITER_OVERLAY_SMOKE_PASS')"
    )
    got = run(["docker","run","--rm","--network","none","--user","10001:10001",
               "--read-only","--entrypoint","python",image,"-B","-c",code],
              timeout=90).stdout.strip()
    need(got == "B2B_WRITER_OVERLAY_SMOKE_PASS", "B2B_OVERLAY_SMOKE_FAILED")

def canary(image, env, source):
    code = """
import json,os,httpx
from leadpilot.serviceit_vps_pg_queue import db
from leadpilot.serviceit_b2b_moscow_geo import check_public_procurement_location
samples=('4677160','4678279','4677404','4676643','4675475')
with db(dict(os.environ)) as conn, conn.transaction():
    conn.execute('SET TRANSACTION READ ONLY')
    rows=conn.execute('SELECT source_id,notice_json FROM serviceit_vps_b2b_inbox '
      "WHERE state='VERIFIED' AND source_id=ANY(%s)",(
       [f'B2B-CENTER-{x}' for x in samples],)).fetchall()
counts={'MOSCOW':0,'OUTSIDE':0,'MIXED':0,'UNKNOWN':0}
with httpx.Client(trust_env=False, follow_redirects=False, timeout=12) as client:
    for key,notice in rows:
        if not isinstance(notice,dict):
            notice=json.loads(notice)
        decision=check_public_procurement_location(notice,client)
        assert decision in counts
        counts[decision]+=1
        tender_id=str(key).rsplit('-',1)[-1]
        if tender_id in samples:
            print('GEO_SAMPLE_'+tender_id+'='+decision)
print('GEO_READONLY_COUNT='+str(len(rows)))
for key,num in counts.items():
    print('GEO_COUNT_'+key+'='+str(num))
print('GEO_CRM_WRITES=0')
print('GEO_PG_WRITES=0')
"""
    args = ["docker","run","--rm","--network","host",
            "--user","10001:10001","--read-only",
            "--security-opt","no-new-privileges","--cap-drop","ALL",
            "--volumes-from",source]
    for key in sorted(env):
        if key.startswith("SERVICEIT_PG_"):
            args += ["--env",key+"="+env[key]]
    args += ["--entrypoint","python",image,"-B","-c",code]
    p=run(args,timeout=240,check=False)
    status={}
    for line in p.stdout.splitlines():
        if re.fullmatch(r"GEO_(?:SAMPLE_[0-9]{5,12}|COUNT_[A-Z]+|READONLY_COUNT|CRM_WRITES|PG_WRITES)=[A-Z0-9]+",line):
            k,v=line.split("=",1)
            status[k]=v
            print(line,flush=True)
    need(p.returncode==0 and status.get("GEO_CRM_WRITES")=="0"
         and status.get("GEO_PG_WRITES")=="0", "GEO_READONLY_CANARY_FAILED")
    return int(status.get("GEO_COUNT_MOSCOW","0")) > 0

def rollback(name,backup):
    run(["docker","rm","-f",name],check=False)
    r=run(["docker","rename",backup,name],check=False)
    if r.returncode==0:
        run(["docker","start",name],check=False)
    return r.returncode==0

def main(argv):
    need(len(argv)==7, "RELEASE_ARGS_INVALID")
    run_id, script_sha, *file_shas = argv
    need(re.fullmatch(r"[0-9]{7,16}",run_id) is not None
         and re.fullmatch(r"[0-9a-f]{64}",script_sha) is not None
         and len(file_shas)==5
         and all(re.fullmatch(r"[0-9a-f]{64}",x) for x in file_shas),
         "RELEASE_CHECKSUM_ARGUMENTS_INVALID")
    need(os.geteuid()!=0,"NONROOT_REQUIRED")
    stage=Path.home()/f"serviceit/staging/b2b-writer-geo-{run_id}"
    need(stage.is_dir() and not stage.is_symlink(),"RELEASE_STAGE_INVALID")
    need(sha(stage/"promote.py")==script_sha,"RELEASE_SCRIPT_HASH_DRIFT")
    for name,want in zip(NAMES,file_shas):
        source=stage/name
        need(source.is_file() and not source.is_symlink() and sha(source)==want,
             "RELEASE_FILE_HASH_DRIFT")

    running(CORE)
    running(RECEIVER)
    old=running(WRITER)
    need(old["Config"]["User"]=="10001:10001"
         and old["HostConfig"]["NetworkMode"]=="host"
         and old["HostConfig"]["ReadonlyRootfs"] is True,
         "OLD_WRITER_ISOLATION_DRIFT")
    mounts={m["Destination"] for m in old.get("Mounts",[])}
    need({"/run/secrets/serviceit_pg_password",
          "/run/secrets/serviceit_bitrix_webhook"} <= mounts,
         "OLD_WRITER_SECRET_MOUNTS_MISSING")
    env=env_of(old)
    image_id=old["Image"]
    need(re.fullmatch(r"sha256:[a-f0-9]{64}",image_id),"OLD_IMAGE_ID_INVALID")
    need(output(["docker","image","inspect","-f","{{.Id}}",image_id])==image_id,
         "OLD_IMAGE_NOT_PRESENT")
    new_image="serviceit-b2b-writer:moscow-"+run_id
    lines=["FROM "+image_id]
    for name in NAMES:
        lines.append("COPY --chmod=0644 "+name+" /app/leadpilot/"+name)
    (stage/"Dockerfile.overlay").write_text("\n".join(lines)+"\n")
    print("B2B_GEO_OLD_IMAGE_VALIDATED=YES",flush=True)
    run(["docker","build","--network","none","--pull=false",
         "--tag",new_image,"-f","Dockerfile.overlay","."],cwd=stage,timeout=300)
    smoke(new_image)
    print("B2B_GEO_NEW_IMAGE_SMOKE=YES",flush=True)
    allow=canary(new_image,env,WRITER)
    print("B2B_GEO_MOSCOW_PROOF="+("YES" if allow else "NO"),flush=True)

    # A true Moscow-only mode is permitted only when the live VPS can
    # actually read an independently verified Moscow performance location.
    # A blocked public source must NOT silently discard future real tenders.
    env["SERVICEIT_B2B_MOSCOW_ONLY_ENABLED"]="YES" if allow else "NO"
    backlog=run(["docker","exec",WRITER,"python","-B","-c",
                 "import os;"
                 "from leadpilot.serviceit_vps_pg_queue import db;"
                 "c=db(dict(os.environ));"
                 "x=c.__enter__();"
                 "print(x.execute(\"SELECT count(*) FROM serviceit_vps_b2b_inbox "
                 "WHERE state='PENDING'\").fetchone()[0]);"
                 "c.__exit__(None,None,None)"],timeout=40,check=False)
    need(backlog.returncode==0 and backlog.stdout.strip()=="0",
         "B2B_PENDING_QUEUE_NONEMPTY_NO_SWAP")
    print("B2B_GEO_INGRESS_BACKLOG_EMPTY=YES",flush=True)

    backup=WRITER.replace("-1","-rollback-"+run_id)
    need(run(["docker","inspect",backup],check=False).returncode!=0,
         "B2B_ROLLBACK_NAME_CONFLICT")
    need(inspect(WRITER)["Id"]==old["Id"],"B2B_WRITER_CHANGED_DURING_STAGE")
    run(["docker","stop","--time","20",WRITER],timeout=30)
    run(["docker","rename",WRITER,backup])
    try:
        args=["docker","run","-d","--name",WRITER,"--restart","unless-stopped",
              "--network","host","--user","10001:10001","--read-only",
              "--init","--tmpfs","/tmp:size=32m,mode=1777",
              "--security-opt","no-new-privileges","--cap-drop","ALL",
              "--pids-limit","128","--memory","400m",
              "--volumes-from",backup]
        for key in sorted(env):
            args+=["--env",key+"="+env[key]]
        args+=["--entrypoint","python",new_image,"-m",
               "leadpilot.serviceit_vps_b2b_bridge"]
        run(args,timeout=50)
        time.sleep(4)
        now=running(WRITER)
        need(now["Image"]==output(["docker","image","inspect","-f","{{.Id}}",new_image]),
             "B2B_NEW_IMAGE_DRIFT")
        verify=run(["docker","exec",WRITER,"python","-B","-c",
                    "import os;"
                    "from leadpilot.serviceit_vps_b2b_bridge import enabled,INTERVAL_SECONDS;"
                    "assert enabled(dict(os.environ),writer=True);"
                    "assert INTERVAL_SECONDS==300;"
                    "print('WRITER_RESTART_PASS')"],timeout=30)
        need(verify.stdout.strip()=="WRITER_RESTART_PASS",
             "B2B_NEW_WRITER_POSTREAD_FAILED")
        running(RECEIVER)
        running(CORE)
    except Exception:
        restored=rollback(WRITER,backup)
        print("B2B_GEO_ROLLBACK="+("YES" if restored else "FAILED"),flush=True)
        raise
    print("B2B_GEO_WRITER_DEPLOYED=YES",flush=True)
    print("B2B_GEO_MOSCOW_ONLY_ENABLED="+("YES" if allow else "NO"),flush=True)
    print("B2B_GEO_RECEIVER_UNTOUCHED=YES",flush=True)
    print("B2B_GEO_CORE_UNTOUCHED=YES",flush=True)
    print("B2B_GEO_CRM_WRITES_BY_INSTALLER=NO",flush=True)

if __name__=="__main__":
    try:
        main(sys.argv[1:])
    except Blocked as e:
        print("B2B_GEO_DEPLOY_BLOCKED="+str(e),flush=True)
        raise SystemExit(2) from None
    except Exception as e:
        print("B2B_GEO_DEPLOY_ERROR="+type(e).__name__.upper(),flush=True)
        raise SystemExit(2) from None
