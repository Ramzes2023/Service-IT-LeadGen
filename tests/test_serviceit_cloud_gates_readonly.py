"""One-shot cloud flag inspection must have no CRM/write/token exposure."""
from pathlib import Path

WF = Path(".github/workflows/serviceit-cloud-gates-readonly.yml").read_text("utf-8")


def test_exact_owner_trigger_and_zero_permissions_to_write():
    assert "issue_comment:" in WF
    assert "github.actor == 'Ramzes2023'" in WF
    assert "github.event.issue.number == 74" in WF
    assert "SERVICEIT_CLOUD_FLAG_READONLY_V1" in WF
    assert "contents: read" in WF
    assert "issues: read" in WF
    assert "contents: write" not in WF
    assert "workflow_dispatch:" not in WF
    assert "schedule:" not in WF


def test_only_flags_and_boolean_secret_presence_are_output():
    for marker in (
        "SERVICEIT_CLOUD_POLL=ENABLED",
        "SERVICEIT_CLOUD_EVENT_WORKER=ENABLED",
        "SERVICEIT_MODEL_PRIVACY=YES",
        "SERVICEIT_MODEL_PRIVACY=NO",
        "SERVICEIT_MODEL_PRIVACY=UNVERIFIED",
        "SERVICEIT_PRELIMINARY_PUBLIC_TITLE_AI=ON_WITH_HOSTS_PRESENT",
        "SERVICEIT_MODEL_KEY=CONFIGURED",
        "SERVICEIT_MODEL_KEY=MISSING",
        "SERVICEIT_WORKER_SECRETS=ALL_PRESENT",
        "SERVICEIT_WORKER_SECRETS=INCOMPLETE",
        "SERVICEIT_CRM_WRITES=0",
        "SERVICEIT_GIGACHAT_REQUESTS=0",
        "SERVICEIT_REPOSITORY_UPDATES=0",
    ):
        assert marker in WF
    for forbidden in (
        "crm.item.add", "crm.item.update", "crm.item.get",
        "crm.item.list", "curl ", "wget ", "git push",
        "gh variable set", "gh secret set", "printenv",
        "echo \"$POLL_MODE\"", "echo \"$PUBLIC_AI_HOSTS\"",
        "echo \"$GIGACHAT_CREDENTIAL_PRESENT\"",
    ):
        assert forbidden not in WF
    assert "secrets.SERVICEIT_BITRIX_WEBHOOK != ''" in WF
    assert "secrets.SERVICEIT_GH_LEDGER_KEY != ''" in WF
    assert "secrets.SERVICEIT_BITRIX_EVENT_BRIDGE_KEY != ''" in WF
