"""End-to-end exact new-card route tests: RouterAI primary / GigaChat standby.

Mocks only GitHub receipt & Bitrix network; never creates synthetic Bitrix
cards or transmits customer information. Tests actual worker selection path.
"""
from types import SimpleNamespace
import httpx
import pytest

from leadpilot import serviceit_bitrix_dispatch_worker as worker


def setup_worker(monkeypatch, *,
                 routerai_failure=False,
                 routerai_incomplete=False,
                 gigachat_failure=False,
                 enable_fallback=True):
    observed=[]
    created=[]
    closed=[]
    result=[]

    class FakeFence:
        def __init__(self,*args,**kwargs): pass
        def reserve(self, card):
            assert card==107
            return "RESERVED"
        def checkpoint_write(self, card, fields):
            pytest.fail("No real CRM write permitted in synthetic route test")
        def finalize(self,card,status):
            observed.append(status)
            return True

    class FakeProvider:
        def __init__(self,config,*,retry_unauthorized=False):
            assert retry_unauthorized is False
            self.config=SimpleNamespace(provider=config.provider,model=config.model)
            created.append(config.provider)
        def classify(self,text):
            assert text=="Публичный заголовок 1С"
            result.append(self.config.provider)
            if self.config.provider=="routerai":
                if routerai_failure:
                    raise RuntimeError("synthetic provider 503")
                if routerai_incomplete:
                    return {"provider":"routerai","quality_status":"needs_review"}
            if self.config.provider=="gigachat" and gigachat_failure:
                raise RuntimeError("synthetic GigaChat failure")
            return {"provider":self.config.provider,"quality_status":"complete",
                    "origin":"AI_hypothesis"}
        def close(self):
            closed.append(self.config.provider)

    def fake_preliminary(store, rest, card, *, model, public_ai_hosts):
        assert card==107
        assert public_ai_hosts==frozenset({"example.com"})
        value=model("Публичный заголовок 1С")
        observed.append(value["provider"])
        return "HOLD_9_PRESENT"

    monkeypatch.setattr(worker,"EncryptedEventFence",FakeFence)
    monkeypatch.setattr(worker,"AIProvider",FakeProvider)
    monkeypatch.setattr(worker,"Rest",lambda *args:lambda method,params:None)
    monkeypatch.setattr(worker,"preliminary_once",fake_preliminary)
    monkeypatch.setattr(worker,"verify_event",lambda raw,secret:107)
    env={
        "SERVICEIT_BITRIX_EVENT_AI_ENABLED":worker.LIVE_FLAG,
        "GITHUB_REPOSITORY":worker.REPOSITORY,
        "GITHUB_REF":"refs/heads/main",
        "GITHUB_EVENT_NAME":"repository_dispatch",
        "SERVICEIT_BITRIX_EVENT_BRIDGE_KEY":"synthetic-event-auth-123456789",
        "SERVICEIT_GH_LEDGER_KEY":"synthetic-ledger-key",
        "SERVICEIT_BITRIX_WEBHOOK":"https://serviseit.bitrix24.ru/rest/1/FakeToken",
        "GITHUB_TOKEN":"synthetic-token",
        "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED":"YES",
        "AI_ENABLE":"1",
        "AI_PROVIDER":"routerai",
        "ROUTERAI_API_KEY":"sk-synthetic-do-not-use-1234567890",
        "SERVICEIT_ROUTERAI_PRIVACY_APPROVED":"YES_1062_21",
        "SERVICEIT_PRELIMINARY_PUBLIC_TITLE_AI_ENABLED":"YES",
        "SERVICEIT_PRELIMINARY_PUBLIC_HOSTS":"example.com",
        "GIGACHAT_CREDENTIALS":"synthetic-GigaChat-not-real",
        "SERVICEIT_ROUTERAI_FALLBACK_TO_GIGACHAT":
            "YES" if enable_fallback else "NO",
    }
    return env,created,result,closed,observed


def execute(env):
    with httpx.Client(transport=httpx.MockTransport(
            lambda req:pytest.fail("External calls forbidden"))) as client:
        return worker.run({"synthetic":True},env,client=client)


def test_routerai_primary_success_never_calls_gigachat(monkeypatch):
    env,created,calls,closed,seen=setup_worker(monkeypatch)
    r=execute(env)
    assert r["status"]=="SERVICEIT_NEW_CARD_EVENT_PROCESSED"
    assert calls==["routerai"]
    assert created==["routerai","gigachat"]
    assert set(closed)=={"routerai","gigachat"}
    assert "routerai" in seen
    assert "gigachat" not in seen


@pytest.mark.parametrize("fail,incomplete",[(True,False),(False,True)])
def test_routerai_failure_falls_back_to_gigachat_once(monkeypatch,fail,incomplete):
    env,created,calls,closed,seen=setup_worker(
        monkeypatch,routerai_failure=fail,routerai_incomplete=incomplete)
    r=execute(env)
    assert r["status"]=="SERVICEIT_NEW_CARD_EVENT_PROCESSED"
    assert calls==["routerai","gigachat"]
    assert "gigachat" in seen
    assert r["replay_allowed"] is False


def test_routerai_failure_and_secondary_unavailable_fails_closed(monkeypatch):
    env,created,calls,closed,seen=setup_worker(
        monkeypatch,routerai_failure=True,gigachat_failure=True)
    r=execute(env)
    assert r["status"]=="SERVICEIT_NEW_CARD_NEEDS_REVIEW"
    assert calls==["routerai","gigachat"]
    assert r["replay_allowed"] is False


def test_fallback_can_be_disabled_for_manual_rollback(monkeypatch):
    env,created,calls,closed,seen=setup_worker(
        monkeypatch,routerai_failure=True,enable_fallback=False)
    r=execute(env)
    assert r["status"]=="SERVICEIT_NEW_CARD_NEEDS_REVIEW"
    assert calls==["routerai"]
    assert created==["routerai"]


def test_unauthorized_routerai_cannot_access_any_model(monkeypatch):
    env,created,calls,closed,seen=setup_worker(monkeypatch)
    env["SERVICEIT_ROUTERAI_PRIVACY_APPROVED"]=""
    r=execute(env)
    assert r["status"]=="MODEL_PRIVACY_OR_CONFIG_INCOMPLETE"
    assert not calls
    assert not created
