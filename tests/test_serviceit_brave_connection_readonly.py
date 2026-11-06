"""No-network proof of read-only Brave/Bitrix credential smoke."""
import json
import httpx
from leadpilot.serviceit_brave_connection_readonly import readiness, STATUS_OK
from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS


def config(**overrides):
    result = {
        "SERVICEIT_BRAVE_SEARCH_API_KEY": "BSAI-SYNTHETIC-ONLY",
        "SERVICEIT_BITRIX_WEBHOOK": "https://serviseit.bitrix24.ru/rest/2/SYNTHETICKEY",
        "SERVICEIT_BITRIX_EVENT_AI_ENABLED": "YES_1062_21_CLOUD",
        "SERVICEIT_BITRIX_POLL_ENABLED": "YES_1062_21_READONLY",
        "SERVICEIT_DEEP_COMPANY_SEARCH_ENABLED": "YES",
        "SERVICEIT_PRELIMINARY_PUBLIC_HOSTS": "hh.ru,b2b-center.ru",
        "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED": "YES",
        "SERVICEIT_PRELIMINARY_PUBLIC_TITLE_AI_ENABLED": "YES",
        "SERVICEIT_AI_PROVIDER": "routerai",
        "SERVICEIT_ROUTERAI_PRIVACY_APPROVED": "YES_1062_21",
        "ROUTERAI_API_KEY": "SYNTHETIC_ROUTERAI",
    }
    result.update(overrides)
    return result


def mock(bad_key=False, missing_field=False):
    def handle(req):
        if req.url.host == "api.search.brave.com":
            assert req.method == "GET"
            assert req.headers["X-Subscription-Token"] == config()["SERVICEIT_BRAVE_SEARCH_API_KEY"]
            assert req.url.params["q"] == "Python official documentation"
            if bad_key:
                return httpx.Response(401, json={"error": "unauthorized"})
            if req.url.path == "/res/v1/llm/context":
                assert req.url.params["maximum_number_of_tokens"] == "1024"
                return httpx.Response(200, json={
                    "grounding": {"generic": [
                        {"url": "https://docs.python.org",
                         "title": "Python Documentation",
                         "snippets": ["Official Python 3 documentation"]}]},
                    "sources": {"https://docs.python.org": {"hostname": "docs.python.org"}},
                })
            assert req.url.path == "/res/v1/web/search"
            return httpx.Response(200, json={"web": {"results": [
                {"title": "Documentation", "url": "https://docs.python.org"}]}})
        assert req.url.host == "serviseit.bitrix24.ru" and req.method == "POST"
        assert req.url.path.endswith("/crm.item.fields.json")
        assert json.loads(req.content) == {"entityTypeId": 1062, "useOriginalUfNames": "Y"}
        fields = {key: {"isReadOnly": False} for key in AI_FIELDS}
        if missing_field:
            fields.pop(next(iter(fields)))
        return httpx.Response(200, json={"result": {"fields": fields}})

    return httpx.Client(transport=httpx.MockTransport(handle), trust_env=False)


def test_readonly_smoke_all_ok_no_pii_or_secret():
    with mock() as client:
        status = readiness(config(), client)
    assert status["readiness"] == STATUS_OK
    assert status["brave"] == "BRAVE_API_200_VALID_WEB_RESULTS"
    assert status["bitrix"] == "BITRIX_NINE_AI_FIELDS_VISIBLE_READONLY_VERIFIED"
    assert status["crm_writes"] == status["ai_inferences"] == 0
    assert "SYNTHETIC" not in str(status)


def test_bad_api_key_blocked_and_not_disclosed():
    with mock(bad_key=True) as client:
        status = readiness(config(), client)
    assert status["brave"] == "BRAVE_INVALID_KEY_401"
    assert status["readiness"] != STATUS_OK


def test_missing_fields_or_privacy_block_readiness():
    with mock(missing_field=True) as client:
        status = readiness(config(), client)
    assert status["bitrix"] == "BITRIX_NINE_AI_FIELDS_NOT_ALL_VISIBLE"
    with mock() as client:
        status = readiness(config(SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED="NO",
                                  SERVICEIT_PRELIMINARY_PUBLIC_HOSTS=""), client)
    assert status["readiness"] != STATUS_OK
    assert status["public_source_allowlist"] == "MISSING_OR_INVALID"


def test_llm_context_readonly_smoke_uses_one_index_call():
    with mock() as client:
        report = readiness(config(SERVICEIT_BRAVE_LLM_CONTEXT_ENABLED="YES"),
                           client)
    assert report["readiness"] == STATUS_OK
    assert report["brave_search_mode"] == "LLM_CONTEXT_ONE_REQUEST"
    assert report["brave"] == "BRAVE_CONTEXT_API_200_VALID_GROUNDING"
    assert report["brave_search_requests"] == 1
    assert report["crm_writes"] == report["ai_inferences"] == 0
