"""Safe webhook-owner identity without user ID prompt or employee lookup."""
from pathlib import Path
import json
import httpx
import pytest

from leadpilot.bitrix_1062_auto_owner_demo_task import owner_id, run
from leadpilot.bitrix_1062_one_shot_task import OneShotTaskBlocked

HOOK = "https://serviseit.bitrix24.ru/rest/18/SYNTHETICMOCKTOKEN7890"


def probe(body=None, *, status=200):
    calls = []
    payload = body if body is not None else {
        "result": {"ID": "18", "NAME": "Тестовый", "LAST_NAME": "Оператор"}
    }
    def handler(request):
        calls.append(request.url.path.rsplit("/", 1)[-1])
        assert request.method == "POST"
        assert request.url.host == "serviseit.bitrix24.ru"
        return httpx.Response(status, json=payload)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        return owner_id(HOOK, client), calls
    finally:
        client.close()


def test_auto_resolves_exact_webhook_owner_without_add():
    actual, calls = probe()
    assert actual == 18
    assert calls == ["profile.json"]


@pytest.mark.parametrize("result,code", [
    ({"ID": "77", "NAME": "Тестовый", "LAST_NAME": "Оператор"}, "OWNER_PROFILE_ID_MISMATCH"),
    ({"ID": "18", "NAME": "Михаил", "LAST_NAME": "Менеджер"}, "MANAGER_RECIPIENT_FORBIDDEN"),
    ({"ID": "18", "NAME": "Mikhail", "LAST_NAME": "Manager"}, "MANAGER_RECIPIENT_FORBIDDEN"),
    ({"ID": "18", "NAME": "", "LAST_NAME": "Оператор"}, "OWNER_FIRST_NAME_MISSING"),
    ({"ID": True, "NAME": "Тестовый", "LAST_NAME": "Оператор"}, "OWNER_PROFILE_ID_MISMATCH"),
    ({"ID": "018", "NAME": "Тестовый", "LAST_NAME": "Оператор"}, "OWNER_PROFILE_ID_MISMATCH"),
])
def test_owner_id_must_be_exact_and_not_excluded_manager(result, code):
    with pytest.raises(OneShotTaskBlocked, match=code):
        probe({"result": result})


def test_missing_profile_fails_closed():
    with pytest.raises(OneShotTaskBlocked, match="OWNER_PROFILE_UNAVAILABLE"):
        probe({"error": "invalid_auth"}, status=401)


def test_owner_is_automatically_passed_to_existing_durable_writer(tmp_path, monkeypatch):
    from leadpilot import bitrix_1062_auto_owner_demo_task as module
    called = []
    def mock_write(base, *, responsible_id, local_ledger, authorization, client):
        called.append((responsible_id, Path(local_ledger), authorization))
        return {"status": "ONE_DEMO_MANAGER_TASK_VERIFIED", "task_created": True}
    monkeypatch.setattr(module, "execute_one_demo_task", mock_write)
    def handler(request):
        assert request.url.path.endswith("/profile.json")
        return httpx.Response(200, json={"result": {
            "ID": "18", "NAME": "Тестовый", "LAST_NAME": "Оператор",
        }})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        out = run(HOOK, tmp_path/"existing-ledger.sqlite", client)
    assert len(called) == 1
    assert called[0][0] == 18
    assert called[0][1] == tmp_path/"existing-ledger.sqlite"
    assert called[0][2] == module.CONFIRM
    assert out["recipient_selection"] == "VERIFIED_WEBHOOK_OWNER_NOT_MANAGER"


def test_manager_denylist_blocks_before_writer(tmp_path, monkeypatch):
    from leadpilot import bitrix_1062_auto_owner_demo_task as module
    def fail(*args, **kwargs):
        raise AssertionError("POST_MUST_NOT_HAPPEN")
    monkeypatch.setattr(module, "execute_one_demo_task", fail)
    def handler(request):
        return httpx.Response(200, json={"result": {
            "ID": "18", "NAME": "Микаил", "LAST_NAME": "Менеджер",
        }})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OneShotTaskBlocked, match="MANAGER_RECIPIENT_FORBIDDEN"):
            run(HOOK, tmp_path/"no-ledger.sqlite", client)
    assert not (tmp_path/"no-ledger.sqlite").exists()



@pytest.mark.parametrize("profile", [
    {"ID":"18","NAME":"Оператор","LAST_NAME":""},
    {"ID":"18","NAME":"Оператор"},
    {"ID":"18","NAME":"Рамзес","LAST_NAME":None},
])
def test_blank_optional_surname_accepted_with_exact_owner_id(profile):
    actual, calls = probe({"result": profile})
    assert actual == 18
    assert calls == ["profile.json"]


@pytest.mark.parametrize("profile,code", [
    ({"ID":"18","NAME":None}, "OWNER_FIRST_NAME_MISSING"),
    ({"ID":"18","NAME":"Миша","LAST_NAME":""}, "MANAGER_RECIPIENT_FORBIDDEN"),
    ({"ID":"18","NAME":"Михаил","LAST_NAME":None}, "MANAGER_RECIPIENT_FORBIDDEN"),
    ({"ID":"18","NAME":"X","LAST_NAME":None}, "OWNER_FIRST_NAME_MISSING"),
    ({"ID":"18","NAME":"Оператор","LAST_NAME":18}, "OWNER_SURNAME_INVALID"),
])
def test_safety_remains_closed_for_ambiguous_owner(profile,code):
    with pytest.raises(OneShotTaskBlocked,match=code):
        probe({"result":profile})
