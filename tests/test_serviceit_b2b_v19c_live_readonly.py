"""Independent V19C real coordinator, 5-minute flag and PG receipt audit is read-only."""
import ast
from pathlib import Path

def test_v19c_live_readonly_proof_no_crm_or_pg_mutations():
    s=Path("ops/serviceit_vps_b2b_v19c_live_five_minute_readonly.py").read_text("utf8")
    ast.parse(s)
    assert "SET TRANSACTION READ ONLY" in s
    assert "FOLLOWUP_ACTIVE_FOR_CYCLE" in s
    assert "FIVE_MINUTE_INTERVAL" in s
    assert "TWO_MODULES_PINNED" in s
    assert "NEXT_SCHEDULABLE_ORIGINAL_POSITION" in s
    assert "CARD_" in s and "_RECEIPT" in s
    for token in ("crm.item.update","crm.item.add","INSERT INTO","DELETE FROM",
                  "UPDATE serviceit_", "DROP TABLE","tasks.task.add",
                  "investigate_routerai", "source_findings("):
        assert token not in s

def test_owner_only_v19c_live_acceptance():
    s=Path(".github/workflows/serviceit-vps-b2b-v19c-live-cycle-readonly.yml").read_text("utf8")
    assert "SERVICEIT_B2B_V19C_LIVE_FIVE_MINUTE_READONLY_489" in s
    assert "github.actor == 'Ramzes2023'" in s
    assert "github.event.comment.user.login == 'Ramzes2023'" in s
    assert "github.event.issue.number == 489" in s
    assert "StrictHostKeyChecking=yes" in s
    assert "UserKnownHostsFile=" in s
    assert "V19C_LIVE_FOLLOWUP_ACTIVE_FOR_CYCLE=YES" in s
    assert "workflow_dispatch:" not in s
