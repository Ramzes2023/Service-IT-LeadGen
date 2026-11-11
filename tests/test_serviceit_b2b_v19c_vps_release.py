"""Owner-scoped V19C daily follow-up VPS rollout must be fail-closed."""
from pathlib import Path
import ast

MODULES=("serviceit_b2b_v19_backfill.py","serviceit_vps_worker.py")

def test_v19c_exact_sha_two_module_offline_rollback():
    text=Path("ops/serviceit_vps_b2b_v19c_daily_retry_activate.py").read_text("utf8")
    ast.parse(text)
    assert 'FILES = ("serviceit_b2b_v19_backfill.py", "serviceit_vps_worker.py")' in text
    assert "AUDITED_OLD_SHAS" in text
    assert "701b903716b82ab044e71120012028c55b566a9d7c15ff3e7b125d5801eef65a" in text
    assert "6d436dcdb8a58c36374882036d604d5aa4dfcc6143e0ed9516e8d765a898cde9" in text
    for required in ('"--network", "none"','"--read-only"',
                     '"--no-deps", "--no-build"',"pending_source_writes()",
                     "V19C_OFFLINE_SMOKE_PASS",
                     "V19C_RELEASE_VPS_LIVE_ENABLED=YES",
                     "V19C_RELEASE_CRM_WRITES_BY_INSTALLER=NO",
                     "V19C_RELEASE_ROLLBACK"):
        assert required in text
    for bad in ("crm.item.update","crm.item.add","tasks.task.add"):
        assert bad not in text

def test_v19c_owner_gated_real_release():
    flow=Path(".github/workflows/serviceit-vps-b2b-v19c-daily-retry-enable.yml").read_text("utf8")
    assert "SERVICEIT_B2B_V19C_DAILY_RETRY_ENABLE_RELEASE_489" in flow
    assert "github.actor == 'Ramzes2023'" in flow
    assert "github.event.comment.user.login == 'Ramzes2023'" in flow
    assert "github.event.issue.number == 489" in flow
    assert "!github.event.issue.pull_request" in flow
    assert "StrictHostKeyChecking=yes" in flow
    assert "serviceit-vps-readonly" in flow
    assert 'names=(serviceit_b2b_v19_backfill.py serviceit_vps_worker.py)' in flow
    assert "V19C_RELEASE_VPS_LIVE_ENABLED=YES" in flow
    assert "V19C_RELEASE_CRM_WRITES_BY_INSTALLER=NO" in flow
    assert "workflow_dispatch:" not in flow
