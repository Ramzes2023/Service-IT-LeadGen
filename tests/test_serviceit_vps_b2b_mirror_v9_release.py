"""V9 one-file source parser release is immutable and rollback-safe."""
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]

def test_only_public_mirror_module_overlaid_and_prior_live_flag_preserved():
    p=ROOT / "ops/serviceit_vps_b2b_mirror_v9_activate.py"
    code=p.read_text("utf8")
    cfg=runpy.run_path(str(p))
    assert cfg["FILES"]==("serviceit_b2b_public_mirrors.py",)
    assert cfg["INN_FLAG"]=="SERVICEIT_VPS_B2B_PUBLIC_INN_ENABLED"
    pin=cfg["make_pin"]("sha256:"+"1"*64,active=True,extra=True,inn=True)
    assert 'SERVICEIT_VPS_B2B_PUBLIC_INN_ENABLED: "YES"' in pin
    for marker in (
        "LIVE_MIRROR_V9_SHA_DRIFT",
        "CURRENT_V8_INN_WRITER_REQUIRED",
        "MIRROR_V9_INFLIGHT_NOT_SAFE_TO_RESTART",
        "B2B_MIRROR_V9_VPS_LIVE_ENABLED=YES",
        "B2B_MIRROR_V9_ROLLBACK=YES",
        "COORDINATOR_PYTHON_SHA_MISMATCH",
        '"--network", "none"',
        '"--no-deps"',
    ):
        assert marker in code
    for forbidden in ("crm.item.update","crm.item.add","tasks.task.add",
                      "docker compose down","TRUNCATE TABLE","DROP TABLE"):
        assert forbidden not in code


def test_workflow_uses_exact_old_v8_source_and_new_reviewed_v9_source():
    s=(ROOT / ".github/workflows/serviceit-vps-b2b-mirror-v9-release.yml").read_text("utf8")
    for marker in (
        "SERVICEIT_B2B_MIRROR_V9_RELEASE_368",
        "github.actor == 'Ramzes2023'",
        "github.event.issue.number == 368",
        "StrictHostKeyChecking=yes",
        "0655587324289efdede8860fdf69e07a25bf5477",
        "a0f483de1d1337b0380bb3cd7d8bbb3b68bced40",
        "serviceit_b2b_public_mirrors.py",
        '== 1 && "${#oldshas[@]}" == 1',
        '== 2 ]]',
        "B2B_MIRROR_V9_VPS_LIVE_ENABLED=YES",
    ):
        assert marker in s
    assert "workflow_dispatch" not in s
    assert "crm.item.update" not in s
