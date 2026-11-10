"""Pure offline assertions for owner-gated B2B VPS activation."""
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]


def test_release_scope_and_frozen_live_digests():
    code = runpy.run_path(str(ROOT / "ops/serviceit_vps_b2b_deep_activate.py"))
    files = code["FILES"]
    assert len(files) == len(set(files)) == 7
    assert "serviceit_vps_worker.py" in files
    assert "serviceit_vps_b2b_deep_research.py" in files
    assert "serviceit_b2b_routerai_deep.py" in files
    assert "serviceit_public_evidence_research.py" in files
    assert "serviceit_vps_native_fence.py" in files
    assert len(code["EXACT_LIVE_BEFORE"]) == 5
    assert all(k in files and len(v) == 64
               for k, v in code["EXACT_LIVE_BEFORE"].items())
    image = "sha256:" + "a"*64
    assert "environment:" not in code["make_pin"](image, active=False)
    enabled = code["make_pin"](image, active=True)
    assert 'SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED: "YES"' in enabled
    assert "pull_policy: never" in enabled


def test_deployment_is_exact_owner_only_and_does_not_write_crm():
    workflow = (ROOT / ".github/workflows/serviceit-vps-b2b-deep-activate.yml"
                ).read_text("utf8")
    script = (ROOT / "ops/serviceit_vps_b2b_deep_activate.py").read_text("utf8")
    assert "SERVICEIT_B2B_DEEP_ACTIVATE_GUARDED_314" in workflow
    assert "github.actor == 'Ramzes2023'" in workflow
    assert "github.event.issue.number == 314" in workflow
    assert "environment: serviceit-vps-readonly" in workflow
    assert "StrictHostKeyChecking=yes" in workflow
    assert '"--network", "none"' in script
    assert '"--no-build"' in script
    assert "old_pin" in script and "ROLLBACK" in script
    for banned in ("crm.item.add", "crm.item.update", "tasks.task.add"):
        assert banned not in script
    assert "secrets.SERVICEIT_VPS_DEPLOY_SSH_KEY" in workflow
