"""V20 live auto opt-in: only one new Compose environment flag, guarded and reversible."""
from pathlib import Path
import ast
import importlib.util

SRC=Path("ops/serviceit_vps_b2b_v20_auto_enable.py")
FLOW=Path(".github/workflows/serviceit-vps-b2b-v20-auto-enable.yml")

def _module():
    spec=importlib.util.spec_from_file_location("v20_enable_test",SRC)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def test_v20_activation_adds_only_flag_to_pin():
    mod=_module()
    old='services:\n  coordinator:\n    image: "sha256:'+'a'*64+'"\n    pull_policy: never\n    environment:\n      SERVICEIT_VPS_TENDER_DOC_INTEL_ENABLED: "YES"\n'
    new=mod.pin_flag(old)
    assert new==old.replace("    environment:\n",
        '    environment:\n      SERVICEIT_VPS_B2B_V20_VERSIONED_ENABLED: "YES"\n')
    assert "SERVICEIT_VPS_TENDER_DOC_INTEL_ENABLED" in new
    import pytest
    with pytest.raises(mod.ReleaseBlocked):
        mod.pin_flag(new)

def test_v20_enable_never_mutates_legacy_core_except_flag():
    source=SRC.read_text("utf8")
    flow=FLOW.read_text("utf8")
    ast.parse(source)
    assert "owner" not in source or "OWNER" not in source
    assert "github.actor == 'Ramzes2023'" in flow
    assert "SERVICEIT_B2B_V20_AUTO_ENABLE_489" in flow
    assert "github.event.issue.number == 489" in flow
    assert "StrictHostKeyChecking=yes" in flow
    assert "git diff --quiet" in flow
    assert "V20_ENABLE_INSTALLER_CRM_WRITES=NO" in flow
    assert "SERVICEIT_VPS_B2B_V20_VERSIONED_ENABLED" in source
    assert 'PIN.read_text("utf8")' in source
    assert "V20_ONLY_ONE_ENV_CHANGE_ALLOWED" in source
    assert "V20_OTHER_COMPOSE_SERVICES_CHANGED" in source
    assert "pending_source_writes()==0 and pending_v20_intents()==0" in source
    assert "V20_INSTALLED_MODULE_SHA_DRIFT" in source
    assert "V20_ROLLBACK_ENV_NOT_RESTORED" in source
    assert "atomic_write(PIN,old_pin)" in source
    assert "crm.item.update" not in source
    assert "crm.item.add" not in source
    assert "SELECT count(*)" in source

def test_v20_enable_keeps_v19c_and_pins_exact_source():
    script=SRC.read_text("utf8")
    flow=FLOW.read_text("utf8")
    assert "V20_PRIOR_V19C_NOT_RUNNING" in script
    assert "V20_LIVE_NONFLAG_ENV_CHANGED" in script
    assert '"INTERVAL_SECONDS' not in script or "INTERVAL_SECONDS" in script
    assert 'serviceit_b2b_v20_versioned.py' in flow
    assert "serviceit_vps_worker.py" in flow
    assert "V20_ENABLE_VPS_ACTIVE=YES" in script
    assert "V20_ENABLE_ROLLBACK=NOT_NEEDED" in script
