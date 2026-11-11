"""B2B V20 production upgrade remains SHA-pinned and OFF by default."""
from pathlib import Path
import ast


def test_v20_release_is_owner_gated_and_no_auto_on_merge():
    workflow = Path(".github/workflows/serviceit-vps-b2b-v20-guarded-install.yml").read_text("utf8")
    assert "issue_comment:" in workflow
    assert "github.actor == 'Ramzes2023'" in workflow
    assert "github.event.issue.number == 489" in workflow
    assert "SERVICEIT_B2B_V20_GUARDED_INSTALL_489" in workflow
    assert "environment: serviceit-vps-readonly" in workflow
    assert "StrictHostKeyChecking=yes" in workflow
    assert "old_worker_reviewed.py" in workflow
    assert "git diff --quiet" in workflow
    assert "V20_RELEASE_CRM_WRITES_BY_INSTALLER=NO" in workflow


def test_v20_installer_has_live_sha_rollback_offline_smoke():
    source = Path("ops/serviceit_vps_b2b_v20_guarded_install.py").read_text("utf8")
    ast.parse(source)
    assert 'FILES = ("serviceit_vps_worker.py", "serviceit_b2b_v20_versioned.py")' in source
    assert "EXISTING_CORE_SHA_DRIFT_NO_DEPLOY" in source
    assert "PENDING_AI_OR_CRM_WRITE_NO_RESTART" in source
    assert "V19_PENDING_UNVERIFIED" in source
    assert "AUDITED_OLD_SHAS = (reviewed_old_worker_sha, \"MISSING\")" in source
    assert '"--network", "none"' in source
    assert '"--read-only"' in source
    assert "MIRROR_OFFLINE_SMOKE_FAILED" in source
    assert "MIRROR_COMPOSE_NON_IMAGE_DRIFT" in source
    assert "MIRROR_ADJACENT_CONTAINER_CHANGED" in source
    assert "V20_RELEASE_CRM_WRITES_BY_INSTALLER=NO" in source
    assert "V20_RELEASE_ROLLBACK=YES" in source
    assert "crm.item.update" not in source
    assert "SERVICEIT_VPS_B2B_V20_VERSIONED_ENABLED" not in source
