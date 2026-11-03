"""Read-only task binding gate: deny privilege escalation and never call task.add."""
import json
from pathlib import Path

import httpx
import pytest

from leadpilot.serviceit_manager_task_scope_probe import (
    TaskGateBlocked, inspect, _query,
)

URL = "https://serviseit.bitrix24.ru/rest/123/SYNTHETIC_REST_HOOK_TOKEN"


def response(req, *, active=True, task_scope=True):
    assert req.method == "POST"
    assert req.url.host == "serviseit.bitrix24.ru"
    args=json.loads(req.content)
    if req.url.path.endswith("tasks.task.getFields.json"):
        assert args == {}
        if not task_scope:
            return httpx.Response(401,json={"error":"insufficient_scope"})
        return httpx.Response(200,json={"result":{"fields":{
            "TITLE":{"type":"string","required":True},
            "DESCRIPTION":{"type":"string"},
            "RESPONSIBLE_ID":{"type":"integer","required":True},
            "UF_CRM_TASK":{"type":"crm"},
        }}})
    if req.url.path.endswith("crm.type.getByEntityTypeId.json"):
        assert args == {"entityTypeId":1062}
        return httpx.Response(200,json={"result":{"type":{
            "entityTypeId":1062,
            "isUseInUserfieldEnabled":True if active else False,
            "linkedUserFields":{"TASKS_TASK|UF_CRM_TASK":True if active else False},
        }}})
    if req.url.path.endswith("crm.enum.ownertype.json"):
        assert args == {}
        return httpx.Response(200,json={"result":[{
            "ID":1062,"SYMBOL_CODE_SHORT":"T426"
        }]})
    pytest.fail("No update/POST task creation permitted")


def test_portal_task_metadata_preflight_is_only_readonly():
    seen=[]
    def handler(req):
        seen.append(req.url.path)
        return response(req)
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        outcome=inspect(c,URL)
    assert len(seen)==3
    assert outcome["status"]=="TASK_CREATE_READONLY_PREFLIGHT"
    assert outcome["task_scope"]=="READONLY_PRESENT"
    assert outcome["smart_process_1062_task_link"]=="VERIFIED"
    assert outcome["manager_user_identity"]=="NOT_VERIFIED"
    assert outcome["task_created"] is False
    assert outcome["crm_writes"]==0


def test_no_task_scope_fails_closed():
    with httpx.Client(transport=httpx.MockTransport(
        lambda req:response(req,task_scope=False))) as c:
        with pytest.raises(TaskGateBlocked,match="TASK_PROBE_METHOD_UNAVAILABLE_OR_SCOPE"):
            inspect(c,URL)


def test_no_spa_task_binding_is_not_silently_writable():
    with httpx.Client(transport=httpx.MockTransport(
        lambda req:response(req,active=False))) as c:
        outcome=inspect(c,URL)
    assert outcome["smart_process_1062_task_link"]=="NOT_CONFIGURED"
    assert outcome["task_write_authorized"] is False


def test_non_readonly_method_is_forbidden_before_network():
    with httpx.Client(transport=httpx.MockTransport(
            lambda req: pytest.fail("No network for task.add"))) as c:
        with pytest.raises(TaskGateBlocked,match="TASK_PROBE_METHOD_NOT_READONLY"):
            _query(c,URL,"tasks.task.add",{"fields":{"TITLE":"NO"}})


def test_owner_issue_workflow_never_prints_task_or_employee_identity():
    workflow=Path(".github/workflows/serviceit-manager-task-scope-readonly.yml").read_text("utf-8")
    module=Path("leadpilot/serviceit_manager_task_scope_probe.py").read_text("utf-8")
    assert "github.actor == 'Ramzes2023'" in workflow
    assert "github.event.issue.number == 74" in workflow
    assert "SERVICEIT_MANAGER_TASK_SCOPE_READONLY_V1" in workflow
    assert "contents: read" in workflow
    assert "issues: read" in workflow
    assert "SERVICEIT_BITRIX_WEBHOOK: ${{ secrets.SERVICEIT_BITRIX_WEBHOOK }}" in workflow
    assert 'crm_writes": 0' in module
    for forbidden in ("tasks.task.add.json","crm.item.update.json",
                      "SERVICEIT_MANAGER_USER_ID", "crm.item.add",
                      "contents: write", "variables: write"):
        assert forbidden not in workflow
    assert "manager_user_identity" in module
    assert "task_created" in module
