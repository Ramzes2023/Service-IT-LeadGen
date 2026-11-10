"""Read-only B2B source attestation never prints secrets."""
from pathlib import Path
p=Path(__file__).resolve().parents[1]/".github/workflows/serviceit-vps-b2b-v6-sha-readonly-368.yml"

def test_readonly_b2b_sha_workflow_is_owner_only():
    text=p.read_text("utf8")
    assert "SERVICEIT_B2B_V6_SHA_READONLY_368" in text
    assert "github.actor == 'Ramzes2023'" in text
    assert "github.event.issue.number == 368" in text
    assert "StrictHostKeyChecking=yes" in text
    assert "B2B_SOURCE=" in text
    assert "B2B_EXTRA_BRAVE_ENABLED=" in text
    assert "CRM_WRITES=0;PG_WRITES=0;RESTARTS=0" in text
    for banned in ("crm.item.update", "DELETE FROM", "INSERT INTO", "docker restart",
                   "docker compose up", "workflow_dispatch"):
        assert banned not in text
