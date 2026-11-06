"""Guard missing sales stages; never mutate existing CRM records or stages."""
import httpx
from leadpilot import serviceit_sales_stages_guarded as s


def rows(*, gap=50, neg=False, agree=False):
    names=[("C4:NEW","Есть интерес",10,"process"),
           ("C4:QUOTE","КП/расчёт",20,"process")]
    if neg:
        names.append(("C4:NEG","Переговоры",30,"process"))
    if agree:
        names.append(("C4:AGR","Согласование",40,"process"))
    names.extend([("C4:WON","Успешно реализовано",20+gap,"success"),
                  ("C4:LOST","Закрыто без продажи",30+gap,"failure")])
    return [{"code":c,"name":n,"sort":sort,"semantic":sem}
            for c,n,sort,sem in names]


def test_stage_gap_plan_only_allows_two_missing_with_sort_space():
    p=s._plan(rows())
    assert p["can_add"] and p["missing"]==2
    assert 20 < p["sorts"][0] < p["sorts"][1] < 70
    assert s._plan(rows(neg=True, agree=True))["ready"] is True
    assert s._plan(rows(neg=True))["can_add"] is False
    assert s._plan(rows(gap=2))["can_add"] is False


def test_no_admin_cannot_add_even_when_owner_apply_requested(monkeypatch):
    from leadpilot import serviceit_sales_funnel_provision as f
    monkeypatch.setattr(f, "validate_webhook",
                        lambda x: "https://serviseit.bitrix24.ru/rest/1/redacted")
    calls=[]
    def handler(req):
        method=req.url.path.rsplit("/",1)[-1].removesuffix(".json")
        calls.append(method)
        import json
        params=json.loads(req.content)
        if method=="user.admin":
            return httpx.Response(200,json={"result":False})
        if method=="crm.category.list":
            return httpx.Response(200,json={"result":{"categories":[
                {"id":0,"name":"Общая","entityTypeId":2,"isDefault":"Y"},
                {"id":4,"name":"Лидогенерация","entityTypeId":2,"isDefault":"N"}]}})
        if method=="crm.category.get":
            return httpx.Response(200,json={"result":{"category":
                {"id":4,"name":"Лидогенерация","entityTypeId":2,"isDefault":"N"}}})
        if method=="crm.status.list":
            assert params=={"filter":{"ENTITY_ID":"DEAL_STAGE_4"}}
            values=[]
            for x in rows():
                sem={"process":"process","success":"success","failure":"failure"}[x["semantic"]]
                values.append({"ENTITY_ID":"DEAL_STAGE_4","STATUS_ID":x["code"],
                               "NAME":x["name"],"SORT":str(x["sort"]),
                               "EXTRA":{"SEMANTICS":sem}})
            return httpx.Response(200,json={"result":values})
        raise AssertionError("Forbidden or unexpected method: "+method)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result=s.run(client,{"SERVICEIT_BITRIX_WEBHOOK":"redacted"},apply=True)
    assert result["status"]=="CRM_ADMIN_REQUIRED_NO_WRITE"
    assert "crm.status.add" not in calls


def test_read_only_plan_never_calls_write(monkeypatch):
    from leadpilot import serviceit_sales_funnel_provision as f
    monkeypatch.setattr(f, "validate_webhook",
                        lambda x: "https://serviseit.bitrix24.ru/rest/1/redacted")
    def handler(req):
        import json
        method=req.url.path.rsplit("/",1)[-1].removesuffix(".json")
        args=json.loads(req.content)
        if method=="user.admin":
            return httpx.Response(200,json={"result":True})
        if method=="crm.category.list":
            return httpx.Response(200,json={"result":{"categories":[
                {"id":0,"name":"Общая","entityTypeId":2,"isDefault":"Y"},
                {"id":4,"name":"Лидогенерация","entityTypeId":2,"isDefault":"N"}]}})
        if method=="crm.category.get":
            return httpx.Response(200,json={"result":{"category":
                {"id":4,"name":"Лидогенерация","entityTypeId":2,"isDefault":"N"}}})
        if method=="crm.status.list":
            assert args=={"filter":{"ENTITY_ID":"DEAL_STAGE_4"}}
            return httpx.Response(200,json={"result":[{
                "ENTITY_ID":"DEAL_STAGE_4","STATUS_ID":x["code"],
                "NAME":x["name"],"SORT":str(x["sort"]),
                "EXTRA":{"SEMANTICS":x["semantic"]}} for x in rows()]})
        raise AssertionError("Read-only must not call "+method)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        r=s.run(client,{"SERVICEIT_BITRIX_WEBHOOK":"redacted"},apply=False)
    assert r["status"]=="TWO_MISSING_STAGES_CAN_BE_ADDED"
    assert r["crm_writes"]==0 and r["missing_required_stages"]==2


def test_append_only_readback_and_cross_funnel_rejection():
    before=rows()
    after=before+[
        {"code":"C4:SVCIT_NEGOTIATION","name":"Переговоры",
         "sort":36,"semantic":"process"}]
    s._verify_append_only(before,after,1)
    from pytest import raises
    with raises(s.StageGateBlocked):
        s._verify_append_only(before,after[:-1],1)


def test_cannot_call_other_rest_methods(monkeypatch):
    from pytest import raises
    with raises(s.StageGateBlocked,match="STAGE_ADD_PARAMS_INVALID"):
        s._add_stage(None,"",4,"BAD_CODE","BAD_NAME",40)
