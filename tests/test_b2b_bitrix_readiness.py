"""Bitrix24 B2B source metadata: four read calls, fail closed, NO writes."""
import httpx
import pytest

from leadpilot.bitrix_1062_mapping import ALL_FIELD_CODES
from leadpilot.b2b_bitrix_readiness import (
    PreflightBlocked, ReadOnlyBitrix, preflight,
)

HOOK = "https://serviseit.bitrix24.ru/rest/123/TESTreadOnlyToken123"


def mock_rest(request):
    m = request.url.path.rsplit("/", 1)[-1].removesuffix(".json")
    data = __import__("json").loads(request.content)
    if m == "crm.type.getByEntityTypeId":
        assert data == {"entityTypeId": 1062}
        result = {"type": {"id": 13, "entityTypeId": 1062,
                           "isAutomationEnabled": False,
                           "isBizProcEnabled": "N"}}
    elif m == "crm.category.list":
        assert data == {"entityTypeId": 1062}
        result = {"categories": [{"id": 21, "name": "AI-сигналы"}]}
    elif m == "crm.item.fields":
        assert data == {"entityTypeId": 1062, "useOriginalUfNames": "Y"}
        result = {"fields": {x: {"type": "string", "isReadOnly": False,
                                "isImmutable": False} for x in ALL_FIELD_CODES}}
    elif m == "crm.status.list":
        assert data == {"filter": {"ENTITY_ID": "DYNAMIC_1062_STAGE_21"}}
        result = [{"ENTITY_ID": "DYNAMIC_1062_STAGE_21", "STATUS_ID": "DT1062_21:NEW"}]
    else:
        pytest.fail("Unexpected REST call " + m)
    return httpx.Response(200, json={"result": result})


def run_with(handler=mock_rest):
    observed = []
    def inner(req):
        observed.append(req.url.path.rsplit("/", 1)[-1])
        return handler(req)
    with httpx.Client(transport=httpx.MockTransport(inner)) as client:
        result = preflight(ReadOnlyBitrix(HOOK, client=client))
    return result, observed


def test_real_metadata_path_no_customer_items_or_writes():
    report, observed = run_with()
    assert report["status"] == "BITRIX_B2B_HOLD_METADATA_READ_ONLY_PASS"
    assert report["crm_writes"] == "NO"
    assert report["crm_customer_records_read"] == 0
    assert report["real_procurement_cards_created"] == 0
    assert report["safe_review_stage"] == "DT1062_21:NEW"
    assert observed == [
        "crm.type.getByEntityTypeId.json", "crm.category.list.json",
        "crm.item.fields.json", "crm.status.list.json",
    ]


def test_automation_or_robots_not_confirmed_disabled_blocks():
    def bad(req):
        out = mock_rest(req)
        if "crm.type.getByEntityTypeId" in req.url.path:
            return httpx.Response(200, json={"result": {"type": {
                "id": 13, "entityTypeId": 1062,
                "isAutomationEnabled": True, "isBizProcEnabled": "N",
            }}})
        return out
    with pytest.raises(PreflightBlocked, match="AUTOMATION_NOT_CONFIRMED_DISABLED"):
        run_with(bad)


def test_missing_custom_field_blocks():
    def bad(req):
        if "crm.item.fields" in req.url.path:
            return httpx.Response(200, json={"result": {"fields": {
                x: {"type": "string", "isReadOnly": False} for x in ALL_FIELD_CODES
                if x != "UF_CRM_13_SVCIT_SIGNAL_KIND"
            }}})
        return mock_rest(req)
    with pytest.raises(PreflightBlocked, match="ORIGINAL_16_FIELDS_MISSING"):
        run_with(bad)


def test_enum_or_readonly_kind_field_blocks():
    def bad(req):
        if "crm.item.fields" in req.url.path:
            fields = {x: {"type": "string", "isReadOnly": False}
                      for x in ALL_FIELD_CODES}
            fields["UF_CRM_13_SVCIT_SIGNAL_KIND"] = {
                "type": "enumeration", "isReadOnly": False}
            return httpx.Response(200, json={"result": {"fields": fields}})
        return mock_rest(req)
    with pytest.raises(PreflightBlocked, match="NOT_WRITABLE_TEXT"):
        run_with(bad)


def test_missing_stage_blocks():
    def bad(req):
        if "crm.status.list" in req.url.path:
            return httpx.Response(200, json={"result": []})
        return mock_rest(req)
    with pytest.raises(PreflightBlocked, match="SAFE_NEW_STAGE_NOT_VERIFIED"):
        run_with(bad)


def test_bad_target_category_blocks():
    def bad(req):
        if "crm.category.list" in req.url.path:
            return httpx.Response(200, json={"result": {"categories": [{"id": 99}]}})
        return mock_rest(req)
    with pytest.raises(PreflightBlocked, match="TARGET_CATEGORY_21_NOT_FOUND"):
        run_with(bad)


def test_webhook_other_domain_denied_before_any_request():
    events = []
    def recorder(req):
        events.append(req)
        return mock_rest(req)
    with httpx.Client(transport=httpx.MockTransport(recorder)) as client:
        with pytest.raises(ValueError, match="WEBHOOK_MUST_BE_OFFICIAL_PORTAL"):
            ReadOnlyBitrix("https://evil.test/rest/123/TESTreadOnlyToken123", client=client)
    assert events == []


def test_access_denied_and_error_response_block():
    def deny(req):
        return httpx.Response(403, json={"error": "ACCESS_DENIED"})
    with pytest.raises(PreflightBlocked, match="REST_HTTP_DENIED"):
        run_with(deny)


def test_no_hidden_pagination():
    def next_page(req):
        return httpx.Response(200, json={"result": {"type": {
            "id": 13, "entityTypeId": 1062,
            "isAutomationEnabled": False, "isBizProcEnabled": False}}, "next": 50})
    with pytest.raises(PreflightBlocked, match="REST_RESULT_DENIED"):
        run_with(next_page)
