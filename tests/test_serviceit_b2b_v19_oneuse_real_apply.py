"""Signed V19 one-use live pilot: owner only, exactly one CRM card, SHA fence."""
from pathlib import Path
import ast

def test_v19_real_oneuse_script_safe_no_fresh_leads():
    s=Path("ops/serviceit_vps_b2b_v19_oneuse_real_apply.py").read_text("utf8")
    ast.parse(s)
    assert "FILES=(\"serviceit_b2b_v19_backfill.py\",\"serviceit_vps_worker.py\")" in s
    assert "LIVE_SOURCE_SHA" in s
    assert "V19_ADDITIVE_NINE_READBACK_VERIFIED" in s
    assert "AUTOMATIC_V19_OFF" in s
    assert "run_once(env,client,conn)" in s
    for danger in ("crm.item.add","crm.contact.add","tasks.task.add",
                   "crm.item.update", "INSERT INTO","DELETE FROM"):
        assert danger not in s

def test_v19_real_oneuse_workflow_owner_exact_marker_and_pinned():
    s=Path(".github/workflows/serviceit-vps-b2b-v19-once-real-apply.yml").read_text("utf8")
    assert "SERVICEIT_B2B_V19_ONCE_REAL_CARD_EVIDENCE_489" in s
    assert "github.actor == 'Ramzes2023'" in s
    assert "github.event.comment.user.login == 'Ramzes2023'" in s
    assert "github.event.issue.number == 489" in s
    assert "!github.event.issue.pull_request" in s
    assert "StrictHostKeyChecking=yes" in s
    assert "V19_ONCE_ONE_CRM_WRITE_VERIFIED=YES" in s
    assert "workflow_dispatch:" not in s
