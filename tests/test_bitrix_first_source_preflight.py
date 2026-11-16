"""Synthetic only: validates Bitrix smart-process portal gate, never CRM writes."""
import httpx
import pytest

from leadpilot.bitrix_first_source_preflight import probe, PreflightError

HOOK = "https://serviseit.bitrix24.ru/rest/123/TESTTOKENABC123"
NAME = "LeadGen AI — Тестирование"


def transport(*, process=NAME, fail=None):
    seen = []
    def handle(request):
        method = request.url.path.rsplit("/", 1)[-1]
        seen.append(method)
        if fail == method:
            return httpx.Response(403, json={"error": "ACCESS_DENIED"})
        if method == "crm.type.list":
            result = {"types": [{"title": process, "entityTypeId": 128},
                                {"title": "Рабочие продажи", "entityTypeId": 150}]}
        elif method == "crm.category.list":
            result = {"categories": [{"id": 0, "name": "Общее"},
                                     {"id": 1, "name": "Тестирование"}]}
        elif method == "crm.item.fields":
            result = {"fields": {"title": {}, "categoryId": {}, "xmlId": {}}}
        else:
            raise AssertionError("unexpected write or read method: " + method)
        return httpx.Response(200, json={"result": result})
    return httpx.MockTransport(handle), seen


def test_only_reads_exact_safe_smart_process():
    t, seen = transport()
    with httpx.Client(transport=t) as c:
        r = probe(HOOK, client=c)
    assert r["status"] == "ISOLATED_READ_ACCESS_CONFIRMED"
    assert seen == ["crm.type.list", "crm.category.list", "crm.item.fields"]
    assert r["smart_process_entity_type_id"] == 128
    assert r["crm_writes"] == "NO" and r["qualified_real_leads"] == 0


def test_no_process_does_not_read_real_contacts_or_write():
    t, seen = transport(process="production")
    with httpx.Client(transport=t) as c:
        r = probe(HOOK, client=c)
    assert r["status"] == "ISOLATED_SMART_PROCESS_NOT_FOUND"
    assert seen == ["crm.type.list"]


def test_portal_mismatch_is_blocked_before_any_api_call():
    t, seen = transport()
    with httpx.Client(transport=t) as c:
        with pytest.raises(PreflightError, match="host differs"):
            probe("https://other.bitrix24.ru/rest/123/TESTTOKENABC123", client=c)
    assert not seen


def test_permission_denied_is_safe_no_token_leak():
    t, seen = transport(fail="crm.category.list")
    with httpx.Client(transport=t) as c:
        r = probe(HOOK, client=c)
    assert r["status"] == "READ_FAILED"
    assert "TESTTOKENABC123" not in str(r)
    assert r["crm_writes"] == "NO"
    assert seen == ["crm.type.list", "crm.category.list"]


def test_invalid_webhook_does_not_leave_secret_in_error():
    with pytest.raises(PreflightError):
        probe("https://serviseit.bitrix24.ru/bitrix/tools/intranet_invite_dialog.php?checkword=SECRET")
