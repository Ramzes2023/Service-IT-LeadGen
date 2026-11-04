"""Stage 3 requires positive read-only proof, never tasks.task.add."""
import json
import httpx
import pytest

from leadpilot.serviceit_manager_task_readonly_v2 import inspect, ManagerPreflightBlocked, _read

BASE = "https://serviseit.bitrix24.ru/rest/123/SYNTHETIC_TOKEN"

def handler(req, *, users=None, tasks=None, bad_task=False):
    assert req.method == "POST" and req.url.host == "serviseit.bitrix24.ru"
    params = json.loads(req.content)
    if req.url.path.endswith("/user.get.json"):
        assert params == {"FILTER": {"NAME_SEARCH": "Гиреев", "ACTIVE": True}}
        rows = users if users is not None else [
            {"ID":"42", "NAME":"Микаил", "LAST_NAME":"Гиреев",
             "ACTIVE":True,"USER_TYPE":"employee"}]
        return httpx.Response(200,json={"result":rows,"total":len(rows)})
    if req.url.path.endswith("/crm.enum.ownertype.json"):
        assert params == {}
        return httpx.Response(200,json={"result":[{"ID":1062,"SYMBOL_CODE_SHORT":"T426"}]})
    if req.url.path.endswith("/tasks.task.list.json"):
        assert params == {"filter":{"UF_CRM_TASK":"T426_3"},
                          "select":["ID","UF_CRM_TASK","RESPONSIBLE_ID"],
                          "order":{"ID":"ASC"},"start":0}
        rows = tasks if tasks is not None else [
            {"id":"26163","ufCrmTask":["T426_3"],"responsibleId":"42"}]
        if bad_task:
            rows[0]["ufCrmTask"]=["T426_999"]
        return httpx.Response(200,json={"result":{"tasks":rows},"total":len(rows)})
    pytest.fail("No CRM or task mutation allowed")

def test_only_three_read_methods_and_candidate_never_authorized():
    seen = []
    def route(req):
        seen.append(req.url.path)
        return handler(req)
    with httpx.Client(transport=httpx.MockTransport(route)) as client:
        report=inspect(client,BASE)
    assert len(seen)==3
    assert report["manager_directory_candidate"]=="ONE_DIRECTORY_CANDIDATE_UNAPPROVED"
    assert report["manager_candidate_count"]==1
    assert report["manager_id_authorized"] is False
    assert report["task_link_demo_count"]==1
    assert report["task_dedupe_for_new_cards"]=="NOT_PROVEN"
    assert report["task_created"] is False and report["crm_writes"]==0
    assert "42" not in json.dumps(report)

def test_ambiguous_manager_identity_does_not_authorize():
    users=[{"ID":"42","NAME":"Микаил","LAST_NAME":"Гиреев","ACTIVE":True},
           {"ID":"43","NAME":"Микаил","LAST_NAME":"Гиреев","ACTIVE":True}]
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:handler(req,users=users))) as client:
        report=inspect(client,BASE)
    assert report["manager_candidate_count"]==2
    assert report["manager_id_authorized"] is False

def test_task_link_mismatch_is_not_silently_accepted():
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:handler(req,bad_task=True))) as client:
        with pytest.raises(ManagerPreflightBlocked,match="TASK_LINK_READBACK_UNVERIFIED"):
            inspect(client,BASE)

def test_no_visible_demo_task_cannot_prove_complete_duplicate_history():
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:handler(req,tasks=[]))) as client:
        report=inspect(client,BASE)
    assert report["task_link_demo_lookup"]=="NONE_VISIBLE_UNVERIFIED"
    assert report["task_write_authorized"] is False

def test_task_mutations_rejected_before_network():
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:pytest.fail("network must not be used"))) as client:
        with pytest.raises(ManagerPreflightBlocked,match="METHOD_NOT_READONLY"):
            _read(client,BASE,"tasks.task.add",{"fields":{"TITLE":"bad"}})
