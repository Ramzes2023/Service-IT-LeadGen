"""V19 read-only live SHA audit must never touch PG or Bitrix."""
from pathlib import Path
import ast

def test_origin_probe_no_side_effects_and_four_hashes():
    s=Path("ops/serviceit_vps_b2b_v19_live_module_origin.py").read_text("utf8")
    ast.parse(s)
    assert '"serviceit_b2b_v19_backfill.py"' in s
    assert 'OLD_V19=' in s
    assert 'V19_ORIGIN_CRM_WRITES=0' in s
    assert 'MODULE_' in s
    for forbidden in ('crm.item.update','crm.item.add','INSERT INTO','UPDATE serviceit_','DELETE FROM','subprocess.run','docker build'):
        assert forbidden not in s

def test_origin_probe_owner_approval_gate():
    s=Path(".github/workflows/serviceit-vps-b2b-v19-live-origin-readonly.yml").read_text("utf8")
    assert 'SERVICEIT_B2B_V19_LIVE_SHA_READONLY_489' in s
    assert "github.actor == 'Ramzes2023'" in s
    assert "github.event.comment.user.login == 'Ramzes2023'" in s
    assert "github.event.issue.number == 489" in s
    assert "!github.event.issue.pull_request" in s
    assert 'StrictHostKeyChecking=yes' in s
    assert 'V19_ORIGIN_CRM_WRITES=0' in s
    assert 'workflow_dispatch:' not in s
