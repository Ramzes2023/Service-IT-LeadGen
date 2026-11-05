"""A new deal category may only be created once, with full admin visibility."""
import httpx
import pytest
from leadpilot import serviceit_sales_funnel_provision as m


class FakeFence:
    used=False
    def __init__(self,*args,**kwargs): pass
    def reserve(self,card):
        if self.used:
            return "ALREADY_FENCED_NO_RETRY"
        type(self).used=True
        return "RESERVED"
    def finalize(self,card,status):return True


def client_state(monkeypatch, *, admin=True, preexisting=False, fail_add=False):
    monkeypatch.setattr(m,"validate_webhook",
                        lambda v:"https://serviseit.bitrix24.ru/rest/1615/redacted")
    monkeypatch.setattr(m,"SalesFunnelFence",FakeFence)
    FakeFence.used=False
    state={"created":preexisting,"add_calls":0,"calls":[],"mutated_other":False}
    base=[{"id":0,"name":"Общая","entityTypeId":2,"isDefault":"Y"}]
    deal={"id":4,"name":"Лидогенерация","entityTypeId":2,"isDefault":"N","sort":550}
    def handler(request):
        method=request.url.path.rsplit("/",1)[-1].replace(".json","")
        state["calls"].append(method)
        args=__import__("json").loads(request.content)
        if method=="user.admin": return httpx.Response(200,json={"result":admin})
        if method=="crm.category.list":
            assert args=={"entityTypeId":2}
            return httpx.Response(200,json={"result":{"categories":base+([deal] if state["created"] else [])}})
        if method=="crm.category.get":
            assert args=={"entityTypeId":2,"id":4}
            return httpx.Response(200,json={"result":{"category":deal}})
        if method=="crm.status.list":
            assert args=={"filter":{"ENTITY_ID":"DEAL_STAGE_4"}}
            stages=[{"ENTITY_ID":"DEAL_STAGE_4","NAME":"Есть интерес",
                     "STATUS_ID":"C4:NEW","EXTRA":{"SEMANTICS":"process"}},
                    {"ENTITY_ID":"DEAL_STAGE_4","NAME":"Успешно",
                     "STATUS_ID":"C4:WON","EXTRA":{"SEMANTICS":"success"}},
                    {"ENTITY_ID":"DEAL_STAGE_4","NAME":"Проиграно",
                     "STATUS_ID":"C4:LOSE","EXTRA":{"SEMANTICS":"failure"}}]
            return httpx.Response(200,json={"result":stages})
        if method=="crm.category.add":
            assert args=={"entityTypeId":2,"fields":{"name":"Лидогенерация","sort":550}}
            state["add_calls"]+=1
            if fail_add:
                raise httpx.ReadTimeout("uncertain")
            state["created"]=True
            return httpx.Response(200,json={"result":{"category":deal}})
        raise AssertionError("Unexpected or destructive REST "+method)
    client=httpx.Client(transport=httpx.MockTransport(handler))
    return client,state


def test_non_admin_never_creates_even_if_category_not_visible(monkeypatch):
    client,state=client_state(monkeypatch,admin=False)
    with client:
        r=m.ensure_funnel(client,{"SERVICEIT_BITRIX_WEBHOOK":"redacted"},apply=True)
    assert r["status"]=="CRM_ADMIN_REQUIRED_NO_WRITE"
    assert state["calls"]==["user.admin"] and state["add_calls"]==0


def test_readonly_admin_does_not_create(monkeypatch):
    client,state=client_state(monkeypatch)
    with client:
        r=m.ensure_funnel(client,{"SERVICEIT_BITRIX_WEBHOOK":"redacted"},apply=False)
    assert r["status"]=="FUNNEL_ABSENT_ADMIN_SCOPED_READONLY"
    assert state["add_calls"]==0


def test_one_time_admin_deal_funnel_create_and_exact_readback(monkeypatch):
    client,state=client_state(monkeypatch)
    env={"SERVICEIT_BITRIX_WEBHOOK":"redacted",
         "GITHUB_TOKEN":"fake-token-long-enough","SERVICEIT_GH_LEDGER_KEY":"dummy"}
    with client:
        first=m.ensure_funnel(client,env,apply=True)
        second=m.ensure_funnel(client,env,apply=True)
    assert first["status"]=="NEW_SALES_DEAL_FUNNEL_CREATED_VERIFIED"
    assert first["support_funnel_changes"]==0
    assert first["existing_deals_changed"]==0
    assert second["status"]=="DEAL_FUNNEL_ALREADY_PRESENT_READBACK_VERIFIED"
    assert state["add_calls"]==1


def test_if_post_timeout_fence_blocks_replay(monkeypatch):
    client,state=client_state(monkeypatch,fail_add=True)
    env={"SERVICEIT_BITRIX_WEBHOOK":"redacted",
         "GITHUB_TOKEN":"fake-token-long-enough","SERVICEIT_GH_LEDGER_KEY":"dummy"}
    with client:
        first=m.ensure_funnel(client,env,apply=True)
        second=m.ensure_funnel(client,env,apply=True)
    assert first["status"]=="FUNNEL_WRITE_UNCERTAIN_NO_RETRY"
    assert second["status"]=="PRIOR_FUNNEL_ATTEMPT_FENCED_REVIEW_REQUIRED"
    assert state["add_calls"]==1


def test_scope_method_whitelist_fails_closed():
    with pytest.raises(m.SalesFunnelBlocked,match="FORBIDDEN_REST_METHOD"):
        m.rest(None,"","crm.deal.update",{})
    with pytest.raises(m.SalesFunnelBlocked,match="ONLY_DEAL_ENTITY_ALLOWED"):
        m.rest(None,"","crm.category.add",{"entityTypeId":1062,"fields":{}})


def test_duplicate_visible_named_funnel_blocks(monkeypatch):
    monkeypatch.setattr(m,"validate_webhook",
                        lambda v:"https://serviseit.bitrix24.ru/rest/1615/redacted")
    def handler(req):
        method=req.url.path.rsplit("/",1)[-1].replace(".json","")
        if method=="user.admin": return httpx.Response(200,json={"result":True})
        if method=="crm.category.list":
            cats=[{"id":0,"name":"Общая","entityTypeId":2,"isDefault":"Y"},
                  {"id":4,"name":"Лидогенерация","entityTypeId":2,"isDefault":"N"},
                  {"id":5,"name":" лидогенерация ","entityTypeId":2,"isDefault":"N"}]
            return httpx.Response(200,json={"result":{"categories":cats}})
        raise AssertionError("MUST_NOT_WRITE")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(m.SalesFunnelBlocked,match="DUPLICATE_NAMED_FUNNEL"):
            m.ensure_funnel(client,{"SERVICEIT_BITRIX_WEBHOOK":"x"},apply=True)


def test_separate_short_lived_admin_secret_preferred_over_everyday_automation(monkeypatch):
    client,state=client_state(monkeypatch,admin=False)
    seen=[]
    def capture(value):
        seen.append(value)
        return "https://serviseit.bitrix24.ru/rest/1615/redacted"
    monkeypatch.setattr(m,"validate_webhook",capture)
    with client:
        m.ensure_funnel(client,{
            "SERVICEIT_BITRIX_WEBHOOK":"ordinary-limited",
            "SERVICEIT_BITRIX_CRM_ADMIN_ONETIME_WEBHOOK":"separate-admin-secret",
        },apply=True)
    assert seen==["separate-admin-secret"]
    assert state["add_calls"]==0
