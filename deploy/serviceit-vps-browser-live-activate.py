#!/usr/bin/env python3
"""Owner-approved, guarded add-on to existing LIVE VPS (no second CRM writer).

Run ONLY through the main-branch owner-only workflow after pinned archive
verification. Browser + HTTPS egress have NO CRM/PG/model secrets. If checks
fail, restore the original coordinator image and private LIVE configuration.
Do not run any forced --once AI job or touch historic Bitrix cards.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import urlopen

APPROVAL = "ACTIVATE_ISOLATED_CHROMIUM_PUBLIC_RESEARCH_1062_21"
LIVE_GATES = {
    "SERVICEIT_VPS_WRITER_MODE": "LIVE",
    "SERVICEIT_VPS_CUTOVER_AUTHORIZED": "YES",
    "SERVICEIT_VPS_OLD_GH_WRITERS_DISABLED_ACK": "YES",
    "SERVICEIT_VPS_POLL_ENABLED": "YES",
    "SERVICEIT_VPS_INTAKE_ENABLED": "NO",
    "SERVICEIT_AI_PROVIDER": "routerai",
    "SERVICEIT_ROUTERAI_PRIVACY_APPROVED": "YES_1062_21",
}
HOST = re.compile(r"(?:[a-z0-9-]{1,63}\.)+[a-z]{2,20}\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
BROWSER_PROJECT = "serviceit-public-browser-vps"
CORE_PROJECT = "serviceit-leadgen-vps"
BROWSER_NAME = "serviceit-public-browser-vps-browser-1"
EGRESS_NAME = "serviceit-public-browser-vps-egress-1"
CORE_NAME = "serviceit-leadgen-vps-coordinator-1"
CORE_SERVICES = [
    CORE_NAME,
    "serviceit-leadgen-vps-auxiliaries-1",
    "serviceit-leadgen-vps-business-1",
    "serviceit-leadgen-vps-observer-1",
]


class Blocked(RuntimeError):
    pass


def cmd(*args: str, env: dict | None = None, timeout: int = 120,
        check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, check=check,
                          timeout=timeout, env=env)


def config_read(path: Path) -> tuple[str, dict]:
    if not path.is_file() or path.is_symlink():
        raise Blocked("PRIVATE_CONFIG_NOT_FOUND")
    st = path.stat()
    if st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) & 0o077:
        raise Blocked("PRIVATE_CONFIG_OWNER_OR_MODE_INVALID")
    raw = path.read_text("utf8")
    values = {}
    for line in raw.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        if "=" not in line:
            raise Blocked("PRIVATE_CONFIG_PARSE_FAILED")
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in values:
            raise Blocked("PRIVATE_CONFIG_KEY_INVALID")
        values[key] = value
    return raw, values


def write_private(path: Path, content: str):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".serviceit-browser-")
    try:
        with os.fdopen(fd, "w", encoding="utf8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def healthy(container: str) -> bool:
    try:
        result = cmd("docker", "inspect", "-f",
                     "{{.State.Running}}:{{.State.Health.Status}}",
                     container).stdout.strip()
        return result == "true:healthy"
    except (OSError, subprocess.CalledProcessError):
        return False


def check_core() -> str:
    if not all(healthy(c) for c in CORE_SERVICES):
        raise Blocked("EXISTING_CORE_NOT_FOUR_HEALTHY")
    env = cmd("docker", "exec", CORE_NAME, "printenv",
              "SERVICEIT_VPS_WRITER_MODE", "SERVICEIT_VPS_POLL_ENABLED")
    if env.stdout.splitlines() != ["LIVE", "YES"]:
        raise Blocked("CORE_NOT_LIVE_POLLING")
    return cmd("docker", "inspect", "-f", "{{.Image}}", CORE_NAME).stdout.strip()


def approved_hosts(values: dict) -> str:
    text = values.get("SERVICEIT_PRELIMINARY_PUBLIC_HOSTS", "")
    hosts = [h.strip().lower() for h in text.split(",") if h.strip()]
    if (not 1 <= len(hosts) <= 20 or any(not HOST.fullmatch(h)
            or h.endswith((".test", ".invalid", ".local", ".localhost"))
            for h in hosts)):
        raise Blocked("BROWSER_SOURCE_HOST_ALLOWLIST_MISSING")
    return ",".join(dict.fromkeys(hosts))


def validate_smoke(url: str, hosts: str):
    parsed = urlsplit(url)
    name = (parsed.hostname or "").lower()
    if (parsed.scheme != "https" or parsed.username or parsed.password
            or parsed.port not in (None, 443) or parsed.fragment
            or not any(name == h or name.endswith("." + h)
                       for h in hosts.split(","))):
        raise Blocked("BROWSER_SMOKE_URL_NOT_APPROVED")


def compose_browser(path: Path, environment: dict, *args: str,
                    timeout: int = 180):
    return cmd("docker", "compose", "--profile", "browser", "-f",
               str(path), *args, env=environment, timeout=timeout)


def compose_core(path: Path, config: Path, *args: str,
                 timeout: int = 180):
    return cmd("docker", "compose", "--env-file", str(config),
               "-f", str(path), *args, timeout=timeout)


def smoke_browser():
    if not healthy(BROWSER_NAME):
        raise Blocked("BROWSER_BRIDGE_UNHEALTHY")
    p = cmd("docker", "inspect", "-f",
            "{{range .NetworkSettings.Networks}}{{.NetworkID}} {{end}}",
            BROWSER_NAME)
    # Browser must be attached to EXACTLY ONE network: the internal-only one.
    if len(p.stdout.split()) != 1:
        raise Blocked("BROWSER_NETWORK_COUNT_INVALID")
    networks = json.loads(cmd("docker", "inspect", BROWSER_NAME,
                              "--format", "{{json .NetworkSettings.Networks}}").stdout)
    if len(networks) != 1 or not next(iter(networks)).endswith("_browser_internal"):
        raise Blocked("BROWSER_NOT_INTERNAL_ONLY")
    netid = next(iter(networks.values()))["NetworkID"]
    details = json.loads(cmd("docker", "network", "inspect", netid).stdout)
    if len(details) != 1 or details[0].get("Internal") is not True:
        raise Blocked("BROWSER_EGRESS_NOT_ISOLATED")

    # Core/PG credentials are neither mounted nor passed to this sidecar.
    env_names = cmd("docker", "inspect", BROWSER_NAME, "--format",
                    "{{json .Config.Env}}").stdout
    for forbidden in ("BITRIX", "POSTGRES", "ROUTERAI", "GITHUB_TOKEN", "SERVICEIT_PG"):
        if forbidden in env_names.upper():
            raise Blocked("BROWSER_SECRET_BOUNDARY_FAILED")

    # Offline actual sandbox launch with JS evaluation, no website visit.
    script = """
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, chromium_sandbox=True,
        args=["--disable-background-networking"])
    page = browser.new_page()
    page.set_content('<div id="ok"></div><script>document.getElementById("ok").innerText="JS_OK"</script>')
    assert page.locator("#ok").inner_text() == "JS_OK"
    browser.close()
"""
    cmd("docker", "exec", BROWSER_NAME, "python", "-c", script, timeout=45)

    # A browser with an internal-only bridge must NEVER reach global IPv4
    # directly; only its dedicated checked HTTPS CONNECT proxy can do so.
    network_test = """
import socket, sys
try:
    s=socket.create_connection(("1.1.1.1", 443), timeout=2)
except OSError:
    sys.exit(0)
else:
    s.close()
    sys.exit(2)
"""
    result = cmd("docker", "exec", BROWSER_NAME, "python", "-c", network_test,
                 timeout=15, check=False)
    if result.returncode != 0:
        raise Blocked("BROWSER_DIRECT_EGRESS_NOT_BLOCKED")
    with urlopen("http://127.0.0.1:18785/health", timeout=4) as response:
        if b"BROWSER_BRIDGE_READY" not in response.read(1000):
            raise Blocked("BROWSER_HOST_LOOPBACK_BRIDGE_FAILED")


def activate(sha: str, approval: str, smoke_url: str):
    if not SHA.fullmatch(sha) or approval != APPROVAL:
        raise Blocked("OWNER_BROWSER_APPROVAL_MISSING")
    if os.geteuid() == 0:
        raise Blocked("NONROOT_DEPLOY_REQUIRED")
    base = Path.home() / "serviceit"
    release = base / "releases" / sha
    config = base / "config" / "vps.env"
    browser_yaml = release / "deploy/serviceit-vps-browser.compose.yml"
    core_yaml = release / "deploy/serviceit-vps.compose.yml"
    if not browser_yaml.is_file() or not core_yaml.is_file():
        raise Blocked("PINNED_BROWSER_RELEASE_NOT_READY")
    original, values = config_read(config)
    if any(values.get(k) != v for k, v in LIVE_GATES.items()):
        raise Blocked("EXISTING_CORE_LIVE_GATES_INVALID")
    if (values.get("SERVICEIT_VPS_BROWSER_RESEARCH_ENABLED", "NO") != "NO"
            or values.get("SERVICEIT_VPS_BROWSER_PROXY_ISOLATION_VERIFIED", "NO") != "NO"):
        raise Blocked("BROWSER_ALREADY_ACTIVE_OR_CONFIG_UNVERIFIED")
    hosts = approved_hosts(values)
    validate_smoke(smoke_url, hosts)
    old_image = check_core()
    browser_env = dict(os.environ)
    browser_env["SERVICEIT_VPS_BROWSER_APPROVED_HOSTS"] = hosts
    seccomp = release / "deploy/playwright-chromium-sandbox-seccomp.json"
    if not seccomp.is_file() or seccomp.is_symlink():
        raise Blocked("OFFICIAL_CHROMIUM_SECCOMP_PROFILE_MISSING")
    browser_env["SERVICEIT_VPS_BROWSER_SECCOMP_PROFILE"] = str(seccomp)

    # Release and config have been validated; install browser first, while
    # four current LIVE workers keep running unchanged.
    config_changed = False
    core_restarted = False
    browser_started = False
    phase = "BROWSER_IMAGE_BUILD"
    try:
        cmd("docker", "build", "--file", str(release / "Dockerfile.serviceit-vps-browser"),
            "--tag", "serviceit-vps-public-browser:staged", str(release),
            timeout=5400)
        phase = "BROWSER_COMPOSE_CONFIG"
        compose_browser(browser_yaml, browser_env, "config", "--quiet")
        phase = "BROWSER_CONTAINERS_START"
        browser_started = True  # Roll back even if Compose starts just one container.
        compose_browser(browser_yaml, browser_env, "up", "-d", "--no-build",
                        "egress", "browser", timeout=240)
        for _ in range(15):
            if healthy(BROWSER_NAME):
                break
            time.sleep(3)
        phase = "BROWSER_SANDBOX_AND_NETWORK_SMOKE"
        smoke_browser()
        # Real approved page must be rendered, with robots/TLS/sandbox rules.
        # --url logs STATUS, HASH and COUNTS only, never public page text.
        phase = "BROWSER_PUBLIC_PAGE_SMOKE"
        result = cmd("docker", "exec", BROWSER_NAME, "python", "-m",
                     "leadpilot.serviceit_vps_public_browser", "--url",
                     smoke_url, timeout=50, check=False)
        if result.returncode != 0 or "BROWSER_RENDERED_PUBLIC_PAGE_UNVERIFIED" not in result.stdout:
            raise Blocked("BROWSER_REAL_PUBLIC_RENDER_UNVERIFIED")
        print("BROWSER_PUBLIC_RENDER_SMOKE=PASS", flush=True)

        # Native coordinator only. The other three LIVE containers untouched.
        phase = "CORE_COORDINATOR_BUILD"
        compose_core(core_yaml, config, "build", "coordinator", timeout=1000)
        new_config = original
        if new_config and not new_config.endswith("\n"):
            new_config += "\n"
        new_config += ("SERVICEIT_VPS_BROWSER_RESEARCH_ENABLED=YES\n"
                       "SERVICEIT_VPS_BROWSER_PROXY_ISOLATION_VERIFIED=YES\n")
        phase = "CORE_CONFIG_WRITE"
        write_private(config, new_config)
        config_changed = True
        phase = "CORE_COORDINATOR_RESTART"
        compose_core(core_yaml, config, "up", "-d", "--no-deps",
                     "--no-build", "--force-recreate", "coordinator", timeout=240)
        core_restarted = True
        phase = "CORE_FOUR_HEALTHY_VERIFY"
        for _ in range(20):
            if healthy(CORE_NAME):
                break
            time.sleep(3)
        if not healthy(CORE_NAME) or not all(healthy(c) for c in CORE_SERVICES):
            raise Blocked("LIVE_COORDINATOR_RESTART_NOT_HEALTHY")
        flags = cmd("docker", "exec", CORE_NAME, "printenv",
                    "SERVICEIT_VPS_BROWSER_RESEARCH_ENABLED",
                    "SERVICEIT_VPS_BROWSER_PROXY_ISOLATION_VERIFIED",
                    "SERVICEIT_VPS_WRITER_MODE",
                    "SERVICEIT_VPS_POLL_ENABLED").stdout.splitlines()
        if flags != ["YES", "YES", "LIVE", "YES"]:
            raise Blocked("LIVE_BROWSER_FLAGS_NOT_VERIFIED")
        print("SERVICEIT_BROWSER_AI_BRIDGE=LIVE_CONNECTED", flush=True)
        print("CORE_FOUR_HEALTHY=PASS", flush=True)
        print("BITRIX_SCOPE=1062_21;MANAGER_OLGA=605;SOURCES=UNCHANGED", flush=True)
        print("REAL_NEW_CARD_E2E=NOT_YET_VERIFIED", flush=True)
    except Exception as error:
        print("BROWSER_ACTIVATION=BLOCKED_ROLLBACK", flush=True)
        print("BLOCKED_PHASE=" + phase, flush=True)
        if isinstance(error, subprocess.CalledProcessError):
            print("BLOCKED_COMMAND_EXIT=" + str(error.returncode), flush=True)
        if config_changed:
            write_private(config, original)
        # Restore the exact prior coordinator digest even if the image tag
        # was advanced by the offline Docker build.
        try:
            cmd("docker", "tag", old_image, "serviceit-leadgen-vps-coordinator:latest")
            if config_changed:
                compose_core(core_yaml, config, "up", "-d", "--no-deps",
                             "--no-build", "--force-recreate", "coordinator",
                             timeout=240)
        except Exception:
            print("CORE_ROLLBACK=NEEDS_OPERATOR_REVIEW", flush=True)
        if browser_started:
            try:
                compose_browser(browser_yaml, browser_env, "down", timeout=120)
            except Exception:
                print("BROWSER_ROLLBACK=NEEDS_OPERATOR_REVIEW", flush=True)
        raise


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sha", required=True)
    p.add_argument("--approval", required=True)
    p.add_argument("--smoke-url", required=True)
    args = p.parse_args()
    try:
        activate(args.sha, args.approval, args.smoke_url)
    except Exception as ex:
        print("SERVICEIT_BROWSER_DEPLOY=NOT_CONFIRMED", flush=True)
        print("FAILURE_CLASS=" + type(ex).__name__, flush=True)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
