"""V19B one-file V19 queue unblock exact guarded release contract."""
import ast
from pathlib import Path

def test_v19b_source_only_release_never_mutates_crm():
    text=Path("ops/serviceit_vps_b2b_v19b_unblocked_queue_activate.py").read_text("utf8")
    ast.parse(text)
    assert 'FILES = ("serviceit_b2b_v19_backfill.py",)' in text
    assert '002fab9fec1e5a59c8b7f74f0cbd174912d32ed1fdd82b4188f1d1783338cb1a' in text
    assert '"--network", "none"' in text
    assert '"--read-only"' in text
    assert '"--no-deps", "--no-build"' in text
    assert 'pending_source_writes()' in text
    assert 'V19B_OFFLINE_SMOKE_PASS' in text
    assert 'V19B_RELEASE_CRM_WRITES_BY_INSTALLER=NO' in text
    assert 'V19B_RELEASE_ROLLBACK' in text
    for forbidden in ('crm.item.update','crm.item.add','tasks.task.add'):
        assert forbidden not in text

def test_owner_gate_only_and_one_module_sha():
    s=Path(".github/workflows/serviceit-vps-b2b-v19b-queue-onefile-release.yml").read_text("utf8")
    assert 'SERVICEIT_B2B_V19B_UNBLOCK_QUEUE_ONEFILE_RELEASE_489' in s
    assert "github.actor == 'Ramzes2023'" in s
    assert "github.event.comment.user.login == 'Ramzes2023'" in s
    assert "github.event.issue.number == 489" in s
    assert "!github.event.issue.pull_request" in s
    assert '"$#" == 1' in s
    assert '"${#shas[@]}" == 1' in s
    assert "StrictHostKeyChecking=yes" in s
    assert "workflow_dispatch:" not in s
    assert 'V19B_RELEASE_VPS_LIVE_ENABLED=YES' in s

def test_native_stale_receipt_filtered_but_durable_receipt_untouched():
    code=Path("leadpilot/serviceit_b2b_v19_backfill.py").read_text("utf8")
    assert "SELECT 1 FROM serviceit_vps_native_receipts n" in code
    assert "n.card_id=b.card_id" in code
    assert "n.status IN ('RESERVED','WRITE_INTENT')" in code
    assert "UPDATE serviceit_vps_native_receipts" not in code
