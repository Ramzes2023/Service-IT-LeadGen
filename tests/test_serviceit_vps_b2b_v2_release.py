"""Read-only offline contract proof for the LIVE coordinator V2 overlay."""
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]


def test_v2_overlay_only_current_approved_eight_modules():
    code = runpy.run_path(str(ROOT / "ops/serviceit_vps_b2b_research_v2_activate.py"))
    files = code["FILES"]
    assert len(files) == len(set(files)) == 8
    assert files[-1] == "serviceit_b2b_public_mirrors.py"
    assert "serviceit_vps_b2b_deep_research.py" in files
    assert "serviceit_b2b_routerai_deep.py" in files
    assert code["FLAG"] == "SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED"
    pinned = code["EXACT_LIVE_BEFORE"]
    assert len(pinned) == 7 and all(k in files and len(v) == 64 for k,v in pinned.items())
    image = "sha256:"+"a"*64
    assert 'SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED: "YES"' in code["make_pin"](image,active=True)


def test_v2_never_changes_tasks_sources_or_contacts():
    script = (ROOT/"ops/serviceit_vps_b2b_research_v2_activate.py").read_text("utf8")
    workflow = (ROOT/".github/workflows/serviceit-vps-b2b-research-v2-activate.yml").read_text("utf8")
    assert "SERVICEIT_B2B_RESEARCH_V2_ACTIVATE_314" in workflow
    assert "github.actor == 'Ramzes2023'" in workflow
    assert "github.event.issue.number == 314" in workflow
    assert "StrictHostKeyChecking=yes" in workflow
    assert "serviceit-deep" in workflow
    assert '"--no-build"' in script
    assert '"--network", "none"' in script
    assert "V1_INFLIGHT_WRITE_NOT_SAFE_TO_RESTART" in script
    assert "old_pin" in script and "ROLLBACK" in script
    for banned in ("crm.item.add","crm.item.update","tasks.task.add","DELETE FROM"):
        assert banned not in script
    assert "secrets.SERVICEIT_VPS_DEPLOY_SSH_KEY" in workflow
    assert "shas[7]" in workflow
