"""Live-shape mocked tests for two-stage atomic-intent, one-use guard."""
import json
import httpx

from leadpilot import serviceit_sales_stages_guarded as m
from leadpilot import serviceit_sales_funnel_provision as f


def fake_portal(monkeypatch, *, timeout=False):
    monkeypatch.setattr(f, "validate_webhook",
                        lambda _: "https://serviseit.bitrix24.ru/rest/1/redacted")
    state = {"writes": 0, "fenced": False, "finished": None}
    stages = [
        {"ENTITY_ID": "DEAL_STAGE_4", "STATUS_ID": "C4:INT",
         "NAME": "Есть интерес", "SORT": "10", "EXTRA": {"SEMANTICS": "process"}},
        {"ENTITY_ID": "DEAL_STAGE_4", "STATUS_ID": "C4:QUOTE",
         "NAME": "КП/расчёт", "SORT": "20", "EXTRA": {"SEMANTICS": "process"}},
        {"ENTITY_ID": "DEAL_STAGE_4", "STATUS_ID": "C4:WON",
         "NAME": "Успешно реализовано", "SORT": "70", "EXTRA": {"SEMANTICS": "success"}},
        {"ENTITY_ID": "DEAL_STAGE_4", "STATUS_ID": "C4:LOST",
         "NAME": "Закрыто без продажи", "SORT": "80", "EXTRA": {"SEMANTICS": "failure"}},
    ]
    class Fence:
        def __init__(self, *_): pass
        def reserve(self, card):
            assert card == 78
            if state["fenced"]:
                return "ALREADY_FENCED"
            state["fenced"] = True
            return "RESERVED"
        def finalize(self, card, status):
            state["finished"] = status
            return True
    monkeypatch.setattr(m, "StageFence", Fence)
    def handler(req):
        method = req.url.path.rsplit("/", 1)[-1].removesuffix(".json")
        payload = json.loads(req.content)
        if method == "user.admin":
            return httpx.Response(200, json={"result": True})
        if method == "crm.category.list":
            return httpx.Response(200, json={"result": {"categories": [
                {"id": 0, "name": "Общая", "entityTypeId": 2, "isDefault": "Y"},
                {"id": 4, "name": "Лидогенерация", "entityTypeId": 2,
                 "isDefault": "N"}]}})
        if method == "crm.category.get":
            return httpx.Response(200, json={"result": {"category":
                {"id": 4, "name": "Лидогенерация", "entityTypeId": 2,
                 "isDefault": "N"}}})
        if method == "crm.status.list":
            assert payload == {"filter": {"ENTITY_ID": "DEAL_STAGE_4"}}
            return httpx.Response(200, json={"result": stages})
        if method == "crm.status.add":
            data = payload["fields"]
            assert data["ENTITY_ID"] == "DEAL_STAGE_4"
            assert data["SEMANTICS"] == ""
            assert data["STATUS_ID"] in m.CODES
            assert data["NAME"] == m.NAMES[m.CODES.index(data["STATUS_ID"])]
            state["writes"] += 1
            if timeout:
                raise httpx.ReadTimeout("ambiguous status.add")
            stages.append({"ENTITY_ID": "DEAL_STAGE_4",
                           "STATUS_ID": "C4:" + data["STATUS_ID"],
                           "NAME": data["NAME"], "SORT": str(data["SORT"]),
                           "EXTRA": {"SEMANTICS": "process"}})
            return httpx.Response(200, json={"result": 1234+state["writes"]})
        raise AssertionError("UNAPPROVED_REST_CALL:"+method)
    client=httpx.Client(transport=httpx.MockTransport(handler))
    return client,state,stages


def test_two_exact_add_calls_and_readbacks(monkeypatch):
    client,state,stages=fake_portal(monkeypatch)
    env={"SERVICEIT_BITRIX_WEBHOOK":"safe-fixture",
         "GITHUB_TOKEN":"fake","SERVICEIT_GH_LEDGER_KEY":"fake"}
    with client:
        out=m.run(client,env,apply=True)
    assert out["status"]=="LEADGEN_TWO_SALES_STAGES_ADDED_AND_VERIFIED"
    assert state["writes"]==2 and state["finished"]=="VERIFIED"
    assert [x["NAME"] for x in stages[-2:]]==list(m.NAMES)
    assert out["support_funnel_writes"]==0
    assert out["existing_stage_updates"]==0


def test_timeout_reservation_never_retries(monkeypatch):
    client,state,_=fake_portal(monkeypatch,timeout=True)
    env={"SERVICEIT_BITRIX_WEBHOOK":"safe-fixture",
         "GITHUB_TOKEN":"fake","SERVICEIT_GH_LEDGER_KEY":"fake"}
    with client:
        first=m.run(client,env,apply=True)
        second=m.run(client,env,apply=True)
    assert first["status"]=="STAGE_WRITE_UNCERTAIN_NO_RETRY"
    assert second["status"]=="STAGE_WRITE_PREVIOUSLY_RESERVED_NO_RETRY"
    assert state["writes"]==1 and state["finished"]=="UNCERTAIN"


def test_existing_names_are_not_ready_in_wrong_order():
    rows=[
        {"name":"Есть интерес","sort":10,"semantic":"process","code":"A"},
        {"name":"КП/расчёт","sort":20,"semantic":"process","code":"B"},
        {"name":"Согласование","sort":30,"semantic":"process","code":"C"},
        {"name":"Переговоры","sort":40,"semantic":"process","code":"D"},
        {"name":"Успешно","sort":50,"semantic":"success","code":"E"},
        {"name":"Проиграно","sort":60,"semantic":"failure","code":"F"},
    ]
    assert m._plan(rows)["status"]=="EXISTING_SALES_STAGE_ORDER_REVIEW_REQUIRED"
