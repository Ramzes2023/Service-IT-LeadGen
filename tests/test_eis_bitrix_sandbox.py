"""No outgoing CRM writes in EIS acceptance; mocked Bitrix24 only."""
import json

import httpx
import pytest

from leadpilot.eis_bitrix_sandbox import (
    EISSmartProcessConfig, preflight, _call, PreflightError,
)

HOOK = "https://testoffice.bitrix24.ru/rest/42/ABCdefGhiJk12"
ENTITY = 164
CATEGORY = 8
FIELD = "ufCrm164_123456789"
NAME = "LeadGen AI — Тестирование"


def config():
    return EISSmartProcessConfig(HOOK, ENTITY, CATEGORY, NAME, FIELD)


def replies(request):
    method = request.url.path.rsplit("/", 1)[-1]
    body = json.loads(request.content)
    if method == "crm.category.list":
        assert body == {"entityTypeId": ENTITY}
        return httpx.Response(200, json={"result": {"categories": [
            {"id": 0, "name": "Общая"},
            {"id": CATEGORY, "name": NAME},
            {"id": 9, "name": "Продажи"}]}})
    if method == "crm.item.fields":
        assert body == {"entityTypeId": ENTITY}
        return httpx.Response(200, json={"result": {"fields": {
            "title": {}, "categoryId": {}, "stageId": {}, FIELD: {}}}})
    if method == "crm.item.list":
        assert body["entityTypeId"] == ENTITY
        assert body["filter"] == {"categoryId": CATEGORY}
        return httpx.Response(200, json={"result": {"items": [{"id": 51}]}})
    pytest.fail("Unapproved Bitrix method invoked " + method)


def test_mock_preflight_reads_smartprocess_only():
    methods = []
    def handler(r):
        methods.append(r.url.path.rsplit("/", 1)[-1])
        return replies(r)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = preflight(config(), client)
    assert result["status"] == "READ_OK_WRITE_AND_DUPLICATE_GATES_CLOSED"
    assert result["category_confirmed"] is True and result["inn_field_found"] is True
    assert result["isolated_item_count"] == 1
    assert methods == ["crm.category.list", "crm.item.fields", "crm.item.list"]
    assert result["crm_writes"] == "NO" and result["crm_ready"] is False
    assert result["write_authorized"] is False
    assert result["existing_clients_checked"] is False
    assert result["company_inn_duplicates_checked"] is False
    assert HOOK not in str(result)


@pytest.mark.parametrize("entity,category", [(1, 7), (2, 7), (164, 0), (-1, 2), (True, 2)])
def test_standard_leads_deals_and_default_pipeline_forbidden(entity, category):
    with pytest.raises(PreflightError):
        EISSmartProcessConfig(HOOK, entity, category, NAME, FIELD)


def test_no_write_even_by_direct_helper():
    with httpx.Client(transport=httpx.MockTransport(
            lambda _: pytest.fail("No transport should be used"))) as client:
        with pytest.raises(PreflightError):
            _call(client, HOOK, "crm.item.add", {})


def test_no_test_category_no_field_or_portal_data():
    def missing(req):
        if req.url.path.endswith("crm.category.list"):
            return httpx.Response(200, json={"result": {"categories": [
                {"id": 9, "name": "Общие продажи"}]}})
        pytest.fail("Cannot continue to fields after mismatch")
    with httpx.Client(transport=httpx.MockTransport(missing)) as client:
        r = preflight(config(), client)
    assert r["status"] == "TEST_CATEGORY_MISSING_OR_UNVERIFIED"
    assert r["methods_called"] == ["crm.category.list"]


def test_bitrix_access_denied_hides_secret():
    with httpx.Client(transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"error": "ACCESS_DENIED", "error_description": HOOK}))) as client:
        r = preflight(config(), client)
    assert r["status"] == "READ_FAILED_OR_PERMISSION_DENIED"
    assert HOOK not in json.dumps(r)


def test_missing_inn_field_blocks_query():
    def handler(r):
        if r.url.path.endswith("crm.category.list"):
            return replies(r)
        if r.url.path.endswith("crm.item.fields"):
            return httpx.Response(200, json={"result": {"fields": {
                "title": {}, "categoryId": {}, "stageId": {}}}})
        pytest.fail("No item.list after missing required custom mapping")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        r = preflight(config(), client)
    assert r["status"] == "REQUIRED_CRM_FIELDS_MISSING"
    assert r["write_authorized"] is False


@pytest.mark.parametrize("hook", [
    "https://evil.example/rest/42/ABCdefGhiJk12",
    "http://testoffice.bitrix24.ru/rest/42/ABCdefGhiJk12",
    "https://testoffice.bitrix24.ru/rest/42/ABCdefGhiJk12?secret=foo",
])
def test_unsafe_webhooks_never_accepted(hook):
    with pytest.raises(PreflightError):
        EISSmartProcessConfig(hook, ENTITY, CATEGORY, NAME, FIELD)
