"""V26 superseded native AI_SUMMARY: PG-only historical status CAS safety."""
from pathlib import Path
import ast

def test_v26_require_proof_before_historical_ack_and_no_bitrix_mutation():
    code=Path("ops/serviceit_vps_b2b_v26_supersede_native_summary.py").read_text("utf8")
    flow=Path(".github/workflows/serviceit-vps-b2b-v26-superseded-native-summary.yml").read_text("utf8")
    ast.parse(code)
    assert 'NS="AI_REPAIR_SUMMARY_V1"' in code
    assert 'TARGET="NEEDS_REVIEW"' in code
    assert 'KEY=PREFIX+"AI_SUMMARY"' in code
    assert 'old_status' not in code or "WRITE_INTENT" in code
    assert "compute_digest(after,salt_v11)" in code
    assert "original_nine(item)==after" in code
    assert "digested(old_value,bytes(salt))" in code
    assert 'fingerprint(first)==fingerprint(second)' in code
    assert "status='NEEDS_REVIEW',updated_at=now()" in code
    assert "status='WRITE_INTENT'" in code
    assert "digest_salt=%s AND expected_digest=%s" in code
    assert "updated_at=%s" in code
    assert 'PG_STATUS_UPDATED' in code
    assert "crm.item.update" not in code
    assert "crm.timeline.comment.add" not in code
    assert "tasks.task.add" not in code
    assert "DELETE FROM" not in code
    assert "github.actor == 'Ramzes2023'" in flow
    assert "github.event.issue.number == 489" in flow
    assert "SERVICEIT_B2B_V26_FIRST_PREVIEW_489" in flow
    assert "SERVICEIT_B2B_V26_FIRST_ONEUSE_PG_ONLY_489" in flow
    assert "git diff --quiet" in flow
    assert "StrictHostKeyChecking=yes" in flow
