"""Strict owner-only read-only comparison of a NEW webhook and GitHub secret."""
from hashlib import sha256
from pathlib import Path
import json
import httpx
import pytest

from leadpilot import serviceit_webhook_identity_readonly as probe

HOOK="https://serviseit.bitrix24.ru/rest/1615/FAKE12345ABCDE"
DIGEST=sha256(HOOK.encode()).hexdigest()

def context(tmp_path, *, comment=None, sender="Ramzes2023"):
    event={"action":"created","repository":{"full_name":"Ramzes2023/Service-IT-LeadGen"},
           "sender":{"login":sender},"issue":{"number":148},
           "comment":{"user":{"login":sender},
                      "body":comment or probe.MARKER+DIGEST}}
    path=tmp_path/"event.json"
    path.write_text(json.dumps(event),encoding="utf8")
    env={"GITHUB_REPOSITORY":"Ramzes2023/Service-IT-LeadGen",
         "GITHUB_REF":"refs/heads/main",
         "GITHUB_EVENT_NAME":"issue_comment",
         "GITHUB_ACTOR":"Ramzes2023",
         "GITHUB_EVENT_PATH":str(path),
         "SERVICEIT_BITRIX_WEBHOOK":HOOK}
    return env

def fixture(req, *, allow_task=True, correct_fields=True):
    method=req.url.path.rsplit("/",1)[-1].removesuffix(".json")
    params=json.loads(req.content)
    assert method in probe.READS, "Only read-only methods permitted"
    if method=="profile":
        return httpx.Response(200,json={"result":{"ID":"1615","ADMIN":False}})
    if method=="scope":
        return httpx.Response(200,json={"result":["crm","task","user_brief"]})
    if method=="crm.type.getByEntityTypeId":
        assert params=={"entityTypeId":1062}
        return httpx.Response(200,json={"result":{"type":{
            "id":13,"entityTypeId":1062,"isUseInUserfieldEnabled":"Y",
            "linkedUserFields":{"TASKS_TASK|UF_CRM_TASK":"Y"}}}})
    if method=="crm.category.get":
        assert params=={"entityTypeId":1062,"id":21}
        return httpx.Response(200,json={"result":{"category":{
            "id":21,"entityTypeId":1062}}})
    if method=="crm.item.fields":
        assert params=={"entityTypeId":1062,"useOriginalUfNames":"Y"}
        rows={k:{"type":"integer" if "LEAD_SCORE" in k else "string",
                 "isReadOnly":False}
              for k in probe.FIELDS}
        if not correct_fields:rows.pop(probe.FIELDS[0])
        return httpx.Response(200,json={"result":{"fields":rows}})
    if method=="tasks.task.getFields":
        assert params=={}
        rows={k:{"type":"crm" if k=="UF_CRM_TASK" else "string"} for k in (
           "TITLE","DESCRIPTION","RESPONSIBLE_ID","UF_CRM_TASK")}
        if not allow_task:return httpx.Response(401,json={"error":"insufficient_scope"})
        return httpx.Response(200,json={"result":{"fields":rows}})
    if method=="tasks.task.list":
        assert params=={"filter":{"ID":0},"select":["ID"],"start":0}
        return httpx.Response(200,json={"result":{"tasks":[]},"total":0})
    if method=="crm.item.list":
        assert params=={"entityTypeId":1062,"filter":{"categoryId":21},
                        "select":["id","categoryId"],"start":0}
        return httpx.Response(200,json={"result":{"items":[{
            "id":120,"categoryId":21}]},"total":1})
    pytest.fail("Unrecognized read-only method")

def test_full_match_and_readonly_scope(tmp_path):
    env=context(tmp_path)
    calls=[]
    def handler(req):
        calls.append(req.url.path)
        return fixture(req)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result=probe.check(client,env)
    assert result["status"]=="SAME_WEBHOOK_CONFIGURED_IN_GITHUB_READONLY_PASS"
    assert result["exact_webhook_match"] is True
    assert result["scope_crm_task_user_brief"] is True
    assert result["task_read_scope"] is True
    assert result["all_four_fields_visible"] is True
    assert result["write_permissions_tested"] is False
    assert result["crm_writes"]==result["task_add_calls"]==0
    assert len(calls)==8
    assert not any(".add." in x or ".update." in x for x in calls)
    assert HOOK not in json.dumps(result)
    assert "120" not in json.dumps(result)

def test_different_github_secret_cannot_mistakenly_activate(tmp_path):
    env=context(tmp_path,comment=probe.MARKER+"a"*64)
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:pytest.fail("Do not query mismatched credentials"))) as client:
        result=probe.check(client,env)
    assert result["status"]=="GITHUB_SECRET_DOES_NOT_MATCH_SUPPLIED_WEBHOOK"
    assert result["exact_webhook_match"] is False

@pytest.mark.parametrize("change",[
    {"GITHUB_ACTOR":"unknown"},
    {"GITHUB_REF":"refs/heads/other"},
    {"GITHUB_EVENT_NAME":"workflow_dispatch"},
    {"GITHUB_REPOSITORY":"attacker/repo"},
])
def test_unapproved_owner_or_repository_blocks_before_remote_read(tmp_path,change):
    env={**context(tmp_path),**change}
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:pytest.fail("Network must be blocked"))) as client:
        with pytest.raises(probe.WebhookReadOnlyBlocked):
            probe.check(client,env)

def test_wrong_comment_author_blocks(tmp_path):
    env=context(tmp_path,sender="attacker")
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:pytest.fail("Network must be blocked"))) as client:
        with pytest.raises(probe.WebhookReadOnlyBlocked,
                           match="OWNER_COMMENT_NOT_AUTHORIZED"):
            probe.check(client,env)

@pytest.mark.parametrize("kwargs",[
    {"allow_task":False},
    {"correct_fields":False},
])
def test_permission_or_missing_score_stops_before_writes(tmp_path,kwargs):
    env=context(tmp_path)
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:fixture(req,**kwargs))) as client:
        with pytest.raises(probe.WebhookReadOnlyBlocked):
            probe.check(client,env)

def test_workflow_is_owner_only_with_no_write_github_permissions():
    body=Path(".github/workflows/serviceit-webhook-identity-readonly.yml").read_text("utf8")
    assert "permissions:\n  contents: read\n  issues: read" in body
    assert "github.actor == 'Ramzes2023'" in body
    assert "github.event.issue.number == 148" in body
    assert "SERVICEIT_BITRIX_WEBHOOK:" in body
    assert "serviceit_webhook_identity_readonly" in body
    assert "contents: write" not in body
