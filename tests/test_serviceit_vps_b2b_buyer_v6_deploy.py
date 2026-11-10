"""B2B V6 guarded one-file deployment with optional public buyer Brave query."""
import runpy
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def test_v6_pinned_deployer_does_not_touch_unrelated_containers():
    script=ROOT/"ops/serviceit_vps_b2b_buyer_v6_deploy.py"
    data=runpy.run_path(str(script))
    content=script.read_text("utf8")
    assert data["FILES"]==("serviceit_vps_b2b_deep_research.py",)
    assert data["FLAG"]=="SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED"
    assert data["EXTRA_FLAG"]=="SERVICEIT_PUBLIC_COMPANY_REQUISITES_EXTRA_SEARCH_ENABLED"
    assert "LIVE_B2B_PREVIOUS_SHA_DRIFT" in content
    assert "V4_OR_AI_INFLIGHT_NOT_SAFE_TO_RESTART" in content
    assert "B2B_BUYER_V6_ROLLBACK" in content
    assert "CURRENT_IMMUTABLE_PIN_DRIFT" in content
    assert '"--network", "none"' in content
    assert "crm.item.update" not in content
    assert "tasks.task.add" not in content
    old=data["make_pin"]("sha256:"+"0"*64,active=True,extra=False)
    new=data["make_pin"]("sha256:"+"0"*64,active=True,extra=True)
    assert data["EXTRA_FLAG"] not in old
    assert data["EXTRA_FLAG"] in new
    assert data["FLAG"] in old and data["FLAG"] in new

def test_v6_owner_only_workflow_pins_precisely_two_code_revisions():
    content=(ROOT/".github/workflows/serviceit-vps-b2b-buyer-v6-deploy.yml").read_text("utf8")
    for token in ("github.actor == 'Ramzes2023'",
                  "SERVICEIT_B2B_BUYER_V6_DEPLOY_368",
                  "github.event.issue.number == 368",
                  "c563d116724be9c5a758c0f326bd9d2a9ca8a8f7",
                  "6124478aad06967e2d2f83e9d0ef097afefed3b4",
                  "StrictHostKeyChecking=yes",
                  "secrets.SERVICEIT_VPS_DEPLOY_SSH_KEY"):
        assert token in content
    assert '[[ "${#shas[@]}" == 1 ]]' in content

def test_v6_failed_remote_preflight_prints_only_sanitized_fixed_codes():
    content=(ROOT/".github/workflows/serviceit-vps-b2b-buyer-v6-deploy.yml").read_text("utf8")
    assert "set +e" in content and "remote_rc=$?" in content
    assert "B2B_BUYER_V6_REMOTE_STATUS_ABSENT=YES" in content
    assert "exit 1" in content
    assert "safe=\"$(grep -E '^B2B_BUYER_V6_" in content
    assert 'cat "$RUNNER_TEMP/serviceit-contacts/status"' not in content

def test_v6_workflow_only_stages_and_compiles_v6_activator():
    content=(ROOT/".github/workflows/serviceit-vps-b2b-buyer-v6-deploy.yml").read_text("utf8")
    assert "ops/serviceit_vps_b2b_contacts_v5_deploy.py" not in content
    assert content.count("ops/serviceit_vps_b2b_buyer_v6_deploy.py") >= 3
