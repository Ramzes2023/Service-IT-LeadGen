"""Guarded V6 one-flag deployment cannot rebuild image or write to CRM."""
import runpy
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def test_v6_only_flag_source_immutable_and_rollback():
    p=ROOT/"ops/serviceit_vps_b2b_v6_flag_only.py"
    env=runpy.run_path(str(p))
    text=p.read_text("utf8")
    assert env["FILES"]==("serviceit_vps_b2b_deep_research.py",)
    assert env["FLAG"]=="SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED"
    assert env["EXTRA_FLAG"]=="SERVICEIT_PUBLIC_COMPANY_REQUISITES_EXTRA_SEARCH_ENABLED"
    assert "LIVE_V6_SHA_DRIFT" in text
    assert "V4_OR_AI_INFLIGHT_NOT_SAFE_TO_RESTART" in text
    assert "new_image = old_image" in text
    assert "B2B_V6_FLAG_ROLLBACK" in text
    assert '"docker", "build"' not in text
    assert "crm.item.update" not in text

def test_v6_flag_workflow_owner_only_stages_correct_script():
    wf=(ROOT/".github/workflows/serviceit-vps-b2b-v6-flag-only.yml").read_text("utf8")
    for token in ("github.actor == 'Ramzes2023'",
                  "SERVICEIT_B2B_V6_FLAG_ONLY_368",
                  "github.event.issue.number == 368",
                  "6124478aad06967e2d2f83e9d0ef097afefed3b4",
                  "StrictHostKeyChecking=yes",
                  "ops/serviceit_vps_b2b_v6_flag_only.py",
                  "B2B_V6_FLAG_ACTIVATED=YES", "remote_rc=$?"):
        assert token in wf
    assert "ops/serviceit_vps_b2b_buyer_v6_deploy.py" not in wf
    assert "set +e" in wf
