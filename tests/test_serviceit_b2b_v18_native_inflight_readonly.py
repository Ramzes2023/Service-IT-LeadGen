"""No-write native receipt diagnostic owner gate."""
from pathlib import Path
import ast

def test_native_receipt_diagnostic_no_write():
    s=Path("ops/serviceit_vps_b2b_v18_native_inflight_audit.py").read_text("utf8")
    ast.parse(s)
    assert "SET TRANSACTION READ ONLY" in s
    assert "SAME_CARD_17" in s and "AGE_OVER_60MIN" in s
    for phrase in ("crm.item.update","UPDATE serviceit_","INSERT INTO",
                   "DELETE FROM", "DROP TABLE","tasks.task.add"):
        assert phrase not in s

def test_owner_gate():
    s=Path(".github/workflows/serviceit-vps-b2b-v18-native-inflight-readonly.yml").read_text("utf8")
    assert "SERVICEIT_B2B_V18_NATIVE_INFLIGHT_READONLY_489" in s
    assert "github.actor == 'Ramzes2023'" in s
    assert "github.event.issue.number == 489" in s
    assert "StrictHostKeyChecking=yes" in s
