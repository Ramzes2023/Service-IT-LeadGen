"""Replace only the B2B writer after a live read-only duplicate check."""
import fcntl
import hashlib
from pathlib import Path
import re
import runpy
import sys
import time

MODULE = "serviceit_b2b_positive_control.py"


def main():
    args = sys.argv[1:]
    modules = {
        "--manager-decision": "serviceit_vps_b2b_bridge.py",
        "--manager-readiness": "serviceit_manager_readiness.py",
    }
    profile = args[0] if args and args[0] in modules else None
    run_id, old_sha, new_sha, helper_sha = args[1:] if profile else args
    module = modules.get(profile, MODULE)
    assert re.fullmatch(r"\d{7,16}", run_id)
    assert all(re.fullmatch(r"[0-9a-f]{64}", x) for x in (old_sha, new_sha, helper_sha))
    stage = Path(__file__).resolve().parent
    helper = stage / "cutover.py"
    for path, expected in ((helper, helper_sha), (stage / module, new_sha)):
        assert not path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest() == expected
    funcs = runpy.run_path(str(helper))
    run, inspect, need = (funcs[k] for k in ("run", "inspect", "need"))
    writer, receiver, core = (funcs[k] for k in ("WRITER", "RECEIVER", "CORE"))
    geo = "SERVICEIT_B2B_MOSCOW_ONLY_ENABLED"
    funcs["ALLOWED_ENV"].add(geo)
    lock = Path.home() / "serviceit/config/.serviceit-five-minute-activate.lock"
    assert not lock.is_symlink()
    with lock.open("a") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        original, env = funcs["validate_existing"](writer)
        neighbors = {name: inspect(name)["Id"] for name in (receiver, core)}
        sha_cmd = ["docker", "exec", writer, "sha256sum", "/app/leadpilot/" + module]
        need(run(sha_cmd).stdout.split()[0] == old_sha, "WRITER_SOURCE_DRIFT")
        image_id = original["Image"]
        need(re.fullmatch(r"sha256:[0-9a-f]{64}", image_id), "IMAGE_ID_INVALID")
        base = "serviceit-b2b-writer-witness-base:" + run_id
        image = "serviceit-b2b-writer-witness:" + run_id
        run(["docker", "tag", image_id, base])
        context = stage / "build"
        context.mkdir(mode=0o700)
        (context / module).write_bytes((stage / module).read_bytes())
        (context / "Dockerfile").write_text(
            f"FROM {base}\nCOPY --chmod=0444 {module} /app/leadpilot/{module}\n")
        run(["docker", "build", "--network", "none", "--pull=false", "-t", image, str(context)],
            timeout=240)
        if profile:
            smoke = (
                "from leadpilot.serviceit_vps_b2b_bridge import MANAGER_DECISION, INTERVAL_SECONDS;"
                "assert MANAGER_DECISION=='UF_CRM_13_SVCIT_REVIEW_DECISION';"
                "assert INTERVAL_SECONDS==300"
            )
            if profile == "--manager-readiness":
                smoke += (
                    ";from leadpilot.serviceit_manager_readiness import KINDS;"
                    "assert 'LICENSES' in KINDS and 'CLOUD' in KINDS"
                )
            run(["docker", "run", "--rm", "--network", "none", "--read-only",
                 "--user", "10001:10001", "--cap-drop", "ALL", "--entrypoint",
                 "python", image, "-B", "-c", smoke], timeout=50)
        funcs["start_new"].__globals__["IMAGE"] = image
        funcs["source_probe"]()
        print("DOSSIER_READBACK_NEW_DUPLICATE_CONTROL=YES", flush=True)
        need(inspect(writer)["Id"] == original["Id"], "WRITER_CHANGED")
        backup = writer + "-pre-witness-" + run_id
        run(["docker", "rename", writer, backup])
        try:
            run(["docker", "stop", "--time", "20", backup], timeout=40)
            funcs["start_new"](writer, backup, env)
            time.sleep(3)
            need(run(sha_cmd).stdout.split()[0] == new_sha, "NEW_WRITER_HASH_MISMATCH")
            need(funcs["validate_existing"](writer)[1].get(geo) == env.get(geo),
                 "WRITER_GEO_GATE_CHANGED")
            need(all(inspect(name)["Id"] == ident for name, ident in neighbors.items()),
                 "NEIGHBOR_CONTAINER_CHANGED")
        except Exception:
            restored = funcs["rollback"]({writer: backup})
            print("DOSSIER_READBACK_WRITER_ROLLBACK=" + ("YES" if restored else "FAILED"), flush=True)
            return 2
        print("DOSSIER_READBACK_WRITER_RELEASE=YES", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        message = str(error)
        code = message if re.fullmatch(r"[A-Z][A-Z0-9_]{3,65}", message) else type(error).__name__.upper()
        print("DOSSIER_READBACK_RELEASE_BLOCK_CODE=" + code, flush=True)
        raise SystemExit(2) from None
