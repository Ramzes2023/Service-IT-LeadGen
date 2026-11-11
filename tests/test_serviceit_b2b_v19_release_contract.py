"""V19 production release/read-only pilot contract and owner-only boundaries."""
import ast
from pathlib import Path

FILES=("serviceit_b2b_tender_index_v18.py","serviceit_b2b_dossier_v11.py",
       "serviceit_vps_worker.py","serviceit_b2b_v19_backfill.py")

def test_v19_exact_four_module_release_safe_rollback():
    s=Path("ops/serviceit_vps_b2b_v19_evidence_activate.py").read_text("utf8")
    ast.parse(s)
    assert all('"'+x+'"' in s for x in FILES)
    assert '"MISSING"' in s and "AUDITED_OLD_SHAS" in s
    assert '"--network", "none"' in s
    assert '"--read-only"' in s
    assert '"--no-deps", "--no-build"' in s
    assert "pending_source_writes()" in s
    assert "V19_RELEASE_VPS_LIVE_ENABLED=YES" in s
    assert "V19_RELEASE_CRM_WRITES_BY_INSTALLER=NO" in s
    assert "V19_OFFLINE_SMOKE_PASS" in s
    for forbidden in ("crm.item.update", "crm.item.add", "tasks.task.add"):
        assert forbidden not in s

def test_v19_owner_only_release_and_preview_gates():
    for file,marker in (
        (".github/workflows/serviceit-vps-b2b-v19-evidence-release.yml",
         "SERVICEIT_B2B_V19_ADD_SOURCE_GUARDED_RELEASE_489"),
        (".github/workflows/serviceit-vps-b2b-v19-four-live-preview.yml",
         "SERVICEIT_B2B_V19_FOUR_REAL_ADDITIVE_PREVIEW_489")):
        s=Path(file).read_text("utf8")
        assert marker in s
        assert "github.actor == 'Ramzes2023'" in s
        assert "github.event.comment.user.login == 'Ramzes2023'" in s
        assert "github.event.issue.number == 489" in s
        assert "!github.event.issue.pull_request" in s
        assert "StrictHostKeyChecking=yes" in s
        assert 'serviceit-vps-readonly' in s
        assert "workflow_dispatch:" not in s
    release=Path(".github/workflows/serviceit-vps-b2b-v19-evidence-release.yml").read_text("utf8")
    preview=Path(".github/workflows/serviceit-vps-b2b-v19-four-live-preview.yml").read_text("utf8")
    assert "V19_RELEASE_VPS_LIVE_ENABLED=YES" in release
    assert "V19_REAL_FOUR_PREVIEWED=4" in preview
    assert "V19_REAL_FOUR_CRM_WRITES=0" in preview

def test_v19_preview_module_is_no_write_and_exact_four_sources():
    s=Path("ops/serviceit_vps_b2b_v19_four_live_preview.py").read_text("utf8")
    ast.parse(s)
    assert all('"'+x+'"' in s for x in FILES)
    assert "run_once(env,client,conn,preview=True)" in s
    assert "EXACT_FOUR_MODULE_SHAS" in s
    for forbidden in ("crm.item.update","crm.item.add","INSERT INTO",
                      "UPDATE serviceit_","tasks.task.add"):
        assert forbidden not in s
