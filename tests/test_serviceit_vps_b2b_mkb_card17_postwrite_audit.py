"""Postwrite audit cannot mutate source, CRM or retry uncertain write."""
import ast
from pathlib import Path

def test_readonly_proof_and_exact_card():
    path=Path("ops/serviceit_vps_b2b_mkb_card17_postwrite_audit.py")
    s=path.read_text("utf8")
    ast.parse(s)
    assert "card_id=17" in s
    assert '"entityTypeId":1062,"id":17' in s
    assert "SET TRANSACTION READ ONLY" in s
    assert "PG_RECEIPT_STATUS" in s
    assert "FACTUAL_APPEND_PRESENT" in s
    assert "ORIGINAL_LINK_MATCH" in s
    assert "EXPECTED_V16_SHA" in s
    for forbidden in ("crm.item.update", "crm.item.add", "INSERT INTO",
                      "UPDATE serviceit_", "DROP TABLE", "CREATE TABLE",
                      "tasks.task.add", "client.post("):
        assert forbidden not in s

def test_owner_guard_for_postwrite_audit():
    f=Path(".github/workflows/serviceit-vps-b2b-four-public-crossportal-readonly.yml").read_text("utf8")
    assert "  mkb_4675475_postwrite_audit:\n" in f
    assert "SERVICEIT_B2B_MKB_4675475_CARD17_AUDIT_READONLY_368" in f
    assert "github.actor == 'Ramzes2023'" in f
    assert "github.event.comment.user.login == 'Ramzes2023'" in f
    assert f.count("github.event.issue.number == 368")==6
    assert "StrictHostKeyChecking=yes" in f
    assert "UserKnownHostsFile=" in f
