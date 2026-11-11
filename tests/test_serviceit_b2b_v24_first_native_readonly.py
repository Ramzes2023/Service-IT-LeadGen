"""Safe V24 first original B2B native fence digest: read-only contract."""
from pathlib import Path
import ast


def test_native_pending_readback_is_owner_only_no_writes():
    src=Path("ops/serviceit_vps_b2b_v24_first_native_readonly.py").read_text("utf8")
    wf=Path(".github/workflows/serviceit-vps-b2b-v24-first-native-readonly.yml").read_text("utf8")
    ast.parse(src)
    assert "SET TRANSACTION READ ONLY" in src
    assert ".reconcile(card,rest)" in src
    assert "expected_digest IS NOT NULL" in src
    assert "NATIVE_NAMESPACES" in src
    assert 'SELECT namespace,status' in src
    assert "PostgresNativeFence(" in src
    assert "crm.item.update" not in src
    assert "UPDATE serviceit_vps_native_receipts" not in src
    assert "DELETE FROM " not in src
    assert "github.actor == 'Ramzes2023'" in wf
    assert "github.event.issue.number == 489" in wf
    assert "SERVICEIT_B2B_V24_FIRST_NATIVE_READONLY_489" in wf
    assert "git diff --quiet" in wf
    assert "StrictHostKeyChecking=yes" in wf
    assert "V24_FIRST_CRM_WRITES=NO" in wf
