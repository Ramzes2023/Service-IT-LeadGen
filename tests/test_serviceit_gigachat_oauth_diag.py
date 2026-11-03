"""Strict synthetic-only OAuth probe safety and fixed status tests."""
from pathlib import Path
import httpx
import pytest

from leadpilot.serviceit_gigachat_oauth_diag import probe, category

BASE={"AI_ENABLE":"1","AI_PROVIDER":"gigachat",
      "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED":"YES",
      "GIGACHAT_CREDENTIALS":"SYNTHETIC_B64_NO_SECRET",
      "GIGACHAT_SCOPE":"GIGACHAT_API_PERS",
      "GIGACHAT_MODEL":"GigaChat-2"}


def test_valid_oauth_response_reports_presence_only_not_token():
    def handler(req):
        assert req.url.host == "ngw.devices.sberbank.ru"
        assert req.method == "POST"
        assert "GIGACHAT_API_PERS" in req.content.decode()
        assert req.headers.get("authorization") == "Basic SYNTHETIC_B64_NO_SECRET"
        return httpx.Response(200,json={"access_token":"NEVER_PRINT_SAMPLE_ACCESS_TOKEN"})
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        output=probe(c,BASE)
    assert output == {"status":"OAUTH_TOKEN_PRESENT","oauth":"HTTP_200"}
    assert "NEVER_PRINT_SAMPLE_ACCESS_TOKEN" not in str(output)


@pytest.mark.parametrize("status", [400,401,403,429,500,502,503])
def test_known_http_rejection_codes_without_remote_body(status):
    with httpx.Client(transport=httpx.MockTransport(
      lambda r:httpx.Response(status,text="SENSITIVE_REMOTE_BODY_NEVER_LOG"))) as c:
        output=probe(c,BASE)
    assert output == {"status":"OAUTH_HTTP_REJECTED","oauth":f"HTTP_{status}"}
    assert "SENSITIVE_REMOTE_BODY" not in str(output)


def test_missing_consent_or_invalid_scope_never_sends_oauth():
    with httpx.Client(transport=httpx.MockTransport(
      lambda r:pytest.fail("No OAuth should be attempted"))) as c:
        assert probe(c,{**BASE,"SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED":"NO"})["status"]=="CONSENT_NOT_VERIFIED"
        assert probe(c,{**BASE,"GIGACHAT_SCOPE":""})["status"]=="SCOPE_INVALID"


def test_tls_and_network_errors_are_fixed_codes_without_exception_leak():
    import ssl
    assert category(ssl.SSLCertVerificationError("SECRET_CERT_PATH"))=="TLS_CERTIFICATE_VERIFY_FAILED"
    assert category(httpx.ConnectError("CERTIFICATE_VERIFY_FAILED SECRET_STRING"))=="TLS_CERTIFICATE_VERIFY_FAILED"
    assert category(httpx.ConnectTimeout("SENSITIVE_URL"))=="NETWORK_CONNECT_FAILED"


def test_owner_workflow_tls_probe_has_no_crm_or_model_completion():
    wf=Path(".github/workflows/serviceit-gigachat-synthetic-once.yml").read_text("utf-8")
    src=Path("leadpilot/serviceit_gigachat_oauth_diag.py").read_text("utf-8")
    assert "SERVICEIT_GIGACHAT_OAUTH_DIAG_V1" in wf
    assert "github.actor == 'Ramzes2023'" in wf
    assert "github.event.issue.number == 74" in wf
    assert "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED: ${{ vars.SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED }}" in wf
    assert "serviceit_gigachat_oauth_diag" in wf
    assert 'httpx.Client(verify=env.get("GIGACHAT_CA_BUNDLE_FILE") or True' in src
    assert "httpx.Client(verify=False" not in src
    for forbidden in ("crm.item.add","crm.item.update","GITHUB_TOKEN","SERVICEIT_BITRIX_WEBHOOK",
                      "chat/completions","AIProvider.classify","print(resp.text)","print(exc)",
                      "contents: write"):
        assert forbidden not in src
    assert "model_completions" in src and "ledger_reservations" in src
