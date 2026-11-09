#!/usr/bin/env python3
"""Owner-approved, fail-closed native VPS 5-minute Docker cutover.

Run only via pinned-SSH GitHub owner workflow. Never logs secrets, CRM content,
Docker environments, customer data or image file contents. Source intake stays
OFF; no CRM writes are initiated by this deployment script. Ongoing native
workers resume their normal fenced processing after restart.
"""
from __future__ import annotations

import base64
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time

BASE = "serviceit-leadgen-vps"
HOME = Path.home()
ENV = HOME / "serviceit/config/vps.env"
CONFIG = ENV.parent
STAGE = HOME / "serviceit/staging/native-5min-20261109"
RELEASES = (HOME / "serviceit/releases").resolve()
LOCK = CONFIG / ".serviceit-five-minute-activate.lock"
ROLE = {
    "core": {
        "service": "coordinator",
        "module": "leadpilot.serviceit_vps_worker",
        "filename": "serviceit_vps_worker.py",
        "pin": CONFIG / "native-five-minute-core-image.compose.yml",
    },
    "aux": {
        "service": "auxiliaries",
        "module": "leadpilot.serviceit_vps_native_aux",
        "filename": "serviceit_vps_native_aux.py",
        "pin": CONFIG / "fns-aux-image-pinned.compose.yml",
    },
    "business": {
        "service": "business",
        "module": "leadpilot.serviceit_vps_native_business",
        "filename": "serviceit_vps_native_business.py",
        "pin": CONFIG / "postcall-business-pinned.compose.yml",
    },
}
ALL = [BASE + "-" + x + "-1" for x in
       ("coordinator", "auxiliaries", "business", "observer", "fns-msp")]
UNMODIFIED = [BASE + "-" + x + "-1" for x in ("observer", "fns-msp")]
MODIFIED_ROLES = ("core", "aux", "business")


class Blocked(Exception):
    pass


def need(ok, code):
    if not ok:
        raise Blocked(code)


def command(argv, timeout=70):
    try:
        return subprocess.run(
            argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=timeout, text=True, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise Blocked("LOCAL_COMMAND_TIMEOUT_OR_UNAVAILABLE") from None


def out(argv, timeout=70):
    result = command(argv, timeout)
    need(result.returncode == 0, "LOCAL_COMMAND_FAILED")
    return result.stdout.strip()


def container(group):
    return BASE + "-" + ROLE[group]["service"] + "-1"


def inspect(name, template):
    return out(["docker", "inspect", "-f", template, name])


def image_id(name):
    value = inspect(name, "{{.Image}}")
    need(re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None,
         "RUNNING_IMAGE_ID_UNVERIFIED")
    return value


def health(name):
    try:
        return inspect(name, "{{.State.Running}}:{{.State.Health.Status}}") == "true:healthy"
    except Blocked:
        return False


def require_health():
    for name in ALL:
        need(health(name), "PRODUCTION_SERVICE_UNHEALTHY")


def flag(name, key):
    return out(["docker", "exec", name, "printenv", key])


def secure_file(path):
    need(path.is_file() and not path.is_symlink(),
         "SECURE_FILE_NOT_PRESENT_OR_LINK")
    st = path.stat()
    need(st.st_uid == os.geteuid() and
         (stat.S_IMODE(st.st_mode) & 0o077) == 0,
         "SECURE_FILE_PERMISSION_INVALID")


def write_private(path, content):
    need(not path.is_symlink(), "IMAGE_PIN_SYMLINK_BLOCKED")
    fd, temp = tempfile.mkstemp(prefix=".five-minute-pin-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass


def pinned_text(service, img):
    return 'services:\n  ' + service + ':\n    image: "' + img + '"\n    pull_policy: never\n'


def paths_for(group):
    name = container(group)
    labels = inspect(name,
        '{{index .Config.Labels "com.docker.compose.project.config_files"}}')
    found = [Path(x.strip()) for x in labels.split(",") if x.strip()]
    expected = 2
    need(len(found) == expected and len(set(found)) == expected,
         "EXISTING_COMPOSE_LABELS_UNEXPECTED")
    for path in found:
        need(path.is_file() and not path.is_symlink(),
             "EXISTING_COMPOSE_FILE_INVALID")
    need(any(
        p.name == "serviceit-vps.compose.yml" and
        p.resolve().is_relative_to(RELEASES) and
        re.search(r"/releases/[a-f0-9]{40}/deploy/serviceit-vps\.compose\.yml$",
                  str(p.resolve()))
        for p in found
    ), "RELEASE_COMPOSE_PATH_UNVERIFIED")
    need(inspect(name,
        '{{index .Config.Labels "com.docker.compose.project"}}') == BASE,
         "COMPOSE_PROJECT_MISMATCH")
    pin = ROLE[group]["pin"]
    if group == "core":
        need(not pin.exists() and not pin.is_symlink(),
             "CORE_FIVE_MINUTE_PIN_ALREADY_EXISTS")
    else:
        need(pin in found, "PREVIOUS_IMAGE_PIN_LABEL_MISSING")
        secure_file(pin)
    return found


def compose_cmd(paths, *args):
    cmd = ["docker", "compose", "--env-file", str(ENV)]
    for path in paths:
        cmd.extend(["-f", str(path)])
    return [*cmd, *args]


def read_model(group, paths):
    model = json.loads(out(compose_cmd(paths, "config", "--format", "json")))
    service = model.get("services", {}).get(ROLE[group]["service"])
    need(isinstance(service, dict) and
         isinstance(service.get("environment"), dict),
         "COMPOSE_SERVICE_OR_ENV_UNVERIFIED")
    return service


def live_env(name):
    values = json.loads(inspect(name, "{{json .Config.Env}}"))
    need(isinstance(values, list) and all(
        isinstance(x, str) and "=" in x for x in values),
        "CONTAINER_ENV_FORMAT_UNVERIFIED")
    return dict(x.split("=", 1) for x in values)


def module_sha_and_seconds(name, module):
    script = """
import hashlib,importlib.util,json,sys
module=sys.argv[1]
spec=importlib.util.find_spec(module)
assert spec and spec.origin and spec.origin.startswith('/app/leadpilot/')
with open(spec.origin,'rb') as handle:
    digest=hashlib.sha256(handle.read()).hexdigest()
import importlib
obj=importlib.import_module(module)
print(json.dumps({'sha':digest,'interval':obj.INTERVAL_SECONDS}))
"""
    result = json.loads(out(["docker", "exec", name, "python", "-B",
                             "-c", script, module], 60))
    need(type(result) is dict and
         re.fullmatch(r"[a-f0-9]{64}", str(result.get("sha"))) is not None and
         type(result.get("interval")) is int,
         "RUNNING_MODULE_IDENTITY_INVALID")
    return result


def build_image(group, original_id, manifest):
    source = STAGE / (group + ".py")
    need(source.is_file() and not source.is_symlink() and
         source.stat().st_uid == os.geteuid() and
         0 < source.stat().st_size < 150_000,
         "REVIEWED_NEW_SOURCE_MISSING")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    need(digest == manifest["new"], "NEW_SOURCE_DIGEST_MISMATCH")
    base_tag = "serviceit-vps-five-minute-baseline-" + group + ":20261109"
    target_tag = "serviceit-vps-five-minute-" + group + ":20261109"
    existing = command(["docker", "image", "inspect", "-f", "{{.Id}}", base_tag])
    need(existing.returncode != 0 or existing.stdout.strip() == original_id,
         "BASE_IMAGE_TAG_COLLISION")
    need(command(["docker", "tag", original_id, base_tag]).returncode == 0,
         "BASE_IMAGE_TAG_FAILED")
    folder = STAGE / ("build-" + group)
    folder.mkdir(mode=0o700, exist_ok=True)
    dst = folder / ROLE[group]["filename"]
    dst.write_bytes(source.read_bytes())
    os.chmod(dst, 0o600)
    dockerfile = folder / "Dockerfile"
    dockerfile.write_text(
        "FROM " + base_tag + "\n" +
        "COPY --chmod=0444 " + ROLE[group]["filename"] +
        " /app/leadpilot/" + ROLE[group]["filename"] + "\n",
        encoding="utf-8")
    built = command(["docker", "build", "--network", "none",
                     "--pull=false", "--no-cache",
                     "-t", target_tag, "-f", str(dockerfile),
                     str(folder)], timeout=210)
    need(built.returncode == 0, "OFFLINE_IMAGE_BUILD_FAILED")
    new_id = out(["docker", "image", "inspect", "-f", "{{.Id}}", target_tag])
    need(re.fullmatch(r"sha256:[a-f0-9]{64}", new_id) is not None and
         new_id != original_id, "NEW_IMAGE_ID_NOT_DISTINCT")
    smoke = """
import hashlib,importlib,importlib.util,sys
module=sys.argv[1]
expected=sys.argv[2]
spec=importlib.util.find_spec(module)
assert spec and spec.origin and spec.origin.startswith('/app/leadpilot/')
assert hashlib.sha256(open(spec.origin,'rb').read()).hexdigest()==expected
assert importlib.import_module(module).INTERVAL_SECONDS==300
print('FIVE_MINUTE_IMAGE_SMOKE_PASS')
"""
    tested = command(["docker", "run", "--rm", "--network", "none",
                      "--read-only", "--user", "10001:10001",
                      "--pids-limit", "64", "--memory", "256m",
                      "--cap-drop", "ALL", "--entrypoint", "python",
                      new_id, "-B", "-c", smoke,
                      ROLE[group]["module"], manifest["new"]], timeout=95)
    need(tested.returncode == 0 and
         "FIVE_MINUTE_IMAGE_SMOKE_PASS" in tested.stdout,
         "NEW_IMAGE_OFFLINE_SMOKE_FAILED")
    print("VPS_5MIN_" + group.upper() + "_IMAGE_SELFTEST=PASS", flush=True)
    return new_id


def restart_one(group, paths, image_expected, sha_expected):
    name = container(group)
    operation = command(
        compose_cmd(paths, "up", "-d", "--no-deps", "--no-build",
                    "--pull", "never", "--force-recreate",
                    ROLE[group]["service"]), timeout=155)
    need(operation.returncode == 0, "SINGLE_SERVICE_RESTART_FAILED")
    for _ in range(22):
        if health(name):
            break
        time.sleep(4)
    need(health(name) and image_id(name) == image_expected,
         "RECREATED_SERVICE_IMAGE_OR_HEALTH_MISMATCH")
    actual = module_sha_and_seconds(name, ROLE[group]["module"])
    need(actual["sha"] == sha_expected and actual["interval"] == 300,
         "NEW_RUNTIME_CADENCE_NOT_300")


def assert_flags():
    core = container("core")
    aux = container("aux")
    business = container("business")
    for name, key, expected in (
        (core, "SERVICEIT_VPS_WRITER_MODE", "LIVE"),
        (core, "SERVICEIT_VPS_POLL_ENABLED", "YES"),
        (core, "SERVICEIT_VPS_INTAKE_ENABLED", "NO"),
        (core, "SERVICEIT_VPS_CUTOVER_AUTHORIZED", "YES"),
        (core, "SERVICEIT_VPS_OLD_GH_WRITERS_DISABLED_ACK", "YES"),
        (aux, "SERVICEIT_VPS_OWN_STOP_ENABLED", "YES"),
        (aux, "SERVICEIT_VPS_CRM_DUPLICATE_ENABLED", "YES"),
        (aux, "SERVICEIT_VPS_KANBAN_TITLE_ENABLED", "YES"),
        (aux, "SERVICEIT_VPS_MANAGER_TASK_ENABLED", "YES"),
        (aux, "SERVICEIT_VPS_FNS_CRM_RESULT_ENABLED", "YES"),
        (aux, "SERVICEIT_VPS_FREE_MSP_SCORE_ENABLED", "YES"),
        (aux, "SERVICEIT_VPS_PROVISIONAL_SCORE_ENABLED", "NO"),
        (business, "SERVICEIT_VPS_POSTCALL_ENABLED", "YES"),
        (business, "SERVICEIT_VPS_FINANCE_ENABLED", "YES"),
    ):
        need(flag(name, key) == expected, "NATIVE_VPS_FLAG_CHANGED")


def main():
    need(os.geteuid() != 0, "ROOT_NOT_ALLOWED")
    need(len(sys.argv) == 2, "MANIFEST_REQUIRED")
    manifest = json.loads(base64.b64decode(sys.argv[1], validate=True))
    need(isinstance(manifest, dict) and
         set(manifest) == set(MODIFIED_ROLES),
         "MANIFEST_ROLES_INVALID")
    for group in MODIFIED_ROLES:
        need(set(manifest[group]) == {"old", "new"} and
             all(re.fullmatch(r"[a-f0-9]{64}", manifest[group][k])
                 for k in ("old", "new")),
             "MANIFEST_HASHES_INVALID")
    secure_file(ENV)
    need(LOCK.parent.is_dir() and not LOCK.is_symlink(), "LOCK_DIR_INVALID")
    changed = []
    old_pins = {}
    existing_paths = {}
    original_images = {}
    new_images = {}
    preserved = {}
    with LOCK.open("a") as lf:
        os.chmod(LOCK, 0o600)
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        print("VPS_5MIN_CUTOVER=START", flush=True)
        try:
            require_health()
            assert_flags()
            for name in UNMODIFIED:
                need(health(name), "UNMODIFIED_SERVICE_NOT_HEALTHY")
                preserved[name] = image_id(name)
            for group in MODIFIED_ROLES:
                existing_paths[group] = paths_for(group)
                orig_id = image_id(container(group))
                original_images[group] = orig_id
                runtime = module_sha_and_seconds(
                    container(group), ROLE[group]["module"])
                need(runtime["sha"] == manifest[group]["old"] and
                     runtime["interval"] == 600,
                     "OLD_RUNTIME_MODULE_NOT_EXACT_BASELINE")
                old_model = read_model(group, existing_paths[group])
                actual_env = live_env(container(group))
                need(all(str(v) == actual_env.get(k)
                         for k, v in old_model["environment"].items()),
                     "LIVE_COMPOSE_ENV_DRIFT_BLOCKED")
                if group != "core":
                    pin = ROLE[group]["pin"]
                    old_pins[group] = pin.read_text("utf-8")
                    need(old_pins[group] == pinned_text(
                        ROLE[group]["service"], orig_id),
                        "EXISTING_IMAGE_PIN_NOT_EXACT")
            print("VPS_5MIN_PREFLIGHT=PASS", flush=True)
            for group in MODIFIED_ROLES:
                new_images[group] = build_image(
                    group, original_images[group], manifest[group])
            print("VPS_5MIN_ALL_IMAGE_SELFTESTS=PASS", flush=True)
            for group in MODIFIED_ROLES:
                pin = ROLE[group]["pin"]
                write_private(pin, pinned_text(
                    ROLE[group]["service"], new_images[group]))
                changed.append(group)
                next_paths = existing_paths[group] + (
                    [pin] if group == "core" else [])
                model = read_model(group, next_paths)
                baseline = read_model(group, existing_paths[group]) if (
                    group == "core") else None
                need(model.get("image") == new_images[group],
                     "COMPOSE_NEW_IMAGE_NOT_EXACT")
                # Preserve every live environment variable, not merely flags.
                observed = live_env(container(group))
                need(all(str(v) == observed.get(k)
                         for k, v in model["environment"].items()),
                     "COMPOSE_RECREATE_ENV_DRIFT")
                if baseline is not None:
                    need(model["environment"] == baseline["environment"],
                         "CORE_ENV_INADVERTENT_CHANGE")
                restart_one(group, next_paths, new_images[group],
                            manifest[group]["new"])
                print("VPS_5MIN_" + group.upper() + "_ACTIVE=YES", flush=True)
            require_health()
            assert_flags()
            for name, expected in preserved.items():
                need(health(name) and image_id(name) == expected,
                     "UNRELATED_IMAGE_OR_HEALTH_CHANGED")
            print("VPS_5MIN_CUTOVER=SUCCESS", flush=True)
            print("VPS_5MIN_CORE_INTERVAL_SECONDS=300", flush=True)
            print("VPS_5MIN_AUX_INTERVAL_SECONDS=300", flush=True)
            print("VPS_5MIN_BUSINESS_INTERVAL_SECONDS=300", flush=True)
            print("VPS_5MIN_OBSERVER_INTERVAL_SECONDS=3600_UNCHANGED", flush=True)
            print("VPS_5MIN_FNS_INTERVAL_SECONDS=86400_UNCHANGED", flush=True)
            print("VPS_5MIN_BITRIX_POLL=ENABLED", flush=True)
            print("VPS_5MIN_SOURCE_INTAKE=OFF", flush=True)
            print("VPS_5MIN_OLD_CARDS_EXPLICITLY_TOUCHED=ZERO", flush=True)
            print("VPS_5MIN_REQUIRES_LIVE_TWO_CYCLE_AUDIT=YES", flush=True)
            return 0
        except Exception as error:
            code = str(error) if isinstance(error, Blocked) else "UNEXPECTED_ERROR"
            if re.fullmatch(r"[A-Z0-9_]+", code) is None:
                code = "UNEXPECTED_ERROR"
            print("VPS_5MIN_CUTOVER=BLOCKED_" + code, flush=True)
            if changed:
                try:
                    for group in reversed(changed):
                        pin = ROLE[group]["pin"]
                        if group == "core":
                            # Keep an explicit original digest pin for safe
                            # rollback; do NOT rely on a mutable image tag.
                            write_private(pin, pinned_text(
                                ROLE[group]["service"], original_images[group]))
                        else:
                            write_private(pin, old_pins[group])
                        paths = existing_paths[group] + (
                            [pin] if group == "core" else [])
                        restarted = command(compose_cmd(
                            paths, "up", "-d", "--no-deps", "--no-build",
                            "--pull", "never", "--force-recreate",
                            ROLE[group]["service"]), timeout=155)
                        need(restarted.returncode == 0,
                             "ROLLBACK_RESTART_COMMAND_FAILED")
                        for _ in range(20):
                            if health(container(group)):
                                break
                            time.sleep(4)
                        need(health(container(group)) and
                             image_id(container(group)) == original_images[group],
                             "ROLLBACK_IMAGE_OR_HEALTH_INVALID")
                    print("VPS_5MIN_ROLLBACK=ORIGINAL_IMAGES_RESTORED", flush=True)
                except Exception:
                    print("VPS_5MIN_ROLLBACK=MANUAL_REVIEW_REQUIRED", flush=True)
            return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("VPS_5MIN_CUTOVER=BLOCKED_PREFLIGHT_OR_MANIFEST", flush=True)
        raise SystemExit(2)
