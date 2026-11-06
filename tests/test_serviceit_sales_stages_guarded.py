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


def test_readonly_diagnostic_is_allowlisted_never_leaks_portal_data(monkeypatch, capsys):
    import json
    from leadpilot import serviceit_sales_stages_guarded as stage
    def block(_env,_apply):
        raise stage.StageGateBlocked("LEADGEN_FUNNEL_NOT_UNIQUE")
    monkeypatch.setattr(stage, "_allowed_event", block)
    assert stage.main() == 2
    assert json.loads(capsys.readouterr().out) == {
        "status": "LEADGEN_STAGE_CHECK_BLOCKED",
        "reason_code": "LEADGEN_FUNNEL_NOT_UNIQUE", "crm_writes": 0,
    }
    def private(_env,_apply):
        raise stage.StageGateBlocked("PRIVATE_CUSTOMER_ID_12345")
    monkeypatch.setattr(stage, "_allowed_event", private)
    assert stage.main() == 2
    assert json.loads(capsys.readouterr().out)["reason_code"] == "UNCLASSIFIED"


def test_bitrix_apology_semantic_is_closed_lost_stage_not_unknown(monkeypatch):
    from leadpilot import serviceit_sales_funnel_provision as funnel
    def rest(client, base, method, args):
        assert method == "crm.status.list"
        assert args == {"filter": {"ENTITY_ID": "DEAL_STAGE_4"}}
        return [
            {"ENTITY_ID": "DEAL_STAGE_4", "STATUS_ID": "C4:NEW",
             "NAME": "Есть интерес", "SORT": "10",
             "EXTRA": {"SEMANTICS": "process"}},
            {"ENTITY_ID": "DEAL_STAGE_4", "STATUS_ID": "C4:WON",
             "NAME": "Успешно реализовано", "SORT": "100",
             "EXTRA": {"SEMANTICS": "success"}},
            {"ENTITY_ID": "DEAL_STAGE_4", "STATUS_ID": "C4:LOSE",
             "NAME": "Закрыто без продажи", "SORT": "110",
             "EXTRA": {"SEMANTICS": "apology"}},
        ]
    monkeypatch.setattr(funnel, "rest", rest)
    got = s._read_stages(None, "https://serviseit.bitrix24.ru/rest/1/x", 4)
    assert [entry["semantic"] for entry in got] == [
        "process", "success", "failure",
    ]


def test_unknown_status_semantics_still_fail_closed(monkeypatch):
    from pytest import raises
    from leadpilot import serviceit_sales_funnel_provision as funnel
    def rest(client, base, method, args):
        return [
            {"ENTITY_ID": "DEAL_STAGE_4", "STATUS_ID": "C4:NEW",
             "NAME": "Есть интерес", "SORT": "10",
             "EXTRA": {"SEMANTICS": "external_unverified"}},
            {"ENTITY_ID": "DEAL_STAGE_4", "STATUS_ID": "C4:WON",
             "NAME": "Успешно реализовано", "SORT": "20",
             "EXTRA": {"SEMANTICS": "success"}},
            {"ENTITY_ID": "DEAL_STAGE_4", "STATUS_ID": "C4:LOSE",
             "NAME": "Закрыто без продажи", "SORT": "30",
             "EXTRA": {"SEMANTICS": "failure"}},
        ]
    monkeypatch.setattr(funnel, "rest", rest)
    with raises(s.StageGateBlocked, match="UNKNOWN_STAGE_SEMANTICS"):
        s._read_stages(None, "https://serviseit.bitrix24.ru/rest/1/x", 4)


def test_safe_order_diagnostics_distinguishes_terminal_sort_problem():
    demo = rows()
    # Real terminal error can be solely an early failed stage; don't leak names/IDs.
    demo[-1]["sort"] = 30
    report = s._safe_order_diagnostics(demo)
    assert report == {
        "anonymous_sorted_stage_roles": "IQFS",
        "sort_values_have_ties": False,
        "process_stages": 2,
        "failed_stages": 1,
        "process_before_success": True,
        "two_sort_slots_before_success": True,
        "failure_before_or_at_success": True,
        "failure_before_or_at_last_process": False,
        "interest_stage_present": True,
        "quote_stage_present": True,
    }
    report_text = str(report)
    assert "C4:" not in report_text
    assert "КП/расчёт" not in report_text
    assert "Есть интерес" not in report_text


def test_stage_sort_diagnostics_does_not_mask_actual_process_after_success():
    demo = rows()
    demo[1]["sort"] = 95
    report = s._safe_order_diagnostics(demo)
    assert report["process_before_success"] is False
    assert report["two_sort_slots_before_success"] is False
