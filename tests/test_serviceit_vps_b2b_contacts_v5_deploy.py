"""One-file guarded V5 deploy, static safety contract."""
import runpy
from pathlib import Path
BASE = Path(__file__).resolve().parents[1]

def test_deployer_only_updates_one_module_and_has_rollback():
    script = BASE / "ops/serviceit_vps_b2b_contacts_v5_deploy.py"
    config = runpy.run_path(str(script))
    source = script.read_text("utf8")
    assert config["FILES"] == ("serviceit_vps_b2b_deep_research.py",)
    assert config["FLAG"] == "SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED"
    for token in ("LIVE_B2B_PREVIOUS_SHA_DRIFT",
                  "V4_OR_AI_INFLIGHT_NOT_SAFE_TO_RESTART",
                  "B2B_CONTACTS_ROLLBACK",
                  "CURRENT_IMMUTABLE_PIN_DRIFT",
                  '"--network", "none"'):
        assert token in source
    assert "crm.item.update" not in source
    assert "tasks.task.add" not in source

def test_workflow_owner_and_pinned_previous_source():
    workflow = (BASE / ".github/workflows/serviceit-vps-b2b-contacts-v5-deploy.yml").read_text("utf8")
    for token in ("github.actor == 'Ramzes2023'",
                  "SERVICEIT_B2B_CONTACTS_V5_DEPLOY_368",
                  "3485e55eef4a63e6b3eff1f5bd6fc298ac5a3a4e",
                  "97a49f49483a3078048439549a9b74f3eb54d0e5",
                  "StrictHostKeyChecking=yes",
                  "secrets.SERVICEIT_VPS_DEPLOY_SSH_KEY"):
        assert token in workflow
    assert "github.event.issue.number == 368" in workflow
    assert '[[ "${#shas[@]}" == 1 ]]' in workflow
