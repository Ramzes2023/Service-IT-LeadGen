"""Synthetic-only POST adapter: httpx.MockTransport, never corporate Bitrix24."""
from datetime import date
import copy
import json

import httpx
import pytest

from leadpilot.bitrix_handoff_dryrun import build_offline_handoff, synthetic_fixture
from leadpilot.bitrix_1062_mapping import build_synthetic_1062_plan, SyntheticPlanRejected
from leadpilot.bitrix_1062_synthetic_add import (
    Bitrix1062SyntheticAddOnly, CONFIRMATION, validate_synthetic_create,
)

NOW = date(2026, 11, 1)
WEBHOOK = "https://serviseit.bitrix24.ru/rest/123/TEST_SYNTHETIC_TOKEN_123456"


def params():
    plan = build_synthetic_1062_plan(
        build_offline_handoff(synthetic_fixture(NOW), today=NOW), today=NOW
    )
    return copy.deepcopy(plan["requests_not_sent"][0]["params"])


def flags():
    return dict(approval=CONFIRMATION, rotated_webhook_confirmed=True,
                robots_disabled_confirmed=True,
                authorized_test_confirmed=True, today=NOW)


def test_canonical_payload_has_sixteen_fields():
    payload = params()
    key = validate_synthetic_create(payload, today=NOW)
    assert key == payload["fields"]["UF_CRM_13_SVCIT_IDEMPOTENCY_KEY"]
    assert sum(field.startswith("UF_CRM") for field in payload["fields"]) == 16


def test_one_post_only_and_unverified_receipt():
    calls = []
    def handler(request):
        calls.append(request)
        assert request.method == "POST"
        assert request.url.host == "serviseit.bitrix24.ru"
        assert request.url.path.endswith("/crm.item.add.json")
        assert json.loads(request.content) == params()
        return httpx.Response(200, json={"result": {"item": {"id": 101}}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        add = Bitrix1062SyntheticAddOnly(WEBHOOK, client=client)
        result = add.add_once(params(), **flags())
        assert result["status"] == "POST_RESPONSE_UNVERIFIED"
        assert result["requires_readback"] is True
        assert result["candidate_id"] == 101
        assert add.add_once(params(), **flags())["status"] == "SECOND_POST_BLOCKED"
    assert len(calls) == 1


@pytest.mark.parametrize("change", [
    {"approval": "WRONG"}, {"rotated_webhook_confirmed": False},
    {"robots_disabled_confirmed": False}, {"authorized_test_confirmed": False},
    {"rotated_webhook_confirmed": 1}, {"robots_disabled_confirmed": 1},
])
def test_missing_explicit_gates_prevent_http(change):
    calls = []
    with httpx.Client(transport=httpx.MockTransport(lambda req: calls.append(req))) as client:
        add = Bitrix1062SyntheticAddOnly(WEBHOOK, client=client)
        assert add.add_once(params(), **{**flags(), **change})["status"] == "WRITE_GATE_DENIED"
    assert calls == []


@pytest.mark.parametrize("change", [
    ("entityTypeId", 2), ("entityTypeId", True), ("useOriginalUfNames", "N"),
])
def test_wrong_entity_or_encoding_rejected(change):
    payload = params()
    payload[change[0]] = change[1]
    with pytest.raises(SyntheticPlanRejected):
        validate_synthetic_create(payload, today=NOW)


@pytest.mark.parametrize("field,value", [
    ("categoryId", 19),
    ("stageId", "C19:NEW"),
    ("title", "Настоящий клиент"),
    ("UF_CRM_13_SVCIT_SIGNAL_SOURCE", "eis-official"),
    ("UF_CRM_13_SVCIT_SIGNAL_URL", "https://real.company.net/buy"),
    ("UF_CRM_13_SVCIT_SIGNAL_KIND", "commercial"),
    ("UF_CRM_13_SVCIT_IDEMPOTENCY_KEY", "DEMO-IDEMP-" + "a"*32),
    ("UF_CRM_13_SVCIT_LEAD_SCORE", 120),
    ("UF_CRM_13_SVCIT_COMPANY_NAME", "Real LLC"),
    ("UF_CRM_13_SVCIT_SIGNAL_DATE", "2026-04-01"),
    ("UF_CRM_13_SVCIT_MANAGER_NEXT_ACTION", "Call +7 999 000 0000 to discuss"),
])
def test_changed_synthetic_fields_are_rejected(field, value):
    payload = params()
    payload["fields"][field] = value
    with pytest.raises(SyntheticPlanRejected):
        validate_synthetic_create(payload, today=NOW)


def test_no_contacts_deals_arbitrary_fields():
    for field, value in (("contactId", 15), ("companyId", 9), ("phone", "123"),
                         ("UF_CRM_13_ADDITIONAL", "test")):
        payload = params()
        payload["fields"][field] = value
        with pytest.raises(SyntheticPlanRejected):
            validate_synthetic_create(payload, today=NOW)


@pytest.mark.parametrize("response", [
    httpx.Response(302, headers={"location": "https://evil.invalid"}),
    httpx.Response(403, json={"error": "ACCESS_DENIED"}),
    httpx.Response(200, text="not-json"),
    httpx.Response(200, json={"error": "INTERNAL_ERROR"}),
    httpx.Response(200, json={"result": {"item": {"id": "A"}}}),
    httpx.Response(200, json={"result": {"item": {"id": True}}}),
    httpx.Response(200, json={"result": {}}),
])
def test_unknown_post_result_is_never_assumed_failed(response):
    with httpx.Client(transport=httpx.MockTransport(lambda _: response)) as client:
        add = Bitrix1062SyntheticAddOnly(WEBHOOK, client=client)
        result = add.add_once(params(), **flags())
        assert result["status"] == "POST_RESULT_UNCERTAIN"
        assert result["crm_writes"] == "UNKNOWN"
        assert result["requires_readback"] is True
        assert add.add_once(params(), **flags())["status"] == "SECOND_POST_BLOCKED"


def test_timeout_does_not_expose_secret_or_allow_replay():
    calls = []
    def timeout(req):
        calls.append(req)
        raise httpx.ConnectTimeout("https://serviseit.bitrix24.ru/rest/123/PRIVATE_TOKEN", request=req)
    with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
        add = Bitrix1062SyntheticAddOnly(WEBHOOK, client=client)
        result = add.add_once(params(), **flags())
        assert result["status"] == "POST_RESULT_UNCERTAIN"
        assert add.add_once(params(), **flags())["status"] == "SECOND_POST_BLOCKED"
        assert "TEST_SYNTHETIC_TOKEN" not in repr(add)
        assert "PRIVATE_TOKEN" not in repr(result)
    assert len(calls) == 1
