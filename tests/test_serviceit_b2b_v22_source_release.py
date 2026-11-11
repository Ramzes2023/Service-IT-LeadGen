"""Guarded V22 VPS release is old-SHA attested, code-only and reversible."""
from pathlib import Path
import ast

SCRIPT=Path("ops/serviceit_vps_b2b_v22_source_release.py")
WORKFLOW=Path(".github/workflows/serviceit-vps-b2b-v22-source-release.yml")

def test_v22_owner_only_source_release_workflow():
    w=WORKFLOW.read_text("utf8")
    assert "github.actor == 'Ramzes2023'" in w
    assert "github.event.issue.number == 489" in w
    assert "SERVICEIT_B2B_V22_SOURCE_RELEASE_489" in w
    assert "StrictHostKeyChecking=yes" in w
    assert "git diff --quiet" in w
    assert "git show" in w and "old-hashes" in w
    assert "serviceit_b2b_tender_index_v18.py" in w
    assert "serviceit_b2b_v19_backfill.py" in w
    assert "serviceit_b2b_routerai_deep.py" in w
    assert "V22_RELEASE_CRM_WRITES_BY_INSTALLER=NO" in w


def test_v22_offline_install_doesnt_interrupt_active_writes_or_core_flags():
    src=SCRIPT.read_text("utf8")
    ast.parse(src)
    assert "V19_V20_PENDING_UNVERIFIED" in src
    assert "PENDING_AI_OR_CRM_WRITE_NO_RESTART" in src
    assert "EXISTING_CORE_SHA_DRIFT_NO_DEPLOY" in src
    assert "V22_OFFLINE_SMOKE_PASS" in src
    assert '"--network", "none"' in src
    assert '"--read-only"' in src
    assert '"SERVICEIT_VPS_B2B_V20_VERSIONED_ENABLED": "YES"' in src
    assert "MIRROR_COMPOSE_NON_IMAGE_DRIFT" in src
    assert "MIRROR_ADJACENT_CONTAINER_CHANGED" in src
    assert "V22_RELEASE_ROLLBACK=YES" in src
    assert "V22_RELEASE_CRM_WRITES_BY_INSTALLER=NO" in src
    assert "crm.item.update" not in src
    assert "crm.item.add" not in src
    assert "tasks.task.add" not in src


def test_v22_source_release_requires_six_sha_args():
    src=SCRIPT.read_text("utf8")
    assert "len(sys.argv) != 2 + 2*len(FILES)" in src
    assert "AUDITED_OLD_SHAS = old_hashes" in src
    assert 'len(old_hashes) == len(FILES)' in src
    assert "runtime_shas() == AUDITED_OLD_SHAS" in src
