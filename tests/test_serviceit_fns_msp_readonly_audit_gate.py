"""FNS MSP state audit must remain owner scoped with no mutations."""
from pathlib import Path
def test_fns_issue_readonly_owner_gate_and_zero_writes():
    text=(Path(__file__).resolve().parents[1] / 
        ".github/workflows/serviceit-vps-fns-msp-issue-readonly-368.yml").read_text("utf8")
    for marker in (
        "github.event_name == 'issue_comment'",
        "github.actor == 'Ramzes2023'",
        "github.event.issue.number == 368",
        "SERVICEIT_FNS_MSP_AUDIT_READONLY_368",
        "StrictHostKeyChecking=yes",
        "SET TRANSACTION READ ONLY",
        "FNS_MSP_COLLECTOR_ENABLED",
        "FNS_CRM_RESULT_ENABLED",
        "BITRIX_FNS_RESULT_FIELD",
    ):
        assert marker in text
    for forbidden in ("crm.item.update","crm.item.add","INSERT INTO","DELETE FROM",
                      "docker restart","docker compose up","workflow_dispatch"):
        assert forbidden not in text
