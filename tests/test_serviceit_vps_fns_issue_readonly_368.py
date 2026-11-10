"""Read-only owner-only VPS FNS-MSP audit contract."""
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def test_fns_issue_readonly_workflow_has_no_mutations():
    text = (ROOT / ".github/workflows/serviceit-vps-fns-msp-issue-readonly-368.yml").read_text("utf8")
    assert "github.actor == 'Ramzes2023'" in text
    assert "github.event.issue.number == 368" in text
    assert "SERVICEIT_FNS_MSP_AUDIT_READONLY_368" in text
    assert "StrictHostKeyChecking=yes" in text
    assert "SET TRANSACTION READ ONLY" in text
    assert "FNS_SNAPSHOT_RECEIPTS=" in text
    assert "FNS_MSP_MATCHED=" in text
    assert "FNS_E2E=NOT_VERIFIED_BY_READONLY_AUDIT" in text
    for operation in ('crm.item.update', 'UPDATE serviceit_', 'DELETE FROM',
                      'INSERT INTO', 'docker restart', 'docker compose up',
                      'source ingestion', 'workflow_dispatch'):
        assert operation not in text
