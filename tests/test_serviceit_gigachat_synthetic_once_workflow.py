"""GigaChat LIVE synthetic-only test must not carry any CRM data/credentials to logs."""
from pathlib import Path

WF=Path(".github/workflows/serviceit-gigachat-synthetic-once.yml").read_text("utf-8")


def test_synthetic_gigachat_owner_only_and_explicit_model_consent():
    for expected in (
        "github.actor == 'Ramzes2023'",
        "github.event.issue.number == 74",
        "SERVICEIT_GIGACHAT_SYNTHETIC_ONCE_V1",
        "vars.SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED == 'YES'",
        "AI_PROVIDER: gigachat",
        "AI_ENABLE: '1'",
        "GIGACHAT_CREDENTIALS: ${{ secrets.GIGACHAT_CREDENTIALS }}",
        "contents: read",
        "issues: read",
        "serviceit_gigachat_cockpit_preflight --live-synthetic",
    ):
        assert expected in WF


def test_synthetic_gigachat_has_no_bitrix_or_customer_data_or_repo_changes():
    for forbidden in (
        "SERVICEIT_BITRIX_WEBHOOK",
        "SERVICEIT_GH_LEDGER_KEY",
        "crm.item.add", "crm.item.update", "crm.item.get",
        "contents: write", "issues: write", "git push",
        "gh variable set", "gh secret set", "schedule:",
        "workflow_dispatch:",
    ):
        assert forbidden not in WF
    source=Path("leadpilot/serviceit_gigachat_cockpit_preflight.py").read_text("utf-8")
    assert "from .bitrix_1062_demo_qualification import AI_TEXT" in source
    assert "run_preflight()" in source
    assert "CRM" not in WF or "no CRM" in WF
    assert "GIGACHAT_PREFLIGHT_BLOCKED" in source
    assert "ledger_reservations" in source
    assert "CRM_WRITES=1" not in WF
