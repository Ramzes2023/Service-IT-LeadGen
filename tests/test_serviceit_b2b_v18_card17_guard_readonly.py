"""One-use real-card guard audit must remain read-only."""
import ast
from pathlib import Path

def test_v18_guard_audit_readonly_and_scoped_to_card17():
    t=Path("ops/serviceit_vps_b2b_v18_card17_guard_audit.py").read_text("utf8")
    ast.parse(t)
    assert "TARGET=17" in t and 'SOURCE="B2B-CENTER-4675475"' in t
    assert "SET TRANSACTION READ ONLY" in t
    assert "crm.item.get" in t and "validate_identity(item,TARGET)" in t
    assert "REPORT_FREE_SPACE" in t and "RECEIPT_STATUS" in t
    assert "NATIVE_INFLIGHT" in t and "TDI_INFLIGHT" in t
    for unsafe in ("crm.item.update","crm.item.add","INSERT INTO","UPDATE serviceit_",
                   "tasks.task.add","DROP TABLE","DELETE FROM"):
        assert unsafe not in t

def test_owner_approved_workflow_only():
    t=Path(".github/workflows/serviceit-vps-b2b-v18-card17-guard-readonly.yml").read_text("utf8")
    assert "SERVICEIT_B2B_V18_MKB_4675475_GUARD_READONLY_489" in t
    assert "github.actor == 'Ramzes2023'" in t
    assert "github.event.comment.user.login == 'Ramzes2023'" in t
    assert "github.event.issue.number == 489" in t
    assert "StrictHostKeyChecking=yes" in t
    assert "V18_17_GUARD_ALL_9_AI_FIELDS=YES" in t
