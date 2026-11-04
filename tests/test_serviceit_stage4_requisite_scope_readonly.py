"""Stage4 readonly exact-INN API scope acceptance: no customer data logged."""
import json
import httpx
import pytest

from leadpilot.serviceit_stage4_requisite_scope_readonly import (
    preflight, RequisitesPreflightBlocked, FIELDS, TEST_ONLY_INN,
)

HOOK = "https://serviseit.bitrix24.ru/rest/123/FAKE_TEST_ONLY_TOKEN"

def handler(req, *, denied=False, inaccurate=False, paginated=False):
    assert req.method == "POST"
    assert req.url.host == "serviseit.bitrix24.ru"
    params = json.loads(req.content)
    if req.url.path.endswith("/crm.requisite.fields.json"):
        assert params == {}
        return httpx.Response(200,json={"result":{
            x:{"type":"string"} for x in FIELDS
        }})
    if req.url.path.endswith("/crm.requisite.list.json"):
        assert params == {
            "filter":{"RQ_INN":TEST_ONLY_INN},
            "select":FIELDS, "start":0,
        }
        if denied:
            return httpx.Response(403,json={"error":"ACCESS_DENIED"})
        rows=[{"ID":"11","ENTITY_TYPE_ID":"4","ENTITY_ID":"30",
               "RQ_INN":"0000000000" if inaccurate else TEST_ONLY_INN}]
        return httpx.Response(200,json={"result":rows,"total":2 if paginated else 1})
    pytest.fail("Forbidden method; only corporate requisite reads allowed")


def test_success_returns_only_permission_metadata_not_inn_or_company():
    calls=[]
    def go(req):
        calls.append(req.url.path)
        return handler(req)
    with httpx.Client(transport=httpx.MockTransport(go)) as client:
        out=preflight(client,HOOK)
    assert len(calls)==2
    assert out["exact_inn_query_available"] is True
    assert out["global_customer_and_stop_clearance"] is False
    assert out["score_write_authorized"] is False
    assert out["crm_writes"]==0
    assert TEST_ONLY_INN not in json.dumps(out)
    assert "30" not in json.dumps(out)


def test_access_denied_fails_closed():
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:handler(req,denied=True))) as client:
        with pytest.raises(RequisitesPreflightBlocked,
                           match="REQUISITE_METHOD_OR_SCOPE_UNAVAILABLE"):
            preflight(client,HOOK)


@pytest.mark.parametrize("case",["inaccurate","paginated"])
def test_wrong_match_or_incomplete_result_blocks(case):
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:handler(req,**{case:True}))) as client:
        with pytest.raises(RequisitesPreflightBlocked):
            preflight(client,HOOK)


def test_wrong_portal_blocks_before_network():
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:pytest.fail("No external portal reads allowed"))) as client:
        with pytest.raises(RequisitesPreflightBlocked,
                           match="PORTAL_OUTSIDE_SERVICEIT"):
            preflight(client,
                "https://other-bitrix24.bitrix24.ru/rest/123/FAKE_TEST_TOKEN")
