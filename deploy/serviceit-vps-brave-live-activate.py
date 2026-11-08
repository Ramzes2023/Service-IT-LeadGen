#!/usr/bin/env python3
"""Owner-only, guarded VPS Brave Search integration into current LIVE coordinator.

Prerequisite: root installs Brave key into /etc/serviceit/secrets/brave_api_key
using the existing interactive installer. Does not read/reveal the token itself.
No source onboarding, CRM writes, existing-card backfill or new background jobs.
Tests generic Brave Web + LLM Context (~2 billable API requests) BEFORE one
coordinator-only recreate. On failure, restores prior image and Compose.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

APPROVAL = "ACTIVATE_VPS_BRAVE_SEARCH_CONTEXT_1062_21"
SHA = re.compile(r"[0-9a-f]{40}\Z")
CORE = "serviceit-leadgen-vps-coordinator-1"
SERVICES = (
    CORE,
    "serviceit-leadgen-vps-auxiliaries-1",
    "serviceit-leadgen-vps-business-1",
    "serviceit-leadgen-vps-observer-1",
)
BROWSER = "serviceit-public-browser-vps-browser-1"
EGRESS = "serviceit-public-browser-vps-egress-1"
GATES = {
    "SERVICEIT_VPS_WRITER_MODE": "LIVE",
    "SERVICEIT_VPS_POLL_ENABLED": "YES",
    "SERVICEIT_VPS_CUTOVER_AUTHORIZED": "YES",
    "SERVICEIT_VPS_OLD_GH_WRITERS_DISABLED_ACK": "YES",
    "SERVICEIT_VPS_INTAKE_ENABLED": "NO",
    "SERVICEIT_AI_PROVIDER": "routerai",
    "SERVICEIT_ROUTERAI_PRIVACY_APPROVED": "YES_1062_21",
    "SERVICEIT_VPS_BROWSER_RESEARCH_ENABLED": "YES",
    "SERVICEIT_VPS_BROWSER_PROXY_ISOLATION_VERIFIED": "YES",
}

class Blocked(RuntimeError):
    pass


def call(*argv: str, timeout=180, check=True):
    return subprocess.run(argv, text=True, capture_output=True, timeout=timeout, check=check)


def valid_private_config(path: Path) -> dict[str, str]:
    if not path.is_file() or path.is_symlink():
        raise Blocked("PRIVATE_VPS_CONFIG_MISSING")
    info = path.stat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise Blocked("PRIVATE_VPS_CONFIG_PERMISSIONS_INVALID")
    values = {}
    for line in path.read_text("utf8").splitlines():
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise Blocked("CONFIG_PARSE_INVALID")
        key, val = line.split("=", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in values:
            raise Blocked("CONFIG_DUPLICATE_OR_BAD_KEY")
        values[key] = val
    return values


def healthy(name: str) -> bool:
    try:
        result = call("docker", "inspect", "-f",
                      "{{.State.Running}}:{{.State.Health.Status}}", name).stdout.strip()
        return result == "true:healthy"
    except (subprocess.SubprocessError, OSError):
        return False


def core_healthy():
    if not all(healthy(s) for s in SERVICES):
        raise Blocked("CORE_FOUR_NOT_HEALTHY")
    if not healthy(BROWSER):
        raise Blocked("BROWSER_NOT_HEALTHY")
    try:
        if call("docker", "inspect", "-f", "{{.State.Running}}", EGRESS).stdout.strip() != "true":
            raise Blocked("BROWSER_EGRESS_NOT_RUNNING")
        env = call("docker", "exec", CORE, "printenv",
                   "SERVICEIT_VPS_WRITER_MODE", "SERVICEIT_VPS_POLL_ENABLED",
                   "SERVICEIT_VPS_BROWSER_RESEARCH_ENABLED",
                   "SERVICEIT_VPS_BROWSER_PROXY_ISOLATION_VERIFIED").stdout.splitlines()
        if env != ["LIVE", "YES", "YES", "YES"]:
            raise Blocked("CORE_BROWSER_OR_POLL_NOT_LIVE")
    except subprocess.SubprocessError:
        raise Blocked("CORE_LIVE_ENV_NOT_VERIFIED") from None


def compose(files: tuple[Path, ...], config: Path, *args: str,
            timeout=180, check=True):
    cmd = ["docker", "compose", "--env-file", str(config)]
    for file in files:
        cmd += ["-f", str(file)]
    return call(*cmd, *args, timeout=timeout, check=check)


def assert_isolated_brave_overlay(files: tuple[Path, ...], config: Path):
    # Prove the one secret goes ONLY to coordinator, not ingress/business,
    # auxiliaries, observer, Chromium or egress.
    spec = json.loads(compose(files, config, "config", "--format", "json").stdout)
    services = spec.get("services")
    if type(services) is not dict:
        raise Blocked("COMPOSE_SCHEMA_INVALID")
    found = []
    for name, service in services.items():
        secrets = service.get("secrets", []) or []
        attached = [s.get("source") for s in secrets if type(s) is dict]
        if "serviceit_brave_search_key" in attached:
            found.append(name)
    if found != ["coordinator"]:
        raise Blocked("BRAVE_SECRET_NOT_COORDINATOR_ONLY")
    environment = services["coordinator"].get("environment", {})
    if environment.get("SERVICEIT_BRAVE_SEARCH_API_KEY_FILE") != (
            "/run/secrets/serviceit_brave_search_key"):
        raise Blocked("BRAVE_SECRET_FILE_ENV_INVALID")
    # Never accept environment variable that embeds the *value* itself.
    if environment.get("SERVICEIT_BRAVE_SEARCH_API_KEY"):
        raise Blocked("BRAVE_RAW_KEY_IN_COMPOSE_FORBIDDEN")
    if spec.get("secrets", {}).get("serviceit_brave_search_key", {}).get("file") != (
            "/etc/serviceit/secrets/brave_api_key"):
        raise Blocked("BRAVE_SECRET_SOURCE_PATH_INVALID")


def safe_probe_labels(stdout: str) -> tuple[str, str]:
    # The probe produces at most one bounded JSON dictionary. Print only
    # constant codes; never raw stdout/stderr or service-provider messages.
    allowed = {
        "BRAVE_API_200_VALID_WEB_RESULTS",
        "BRAVE_CONTEXT_API_200_VALID_GROUNDING",
        "BRAVE_INVALID_KEY_401", "BRAVE_FORBIDDEN_403",
        "BRAVE_RATE_LIMIT_429",
        "BRAVE_PLAN_OR_QUERY_REJECTED_402",
        "BRAVE_PLAN_OR_QUERY_REJECTED_422",
        "BRAVE_VALIDATION_CACHE_CONTROL_422",
        "BRAVE_VALIDATION_COUNTRY_422",
        "BRAVE_VALIDATION_SEARCH_LANG_422",
        "BRAVE_RESPONSE_SIZE_INVALID", "BRAVE_NOT_JSON",
        "BRAVE_CONTEXT_SCHEMA_UNVERIFIED", "BRAVE_RESULT_SCHEMA_UNVERIFIED",
        "BRAVE_TRANSPORT_OR_RESPONSE_UNVERIFIED",
        "BRAVE_VPS_SECRET_MISSING_OR_UNVERIFIED",
        "BRAVE_KEY_MISSING_OR_INVALID", "NOT_CALLED",
    }
    if len(stdout) > 1600:
        return "UNVERIFIED", "UNVERIFIED"
    try:
        data = json.loads(stdout)
    except (TypeError, ValueError):
        return "UNVERIFIED", "UNVERIFIED"
    if type(data) is not dict:
        return "UNVERIFIED", "UNVERIFIED"
    def safe(value):
        return value if type(value) is str and value in allowed else "UNVERIFIED"
    return safe(data.get("web")), safe(data.get("context"))


def verify_probe(stdout: str):
    if len(stdout) > 1600:
        raise Blocked("BRAVE_PROBE_OUTPUT_UNVERIFIED")
    data = json.loads(stdout)
    if (type(data) is not dict or
            data.get("status") != "BRAVE_SEARCH_VPS_READY" or
            data.get("web") != "BRAVE_API_200_VALID_WEB_RESULTS" or
            data.get("context") != "BRAVE_CONTEXT_API_200_VALID_GROUNDING" or
            data.get("crm_writes") != 0 or data.get("model_calls") != 0 or
            data.get("api_requests_max") != 2):
        raise Blocked("BRAVE_WEB_AND_CONTEXT_API_NOT_READY")


def main(sha: str, approval: str):
    if os.geteuid() == 0:
        raise Blocked("NONROOT_DEPLOY_ONLY")
    if not SHA.fullmatch(sha) or approval != APPROVAL:
        raise Blocked("BRAVE_ACTIVATION_OWNER_APPROVAL_MISSING")
    config = Path.home() / "serviceit/config/vps.env"
    env = valid_private_config(config)
    if any(env.get(k, "NO") != v for k, v in GATES.items()):
        raise Blocked("CURRENT_NATIVE_CORE_OR_BROWSER_GATES_INVALID")
    if env.get("SERVICEIT_VPS_SECRETS_DIR") != "/etc/serviceit/secrets":
        raise Blocked("BRAVE_SECRETS_LOCATION_UNVERIFIED")
    release = Path.home() / "serviceit/releases" / sha
    base = release / "deploy/serviceit-vps.compose.yml"
    overlay = release / "deploy/serviceit-vps-brave.compose.yml"
    if not base.is_file() or not overlay.is_file() or base.is_symlink() or overlay.is_symlink():
        raise Blocked("BRAVE_RELEASE_FILES_MISSING")
    core_healthy()
    old_image = call("docker", "inspect", "-f", "{{.Image}}", CORE).stdout.strip()
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", old_image):
        raise Blocked("PREVIOUS_COORDINATOR_IMAGE_NOT_PINNED")
    files = (base, overlay)
    assert_isolated_brave_overlay(files, config)
    changed = False
    built = False
    phase = "BRAVE_COORDINATOR_IMAGE_BUILD"
    try:
        # Only build the new coordinator; no other LIVE service is changed.
        built = True  # Restore old image tag even after a partial build failure.
        compose(files, config, "build", "coordinator", timeout=1200)
        phase = "BRAVE_AUTH_GENERIC_WEB_AND_CONTEXT"
        # A disposable container, with the same Brave secret mount but no
        # coordinator loop, CRM requests, AI inference or PostgreSQL changes.
        test = compose(files, config, "run", "--rm", "--no-deps", "-T",
                       "coordinator", "python", "-m",
                       "leadpilot.serviceit_vps_brave_auth_probe",
                       timeout=95, check=False)
        web_code, context_code = safe_probe_labels(test.stdout)
        print("BRAVE_WEB_STATUS=" + web_code, flush=True)
        print("BRAVE_CONTEXT_STATUS=" + context_code, flush=True)
        if test.returncode != 0:
            raise Blocked("BRAVE_AUTH_NOT_CONFIRMED_NO_LIVE_CUTOVER")
        verify_probe(test.stdout)
        print("BRAVE_WEB_SEARCH=PASS;BRAVE_LLM_CONTEXT=PASS", flush=True)
        phase = "BRAVE_COORDINATOR_ONLY_RESTART"
        changed = True  # Restore old coordinator even after partial Compose failure.
        compose(files, config, "up", "-d", "--no-deps", "--no-build",
                "--force-recreate", "coordinator", timeout=240)
        phase = "BRAVE_FOUR_LIVE_AND_BROWSER_VERIFY"
        for attempt in range(20):
            try:
                core_healthy()
                break
            except Blocked:
                if attempt == 19:
                    raise
                time.sleep(3)
        inspect = json.loads(call("docker", "inspect", CORE).stdout)
        mount_targets = [x.get("Destination") for x in inspect[0].get("Mounts", [])]
        if "/run/secrets/serviceit_brave_search_key" not in mount_targets:
            raise Blocked("BRAVE_SECRET_MOUNT_NOT_VERIFIED")
        # Check *path* only; never print or compare the secret value.
        token_path = call("docker", "exec", CORE, "printenv",
                          "SERVICEIT_BRAVE_SEARCH_API_KEY_FILE").stdout.strip()
        if token_path != "/run/secrets/serviceit_brave_search_key":
            raise Blocked("BRAVE_SECRET_PATH_NOT_VERIFIED")
        # Repeat the exact generic probe inside the live coordinator is not
        # needed: the disposable probe already tested BOTH external APIs.
        print("BRAVE_VPS_NATIVE_COORDINATOR=LIVE_ENABLED", flush=True)
        print("CORE_FOUR_HEALTHY=PASS;BROWSER_STILL_LIVE=PASS", flush=True)
        print("BRAVE_MAX_SEARCH_CALLS_PER_CARD=3", flush=True)
        print("SOURCES=UNCHANGED;CRM_WRITES_BY_ACTIVATOR=0", flush=True)
        print("REAL_NEW_CARD_E2E=NOT_YET_VERIFIED", flush=True)
    except Exception as ex:
        print("BRAVE_VPS_ACTIVATION=BLOCKED_ROLLBACK", flush=True)
        print("BLOCKED_PHASE=" + phase, flush=True)
        try:
            if built:
                call("docker", "tag", old_image,
                     "serviceit-leadgen-vps-coordinator:latest")
            if changed:
                compose((base,), config, "up", "-d", "--no-build",
                        "--no-deps", "--force-recreate", "coordinator",
                        timeout=240)
                core_healthy()
                print("BRAVE_ROLLBACK=RESTORED_CORE_AND_BROWSER", flush=True)
            else:
                # Only an image tag might have changed during build. The
                # original LIVE container was never recreated; prove this.
                core_healthy()
                current = call("docker", "inspect", "-f", "{{.Image}}",
                               CORE).stdout.strip()
                if current != old_image:
                    raise Blocked("COORDINATOR_CHANGED_BEFORE_CUTOVER")
                print("BRAVE_ROLLBACK=NO_LIVE_CONTAINER_CHANGED", flush=True)
        except Exception:
            print("BRAVE_ROLLBACK=NEEDS_OPERATOR_REVIEW", flush=True)
        if isinstance(ex, subprocess.CalledProcessError):
            print("BLOCKED_COMMAND_EXIT=" + str(ex.returncode), flush=True)
        raise


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--sha", required=True)
    p.add_argument("--approval", required=True)
    args = p.parse_args()
    try:
        main(args.sha, args.approval)
    except Exception as exc:
        # Never print exception values: they could contain private paths,
        # model/vendor API responses, company details or credentials.
        print("SERVICEIT_BRAVE_VPS_DEPLOY=NOT_CONFIRMED", flush=True)
        print("FAILURE_CLASS=" + type(exc).__name__, flush=True)
        raise SystemExit(2)
