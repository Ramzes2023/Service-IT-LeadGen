"""Bitrix24 corporate metadata-only preflight, all network mocked."""
import json

import httpx
import pytest

from leadpilot.bitrix_metadata_audit import (
    validate_webhook, safe_name, offline_blueprint, audit, _post_read,
)

WEBHOOK = "https://serviseit.bitrix24.ru/rest/1607/SecretToken_Synthetic0001"


@pytest.mark.parametrize("bad", [
    "", "https://attacker.example/rest/1/secret123456",
    "http://serviseit.bitrix24.ru/rest/1607/secret123456",
    "https://serviseit.bitrix24.ru.evil.example/rest/1607/secret123456",
    "https://serviseit.bitrix24.ru/rest/0/secret123456",
    "https://serviseit.bitrix24.ru/rest/1/short",
    "https://serviseit.bitrix24.ru/rest/1/secret123456?private=1",
    "https://user:pass@serviseit.bitrix24.ru/rest/1/secret123456",
    "https://serviseit.bitrix24.ru/rest/1/secret123456/crm.item.add",
    "https://another.bitrix24.ru/rest/1/secret123456",
])
def test_only_exact_official_portal_and_webhook_path(bad):
    with pytest.raises(ValueError):
        validate_webhook(bad)


def test_webhook_not_in_reports():
    assert validate_webhook(WEBHOOK) == WEBHOOK
    assert "SecretToken" not in json.dumps(offline_blueprint())


def test_private_text_is_hidden_from_funnel_name():
    assert safe_name("Техническая поддержка") == "Техническая поддержка"
    assert safe_name("sales@company.example") == "[название скрыто]"
    assert safe_name("+7 928 123-45-67") == "[название скрыто]"
    assert safe_name("http://internal.company") == "[название скрыто]"
    assert safe_name("Team\nNew") == "[название скрыто]"


def test_mocked_portal_metadata_audit_no_customer_access():
    called = []

    def handler(request: httpx.Request) -> httpx.Response:
        method = request.url.path.split("/")[-1]
        called.append(method)
        assert request.method == "POST"
        assert request.url.host == "serviseit.bitrix24.ru"
        payload = json.loads(request.content)
        if method == "crm.category.list":
            if payload["entityTypeId"] == 128:
                return httpx.Response(200, json={"result": {"categories": [
                    {"id": 3, "name": "Карточки обращений"},
                ]}})
            assert payload == {"entityTypeId": 2}
            return httpx.Response(200, json={"result": {"categories": [
                {"id": 0, "name": "Общая"},
                {"id": 5, "name": "Техническая поддержка"},
                {"id": 10, "name": "LeadGen AI — Тестирование"},
                {"id": 11, "name": "info@private.example"},
            ]}})
        if method == "crm.item.fields":
            assert payload["entityTypeId"] in (1, 2, 3, 4, 128)
            assert payload["useOriginalUfNames"] == "Y"
            return httpx.Response(200, json={"result": {"fields": {
                "title": {"title": "Real customer phone +7 999 999-99-99"},
                "UF_CRM_1699": {"title": "Private"},
                "not safe key": {"title": "Not used"},
            }}})
        if method == "crm.type.list":
            assert payload == {}
            return httpx.Response(200, json={"result": {"types": [
                {"entityTypeId": 128, "title": "Тестовые лиды"},
                {"entityTypeId": 2, "title": "Неподходящий системный тип"},
            ]}})
        if method == "crm.status.list":
            entity_code = payload["filter"]["ENTITY_ID"]
            return httpx.Response(200, json={"result": [
                {"ENTITY_ID": entity_code, "STATUS_ID": "NEW", "NAME": "Новая"},
                {"ENTITY_ID": entity_code, "STATUS_ID": "PRIVATE", "NAME": "owner@example.org"},
                {"ENTITY_ID": "OTHER", "STATUS_ID": "HIDDEN", "NAME": "Не та стадия"},
            ]})
        raise AssertionError("Forbidden API method: " + method)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = audit(WEBHOOK, client=client)

    assert result["status"] == "METADATA_READ_PASS"
    assert result["crm_writes"] == "NO"
    assert result["customer_records_read"] == 0
    assert result["crm_write_authorized"] is False
    assert [x["id"] for x in result["deal_categories"]] == [0, 5, 10, 11]
    assert result["deal_categories"][3]["name"] == "[название скрыто]"
    assert len(result["smart_processes"]) == 1
    assert result["smart_processes"][0]["entityTypeId"] == 128
    assert result["entity_fields"]["deal"]["user_field_codes"] == ["UF_CRM_1699"]
    assert result["entity_fields"]["contact"]["user_field_codes"] == ["UF_CRM_1699"]
    assert [s["id"] for s in result["deal_categories"][1]["stages"]] == ["NEW", "PRIVATE"]
    assert result["smart_processes"][0]["fields"]["user_field_codes"] == ["UF_CRM_1699"]
    assert [s["id"] for s in result["smart_processes"][0]["categories"][0]["stages"]] == ["NEW", "PRIVATE"]
    assert result["deal_categories"][1]["stages"][1]["name"] == "[название скрыто]"
    assert called.count("crm.status.list") == 5
    assert "SecretToken" not in str(result)
    assert "Real customer phone" not in str(result)


def test_api_denial_fail_closed_and_redacted():
    def handler(request):
        return httpx.Response(200, json={"error": "ACCESS_DENIED",
                                         "error_description": WEBHOOK})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = audit(WEBHOOK, client=client)
    assert result["status"] == "PARTIAL_OR_ACCESS_DENIED"
    assert result["safe_test_space"] == "NOT_VERIFIED"
    assert not result["deal_categories"]
    assert "SecretToken" not in str(result)


def test_write_method_forbidden_before_any_network():
    with httpx.Client(transport=httpx.MockTransport(
            lambda _: pytest.fail("Unexpected network request"))) as client:
        with pytest.raises(ValueError, match="NOT_ALLOWED"):
            _post_read(client, WEBHOOK, "crm.item.add", {})
        with pytest.raises(ValueError, match="NOT_ALLOWED"):
            _post_read(client, WEBHOOK, "crm.lead.add", {})
        with pytest.raises(ValueError, match="NOT_ALLOWED"):
            _post_read(client, WEBHOOK, "crm.item.list", {})
        with pytest.raises(ValueError, match="NOT_ALLOWED"):
            _post_read(client, WEBHOOK, "crm.deal.add", {})


def test_offline_blueprint_has_no_external_effects():
    blueprint = offline_blueprint()
    assert blueprint["crm_writes"] == "NO"
    assert blueprint["customer_records_read"] == 0
    assert blueprint["production_support_funnel"] == "PRESERVE_AND_DO_NOT_WRITE"
    assert blueprint["mode"] == "OFFLINE_PLAN_NO_NETWORK"


def test_live_audit_runner_is_ascii_safe_for_windows_powershell_51():
    from pathlib import Path

    script = (Path(__file__).resolve().parents[1] / "scripts" /
              "RUN_SERVICEIT_BITRIX_METADATA_AUDIT.ps1")
    raw = script.read_bytes()
    assert all(byte < 128 for byte in raw)
    source = raw.decode("ascii")
    assert "Read-Host" in source and "-AsSecureString" in source
    assert "BITRIX_METADATA_OPERATOR_APPROVAL_REQUIRED" in source
    assert "crm.item.fields" not in source  # PowerShell runner delegates to the audited module.
