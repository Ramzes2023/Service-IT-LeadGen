"""Activate the already deployed Tavily adapter on the existing coordinator."""
import fcntl
import os
import subprocess
import time

import serviceit_vps_b2b_v37_quality_release as runtime

OVERLAY = runtime.CONFIG / "native-tavily-search.compose.yml"
SECRET = "/etc/serviceit/secrets/tavily_api_key"
SECRET_NAME = "serviceit_tavily_search_key"
TARGET = "/run/secrets/" + SECRET_NAME
FLAGS = {
    "SERVICEIT_VPS_B2B_TAVILY_SEARCH_ENABLED": "YES",
    "SERVICEIT_TAVILY_SEARCH_API_KEY_FILE": TARGET,
}
SPEC = f"""services:
  coordinator:
    environment:
      SERVICEIT_VPS_B2B_TAVILY_SEARCH_ENABLED: "YES"
      SERVICEIT_TAVILY_SEARCH_API_KEY_FILE: {TARGET}
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
path = Path('/run/secrets/serviceit_tavily_search_key')
meta = path.stat()
assert stat.S_IMODE(meta.st_mode) == 0o400
assert (meta.st_uid, meta.st_gid) == (10001, 10001)
assert 20 <= meta.st_size <= 221
assert re.fullmatch(r'tvly-[!-~]{15,215}', path.read_text().strip())
print('TAVILY_KEY_FILE_OK')
"""
PROBE = """
import os
import httpx
from urllib.parse import urlsplit
from leadpilot.serviceit_vps_pg_queue import secret_file
from leadpilot.serviceit_b2b_tavily_fallback_v44 import _search_once, permitted
from leadpilot.serviceit_b2b_public_mirrors import _from_page_or_public_browser
assert permitted(os.environ)
key = secret_file(os.environ, 'SERVICEIT_TAVILY_SEARCH_API_KEY')
def report_http(response):
    if response.request.url.host == 'api.tavily.com':
        print('TAVILY_PROBE_HTTP=' + str(response.status_code), flush=True)
with httpx.Client(trust_env=False, follow_redirects=False, timeout=18,
                  event_hooks={'response': [report_http]}) as client:
    status, urls = _search_once(client, key, '4677404')
    print('TAVILY_PROBE_STATUS=' + status, flush=True)
    assert status in ('CANDIDATES', 'NO_RESULTS')
    verified = 0
    for url in urls:
        try:
            page = _from_page_or_public_browser(
                client, url, '4677404', 'Приобретение лицензий 1С', urlsplit(url).hostname
            )
            verified += bool(page)
        except (ValueError, httpx.HTTPError):
            pass
    print('TAVILY_PROBE_CANDIDATES=' + str(len(urls)), flush=True)
    print('TAVILY_PROBE_VERIFIED_PAGES=' + str(verified), flush=True)
"""


def run():
    with runtime.LOCK.open("a") as lock:
        os.chmod(runtime.LOCK, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise runtime.ReleaseBlocked("COORDINATOR_RELEASE_BUSY") from None
        paths = runtime.inspect_compose_paths()
        image = runtime.image(runtime.CORE)
        runtime.require(runtime.healthy(runtime.CORE), "COORDINATOR_NOT_HEALTHY")
        if OVERLAY in paths:
            env = runtime.env_of(runtime.CORE)
            runtime.require(all(env.get(k) == v for k, v in FLAGS.items()),
                            "EXISTING_TAVILY_OVERLAY_CONFLICT")
            runtime.require(runtime.output(["docker", "exec", runtime.CORE, "python", "-B", "-c", CHECK])
                            == "TAVILY_KEY_FILE_OK", "MOUNTED_TAVILY_SECRET_UNVERIFIED")
            print("TAVILY_ACTIVATION=ALREADY_LIVE", flush=True)
            return
        key_check = runtime.call([
            "docker", "run", "--rm", "--network", "none", "--pull", "never",
            "--read-only", "--user", "10001:10001", "--cap-drop", "ALL",
            "--mount", f"type=bind,src={SECRET},dst={TARGET},readonly",
            "--entrypoint", "python", image, "-B", "-c", CHECK,
        ], timeout=30, check=False)
        runtime.require(key_check.returncode == 0 and key_check.stdout.strip() == "TAVILY_KEY_FILE_OK",
                        "TAVILY_KEY_MISSING_OR_INVALID")
        runtime.require(not OVERLAY.exists() and not OVERLAY.is_symlink(), "TAVILY_OVERLAY_CONFLICT")
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
                            "TAVILY_MOUNT_SOURCE_UNVERIFIED")
            probe = runtime.call(runtime.compose(updated, "run", "--rm", "--no-deps", "-T",
                                                  "coordinator", "python", "-B", "-c", PROBE),
                                 timeout=130, check=False)
            for line in probe.stdout.splitlines():
                if line in ("TAVILY_PROBE_STATUS=CANDIDATES", "TAVILY_PROBE_STATUS=NO_RESULTS",
                            "TAVILY_PROBE_STATUS=PROVIDER_FAILED", "TAVILY_PROBE_STATUS=PROVIDER_LIMITED"):
                    print(line, flush=True)
                elif line.startswith(("TAVILY_PROBE_CANDIDATES=", "TAVILY_PROBE_VERIFIED_PAGES=")):
                    _, _, count = line.partition("=")
                    if count.isdigit() and len(count) == 1:
                        print(line, flush=True)
                elif line.startswith("TAVILY_PROBE_HTTP=") and line.removeprefix("TAVILY_PROBE_HTTP=").isdigit():
                    print(line, flush=True)
            runtime.require(probe.returncode == 0, "TAVILY_API_CHECK_FAILED")
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
                            "TAVILY_LIVE_CHECK_FAILED")
            runtime.require(runtime.output(["docker", "exec", runtime.CORE, "python", "-B", "-c", CHECK])
                            == "TAVILY_KEY_FILE_OK", "MOUNTED_TAVILY_SECRET_UNVERIFIED")
        except Exception:
            if started:
                try:
                    runtime.call(runtime.compose(paths, "up", "-d", "--no-deps", "--no-build",
                                                  "--pull", "never", "--force-recreate", "coordinator"),
                                 timeout=180)
                    print("TAVILY_ROLLBACK=COORDINATOR_RESTORED", flush=True)
                except (OSError, subprocess.SubprocessError, runtime.ReleaseBlocked):
                    print("TAVILY_ROLLBACK=UNVERIFIED", flush=True)
                    raise
            OVERLAY.unlink()
            raise
    print("TAVILY_ACTIVATION=LIVE", flush=True)


if __name__ == "__main__":
    try:
        run()
    except runtime.ReleaseBlocked as error:
        print("TAVILY_BLOCKED=" + str(error), flush=True)
        raise SystemExit(2) from None
    except Exception:
        print("TAVILY_BLOCKED=UNVERIFIED", flush=True)
        raise SystemExit(2) from None
