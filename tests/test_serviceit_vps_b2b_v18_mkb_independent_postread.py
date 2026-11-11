"""Postwrite acceptor must be a fully independent read-only PG/CRM audit."""
from pathlib import Path
import ast

def test_postread_receipt_proves_exact_real_nine():
    f=Path("ops/serviceit_vps_b2b_mkb_v18_independent_postread.py")
    text=f.read_text("utf8")
    ast.parse(text)
    assert "CARD=17" in text
    assert 'TENDER="4675475"' in text
    assert "SET TRANSACTION READ ONLY" in text
    assert "crm.item.get" in text
    assert "EXACT_NINE_FIELD_READBACK" in text
    assert "DURABLE_DIGEST" in text
    assert "INDEPENDENT_ACCEPTANCE" in text
    assert "EXACT_THREE_FIELDS_CHANGED" in text
    for pattern in ("crm.item.update","crm.item.add","INSERT INTO",
                    "UPDATE serviceit_","DELETE FROM", "DROP TABLE",
                    "tasks.task.add", "investigate_routerai"):
        assert pattern not in text

def test_readonly_workflow_exact_owner():
    f=Path(".github/workflows/serviceit-vps-b2b-v18-mkb-independent-postread.yml")
    text=f.read_text("utf8")
    assert "SERVICEIT_B2B_V18_MKB_4675475_POSTWRITE_READONLY_489" in text
    assert "github.actor == 'Ramzes2023'" in text
    assert "github.event.comment.user.login == 'Ramzes2023'" in text
    assert "github.event.issue.number == 489" in text
    assert "StrictHostKeyChecking=yes" in text
    assert "MKB_V18_POSTREAD_INDEPENDENT_ACCEPTANCE=YES" in text
