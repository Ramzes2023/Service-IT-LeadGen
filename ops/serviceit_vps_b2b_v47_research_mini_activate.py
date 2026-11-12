"""Guarded Tavily Research MINI activation; no paid probe or CRM mutation.

Runs only after V47 code-only release and GET /usage proves the API is reachable
from the VPS and >=110 INCLUDED credits remain. No Research task is launched
by installer. Existing coordinator and Compose services are preserved.
"""
import fcntl
import os
import subprocess
import time
from datetime import datetime, timezone

import serviceit_vps_b2b_v37_quality_release as runtime

OVERLAY = runtime.CONFIG / "native-tavily-research-pro.compose.yml"
SECRET = "/etc/serviceit/secrets/tavily_api_key"
SECRET_NAME = "serviceit_tavily_research_mini_key"
TARGET = "/run/secrets/" + SECRET_NAME
ACTIVE = "SERVICEIT_VPS_B2B_TAVILY_RESEARCH_MINI_ACTIVE_SINCE"
STAMP = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z")
FLAGS = {
    ACTIVE: STAMP,
    "SERVICEIT_VPS_B2B_TAVILY_RESEARCH_MINI_ENABLED": "YES",
    "SERVICEIT_TAVILY_SEARCH_API_KEY_FILE": TARGET,
}
SPEC = f"""services:
  coordinator:
    environment:
      SERVICEIT_VPS_B2B_TAVILY_RESEARCH_MINI_ENABLED: "YES"
      SERVICEIT_TAVILY_SEARCH_API_KEY_FILE: {TARGET}
      SERVICEIT_VPS_B2B_TAVILY_RESEARCH_MINI_ACTIVE_SINCE: "{STAMP}"
    secrets:
      - {SECRET_NAME}
secrets:
  {SECRET_NAME}:
    file: {SECRET}
"""
CHECK = """
from pathlib import Path
import re
import stat
path = Path('/run/secrets/serviceit_tavily_research_mini_key')
meta = path.stat()
assert stat.S_IMODE(meta.st_mode) == 0o400
assert (meta.st_uid, meta.st_gid) == (10001, 10001)
assert 20 <= meta.st_size <= 221
assert re.fullmatch(r'tvly-[!-~]{15,215}', path.read_text().strip())
print('RESEARCH_MINI_KEY_FILE_OK')
"""
PROBE = """
import os
import httpx
from leadpilot.serviceit_vps_pg_queue import secret_file
from leadpilot.serviceit_b2b_tavily_research_mini_v47 import approved,MAX_MINI_CREDITS
assert approved(os.environ)
key=secret_file(os.environ,'SERVICEIT_TAVILY_SEARCH_API_KEY')
with httpx.Client(trust_env=False,follow_redirects=False,timeout=15) as client:
    response=client.get('https://api.tavily.com/usage',
                        headers={'Authorization':'Bearer '+key,
                                 'Accept':'application/json'})
    print('RESEARCH_MINI_USAGE_HTTP='+str(response.status_code),flush=True)
    assert response.status_code==200
    assert 'application/json' in response.headers.get('content-type','')
    data=response.json()
    account=data.get('account')
    assert isinstance(account,dict)
    limit,used=account.get('plan_limit'),account.get('plan_usage')
    assert type(limit) is int and type(used) is int and 0<=used<=limit
    assert limit-used >= MAX_MINI_CREDITS
    print('RESEARCH_MINI_INCLUDED_CREDITS_READY=YES',flush=True)
"""


def run():
    with runtime.LOCK.open("a") as lock:
        os.chmod(runtime.LOCK, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise runtime.ReleaseBlocked("COORDINATOR_RELEASE_BUSY") from None
        paths = runtime.inspect_compose_paths()
        runtime.require(
            runtime.env_of(runtime.CORE).get("SERVICEIT_VPS_B2B_TAVILY_RESEARCH_PRO_ENABLED") != "YES",
            "OTHER_TAVILY_PRO_MUST_BE_DISABLED")
        image = runtime.image(runtime.CORE)
        runtime.require(runtime.healthy(runtime.CORE), "COORDINATOR_NOT_HEALTHY")
        if OVERLAY in paths:
            env = runtime.env_of(runtime.CORE)
            # Existing activation timestamp is immutable: never move cutoff.
            live_flags = {**FLAGS, ACTIVE: env.get(ACTIVE)}
            runtime.require(all(env.get(k) == v for k, v in live_flags.items()) and
                            isinstance(env.get(ACTIVE), str) and
                            env.get(ACTIVE, "").endswith("Z"),
                            "EXISTING_RESEARCH_MINI_OVERLAY_CONFLICT")
            runtime.require(runtime.output(["docker", "exec", runtime.CORE, "python", "-B", "-c", CHECK])
                            == "RESEARCH_MINI_KEY_FILE_OK", "MOUNTED_RESEARCH_MINI_SECRET_UNVERIFIED")
            print("RESEARCH_MINI_ACTIVATION=ALREADY_LIVE", flush=True)
            return
        key_check = runtime.call([
            "docker", "run", "--rm", "--network", "none", "--pull", "never",
            "--read-only", "--user", "10001:10001", "--cap-drop", "ALL",
            "--mount", f"type=bind,src={SECRET},dst={TARGET},readonly",
            "--entrypoint", "python", image, "-B", "-c", CHECK,
        ], timeout=30, check=False)
        runtime.require(key_check.returncode == 0 and key_check.stdout.strip() == "RESEARCH_MINI_KEY_FILE_OK",
                        "RESEARCH_MINI_KEY_MISSING_OR_INVALID")
        runtime.require(not OVERLAY.exists() and not OVERLAY.is_symlink(), "RESEARCH_MINI_OVERLAY_CONFLICT")
        before = runtime.compose_json(paths)
        runtime.atomic_write(OVERLAY, SPEC)
        updated = [*paths, OVERLAY]
        started = False
        try:
            after = runtime.compose_json(updated)
            original = before["services"]["coordinator"]
            target = after["services"]["coordinator"]
            extra = [entry for entry in target["secrets"] if entry.get("source") == SECRET_NAME]
            known_secrets = [entry for entry in target["secrets"] if entry.get("source") != SECRET_NAME]
            allowed = {**original, "environment": {**original["environment"], **FLAGS}}
            runtime.require({**target, "secrets": known_secrets} == allowed
                            and target["image"] == image and len(extra) == 1
                            and extra[0].get("target", TARGET) in (TARGET, SECRET_NAME),
                            "COORDINATOR_OVERLAY_DIFF_UNEXPECTED")
            runtime.require({**after["services"], "coordinator": original} == before["services"],
                            "OTHER_SERVICE_CONFIG_CHANGED")
            runtime.require(after["secrets"][SECRET_NAME]["file"] == SECRET,
                            "RESEARCH_MINI_MOUNT_SOURCE_UNVERIFIED")
            probe = runtime.call(runtime.compose(updated, "run", "--rm", "--no-deps", "-T",
                                                  "coordinator", "python", "-B", "-c", PROBE),
                                 timeout=130, check=False)
            for line in probe.stdout.splitlines():
                if line in ('RESEARCH_MINI_USAGE_HTTP=200',
                            'RESEARCH_MINI_INCLUDED_CREDITS_READY=YES'):
                    print(line,flush=True)
                elif line.startswith('RESEARCH_MINI_USAGE_HTTP='):
                    v=line.partition('=')[2]
                    if v.isdigit() and len(v)==3:
                        print(line,flush=True)
            runtime.require(probe.returncode == 0, "RESEARCH_MINI_API_CHECK_FAILED")
            runtime.require(runtime.pending_source_writes() == 0, "CRM_WRITE_IN_PROGRESS")
            started = True
            runtime.call(runtime.compose(updated, "up", "-d", "--no-deps", "--no-build",
                                         "--pull", "never", "--force-recreate", "coordinator"),
                         timeout=180)
            for _ in range(32):
                if runtime.healthy(runtime.CORE):
                    break
                time.sleep(3)
            env = runtime.env_of(runtime.CORE)
            runtime.require(runtime.healthy(runtime.CORE) and runtime.image(runtime.CORE) == image
                            and all(env.get(k) == v for k, v in {**runtime.GATES, **FLAGS}.items()),
                            "RESEARCH_MINI_LIVE_CHECK_FAILED")
            runtime.require(runtime.output(["docker", "exec", runtime.CORE, "python", "-B", "-c", CHECK])
                            == "RESEARCH_MINI_KEY_FILE_OK", "MOUNTED_RESEARCH_MINI_SECRET_UNVERIFIED")
        except Exception:
            if started:
                try:
                    runtime.call(runtime.compose(paths, "up", "-d", "--no-deps", "--no-build",
                                                  "--pull", "never", "--force-recreate", "coordinator"),
                                 timeout=180)
                    print("RESEARCH_MINI_ROLLBACK=COORDINATOR_RESTORED", flush=True)
                except (OSError, subprocess.SubprocessError, runtime.ReleaseBlocked):
                    print("RESEARCH_MINI_ROLLBACK=UNVERIFIED", flush=True)
                    raise
            OVERLAY.unlink()
            raise
    print("RESEARCH_MINI_ACTIVATION=LIVE", flush=True)


if __name__ == "__main__":
    try:
        run()
    except runtime.ReleaseBlocked as error:
        print("RESEARCH_MINI_BLOCKED=" + str(error), flush=True)
        raise SystemExit(2) from None
    except Exception:
        print("RESEARCH_MINI_BLOCKED=UNVERIFIED", flush=True)
        raise SystemExit(2) from None
