"""Offline security regression tests for the *read-only* cutover diagnostic."""
import json
from pathlib import Path

import httpx
import pytest

from leadpilot.serviceit_cloud_cutover_readonly import main


SAFE_FAKE_HOOK = "https://serviseit.bitrix24.ru/rest/1/syntheticSECRET01234567"


def mock_http_client(monkeypatch, route):
    original = httpx.Client
    def factory(**kwargs):
        return original(transport=httpx.MockTransport(route), **kwargs)
    monkeypatch.setattr("leadpilot.serviceit_cloud_cutover_readonly.httpx.Client", factory)


def test_realistic_success_does_not_leak_customer_fields(tmp_path, monkeypatch, capsys):
    from leadpilot.serviceit_cloud_cutover_readonly import latest_existing_card_id
    def rest(method, params):
        assert method in ("crm.item.list", "crm.item.get")
        if method == "crm.item.list":
            return {"items": [{"id": 123, "categoryId": 21}]}
        return {"item": {"id": 123, "entityTypeId": 1062, "categoryId": 21,
                         "title": "CUSTOMER_SECRET_MUST_NOT_LOG"}}
    assert latest_existing_card_id(rest) == 123


def test_readonly_access_denied_reports_only_safe_whitelisted_code(monkeypatch, capsys):
    secret = "HIDDEN_CLIENT_DATA_AND_SECRET_CREDENTIAL"
    def route(request):
        assert request.url.path.endswith("crm.item.list.json")
        return httpx.Response(200, json={
            "error": "ACCESS_DENIED",
            "error_description": secret,
        })
    mock_http_client(monkeypatch, route)
    monkeypatch.setenv("SERVICEIT_BITRIX_WEBHOOK", SAFE_FAKE_HOOK)
    assert main() == 2
    output = capsys.readouterr().out
    assert "SERVICEIT_CLOUD_CUTOVER=UNVERIFIED_READ_ONLY" in output
    assert "SERVICEIT_CLOUD_CUTOVER_DIAGNOSTIC=LIST:REST_RESPONSE_UNVERIFIED:HTTP_200:API_ACCESS_DENIED" in output
    assert secret not in output and "syntheticSECRET" not in output


def test_readonly_top_get_error_reports_exact_stage_not_api_content(monkeypatch, capsys):
    def route(request):
        if request.url.path.endswith("crm.item.list.json"):
            return httpx.Response(200, json={"result": {
                "items": [{"id": 101, "categoryId": 21}]
            }})
        assert request.url.path.endswith("crm.item.get.json")
        return httpx.Response(200, json={
            "error": "LEAKED_CLIENT_SECRET_UNTRUSTED",
            "error_description": "CUSTOMER DATA",
        })
    mock_http_client(monkeypatch, route)
    monkeypatch.setenv("SERVICEIT_BITRIX_WEBHOOK", SAFE_FAKE_HOOK)
    assert main() == 2
    output = capsys.readouterr().out
    assert "SERVICEIT_CLOUD_CUTOVER_DIAGNOSTIC=GET_TOP:REST_RESPONSE_UNVERIFIED:HTTP_200:API_OTHER_REDACTED" in output
    assert "LEAKED_CLIENT_SECRET" not in output and "CUSTOMER DATA" not in output


def test_shape_error_reports_only_fixed_reason(monkeypatch, capsys):
    def route(request):
        return httpx.Response(200, json={"result": {"items": [{
            "id": 10, "categoryId": 99, "private_text": "CUSTOMER_SECRET"}]}})
    mock_http_client(monkeypatch, route)
    monkeypatch.setenv("SERVICEIT_BITRIX_WEBHOOK", SAFE_FAKE_HOOK)
    assert main() == 2
    output = capsys.readouterr().out
    assert "LIST:CUTOVER_CATEGORY_MISMATCH:HTTP_200:API_NONE" in output
    assert "CUSTOMER_SECRET" not in output


def test_transport_failed_does_not_expose_url(monkeypatch, capsys):
    def route(request):
        raise httpx.ConnectTimeout("http://secret-credential-do-not-print")
    mock_http_client(monkeypatch, route)
    monkeypatch.setenv("SERVICEIT_BITRIX_WEBHOOK", SAFE_FAKE_HOOK)
    assert main() == 2
    out = capsys.readouterr().out
    assert "LIST:HTTP_TIMEOUT:HTTP_NOT_SENT:API_NONE" in out
    assert "secret-credential" not in out


def test_windows_diagnostic_does_not_trigger_credential_upload_or_task_disable():
    source = Path("scripts/SETUP_SERVICEIT_FREE_GITHUB_ACTIONS.ps1").read_text("utf-8")
    assert "param([switch]$Apply, [switch]$DiagnoseCutover)" in source
    before = source.index("if ($DiagnoseCutover) {")
    after = source.index("function Set-SecretFromStdin")
    assert before < after
    diagnose = source[before:after]
    assert "Read-DpapiSecret $BitrixFile" in diagnose
    assert "Disable-ScheduledTask" not in diagnose
    assert "Set-Variable" not in diagnose
    assert "secret set" not in diagnose
    assert "SERVICEIT_CLOUD_CUTOVER_DIAGNOSTIC" not in diagnose or "DIAGNOSTIC" in diagnose


@pytest.mark.parametrize("bad_item,kind", [
    (None, "NULL"),
    ("UNTRUSTED_SOURCE_SECRET_1234", "TEXT"),
    ([{"id": 67, "categoryId": 21}], "LIST"),
    (23, "NUMBER"),
    (False, "BOOL"),
])
def test_nonobject_item_has_only_bounded_index_and_safe_kind(
        monkeypatch, capsys, bad_item, kind):
    def route(request):
        return httpx.Response(200, json={"result": {"items": [
            {"id": 90, "categoryId": 21},
            bad_item,
        ]}})
    mock_http_client(monkeypatch, route)
    monkeypatch.setenv("SERVICEIT_BITRIX_WEBHOOK", SAFE_FAKE_HOOK)
    assert main() == 2
    out = capsys.readouterr().out
    assert ("SERVICEIT_CLOUD_CUTOVER_DIAGNOSTIC="
            "LIST:CUTOVER_ITEM_INVALID:HTTP_200:API_NONE:INDEX_1:KIND_" + kind) in out
    assert "UNTRUSTED_SOURCE_SECRET_1234" not in out
    assert "syntheticSECRET" not in out
    assert "90" not in out


def test_empty_or_out_of_scope_item_still_blocks_without_guessing():
    from leadpilot.serviceit_cloud_cutover_readonly import latest_existing_card_id
    with pytest.raises(ValueError, match="CUTOVER_ITEM_INVALID"):
        latest_existing_card_id(lambda *args, **kwargs: {
            "items": [None]})
