"""Read-only contract for manager post-call and funnel preflight."""
from pathlib import Path

def test_no_writes_in_manager_scope_audit():
    from leadpilot.serviceit_postcall_funnel_scope_readonly import EXPECTED, NEEDED
    assert EXPECTED == {"crm.item.fields", "crm.category.list",
                        "crm.activity.fields", "crm.activity.list",
                        "crm.status.list"}
    assert "postcall_next_action" in NEEDED
    body=Path("leadpilot/serviceit_postcall_funnel_scope_readonly.py").read_text("utf-8")
    assert "crm.item.update.json" not in body
    assert "tasks.task.add.json" not in body
    assert "crm.deal.add.json" not in body
    assert "revenue_verified\":False" in body


def test_real_deal_stage_audit_requires_exact_category_and_both_outcomes():
    import httpx
    from leadpilot import serviceit_postcall_funnel_scope_readonly as mod
    seen=[]
    def handler(request):
        seen.append(request.url.path)
        assert request.url.path.endswith("/crm.status.list.json")
        assert request.method=="POST"
        assert request.content==b'{"filter":{"ENTITY_ID":"DEAL_STAGE_4"}}'
        stages=[
            ("C4:NEW","Новый лид","process"),
            ("C4:INT","Есть интерес","process"),
            ("C4:OFFER","КП отправлено","process"),
            ("C4:NEG","Переговоры","process"),
            ("C4:APP","Согласование","process"),
            ("C4:WON","Успешно реализовано","success"),
            ("C4:LOSE","Отказ","failure"),
        ]
        rows=[{"ENTITY_ID":"DEAL_STAGE_4","STATUS_ID":code,
               "NAME":name,"EXTRA":{"SEMANTICS":semantic}}
              for code,name,semantic in stages]
        return httpx.Response(200,json={"result":rows})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        out=mod.sales_stage_readback(client,"https://serviseit.bitrix24.ru/rest/1/redacted",4)
    assert len(seen)==1
    assert out["status"]=="SALES_STAGES_READBACK_VERIFIED"
    assert out["visible_stage_count"]==7
    assert out["recommended_sales_flow_visible"] is True
    assert out["sales_stage_semantics_ready"] is True
    assert all(out["sales_flow_requirements"].values())
    assert "names" not in out and "category_id" not in out


def test_cross_funnel_stages_cannot_be_accepted():
    import httpx
    import pytest
    from leadpilot import serviceit_postcall_funnel_scope_readonly as mod
    def handler(req):
        return httpx.Response(200,json={"result":[
            {"ENTITY_ID":"DEAL_STAGE_5","STATUS_ID":"C5:WON",
             "NAME":"Успешно","EXTRA":{"SEMANTICS":"success"}}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(mod.PortalReadBlocked,match="DEAL_STAGES_NOT_SCOPED"):
            mod.sales_stage_readback(
                client,"https://serviseit.bitrix24.ru/rest/1/redacted",4)


def test_stage_inspector_is_read_only_and_rejects_support_category():
    import pytest
    from leadpilot import serviceit_postcall_funnel_scope_readonly as mod
    with pytest.raises(mod.PortalReadBlocked,match="ONLY_ISOLATED_DEAL"):
        mod.safe_request(None,"","crm.status.list",{
            "filter":{"ENTITY_ID":"DEAL_STAGE_0"}})
    with pytest.raises(mod.PortalReadBlocked,match="METHOD_NOT_READONLY"):
        mod.safe_request(None,"","crm.deal.add",{})
    with pytest.raises(mod.PortalReadBlocked,match="CATEGORY_ID_INVALID"):
        mod.sales_stage_readback(None,"",0)
