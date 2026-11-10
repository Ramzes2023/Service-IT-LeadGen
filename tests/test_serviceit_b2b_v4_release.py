"""Offline V4 owner-gated coordinator-only release contract."""
import runpy
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]


def test_v4_overlay_scoped_to_exact_8_current_live_shas():
    script=BASE/"ops/serviceit_vps_b2b_research_v4_activate.py"
    code=runpy.run_path(str(script))
    assert len(code["FILES"]) == 8
    assert set(code["EXACT_LIVE_BEFORE"])==set(code["FILES"])
    assert all(len(v)==64 for v in code["EXACT_LIVE_BEFORE"].values())
    assert code["FLAG"]=="SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED"
    assert "B2B_RESEARCH_V4" in script.read_text(encoding="utf8")
    assert "V3_INFLIGHT_WRITE_NOT_SAFE_TO_RESTART" in script.read_text(encoding="utf8")
    assert '"--network", "none"' in script.read_text(encoding="utf8")
    assert "old_pin" in script.read_text(encoding="utf8")
    assert "ROLLBACK" in script.read_text(encoding="utf8")


def test_v4_workflow_owned_pinned_and_no_new_crm_or_task():
    script=(BASE/"ops/serviceit_vps_b2b_research_v4_activate.py").read_text("utf8")
    workflow=(BASE/".github/workflows/serviceit-vps-b2b-research-v4-activate.yml").read_text("utf8")
    assert "SERVICEIT_B2B_ROUTERAI_V4_ACTIVATE_314" in workflow
    assert "github.actor == 'Ramzes2023'" in workflow
    assert "environment: serviceit-vps-readonly" in workflow
    assert "StrictHostKeyChecking=yes" in workflow
    assert "shas[7]" in workflow
    assert "secrets.SERVICEIT_VPS_DEPLOY_SSH_KEY" in workflow
    for banned in ("crm.item.add","crm.item.update","tasks.task.add","DELETE FROM"):
        assert banned not in script
