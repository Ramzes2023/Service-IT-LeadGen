"""Acceptance for no-write historical V16 real-card comparison."""
import ast
from pathlib import Path

SCRIPT=Path("ops/serviceit_vps_b2b_crossportal_four_v16_preview.py")
FLOW=Path(".github/workflows/serviceit-vps-b2b-v16-four-real-preview.yml")


def test_v16_four_preview_readonly_does_not_write_or_call_model():
    s=SCRIPT.read_text("utf8")
    ast.parse(s)
    assert "serviceit_vps_b2b_public_v15_receipts" in s
    assert "V15_SNAPSHOT_CRM_DRIFT" in s
    assert 'say(prefix+"NEW_UNSEEN_QUOTES"' in s
    assert 'say(prefix+"NEW_TECH_FAMILIES"' in s
    assert "SET TRANSACTION READ ONLY" in s
    for bad in ("crm.item.update","crm.item.add","tasks.task.add",
                "investigate_routerai(","insert_new(","claim_one(",
                "UPDATE serviceit_", "INSERT INTO serviceit_"):
        assert bad not in s


def test_v16_four_preview_gated_to_owner_and_production_commit():
    f=FLOW.read_text("utf8")
    assert "SERVICEIT_B2B_V16_FOUR_REAL_PREVIEW_READONLY_368" in f
    assert "github.actor == 'Ramzes2023'" in f
    assert "github.event.issue.number == 368" in f
    assert "e08f1dca84548cf782060e08f19e9704b5a0d5dd" in f
    assert "serviceit_vps_b2b_crossportal_four_v16_preview.py" in f
    assert "StrictHostKeyChecking=yes" in f
    assert "UserKnownHostsFile=" in f
    assert "READONLY_FINISHED=YES" in f
    assert "crm.item.update" not in f
