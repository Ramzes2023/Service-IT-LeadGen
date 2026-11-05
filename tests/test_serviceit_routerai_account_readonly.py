"""RouterAI account diagnostic reads only /credits and /models, no inference."""
import json
import httpx
import pytest
from leadpilot import serviceit_routerai_account_readonly as m


def env():
    return {
        "SERVICEIT_AI_PROVIDER":"routerai",
        "SERVICEIT_ROUTERAI_PRIVACY_APPROVED":"YES_1062_21",
        "ROUTERAI_API_KEY":"key-only-for-mock-very-long",
    }


def test_balance_positive_with_expected_account_schema():
    assert m.bucket_credits({"data":{"balance":1000}})=="BALANCE_POSITIVE"
    assert m.bucket_credits({"total_credits":1100,"total_usage":100})=="BALANCE_POSITIVE"


@pytest.mark.parametrize("x",[
    {"balance":"0"},
    {"balance":"-1"},
    {"data":{"balance":0}},
    {"total_credits":100,"total_usage":150},
])
def test_empty_credits_no_amount_leaked(x):
    assert m.bucket_credits(x)=="BALANCE_EMPTY_OR_ZERO"


@pytest.mark.parametrize("x",[
    {"other":{"balance":1000}},
    {"balance":"nan"},
    {"balance":None},
    {"data":{"balance":"not-numeric"}},
    [],
    99,
])
def test_unrecognized_schema_fails_unknown(x):
    assert m.bucket_credits(x)=="CREDITS_RESPONSE_SCHEMA_UNKNOWN"


def test_exact_free_endpoint_allowlist(monkeypatch):
    seen=[]
    def mock(req):
        seen.append((req.method,str(req.url)))
        if req.url.path.endswith("/credits"):
            return httpx.Response(200,json={"data":{"balance":1000}})
        if req.url.path.endswith("/models"):
            return httpx.Response(200,json={"data":[{"id":"deepseek/deepseek-v4.1-flash"}]})
        pytest.fail("Unexpected endpoint")
    with httpx.Client(transport=httpx.MockTransport(mock)) as c:
        x=m.inspect(c,env())
    assert x["balance_bucket"]=="BALANCE_POSITIVE"
    assert x["model_listed_in_visible_page"] is True
    assert x["chat_generation_verified"] is False
    assert x["paid_inference_calls"]==0
    assert x["account_balance_exact_amount_disclosed"] is False
    assert len(seen)==2
    assert all(z[0]=="GET" for z in seen)
    assert "1000" not in json.dumps(x)
    with httpx.Client(transport=httpx.MockTransport(mock)) as c:
        with pytest.raises(m.AccountDiagnosticBlocked):
            m.api_read(c,"/chat/completions",env()["ROUTERAI_API_KEY"])


def test_credits_denied_but_models_public_does_not_claim_account_funded():
    def mock(req):
        if req.url.path.endswith("/credits"):
            return httpx.Response(403,json={"error":"forbidden"})
        return httpx.Response(200,json={"data":[{"id":"deepseek/deepseek-v4.1-flash"}]})
    with httpx.Client(transport=httpx.MockTransport(mock)) as c:
        x=m.inspect(c,env())
    assert x["credit_endpoint_status"]=="HTTP_403"
    assert x["models_endpoint_status"]=="HTTP_200"
    assert x["balance_bucket"]=="BALANCE_NOT_VERIFIABLE"
    assert x["paid_inference_calls"]==0


def test_invalid_account_configuration_makes_zero_calls():
    def mock(req):pytest.fail("Must not contact provider")
    with httpx.Client(transport=httpx.MockTransport(mock)) as c:
        x=m.inspect(c,{**env(),"SERVICEIT_AI_PROVIDER":"gigachat"})
        assert x["status"]=="ROUTERAI_CONFIG_NOT_SELECTED"
        x=m.inspect(c,{**env(),"ROUTERAI_API_KEY":""})
        assert x["status"]=="ROUTERAI_KEY_NOT_AVAILABLE"


def test_owner_event_rejects_wrong_comments(tmp_path):
    event={"action":"created",
           "repository":{"full_name":m.REPOSITORY},
           "issue":{"number":148},
           "sender":{"login":"Ramzes2023"},
           "comment":{"body":m.MARKER,"user":{"login":"Ramzes2023"}}}
    p=tmp_path/"event.json"
    p.write_text(json.dumps(event),encoding="utf8")
    good={"GITHUB_REPOSITORY":m.REPOSITORY,
          "GITHUB_REF":"refs/heads/main",
          "GITHUB_EVENT_NAME":"issue_comment",
          "GITHUB_ACTOR":"Ramzes2023",
          "GITHUB_EVENT_PATH":str(p)}
    m.owner_event(good)
    for wrong in (
        {"GITHUB_ACTOR":"somebody"},
        {"GITHUB_REF":"refs/heads/test"},
        {"GITHUB_EVENT_NAME":"schedule"},
        {"GITHUB_EVENT_PATH":"/not-a-file"},
    ):
        with pytest.raises(m.AccountDiagnosticBlocked):
            m.owner_event({**good,**wrong})
    event["comment"]["body"]="CHECK_PAID_DATA"
    p.write_text(json.dumps(event))
    with pytest.raises(m.AccountDiagnosticBlocked):
        m.owner_event(good)
