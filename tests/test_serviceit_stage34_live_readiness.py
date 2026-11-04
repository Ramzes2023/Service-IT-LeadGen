"""Live-readiness preflight verifies task and score metadata without mutation."""
from datetime import datetime, timezone
import json

import httpx
import pytest

from leadpilot import serviceit_stage34_live_readiness as readiness

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
HOOK = "https://serviseit.bitrix24.ru/rest/123/TEST_TOKEN"
ENV = {"SERVICEIT_BITRIX_POLL_START_UTC": "2026-11-04T09:50:00Z"}


def fixture(req, *, bad_category=False, missing_score=False,
            different_page_total=False, no_task_link=False):
    assert req.method == "POST"
    assert req.url.host == "serviseit.bitrix24.ru"
    body = json.loads(req.content)
    path = req.url.path
    if path.endswith("/crm.type.getByEntityTypeId.json"):
        assert body == {"entityTypeId": 1062}
        return httpx.Response(200, json={"result": {"type": {
            "id": 13, "entityTypeId": 1062,
            "isUseInUserfieldEnabled": "Y",
            "linkedUserFields": {
                "TASKS_TASK|UF_CRM_TASK": "N" if no_task_link else "Y"},
        }}})
    if path.endswith("/crm.category.get.json"):
        assert body == {"entityTypeId": 1062, "id": 21}
        return httpx.Response(200, json={"result": {"category": {
            "id": 22 if bad_category else 21,
            "entityTypeId": 1062, "name": "Сервис ИТ — AI-сигналы",
        }}})
    if path.endswith("/tasks.task.getFields.json"):
        assert body == {}
        return httpx.Response(200, json={"result": {"fields": {
            "TITLE": {"type": "string"},
            "DESCRIPTION": {"type": "string"},
            "RESPONSIBLE_ID": {"type": "integer"},
            "UF_CRM_TASK": {"type": "crm"},
        }}})
    if path.endswith("/crm.enum.ownertype.json"):
        assert body == {}
        return httpx.Response(200, json={"result": [
            {"ID": 1062, "SYMBOL_CODE_SHORT": "T426"}]})
    if path.endswith("/crm.item.fields.json"):
        assert body == {"entityTypeId": 1062, "useOriginalUfNames": "Y"}
        fields = {
            "UF_CRM_13_SVCIT_LEAD_SCORE": {"type": "integer", "isReadOnly": False},
            "UF_CRM_13_SVCIT_SCORE_VERSION": {"type": "string", "isReadOnly": False},
            "UF_CRM_13_SVCIT_AI_SCORE_BADGE": {"type": "string", "isReadOnly": False},
        }
        if missing_score:
            fields.pop("UF_CRM_13_SVCIT_LEAD_SCORE")
        return httpx.Response(200, json={"result": {"fields": fields}})
    if path.endswith("/crm.item.list.json"):
        assert body["entityTypeId"] == 1062
        assert body["filter"]["categoryId"] == 21
        assert body["select"] == ["id", "categoryId", "createdTime"]
        assert body["start"] == 0
        return httpx.Response(200, json={
            "result": {"items": [], **({"total": 1} if different_page_total else {})},
            "total": 0,
        })
    pytest.fail(f"Only read-only REST allowed, got {path}")


def test_readonly_metadata_all_fields_present_and_no_future_cards():
    calls = []
    def handler(req):
        calls.append(req.url.path.rsplit("/", 1)[-1])
        return fixture(req)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = readiness.inspect(client, HOOK, ENV, now=NOW)
    assert len(calls) == 6
    assert result["status"] == "SERVICEIT_STAGE34_METADATA_CONTRACT_PASS"
    assert result["metadata_contract_complete"] is True
    assert result["task_link_configured"] is True
    assert result["task_fields_visible"] is True
    assert result["can_create_task_scope_confirmed"] is False
    assert all(result["score_fields_readonly_schema"].values())
    assert result["eligible_future_card_count"] == 0
    assert result["crm_writes"] == 0
    assert result["task_add_calls"] == 0
    assert all("add.json" not in x and "update.json" not in x for x in calls)


def test_missing_score_field_reported_without_any_crm_write():
    with httpx.Client(transport=httpx.MockTransport(
            lambda req: fixture(req, missing_score=True))) as client:
        result = readiness.inspect(client, HOOK, ENV, now=NOW)
    assert result["score_fields_readonly_schema"]["score_valid"] is False
    assert result["status"] == "SERVICEIT_STAGE34_METADATA_GAPS"
    assert result["metadata_contract_complete"] is False
    assert result["can_create_task_scope_confirmed"] is False


def test_task_link_disabled_reported_not_fabricated():
    with httpx.Client(transport=httpx.MockTransport(
            lambda req: fixture(req, no_task_link=True))) as client:
        result = readiness.inspect(client, HOOK, ENV, now=NOW)
    assert result["task_link_configured"] is False
    assert result["status"] == "SERVICEIT_STAGE34_METADATA_GAPS"


def test_wrong_category_blocks_before_querying_any_task_or_score_metadata():
    seen = []
    def handler(req):
        seen.append(req.url.path)
        return fixture(req, bad_category=True)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(readiness.ReadinessBlocked, match="SERVICEIT_CATEGORY_MISMATCH"):
            readiness.inspect(client, HOOK, ENV, now=NOW)
    assert len(seen) == 2


def test_metadata_conflict_in_list_blocks_unsafe_incomplete_scan():
    with httpx.Client(transport=httpx.MockTransport(
            lambda req: fixture(req, different_page_total=True))) as client:
        with pytest.raises(readiness.ReadinessBlocked, match="LIST_PAGINATION_CONFLICT"):
            readiness.inspect(client, HOOK, ENV, now=NOW)


@pytest.mark.parametrize("method,args", [
    ("tasks.task.add", {"fields": {"TITLE": "not allowed"}}),
    ("crm.item.update", {"entityTypeId": 1062, "id": 99, "fields": {}}),
    ("crm.item.list", {"entityTypeId": 1062,
                       "filter": {"categoryId": 22, ">=createdTime": "2026-11-04"}}),
    ("crm.category.get", {"entityTypeId": 1062, "id": 22}),
    ("crm.item.fields", {"entityTypeId": 2, "useOriginalUfNames": "Y"}),
])
def test_write_or_other_category_rejected_before_network(method, args):
    with httpx.Client(transport=httpx.MockTransport(
            lambda req: pytest.fail("unapproved network access"))) as client:
        with pytest.raises(readiness.ReadinessBlocked,
                           match="READONLY_METHOD_OR_SCOPE_FORBIDDEN"):
            readiness.read(client, HOOK, method, args)
