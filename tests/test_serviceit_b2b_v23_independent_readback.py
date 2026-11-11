"""Separate read-only V23 receipt check must never replay Bitrix writes."""
from pathlib import Path
import ast


def test_v23_independent_verified_receipt_contract_and_owner_trigger():
    path=Path("ops/serviceit_vps_b2b_v23_independent_readback.py")
    src=path.read_text("utf8")
    wf=Path(".github/workflows/serviceit-vps-b2b-v23-independent-readback.yml").read_text("utf8")
    ast.parse(src)
    assert "SET TRANSACTION READ ONLY" in src
    assert "serviceit_vps_b2b_v23_primary_source_receipts" in src
    assert "original_nine(current)==after" in src
    assert "compute_digest(after,salt)==digest" in src
    assert "after[REPORT].startswith(before[REPORT])" in src
    assert "changed==AFFECTED" in src
    assert "CARD_1_NATIVE_PENDING" in src
    assert "crm.item.update" not in src
    assert "crm.timeline.comment.add" not in src
    assert "tasks.task.add" not in src
    assert "github.actor == 'Ramzes2023'" in wf
    assert "github.event.issue.number == 489" in wf
    assert "SERVICEIT_B2B_V23_INDEPENDENT_READBACK_489" in wf
    assert "git diff --quiet" in wf
    assert "StrictHostKeyChecking=yes" in wf
    assert "V23_AUDIT_ALL_THREE_INDEPENDENTLY_VERIFIED=YES" in wf
