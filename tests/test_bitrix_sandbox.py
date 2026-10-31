"""Mocked CRM tests: the sandbox preflight must never mutate production data."""
import json
import httpx
import pytest

from leadpilot.bitrix_sandbox import (
    PreflightConfig, PreflightError, read_only_preflight, suggested_blueprint,
    validate_webhook, _read_call,
)

WEBHOOK = "https://my-test.bitrix24.ru/rest/42/AbcdEfgHIj_45"


@pytest.mark.parametrize("bad", [
    "", "http://my-test.bitrix24.ru/rest/42/token123", "https://evil.com/rest/42/token123",
    "https://my-test.bitrix24.ru.evil.org/rest/42/token123",
    "https://my-test.bitrix24.ru/rest/42/token123?preview=1",
    "https://user:pass@my-test.bitrix24.ru/rest/42/token123",
    "https://my-test.bitrix24.ru/rest/0/token123",
    "https://my-test.bitrix24.ru/rest/42/short",
    "https://my-test.bitrix24.ru/rest/42/token123/crm.item.add",
])
def test_bad_webhook_rejected(bad):
    with pytest.raises(PreflightError):
        validate_webhook(bad)


def test_valid_url_never_logged():
    assert validate_webhook(WEBHOOK) == WEBHOOK
    assert "token123" not in repr(suggested_blueprint())


def test_default_category_never_accepted():
    with pytest.raises(PreflightError):
        PreflightConfig(WEBHOOK, category_id=0)


def responses(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    path = str(request.url)
    if path.endswith('/crm.category.list'):
        assert body == {'entityTypeId': 2}
        return httpx.Response(200, json={'result': {'categories': [
            {'id': 0, 'name': 'Общая'},
            {'id': 9, 'name': 'LeadGen AI — Тестирование'},
            {'id': 10, 'name': 'Основные сделки'}
        ]}})
    if path.endswith('/crm.item.fields'):
        assert body == {'entityTypeId': 2}
        return httpx.Response(200, json={'result': {
            'fields': {'title': {}, 'categoryId': {}, 'stageId': {}}}})
    if path.endswith('/crm.status.list'):
        assert body == {'filter': {'ENTITY_ID': 'DEAL_STAGE_9'}}
        return httpx.Response(200, json={'result': [
            {'ENTITY_ID': 'DEAL_STAGE_9', 'STATUS_ID': 'C9:NEW', 'NAME': 'Новый сигнал'},
            {'ENTITY_ID': 'DEAL_STAGE_10', 'STATUS_ID': 'C10:NEW', 'NAME': 'Чужая воронка'}
        ]})
    raise AssertionError('Unexpected API method: ' + path)


def test_preflight_only_reads_and_returns_safe_report():
    called = []
    def handler(request):
        called.append(request.url.path.rsplit('/', 1)[-1])
        return responses(request)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = read_only_preflight(PreflightConfig(WEBHOOK), client)
    assert result['status'] == 'READ_ACCESS_CONFIRMED'
    assert result['sandbox_id'] == 9
    assert result['stages'] == [{'id': 'C9:NEW', 'name': 'Новый сигнал'}]
    assert called == ['crm.category.list', 'crm.item.fields', 'crm.status.list']
    assert 'AbcdEfg' not in str(result)
    assert result['crm_writes'] == 'NO'


def test_category_mismatch_does_not_probe_more():
    called = []
    def handler(request):
        called.append(request.url.path)
        return responses(request)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = read_only_preflight(PreflightConfig(WEBHOOK, category_id=10), client)
    assert result['status'] == 'SANDBOX_NOT_FOUND_OR_AMBIGUOUS'
    assert len(called) == 1


def test_api_permission_denied_fail_closed():
    def deny(_request):
        return httpx.Response(200, json={
            'error': 'ACCESS_DENIED', 'error_description': WEBHOOK})
    with httpx.Client(transport=httpx.MockTransport(deny)) as client:
        result = read_only_preflight(PreflightConfig(WEBHOOK), client)
    assert result['status'] == 'READ_FAILED'
    assert WEBHOOK not in str(result)


def test_allowlist_rejects_any_write_operation():
    with httpx.Client(transport=httpx.MockTransport(
            lambda _: pytest.fail('Unexpected HTTP request'))) as client:
        with pytest.raises(PreflightError):
            _read_call(client, WEBHOOK, 'crm.item.add', {})


def test_blueprint_never_contains_remote_request_or_secrets():
    data = suggested_blueprint()
    assert data['crm_writes'] == 'NO'
    assert data['entityTypeId'] == 2
    assert data['category_name'] == 'LeadGen AI — Тестирование'
    assert len(data['stages']) >= 5
