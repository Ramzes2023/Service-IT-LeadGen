"""RouterAI VPN/provider differential scanner: synthetic local transport only."""
import json
import httpx
import pytest

from leadpilot import serviceit_routerai_transport_readonly as t


def test_models_502_even_anonymous_points_away_from_key():
    cases=[]
    def fake(req):
        if req.url.path.startswith("/api/v1/"):
            return httpx.Response(502,headers={"server":"nginx"},
                                  text="secret-key never logged")
        return httpx.Response(200,text="OK")
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        for label,path,auth in t.SCENARIOS:
            o=t.request_one(client,path,"a"*24,auth)
            cases.append((label,o))
    results=dict(cases)
    assert results["homepage_public"]["result"]=="HTTP_200"
    assert results["docs_public"]["result"]=="HTTP_200"
    assert results["models_anonymous"]["result"]=="HTTP_502"
    assert results["models_authorized"]["result"]=="HTTP_502"
    assert results["credits_authorized"]["result"]=="HTTP_502"
    assert "secret" not in str(results)
    assert results["models_authorized"]["server_family"]=="nginx"


def test_no_secret_on_non_api_pages():
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:httpx.Response(200))) as client:
        with pytest.raises(t.TransportBlocked,match="PUBLIC_PATH_KEY_FORBIDDEN"):
            t.request_one(client,"/","a"*32,True)


def test_no_model_post_or_external_hosts():
    assert all(path in ("/","/docs/guides/overview/quickstart",
                        "/api/v1/models","/api/v1/credits")
               for _,path,_ in t.SCENARIOS)
    with httpx.Client() as client:
        with pytest.raises(t.TransportBlocked,match="PATH_FORBIDDEN"):
            t.request_one(client,"/api/v1/chat/completions","a"*32,True)


def test_owner_exact_repo_issue_event_and_token(tmp_path):
    obj={"action":"created","repository":{"full_name":t.REPO},
         "issue":{"number":148},"sender":{"login":t.OWNER},
         "comment":{"body":t.MARKER,"user":{"login":t.OWNER}}}
    path=tmp_path/"event.json"
    path.write_text(json.dumps(obj),encoding="utf8")
    env={"GITHUB_REPOSITORY":t.REPO,
         "GITHUB_REF":"refs/heads/main",
         "GITHUB_EVENT_NAME":"issue_comment",
         "GITHUB_ACTOR":t.OWNER,
         "GITHUB_EVENT_PATH":str(path)}
    t.event_valid(env)
    with pytest.raises(t.TransportBlocked):
        t.event_valid({**env,"GITHUB_ACTOR":"someone-else"})
    obj["comment"]["body"]="OTHER"
    path.write_text(json.dumps(obj),encoding="utf8")
    with pytest.raises(t.TransportBlocked):
        t.event_valid(env)


def test_empty_key_stops_before_network(monkeypatch):
    env={"SERVICEIT_AI_PROVIDER":"routerai",
         "SERVICEIT_ROUTERAI_PRIVACY_APPROVED":"YES_1062_21",
         "ROUTERAI_API_KEY":""}
    with pytest.raises(t.TransportBlocked,match="KEY_UNAVAILABLE"):
        t.diagnose(env)
