"""Owner-approved Olga 605 internal-review task migration tests.

All tests are offline and use synthetic users only; never invoke Bitrix REST,
RouterAI, tasks.task.add, CRM writes, or the production VPS.
"""
import importlib.util
from pathlib import Path
import stat

import pytest

from leadpilot.serviceit_stage3_all_new_task_automation import (
    task_recipient_for_card, TaskAutomationBlocked,
)

SCRIPT = Path("deploy/serviceit-vps-olga-task-readonly-prepare.py")
spec = importlib.util.spec_from_file_location("vps_olga605_prepare", SCRIPT)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

BASE = (
    "SERVICEIT_VPS_WRITER_MODE=READONLY\n"
    "SERVICEIT_VPS_CUTOVER_AUTHORIZED=NO\n"
    "SERVICEIT_VPS_POLL_ENABLED=NO\n"
    "SERVICEIT_VPS_INTAKE_ENABLED=NO\n"
    "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED=YES\n"
    "SERVICEIT_ROUTERAI_PRIVACY_APPROVED=YES_1062_21\n"
    "SERVICEIT_VPS_MANAGER_TASK_ENABLED=NO\n"
    "PRESERVE_PRIVATE_CONFIG=DO_NOT_LOG_OR_CHANGE\n"
)


def config(tmp_path, value=BASE):
    f = tmp_path / "vps.env"
    f.write_text(value, encoding="utf8")
    f.chmod(0o600)
    return f


def test_save_only_two_names_with_backup_and_idempotence(tmp_path):
    f = config(tmp_path)
    assert mod.apply(f) == "PREPARED"
    written = f.read_text(encoding="utf8")
    assert written == BASE + (
        "SERVICEIT_VPS_TASK_ASSIGNEE_APPROVED=YES_1062_21_INTERNAL_MANAGER\n"
        "SERVICEIT_VPS_TASK_ASSIGNEE_ID=605\n"
    )
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    copies = list(tmp_path.glob("vps.env.bak-olga605-*"))
    assert len(copies) == 1
    assert copies[0].read_text(encoding="utf8") == BASE
    assert stat.S_IMODE(copies[0].stat().st_mode) == 0o600
    assert mod.apply(f) == "ALREADY_PREPARED"
    assert len(list(tmp_path.glob("vps.env.bak-olga605-*"))) == 1


@pytest.mark.parametrize("changed", [
    "SERVICEIT_VPS_WRITER_MODE=LIVE",
    "SERVICEIT_VPS_CUTOVER_AUTHORIZED=YES",
    "SERVICEIT_VPS_POLL_ENABLED=YES",
    "SERVICEIT_VPS_INTAKE_ENABLED=YES",
    "SERVICEIT_VPS_MANAGER_TASK_ENABLED=YES",
    "SERVICEIT_VPS_OLD_GH_WRITERS_DISABLED_ACK=YES",
    "SERVICEIT_VPS_NATIVE_ACTIVATION_UTC=2026-11-08T15:00:00Z",
    "SERVICEIT_VPS_CRM_DUPLICATE_ENABLED=YES",
])
def test_reject_all_active_writers_and_existing_cutover(tmp_path, changed):
    key = changed.split("=",1)[0]
    original = BASE
    if (key + "=") in original:
        original = "\n".join(changed if l.startswith(key+"=") else l
                             for l in original.split("\n"))
    else:
        original += changed + "\n"
    f = config(tmp_path, original)
    with pytest.raises(mod.Blocked):
        mod.apply(f)
    assert f.read_text(encoding="utf8") == original


def test_reject_replacing_unapproved_employee_or_weak_file(tmp_path):
    f = config(tmp_path, BASE + "SERVICEIT_VPS_TASK_ASSIGNEE_ID=777\n")
    with pytest.raises(mod.Blocked):
        mod.apply(f)
    f.write_text(BASE, encoding="utf8")
    f.chmod(0o644)
    with pytest.raises(mod.Blocked):
        mod.apply(f)


def test_vps_new_task_always_goes_to_olga_but_card_owner_is_unchanged():
    env = {
        "SERVICEIT_VPS_TASK_ASSIGNEE_APPROVED": "YES_1062_21_INTERNAL_MANAGER",
        "SERVICEIT_VPS_TASK_ASSIGNEE_ID": "605",
    }
    # A native task can go to Olga even if CRM item has another assigned user.
    assert task_recipient_for_card("777", env, native_fence=object()) == 605
    assert task_recipient_for_card(None, env, native_fence=object()) == 605
    # Legacy cloud task processing preserves current card owner.
    assert task_recipient_for_card("777", env, native_fence=None) == 777


def test_native_task_never_accepts_unapproved_or_other_user():
    with pytest.raises(TaskAutomationBlocked):
        task_recipient_for_card("777", {
            "SERVICEIT_VPS_TASK_ASSIGNEE_ID":"605",
            "SERVICEIT_VPS_TASK_ASSIGNEE_APPROVED":"NO",
        }, native_fence=object())
    with pytest.raises(TaskAutomationBlocked):
        task_recipient_for_card("777", {
            "SERVICEIT_VPS_TASK_ASSIGNEE_ID":"606",
            "SERVICEIT_VPS_TASK_ASSIGNEE_APPROVED":
                "YES_1062_21_INTERNAL_MANAGER",
        }, native_fence=object())


def test_empty_stop_rules_stay_empty_and_no_task_write_implicitly_enabled():
    assert '"rules": []' in Path(
        "config/serviceit_own_signal_stop.json").read_text(encoding="utf8")
    compose = Path("deploy/serviceit-vps.compose.yml").read_text(encoding="utf8")
    assert "SERVICEIT_VPS_MANAGER_TASK_ENABLED:-NO" in compose
    assert "SERVICEIT_VPS_TASK_ASSIGNEE_ID:-" in compose
    assert "SERVICEIT_VPS_TASK_ASSIGNEE_APPROVED:-NO" in compose


def test_readonly_real_user_probe_contract_offline(monkeypatch):
    import httpx
    from leadpilot import serviceit_vps_pg_queue, bitrix_metadata_audit
    from leadpilot import serviceit_stage34_signed_readonly_sidecar as sidecar
    from leadpilot import serviceit_stage3_all_new_task_automation as task

    class ContextClient:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False
        def __enter__(self): return self
        def __exit__(self,*args): return False

    def fake_request(client,base,method,args):
        assert method == "user.get"
        assert args["FILTER"]["ID"] == 605
        assert args["FILTER"]["ACTIVE"] is True
        return {"result": [{
            "ID":"605", "NAME":"Ольга", "LAST_NAME":"Торгашина",
            "ACTIVE":True, "USER_TYPE":"employee",
        }], "total":1}

    monkeypatch.setattr(httpx,"Client",ContextClient)
    monkeypatch.setattr(serviceit_vps_pg_queue,"secret_file",lambda *a:"FAKE")
    monkeypatch.setattr(bitrix_metadata_audit,"validate_webhook",
                        lambda *a:"https://serviseit.bitrix24.ru/rest/1/FAKE")
    monkeypatch.setattr(sidecar,"request",fake_request)
    monkeypatch.setattr(task,"check_task_binding",lambda *a:None)
    env = {
        "SERVICEIT_VPS_WRITER_MODE":"READONLY",
        "SERVICEIT_VPS_CUTOVER_AUTHORIZED":"NO",
        "SERVICEIT_VPS_POLL_ENABLED":"NO",
        "SERVICEIT_VPS_INTAKE_ENABLED":"NO",
        "SERVICEIT_VPS_OLD_GH_WRITERS_DISABLED_ACK":"NO",
        "SERVICEIT_VPS_MANAGER_TASK_ENABLED":"NO",
    }
    assert mod.probe(env) == ("USER_605_VERIFIED","VERIFIED")
    env["SERVICEIT_VPS_WRITER_MODE"] = "LIVE"
    with pytest.raises(mod.Blocked):
        mod.probe(env)


def test_workflow_owner_only_readonly_user_verified_before_config_mutation():
    text = Path(".github/workflows/serviceit-vps-olga-task-readonly-prepare.yml").read_text(encoding="utf8")
    assert "github.actor == 'Ramzes2023'" in text
    assert "PREPARE_OLGA_605_READONLY" in text
    assert text.index(" --probe ") < text.index(" --apply ")
    assert "serviceit-vps-native-shadow-readonly" in text
    assert "CRM_WRITES=0;TASKS_CREATED=0;CUTOVER=NO" in text
    assert "gh workflow disable" not in text
    assert "tasks.task.add" not in text
