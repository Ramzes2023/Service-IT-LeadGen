"""Guard FNS-optional public-research score rollout against cross-service writes."""
from pathlib import Path


def test_research_score_activation_is_existing_aux_only_and_flag_preserving():
    code = Path("ops/serviceit_vps_b2b_score_research_activate.py").read_text("utf8")
    compile(code, "serviceit_vps_b2b_score_research_activate.py", "exec")
    assert 'MODULE="serviceit_stage4_provisional_score_live.py"' in code
    assert "OLD_FLAGS=dict(NEW_FLAGS)" in code
    for label, value in (
        ("SERVICEIT_VPS_FNS_CRM_RESULT_ENABLED", "NO"),
        ("SERVICEIT_VPS_FREE_MSP_SCORE_ENABLED", "NO"),
        ("SERVICEIT_VPS_PROVISIONAL_SCORE_ENABLED", "YES"),
    ):
        assert f'"{label}":"{value}"' in code
    assert '"--no-deps"' in code and '"--network","none"' in code
    assert "SCORE_RESEARCH_FNS_REQUIRED=NO" in code
    assert "SCORE_RESEARCH_INN_WORKER_UNCHANGED=YES" in code
    assert "SCORE_RESEARCH_OTHER_IMAGES_UNCHANGED=YES" in code
    assert "need(healthy(AUX) and healthy(CORE)," in code
    assert "need(healthy(AUX) and healthy(CORE) and healthy(FNS)," not in code


def test_exact_owner_manual_guard_and_sanitized_readback():
    workflow = Path(".github/workflows/serviceit-vps-b2b-score-research-release.yml").read_text("utf8")
    assert "github.actor == 'Ramzes2023'" in workflow
    assert "github.event.issue.number == 314" in workflow
    assert "SERVICEIT_VPS_B2B_SCORE_RESEARCH_STATUS_DEPLOY_314" in workflow
    assert "environment: serviceit-vps-readonly" in workflow
    assert "StrictHostKeyChecking=yes" in workflow
    assert "git diff --quiet 26938349f0bc938490578ad3fa367e693367cc01 HEAD" in workflow
    assert "SERVICEIT_BITRIX_WEBHOOK" not in workflow
    assert "SCORE_RESEARCH_AUX_ACTIVATED=YES" in workflow
