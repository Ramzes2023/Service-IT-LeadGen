"""Corporate 1062/21 shared layout can be proposed, never mutated by this preflight."""
import json
import httpx
import pytest

from leadpilot import serviceit_layout_scope_readonly_v2 as scope

URL="https://serviseit.bitrix24.ru/rest/123/TEST_ONLY_FAKE_TOKEN"

def response(req, *, admin=False, category=21):
    assert req.method == "POST"
    assert req.url.host == "serviseit.bitrix24.ru"
    params=json.loads(req.content)
    path=req.url.path
    if path.endswith("/user.admin.json"):
        assert params == {}
        return httpx.Response(200,json={"result":admin})
    if path.endswith("/crm.type.getByEntityTypeId.json"):
        assert params == {"entityTypeId":1062}
        return httpx.Response(200,json={"result":{"type":{"id":13,"entityTypeId":1062}}})
    if path.endswith("/crm.category.get.json"):
        assert params == {"entityTypeId":1062,"id":21}
        return httpx.Response(200,json={"result":{"category":{"id":category,
             "entityTypeId":1062,"name":"Сервис ИТ — AI-сигналы"}}})
    if path.endswith("/crm.item.details.configuration.get.json"):
        assert params == {"entityTypeId":1062,"scope":"C",
                          "extras":{"categoryId":21}}
        return httpx.Response(200,json={"result":[{"name":"base",
             "title":"Исходная форма","type":"section",
             "elements":[{"name":"TITLE","optionFlags":1}]}]})
    if path.endswith("/crm.item.fields.json"):
        assert params == {"entityTypeId":1062,"useOriginalUfNames":"Y"}
        return httpx.Response(200,json={"result":{"fields":{"TITLE":{"type":"string"}}}})
    pytest.fail("Unexpected operation — no other Bitrix requests allowed")

@pytest.mark.parametrize("is_admin",[True,False])
def test_exact_scope_and_no_writes_even_when_api_owner_admin(monkeypatch,is_admin):
    monkeypatch.setattr(scope,"plan_shared_manager_layout",lambda current,fields:{
        "added_count":1,
        "before":current,
        "after":current,
    })
    seen=[]
    def handler(req):
        seen.append(req.url.path)
        return response(req,admin=is_admin)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result=scope.inspect(client,URL)
    assert len(seen)==5
    assert result["connected_webhook_user_is_portal_admin"] is is_admin
    assert result["only_one_funnel_targeted"] is True
    assert result["admin_approval_from_company_recorded"] is False
    assert result["shared_layout_write_performed"] is False
    assert result["crm_writes"]==0
    assert result["category_id"]==21
    assert result["proposed_missing_fields"]==1

def test_other_category_rejected_before_any_layout_read(monkeypatch):
    monkeypatch.setattr(scope,"plan_shared_manager_layout",
                        lambda *_: pytest.fail("unreachable"))
    seen=[]
    def handler(req):
        seen.append(req.url.path)
        return response(req,admin=True,category=22)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(scope.LayoutScopeBlocked,match="FUNNEL_ID_MISMATCH"):
            scope.inspect(client,URL)
    assert len(seen)==3

def test_attempt_to_read_cross_category_or_set_rejected_before_network():
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:pytest.fail("no network"))) as client:
        for method,params in [
            ("crm.item.details.configuration.set",
             {"entityTypeId":1062,"scope":"C",
              "extras":{"categoryId":21},"data":[]}),
            ("crm.item.details.configuration.get",
             {"entityTypeId":1062,"scope":"C","extras":{"categoryId":22}}),
            ("crm.item.details.configuration.get",
             {"entityTypeId":2,"scope":"C","extras":{"categoryId":21}}),
        ]:
            with pytest.raises(scope.LayoutScopeBlocked,
                               match="METHOD_OR_SCOPE_NOT_ALLOWED"):
                scope._read(client,URL,method,params)
