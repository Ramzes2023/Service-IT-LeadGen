"""V19 four-card source selection read-only SQL status audit."""
import ast
from pathlib import Path

def test_selection_audit_never_modifies_crm_or_receipts():
    text=Path("ops/serviceit_vps_b2b_v19_selection_readonly.py").read_text("utf8")
    ast.parse(text)
    assert "SET TRANSACTION READ ONLY" in text
    assert "select_one(conn,preview=True)" in text
    assert "select_one(conn,preview=False)" in text
    assert "CARD_" in text and "V19_RECEIPT" in text
    for forbidden in ("crm.item.update","crm.item.add","INSERT INTO",
                      "UPDATE serviceit_","DELETE FROM","DROP TABLE","tasks.task.add"):
        assert forbidden not in text

def test_selection_audit_owner_only():
    text=Path(".github/workflows/serviceit-vps-b2b-v19-four-selection-readonly.yml").read_text("utf8")
    assert 'SERVICEIT_B2B_V19_FOUR_SELECTION_READONLY_489' in text
    assert "github.actor == 'Ramzes2023'" in text
    assert "github.event.comment.user.login == 'Ramzes2023'" in text
    assert "github.event.issue.number == 489" in text
    assert "!github.event.issue.pull_request" in text
    assert "StrictHostKeyChecking=yes" in text
    assert 'V19_SELECT_FOUR_ORIGINAL=YES' in text
    assert "workflow_dispatch:" not in text
