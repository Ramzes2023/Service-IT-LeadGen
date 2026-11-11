"""Owner-gated one-use V19 write only to previously proven source-verified second real card."""
import ast
from pathlib import Path

SCRIPT=Path("ops/serviceit_vps_b2b_v19_oneuse_verified_card2.py")
FLOW=Path(".github/workflows/serviceit-vps-b2b-v19-real-card2-oneuse.yml")

def test_oneuse_is_exact_signed_card_and_does_not_disable_protection():
    s=SCRIPT.read_text("utf8")
    ast.parse(s)
    assert "preview=select_one(conn,preview=True)" in s
    assert "expected=preview[1]" in s
    assert "target=select_one(conn,preview=False)" in s
    assert "target[0]==expected[0]" in s
    assert "active_card_write(conn,target[0])" in s
    assert "run_once(env,client,conn,preview=False)" in s
    assert 'status=="V19_ADDITIVE_NINE_READBACK_VERIFIED"' in s
    assert "INTERVAL_SECONDS==300" in s
    assert "SOURCE_PIN_OK" in s
    assert "SERVICEIT_VPS_B2B_V19_BACKFILL_ENABLED" in s
    for forbidden in ("crm.item.add", "tasks.task.add", "DELETE FROM",
                      "UPDATE serviceit_vps_native_receipts",
                      "crm.contact.add", "docker compose"):
        assert forbidden not in s

def test_oneuse_workflow_requires_owner_and_runtime_exact_sha():
    s=FLOW.read_text("utf8")
    assert 'SERVICEIT_V19_REAL_SECOND_SIGNED_CARD_ONEUSE_489' in s
    assert "github.actor == 'Ramzes2023'" in s
    assert "github.event.comment.user.login == 'Ramzes2023'" in s
    assert "github.event.issue.number == 489" in s
    assert "!github.event.issue.pull_request" in s
    assert "StrictHostKeyChecking=yes" in s
    assert "V19_ADDITIVE_NINE_READBACK_VERIFIED" in SCRIPT.read_text("utf8")
    assert "B2B_V19_ONEUSE_ONE_REAL_CRM_WRITE=YES" in s
    assert '"${#shas[@]}" == 4' in s
    assert "workflow_dispatch:" not in s
