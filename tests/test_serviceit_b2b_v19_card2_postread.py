"""V19 second signed real card acceptance uses Bitrix and PG read-only."""
import ast
from pathlib import Path

def test_source_addendum_postread_is_safely_scoped():
    s=Path("ops/serviceit_vps_b2b_v19_second_card_postread.py").read_text("utf8")
    ast.parse(s)
    assert "SET TRANSACTION READ ONLY" in s
    assert "select_one(conn,preview=True)" in s
    assert "four[1]" in s
    assert "crm.item.get" in s
    assert "NINE_FIELDS_DIGEST_READBACK" in s
    assert "ORIGINAL_TEXT_PRESERVED" in s
    assert "EXACT_THREE_NARRATIVE_FIELDS" in s
    assert "V19_SOURCED_ADDENDUM_PRESENT" in s
    assert "INDEPENDENT_ACCEPTANCE" in s
    for forbidden in ("crm.item.update","crm.item.add","INSERT INTO","UPDATE serviceit_",
                      "DELETE FROM", "DROP TABLE", "tasks.task.add","investigate_routerai"):
        assert forbidden not in s

def test_postread_requires_owner():
    s=Path(".github/workflows/serviceit-vps-b2b-v19-card2-postread.yml").read_text("utf8")
    assert "SERVICEIT_B2B_V19_SECOND_CARD_POSTREAD_489" in s
    assert "github.actor == 'Ramzes2023'" in s
    assert "github.event.comment.user.login == 'Ramzes2023'" in s
    assert "github.event.issue.number == 489" in s
    assert "!github.event.issue.pull_request" in s
    assert "StrictHostKeyChecking=yes" in s
    assert "V19_CARD2_POSTREAD_INDEPENDENT_ACCEPTANCE=YES" in s
    assert "workflow_dispatch:" not in s
