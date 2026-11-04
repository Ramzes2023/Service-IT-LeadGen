"""RouterAI one-time paid API diagnostic uses no client records or key logs."""
import httpx

from leadpilot.serviceit_routerai_synthetic_preflight import (
    inspect, MODEL, SYNTHETIC_TEXT, URL,
)

ENV = {
    "GITHUB_REPOSITORY":"Ramzes2023/Service-IT-LeadGen",
    "GITHUB_REF":"refs/heads/main",
    "GITHUB_EVENT_NAME":"issue_comment",
    "SERVICEIT_AI_PROVIDER":"routerai",
    "SERVICEIT_ROUTERAI_PRIVACY_APPROVED":"YES_1062_21",
    "ROUTERAI_API_KEY":"THIS-IS-A-FAKE-SECRET",
}


def test_no_real_data_synthetic_request_and_output_redacted():
    def transport(req):
        import json
        assert req.method == "POST"
        assert str(req.url) == URL
        assert req.headers["Authorization"] == "Bearer THIS-IS-A-FAKE-SECRET"
        payload = json.loads(req.content)
        assert payload["model"] == MODEL
        assert payload["messages"][1]["content"] == SYNTHETIC_TEXT
        assert payload["response_format"] == {"type": "json_object"}
        assert len(payload["messages"]) == 2
        answer = {"service":"1С","possible_need":"Возможно сопровождение 1С",
                  "confidence":0.8,"evidence_quote":"сопровождение 1С"}
        return httpx.Response(200,json={"choices":[{"message":{
            "content":json.dumps(answer,ensure_ascii=False)}}]})
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        result=inspect(client,ENV)
    assert result == {"provider":"routerai", "crm_writes":0,
        "real_customer_data_sent":False,
        "status":"ROUTERAI_API_AND_AI_VALIDATION_PASS"}
    assert "THIS-IS-A-FAKE-SECRET" not in str(result)


def test_missing_provider_flag_no_external_call():
    with httpx.Client(transport=httpx.MockTransport(
            lambda req: (_ for _ in ()).throw(AssertionError("network blocked")))) as client:
        assert inspect(client, {**ENV,"SERVICEIT_AI_PROVIDER":"gigachat"})["status"] == (
            "ROUTERAI_PROVIDER_VARIABLE_NOT_ENABLED")


def test_missing_corporate_approval_no_external_call():
    with httpx.Client(transport=httpx.MockTransport(
            lambda req: (_ for _ in ()).throw(AssertionError("network blocked")))) as client:
        assert inspect(client, {**ENV,"SERVICEIT_ROUTERAI_PRIVACY_APPROVED":""})["status"] == (
            "ROUTERAI_PRIVACY_APPROVAL_NOT_SET")


def test_absent_secret_report_is_redacted():
    with httpx.Client(transport=httpx.MockTransport(
            lambda req: (_ for _ in ()).throw(AssertionError("network blocked")))) as client:
        assert inspect(client, {**ENV,"ROUTERAI_API_KEY":""})["status"] == (
            "ROUTERAI_API_KEY_SECRET_NOT_FOUND")


def test_provider_auth_error_does_not_log_secret():
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:httpx.Response(401,json={"message":"fake secret"}))) as client:
        answer=inspect(client,ENV)
    assert answer["status"]=="ROUTERAI_API_KEY_REJECTED_401"
    assert "fake secret" not in str(answer)


def test_provider_requires_balance():
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:httpx.Response(402))) as client:
        assert inspect(client,ENV)["status"]=="ROUTERAI_BALANCE_OR_QUOTA_REQUIRED"


def test_unsupported_json_mode_is_not_misreported_as_bad_key():
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:httpx.Response(400))) as client:
        assert inspect(client,ENV)["status"]=="ROUTERAI_MODEL_OR_REQUEST_REJECTED"


def test_invalid_quote_never_reported_as_valid():
    import json
    def transport(req):
        a={"service":"1С","possible_need":"Фальшивая информация",
           "confidence":0.95,"evidence_quote":"не существовало"}
        return httpx.Response(200,json={"choices":[{"message":{
            "content":json.dumps(a,ensure_ascii=False)}}]})
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        assert inspect(client,ENV)["status"]==(
            "ROUTERAI_API_AUTH_OK_BUT_AI_REQUIRES_REVIEW")


def test_valid_key_but_model_denied_is_distinct_from_bad_key():
    def transport(req):
        if req.method == "GET" and req.url.path.endswith("/models"):
            return httpx.Response(200,json={"data":[]})
        if req.method == "GET" and req.url.path.endswith("/credits"):
            return httpx.Response(200,json={"balance":11.0})
        return httpx.Response(403,json={"error":"private response not logged"})
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        result=inspect(client,ENV)
    assert result["status"]=="ROUTERAI_MODEL_FORBIDDEN_WITH_POSITIVE_BALANCE"
    assert "THIS-IS-A-FAKE-SECRET" not in str(result)


def test_account_or_ip_forbidden_even_on_models_endpoint():
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:httpx.Response(403))) as client:
        assert inspect(client,ENV)["status"]==(
            "ROUTERAI_ACCOUNT_OR_RUNNER_ACCESS_DENIED_403")


def test_chat_401_models_ok_with_zero_credits():
    def transport(req):
        if req.method == "GET" and req.url.path.endswith("/models"):
            return httpx.Response(200,json={"data":[]})
        if req.method == "GET" and req.url.path.endswith("/credits"):
            return httpx.Response(200,json={"data":{"total_credits":0.0,"total_usage":0.0}})
        return httpx.Response(401)
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        assert inspect(client,ENV)["status"]=="ROUTERAI_BILLING_BALANCE_EMPTY"


def test_chat_401_models_ok_and_positive_credits():
    def transport(req):
        if req.method == "GET" and req.url.path.endswith("/models"):
            return httpx.Response(200,json={"data":[]})
        if req.method == "GET" and req.url.path.endswith("/credits"):
            return httpx.Response(200,json={"balance":1.0})
        return httpx.Response(401)
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        assert inspect(client,ENV)["status"]==(
            "ROUTERAI_CHAT_401_WITH_POSITIVE_BALANCE")


def test_html_models_page_is_not_auth_evidence():
    def transport(req):
        return httpx.Response(200,text="<html>captcha</html>") if req.method=="GET" else httpx.Response(401)
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        assert inspect(client,ENV)["status"]=="ROUTERAI_MODELS_RESPONSE_NOT_VERIFIED"


def test_credit_access_denied_is_explicitly_unknown_not_zero():
    def transport(req):
        if req.method=="GET" and req.url.path.endswith("/models"):
            return httpx.Response(200,json={"data":[]})
        if req.method=="GET" and req.url.path.endswith("/credits"):
            return httpx.Response(403)
        return httpx.Response(401)
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        assert inspect(client,ENV)["status"]=="ROUTERAI_CHAT_401_WITH_CREDITS_UNVERIFIED"
