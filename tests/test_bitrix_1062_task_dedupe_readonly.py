"""Guarded task duplicate discovery with real Bitrix response shapes."""
from hashlib import sha256
import json

import httpx
import pytest

from leadpilot.bitrix_1062_mapping import ALL_FIELD_CODES, CONTRACT_VERSION
from leadpilot.bitrix_1062_task_dedupe_readonly import (
    TaskDiscoveryBlocked, discover_demo_manager_task, TASK_TITLE, CRM_BINDING,
)

HOOK = "https://serviseit.bitrix24.ru/rest/5/SYNTHETICFAKESECRETABCDEFG"


def base_metadata():
    fields = {name: "DEMO" for name in ALL_FIELD_CODES}
    fields.update({
        "id": 3, "entityTypeId": 1062, "categoryId": 21,
        "title": "DEMO ServiceIT AI — DEMO-ITREQ-001",
        "stageId": "DT1062_21:CLIENT", "contactIds": [], "companyId": 0,
        "UF_CRM_13_SVCIT_COMPANY_INN": "7700000009",
        "UF_CRM_13_SVCIT_COMPANY_NAME": "DEMO COMPANY-001",
        "UF_CRM_13_SVCIT_SIGNAL_SOURCE": "synthetic-fixture",
        "UF_CRM_13_SVCIT_SIGNAL_URL": "https://example.invalid/serviceit-demo/request/001",
        "UF_CRM_13_SVCIT_SIGNAL_ID": "DEMO-ITREQ-001",
        "UF_CRM_13_SVCIT_IDEMPOTENCY_KEY": "DEMO-IDEMP-" + sha256(
            f"{CONTRACT_VERSION}\x007700000009\x00DEMO-ITREQ-001".encode()
        ).hexdigest()[:32],
    })
    return {
        "crm.type.getByEntityTypeId": {"type": {
            "entityTypeId": 1062, "isUseInUserfieldEnabled": "Y",
            "linkedUserFields": {"TASKS_TASK|UF_CRM_TASK": "true"},
            "isAutomationEnabled": "N", "isBizProcEnabled": "N",
        }},
        "crm.enum.ownertype": [{"ID": 1062, "SYMBOL_CODE_SHORT": "T426"}],
        "tasks.task.getFields": {"fields": {
            "TITLE": {"type": "string"}, "RESPONSIBLE_ID": {"type": "integer"},
            "UF_CRM_TASK": {"type": "crm"},
        }},
        "crm.item.get": {"item": fields},
    }


def call(*, linked=None, titled=None, override=None):
    calls = []
    meta = base_metadata()
    linked = [] if linked is None else linked
    titled = [] if titled is None else titled
    def handler(req):
        method = req.url.path.rsplit("/", 1)[-1].removesuffix(".json")
        params = json.loads(req.content)
        calls.append((method, params))
        assert method in (*meta, "tasks.task.list")
        if method == "tasks.task.list":
            assert params["select"] == ["ID", "TITLE", "RESPONSIBLE_ID", "UF_CRM_TASK"]
            assert params["start"] == 0
            f = params["filter"]
            assert len(f) == 1
            if "UF_CRM_TASK" in f:
                assert f["UF_CRM_TASK"] == CRM_BINDING
                chosen = linked
            else:
                assert f["TITLE"] == TASK_TITLE
                chosen = titled
            if override == "scope":
                return httpx.Response(401, json={"error": "INSUFFICIENT_SCOPE", "secret": HOOK})
            if override == "many":
                return httpx.Response(200, json={"result": {"tasks": chosen}, "next": None})
            return httpx.Response(200, json={"result": {"tasks": chosen}})
        return httpx.Response(200, json={"result": meta[method]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return discover_demo_manager_task(HOOK, client=client), calls


def task(n=123):
    return {"id": str(n), "title": TASK_TITLE,
            "responsibleId": "5", "ufCrmTask": [CRM_BINDING]}


def test_no_visible_match_is_not_permission_or_global_uniqueness():
    r,calls = call()
    assert r["status"] == "NO_VISIBLE_MATCH_NOT_GLOBAL_PROOF"
    assert r["safe_to_create_task_now"] is False
    assert r["visibility_complete_for_all_company_tasks"] is False
    assert r["task_created"] is False
    assert r["scans_completed"] == 2
    assert [m for m,_ in calls] == [
        "crm.type.getByEntityTypeId","crm.enum.ownertype","tasks.task.getFields",
        "crm.item.get","tasks.task.list","tasks.task.list",
    ]


def test_existing_linked_and_title_reconciles_to_one():
    r,calls = call(linked=[task(123)],titled=[task(123)])
    assert r["status"] == "EXISTING_VISIBLE_TASK_FOUND"
    assert r["existing_visible_task_ids"] == [123]
    assert r["visible_unique_task_count"] == 1
    assert r["task_created"] is False


def test_existing_name_only_catches_unlinked_demo():
    r,_=call(titled=[{"id":"14","title":TASK_TITLE}])
    assert r["status"] == "EXISTING_VISIBLE_TASK_FOUND"
    assert r["existing_visible_task_ids"] == [14]


def test_existing_link_only_catches_different_title():
    r,_=call(linked=[{"id":"44","title":"Другой учебный заголовок","ufCrmTask":[CRM_BINDING]}])
    assert r["status"] == "EXISTING_VISIBLE_TASK_FOUND"
    assert r["existing_visible_task_ids"] == [44]


@pytest.mark.parametrize("linked,titled,code", [
    ([{"id":"1","title":"Unrelated","ufCrmTask":[]}],None,"TASK_CRM_FILTER_IGNORED"),
    (None,[{"id":"1","title":"Unrelated"}],"TASK_TITLE_FILTER_NOT_EXACT"),
    ([{"id":"0","title":TASK_TITLE,"ufCrmTask":[CRM_BINDING]}],None,"TASK_ID_INVALID"),
    ([{"id":"1","title":TASK_TITLE,"ufCrmTask":7}],None,"CRM_TASK_LINK_UNEXPECTED_SHAPE"),
])
def test_ambiguous_returned_rows_block(linked,titled,code):
    with pytest.raises(TaskDiscoveryBlocked,match=code):
        call(linked=linked,titled=titled)


def test_full_unpaginated_page_blocks():
    with pytest.raises(TaskDiscoveryBlocked,match="TASK_PAGINATION_UNVERIFIED"):
        call(linked=[task(n) for n in range(1,51)],override="many")


def test_invalid_scope_does_not_show_auth_body():
    with pytest.raises(TaskDiscoveryBlocked,match="TASK_LIST_HTTP_401") as exc:
        call(override="scope")
    assert HOOK not in str(exc.value)


def test_metadata_requires_same_demo_card():
    # Contract is separately tested in test_bitrix_1062_task_metadata.
    assert CRM_BINDING == "T426_3"
    assert "НЕ ЗВОНИТЬ" in TASK_TITLE



@pytest.mark.parametrize("http_code,expected", [
    (400, "TASK_LIST_HTTP_400"),
    (403, "TASK_LIST_HTTP_403"),
    (429, "TASK_LIST_HTTP_429"),
    (502, "TASK_LIST_HTTP_OTHER"),
])
def test_task_list_http_status_is_sanitized(http_code, expected):
    import httpx
    from leadpilot.bitrix_1062_task_dedupe_readonly import _list_tasks
    def handler(req):
        assert req.url.path.endswith("/tasks.task.list.json")
        return httpx.Response(http_code, json={
            "error": "CUSTOM_SECRET_INFO_CANNOT_BE_EXPOSED",
            "error_description": "PRIVATE", "webhook": HOOK,
        })
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TaskDiscoveryBlocked, match=expected) as exc:
            _list_tasks(client, HOOK, "UF_CRM_TASK", CRM_BINDING)
    assert HOOK not in str(exc.value)
    assert "CUSTOM_SECRET_INFO" not in str(exc.value)



@pytest.mark.parametrize("flag,reason", [
    ("owner_symbol_verified", "EXACT_DEMO_GATE_OWNER_SYMBOL_FAILED"),
    ("task_link_enabled", "EXACT_DEMO_GATE_TASK_LINK_FAILED"),
    ("automation_confirmed_off", "EXACT_DEMO_GATE_AUTOMATION_OFF_FAILED"),
    ("task_fields_ready", "EXACT_DEMO_GATE_TASK_FIELDS_FAILED"),
    ("exact_demo_card_readback", "EXACT_DEMO_GATE_EXACT_CARD_FAILED"),
])
def test_metadata_reports_the_one_failed_gate_without_scanning_tasks(monkeypatch, flag, reason):
    from leadpilot import bitrix_1062_task_dedupe_readonly as module
    gates = dict.fromkeys((
        "owner_symbol_verified", "task_link_enabled", "automation_confirmed_off",
        "task_fields_ready", "exact_demo_card_readback",
    ), True)
    gates[flag] = False
    def meta(*args, **kwargs):
        return {"status": "LINKED_TASK_METADATA_BLOCKED", **gates}
    monkeypatch.setattr(module, "audit_synthetic_task_link", meta)
    with pytest.raises(TaskDiscoveryBlocked, match="^"+reason+"$"):
        call()


def test_metadata_multiple_failed_gates_stays_fail_closed(monkeypatch):
    from leadpilot import bitrix_1062_task_dedupe_readonly as module
    monkeypatch.setattr(module, "audit_synthetic_task_link",
        lambda *a,**k: {"status":"LINKED_TASK_METADATA_BLOCKED",
                         "exact_demo_card_readback": False,
                         "task_link_enabled": False})
    with pytest.raises(TaskDiscoveryBlocked, match="EXACT_DEMO_MULTIPLE_OR_UNKNOWN_GATES_FAILED"):
        call()


@pytest.mark.parametrize("code,expected", [
    ("READ_HTTP_UNAVAILABLE", "EXACT_DEMO_METADATA_READ_HTTP_UNAVAILABLE"),
    ("READ_RESPONSE_UNAVAILABLE", "EXACT_DEMO_METADATA_READ_RESPONSE_UNAVAILABLE"),
    ("SCHEMA_UNEXPECTED", "EXACT_DEMO_METADATA_SCHEMA_UNEXPECTED"),
])
def test_safe_metadata_reader_errors_not_masked(monkeypatch, code, expected):
    from leadpilot import bitrix_1062_task_dedupe_readonly as module
    from leadpilot.bitrix_1062_task_metadata import TaskMetadataReadError
    def fail(*args,**kwargs):
        raise TaskMetadataReadError(code)
    monkeypatch.setattr(module, "audit_synthetic_task_link", fail)
    with pytest.raises(TaskDiscoveryBlocked, match="^"+expected+"$"):
        call()


def test_metadata_exception_text_from_unknown_source_not_leaked(monkeypatch):
    from leadpilot import bitrix_1062_task_dedupe_readonly as module
    from leadpilot.bitrix_1062_task_metadata import TaskMetadataReadError
    def fail(*args,**kwargs):
        raise TaskMetadataReadError("UNTRUSTED_PRIVATE_DATA_" + HOOK)
    monkeypatch.setattr(module, "audit_synthetic_task_link", fail)
    with pytest.raises(TaskDiscoveryBlocked, match="EXACT_DEMO_METADATA_READ_UNCLASSIFIED") as caught:
        call()
    assert HOOK not in str(caught.value)



def test_cli_read_only_on_metadata_failure_uses_sanitized_diagnostic(monkeypatch, capsys):
    from leadpilot import bitrix_1062_task_dedupe_readonly as module
    from leadpilot import bitrix_1062_task_diagnostics as diagnostics
    monkeypatch.setenv("SERVICEIT_TASK_READONLY_WEBHOOK", HOOK)
    def blocked(*args, **kwargs):
        raise TaskDiscoveryBlocked("EXACT_DEMO_GATE_EXACT_CARD_FAILED")
    monkeypatch.setattr(module, "discover_demo_manager_task", blocked)
    monkeypatch.setattr(diagnostics, "diagnose_task_metadata", lambda hook: {
        "status": "BLOCKED_READ_ONLY",
        "methods": {"crm.item.get": {"state": "OK", "result_shape": "OBJECT"}},
        "metadata_gates": {
            "exact_demo_card_readback": False,
            "owner_symbol_verified": True,
            "task_link_enabled": True,
            "automation_confirmed_off": True,
            "task_fields_ready": True,
        },
    })
    with pytest.raises(SystemExit) as ex:
        module.main()
    assert ex.value.code == 2
    output = capsys.readouterr().out
    assert "EXACT_DEMO_GATE_EXACT_CARD_FAILED" in output
    assert '"exact_demo_card_readback": false' in output
    assert HOOK not in output
    assert "TASK_ADDED" not in output



def test_cli_card_subchecks_propagate_without_values(monkeypatch,capsys):
    from leadpilot import bitrix_1062_task_dedupe_readonly as module
    from leadpilot import bitrix_1062_task_diagnostics as diagnostics
    monkeypatch.setenv("SERVICEIT_TASK_READONLY_WEBHOOK", HOOK)
    def blocked(*args, **kwargs):
        raise TaskDiscoveryBlocked("EXACT_DEMO_GATE_EXACT_CARD_FAILED")
    monkeypatch.setattr(module,"discover_demo_manager_task",blocked)
    monkeypatch.setattr(diagnostics,"diagnose_task_metadata",lambda hook: {
        "status":"BLOCKED_READ_ONLY",
        "methods":{"crm.item.get":{"state":"OK"}},
        "metadata_gates":{"exact_demo_card_readback":False},
        "exact_demo_card_checks":{
            "stage_matches":False,
            "all_16_original_uf_names_present":True,
        },
    })
    with pytest.raises(SystemExit) as exit_info:
        module.main()
    assert exit_info.value.code == 2
    printed=capsys.readouterr().out
    assert '"stage_matches": false' in printed
    assert '"all_16_original_uf_names_present": true' in printed
    assert HOOK not in printed
    assert "crm.item.update" not in printed
