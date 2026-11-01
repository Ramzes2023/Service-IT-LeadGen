"""Pure httpx.MockTransport tests: no live Bitrix24 call or stored credential."""
import copy
import json

import httpx
import pytest

from leadpilot.bitrix_1062_rest_read import (
    Bitrix1062ReadError, Bitrix1062ReadOnly, KEY_FIELD, LIST_SELECT,
    validate_read_request,
)
from leadpilot.bitrix_1062_reconcile import inspect_remote_synthetic_key

WEBHOOK = "https://serviseit.bitrix24.ru/rest/123/TESTROTATEDSECRET123456"
KEY = "DEMO-IDEMP-" + "a" * 32


def list_params():
    return {
        "entityTypeId": 1062,
        "filter": {"categoryId": 21, KEY_FIELD: KEY},
        "select": list(LIST_SELECT),
        "useOriginalUfNames": "Y",
        "start": 0,
    }


def get_params():
    return {"entityTypeId": 1062, "id": 101, "useOriginalUfNames": "Y"}


def sample_item():
    return {"id": 101, "entityTypeId": 1062, "categoryId": 21,
            "stageId": "DT1062_21:NEW", "title": "DEMO ServiceIT AI — DEMO-ITREQ-001",
            KEY_FIELD: KEY}


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _api(request):
    assert request.method == "POST"
    assert request.url.host == "serviseit.bitrix24.ru"
    assert request.url.path.startswith("/rest/123/TESTROTATEDSECRET123456/")
    method = request.url.path.rsplit("/", 1)[-1]
    payload = json.loads(request.content)
    if method == "crm.item.list.json":
        assert payload == list_params()
        return httpx.Response(200, json={"result": {"items": [sample_item()]}})
    assert method == "crm.item.get.json"
    assert payload == get_params()
    return httpx.Response(200, json={"result": {"item": sample_item()}})


def test_read_adapter_integrates_with_existing_reconcile():
    with client_for(_api) as client:
        adapter = Bitrix1062ReadOnly(WEBHOOK, client=client)
        r = inspect_remote_synthetic_key(adapter, KEY)
    assert r["status"] == "EXACT_SYNTHETIC_ITEM_VISIBLE"
    assert r["item"]["id"] == 101
    assert r["crm_writes"] == "NO"


def test_read_adapter_cannot_call_mutations():
    calls = []
    def handler(req):
        calls.append(req)
        raise AssertionError("SHOULD_NOT_EXECUTE")
    with client_for(handler) as client:
        adapter = Bitrix1062ReadOnly(WEBHOOK, client=client)
        for method in ("crm.item.add", "crm.item.delete", "crm.item.update",
                       "crm.deal.list", "batch", "crm.contact.list"):
            with pytest.raises(Bitrix1062ReadError, match="READ_METHOD_FORBIDDEN"):
                adapter(method, list_params())
    assert not calls


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(entityTypeId=2),
    lambda p: p["filter"].update(categoryId=19),
    lambda p: p["filter"].update({KEY_FIELD: "NOT-SYNTHETIC"}),
    lambda p: p["filter"].update(companyId=1),
    lambda p: p.update(useOriginalUfNames="N"),
    lambda p: p.update(start=50),
    lambda p: p.update(select=["*"]),
    lambda p: p.update(extra=True),
])
def test_list_scope_is_strict(mutation):
    p = copy.deepcopy(list_params())
    mutation(p)
    with pytest.raises(Bitrix1062ReadError):
        validate_read_request("crm.item.list", p)


@pytest.mark.parametrize("bad_params", [
    {"entityTypeId": 2, "id": 101, "useOriginalUfNames": "Y"},
    {"entityTypeId": 1062, "id": 0, "useOriginalUfNames": "Y"},
    {"entityTypeId": 1062, "id": True, "useOriginalUfNames": "Y"},
    {"entityTypeId": 1062, "id": 101, "useOriginalUfNames": "N"},
    {"entityTypeId": 1062, "id": 101, "useOriginalUfNames": "Y", "other": 1},
])
def test_get_scope_is_strict(bad_params):
    with pytest.raises(Bitrix1062ReadError):
        validate_read_request("crm.item.get", bad_params)


def test_broad_read_without_exact_key_rejected():
    p = list_params()
    p["filter"] = {"categoryId": 21}
    with pytest.raises(Bitrix1062ReadError, match="NOT_EXACT_SYNTHETIC_KEY"):
        validate_read_request("crm.item.list", p)


@pytest.mark.parametrize("response", [
    httpx.Response(302, headers={"Location": "https://evil.invalid/"}),
    httpx.Response(403, text="ACCESS_DENIED with private details"),
    httpx.Response(200, text="NOT JSON"),
    httpx.Response(200, json={"error": "ACCESS_DENIED", "error_description": "PRIVATE"}),
    httpx.Response(200, json={"result": {"items": None}}),
    httpx.Response(200, json={"result": {"items": [{}] * 50}}),
    httpx.Response(200, json={"result": {"items": [sample_item()]}, "next": 50}),
    httpx.Response(200, json={"result": {"items": [sample_item()]}, "total": 2}),
])
def test_errors_malformed_and_pagination_fail_closed_without_secret(response):
    with client_for(lambda _: response) as client:
        adapter = Bitrix1062ReadOnly(WEBHOOK, client=client)
        with pytest.raises(Bitrix1062ReadError) as err:
            adapter("crm.item.list", list_params())
    assert "TESTROTATEDSECRET" not in str(err.value)
    assert "/rest/" not in str(err.value)


def test_timeout_is_sanitized():
    def timeout(request):
        raise httpx.ConnectTimeout("https://serviseit.bitrix24.ru/rest/123/PRIVATE", request=request)
    with client_for(timeout) as client:
        adapter = Bitrix1062ReadOnly(WEBHOOK, client=client)
        with pytest.raises(Bitrix1062ReadError) as err:
            adapter("crm.item.list", list_params())
    assert "PRIVATE" not in str(err.value)
    assert "TESTROTATEDSECRET" not in str(err.value)


def test_webhook_target_guard():
    with client_for(_api) as client:
        for webhook in (
            "http://serviseit.bitrix24.ru/rest/123/TESTROTATEDSECRET123456",
            "https://evil.invalid/rest/123/TESTROTATEDSECRET123456",
            "https://serviseit.bitrix24.ru/rest/123/TESTROTATEDSECRET123456?debug=1",
        ):
            with pytest.raises(ValueError):
                Bitrix1062ReadOnly(webhook, client=client)


def test_repr_is_secret_free():
    with client_for(_api) as client:
        obj = Bitrix1062ReadOnly(WEBHOOK, client=client)
        assert "TESTROTATEDSECRET" not in repr(obj)
        assert "/rest/" not in repr(obj)
