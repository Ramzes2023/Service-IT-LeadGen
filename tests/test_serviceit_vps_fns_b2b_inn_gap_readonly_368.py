"""Read-only FNS/INN data-loss diagnosis, counts only and no CRM writes."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def test_inn_gap_workflow_is_readonly_scoped_and_no_identifiers_in_logs():
    source=(ROOT/".github/workflows/serviceit-vps-fns-b2b-inn-gap-readonly-368.yml").read_text("utf8")
    assert "github.actor == 'Ramzes2023'" in source
    assert "SERVICEIT_FNS_B2B_INN_GAP_READONLY_368" in source
    assert "github.event.issue.number == 368" in source
    assert "StrictHostKeyChecking=yes" in source
    assert "SET TRANSACTION READ ONLY" in source
    assert "B2B_INN_VALID=" in source
    assert "B2B_INN_CANDIDATE_AI_ONLY=" in source
    assert "FNS_ACTIVATION_WINDOW_NEW_CARDS=" in source
    assert "CRM_WRITES=0;PG_WRITES=0;FNS_DOWNLOADS=0" in source
    for token in ("crm.item.update", "INSERT INTO", "DELETE FROM", "UPDATE serviceit_",
                  "docker restart", "docker compose up", "print(inn)",
                  "print(item)", "workflow_dispatch"):
        assert token not in source
