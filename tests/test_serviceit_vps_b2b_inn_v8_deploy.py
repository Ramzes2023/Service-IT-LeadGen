"""V8 owner-only four-module overlay: no blind CRM replay, rollback-safe."""
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "serviceit_vps_native_fence.py",
    "serviceit_vps_b2b_deep_research.py",
    "serviceit_b2b_public_supplement.py",
    "serviceit_vps_b2b_public_inn.py",
)


def test_v8_activation_guarded_rollforward_and_rollback():
    path = ROOT / "ops/serviceit_vps_b2b_inn_v8_activate.py"
    text = path.read_text("utf8")
    cfg = runpy.run_path(str(path))
    assert cfg["FILES"] == FILES
    assert cfg["INN_FLAG"] == "SERVICEIT_VPS_B2B_PUBLIC_INN_ENABLED"
    pin = cfg["make_pin"]("sha256:"+"a"*64, active=True, extra=True, inn=True)
    assert 'SERVICEIT_VPS_B2B_PUBLIC_INN_ENABLED: "YES"' in pin
    assert cfg["make_pin"]("sha256:"+"b"*64, active=True, extra=True, inn=True) != pin
    for marker in (
        "CURRENT_V7_INN_WRITER_REQUIRED",
        "LIVE_INN_V8_SHA_DRIFT",
        "ONLY_CODE_IMAGE_CHANGED",
        "V8_RETRY_SCHEMA_MIGRATION_FAILED",
        "V4_OR_AI_INFLIGHT_NOT_SAFE_TO_RESTART",
        "B2B_INN_V8_ROLLBACK=YES",
        "B2B_PUBLIC_INN_V2",
        '"--network", "none"',
        "COORDINATOR_PYTHON_SHA_MISMATCH",
    ):
        assert marker in text
    for forbidden in ("crm.item.update", "crm.item.add", "tasks.task.add",
                      "docker compose down", "DROP TABLE", "TRUNCATE TABLE"):
        assert forbidden not in text


def test_v8_workflow_pins_known_v7_runtime_and_exact_v8_code():
    workflow = (ROOT / ".github/workflows/serviceit-vps-b2b-inn-v8-release.yml").read_text("utf8")
    for marker in (
        "github.actor == 'Ramzes2023'",
        "github.event.issue.number == 368",
        "SERVICEIT_B2B_INN_V8_RELEASE_368",
        "StrictHostKeyChecking=yes",
        "0655587324289efdede8860fdf69e07a25bf5477",
        "6124478aad06967e2d2f83e9d0ef097afefed3b4",
        "ce706d0777295db4b03fae714c12a83d47ffa2c9",
        '== 4 && "${#oldshas[@]}" == 4',
        '== 8 ]]',
        "B2B_INN_V8_VPS_LIVE_ENABLED=YES",
        "remote_rc=$?",
        "ops/serviceit_vps_b2b_inn_v8_activate.py",
    ):
        assert marker in workflow
    assert "workflow_dispatch" not in workflow
    assert "docker compose down" not in workflow
