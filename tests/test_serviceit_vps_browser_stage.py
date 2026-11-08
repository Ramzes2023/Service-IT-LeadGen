"""Optional browser IMAGE-ONLY stage must remain separate from CRM deployment."""
from pathlib import Path

WORKFLOW = Path(".github/workflows/serviceit-vps-browser-image-stage-no-run.yml")
BODY = WORKFLOW.read_text("utf8")


def test_qualified_owner_only_no_auto_or_customer_calls():
    assert "workflow_dispatch:" in BODY
    assert "github.actor == 'Ramzes2023'" in BODY
    assert "github.ref == 'refs/heads/main'" in BODY
    assert "inputs.approval == 'STAGE_CHROMIUM_IMAGE_NO_RUN'" in BODY
    assert "environment: serviceit-vps-readonly" in BODY
    # Slow base layer pull must not be interrupted by the old 25m job limit.
    assert "timeout-minutes: 90" in BODY
    assert "serviceit-vps-native-shadow-readonly" in BODY
    assert "Dockerfile.serviceit-vps-browser" in BODY
    assert "BROWSER_IMAGE_ON_VPS=STAGED_NOT_RUNNING" in BODY
    assert "BROWSER_SANDBOX_AND_EGRESS_FIREWALL=NOT_VERIFIED" in BODY
    for token in ("docker run", "docker compose up",
                  "SERVICEIT_VPS_WRITER_MODE=LIVE",
                  "gh variable set", "crm.item.update", "tasks.task.add",
                  "SERVICEIT_BITRIX_WEBHOOK", "GITHUB_TOKEN",
                  "ROUTERAI_API_KEY"):
        assert token not in BODY


def test_stage_cannot_modify_native_four_worker_compose():
    active = Path("deploy/serviceit-vps.compose.yml").read_text("utf8")
    assert "serviceit_vps_public_browser" not in active
    assert "profiles: [\"browser\"]" in Path(
        "deploy/serviceit-vps-browser.compose.yml").read_text("utf8")
