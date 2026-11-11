"""V20 read-only live audit contract: no CRM writes or secret disclosure."""
from pathlib import Path
import ast
def test_v20_no_write_owner_workflow():
    workflow=Path(".github/workflows/serviceit-vps-b2b-v20-live-readonly.yml").read_text("utf8")
    script=Path("ops/serviceit_vps_b2b_v20_live_readonly.py").read_text("utf8")
    ast.parse(script)
    assert "github.actor == 'Ramzes2023'" in workflow
    assert "github.event.issue.number == 489" in workflow
    assert "SERVICEIT_B2B_V20_LIVE_READONLY_489" in workflow
    assert "StrictHostKeyChecking=yes" in workflow
    assert "git diff --quiet" in workflow
    assert "SET TRANSACTION READ ONLY" in script
    assert "V20_LIVE_" in script
    assert "crm.item.update" not in script
    assert "crm.item.add" not in script
    assert "ON CONFLICT" not in script
    assert "DELETE FROM" not in script
    assert "UPDATE serviceit" not in script
    assert "SERVICEIT_BITRIX_WEBHOOK" not in script
