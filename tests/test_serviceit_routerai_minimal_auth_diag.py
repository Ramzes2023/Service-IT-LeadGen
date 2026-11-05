import httpx
from leadpilot import serviceit_routerai_minimal_auth_diag as m


def en():
    return {"SERVICEIT_AI_PROVIDER":"routerai",
            "SERVICEIT_ROUTERAI_PRIVACY_APPROVED":"YES_1062_21",
            "ROUTERAI_API_KEY":"sk-shared-test-only-value-never-live"}


def test_401_same_with_and_without_key_does_not_prove_key_valid():
    seen=[]
    def handler(req):
        seen.append(req)
        return httpx.Response(401,json={"error":{"type":"unauthorized","message":"Not allowed"}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        r=m.diagnose(c,en())
    assert r["chat_with_existing_key"]["status"]=="HTTP_401"
    assert r["chat_without_key"]["status"]=="HTTP_401"
    assert r["key_format"]["token_format_matches_docs_sk_prefix"]
    assert r["paid_chat_attempts_max"]==1
    assert len(seen)==2
    assert "Authorization" in seen[0].headers
    assert "Authorization" not in seen[1].headers
    assert all(req.url.path=="/api/v1/chat/completions" for req in seen)
    assert all(req.method=="POST" for req in seen)
    assert "GIGACHAT" not in str(r)
    assert "sk-shared-test" not in str(r)


def test_successful_authenticated_call_does_not_make_second_paid_call():
    called=[]
    def handler(req):
        called.append(req)
        return httpx.Response(200,json={"choices":[
            {"message":{"content":"ОК"}}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        r=m.diagnose(c,en())
    assert r["chat_with_existing_key"]["status"]=="HTTP_200"
    assert r["chat_with_existing_key"]["usable_json"]
    assert r["chat_without_key"]["status"]=="SKIPPED"
    assert len(called)==1


def test_key_shape_catches_double_bearer_and_newlines_without_disclosure():
    shape=m.key_shape(" Bearer abc-def-123456789 \n")
    assert shape["bearer_prefix_in_secret"]
    assert shape["leading_or_trailing_whitespace"]
    assert shape["unexpected_internal_whitespace"]
    assert not shape["token_format_matches_docs_sk_prefix"]


def test_error_body_classified_without_leaking_raw_body():
    x={"error":{"code":"invalid_api_key","message":"secret xxxxx"}}
    assert m.category_from_error(x)=="ERROR_INVALID_OR_REVOKED_KEY"
    assert m.category_from_error({"error":{"message":"insufficient balance"}})=="ERROR_BALANCE_OR_CREDIT"
    assert m.category_from_error({"error":{"message":"rate limit exceeded"}})=="ERROR_RATE_LIMIT"


def test_no_external_customers_in_minimal_request():
    def handler(req):
        body=req.content.decode("utf-8")
        assert "Ответь только: ОК" in body
        assert '"max_tokens":8' in body or '"max_tokens": 8' in body
        assert "response_format" not in body
        assert "tools" not in body
        return httpx.Response(402,json={"error":{"code":"payment_required"}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        r=m.diagnose(c,en())
    assert r["chat_with_existing_key"]["status"]=="HTTP_402"
    assert r["chat_without_key"]["status"]=="SKIPPED"
    assert r["crm_writes"]==0
