"""Only synthetic post-call evidence: never contact real CRM in tests."""
from datetime import datetime, timezone, timedelta
import copy
import httpx
import pytest

from leadpilot import serviceit_postcall_live_worker as m

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


def card(interest="YES", action="ASK_DETAILS",
         updated_by=42, approval="2026-11-06T11:00:00Z"):
    return {
        "id": 123, "categoryId": 21, "entityTypeId": 1062,
        "createdTime": "2026-11-06T10:45:00Z",
        "updatedTime": approval,
        "updatedBy": updated_by,
        "assignedById": 42,
        "stageId": "DT1062_21:CLIENT",
        m.REVIEW: "POSTCALL_APPROVED_" + interest,
        m.NEXT_ACTION: (
            "DO_NOT_CONTACT" if interest == "NO"
            else action + "|2026-11-07T11:00:00+00:00"
        ),
    }


def activity():
    return {
        "ID": 777, "OWNER_ID": 123,
        "OWNER_TYPE_ID": 1062, "TYPE_ID": 2,
        "COMPLETED": "Y", "RESPONSIBLE_ID": 42,
        "LAST_UPDATED": "2026-11-06T10:55:00Z",
        "DESCRIPTION": "Клиент попросил уточнить возможности 1С. Возражение: срок.",
    }


def test_manual_approval_is_not_an_ai_guess_and_requires_responsible_editor():
    good = m._approved_fields(card(), now=NOW)
    assert good[0] == "YES" and good[1] == "ASK_DETAILS"
    assert good[3] == 42
    with pytest.raises(m.PostCallLiveBlocked, match="MANAGER_NOT_AUTHOR"):
        m._approved_fields(card(updated_by=7), now=NOW)
    x = card()
    x[m.REVIEW] = "AI_SAYS_YES"
    with pytest.raises(m.PostCallLiveBlocked, match="MANAGER_DECISION"):
        m._approved_fields(x, now=NOW)
    x = card(interest="NO")
    x[m.NEXT_ACTION] = "ASK_DETAILS|2026-11-07T12:00:00Z"
    with pytest.raises(m.PostCallLiveBlocked, match="NO_CONTACT"):
        m._approved_fields(x, now=NOW)


def test_manager_unknown_never_sends_or_treats_as_yes():
    x = card("UNKNOWN", "MANAGER_REVIEW")
    y = m._approved_fields(x, now=NOW)
    assert y[0] == "UNKNOWN" and y[1] == "MANAGER_REVIEW"
    assert y[2] is not None
    x[m.NEXT_ACTION] = "SEND_PROPOSAL|2026-11-07T12:00:00Z"
    with pytest.raises(m.PostCallLiveBlocked):
        m._approved_fields(x, now=NOW)


def test_completed_call_must_belong_to_card_and_assignee(monkeypatch):
    seen = []
    def fake_api(client, base, method, args):
        seen.append(method)
        if method == "crm.activity.list":
            return {"result": [activity()]}
        if method == "crm.activity.get":
            return {"result": activity()}
        raise AssertionError(method)
    monkeypatch.setattr(m, "api", fake_api)
    with httpx.Client() as client:
        got, summary = m._completed_call(
            client, "https://example.invalid/rest", 123, 42,
            m.parse_date("2026-11-06T11:00:00Z"), now=NOW
        )
    assert got["ID"] == 777 and "1С" in summary
    assert seen == ["crm.activity.list", "crm.activity.get"]
    monkeypatch.setattr(m, "api", lambda c,b,k,p: {
        "result": [dict(activity(), RESPONSIBLE_ID=7)]
    } if k == "crm.activity.list" else {"result": activity()})
    with httpx.Client() as client:
        with pytest.raises(m.PostCallLiveBlocked, match="COMPLETED_MANAGER_CALL"):
            m._completed_call(client, "x", 123, 42,
                              m.parse_date("2026-11-06T11:00:00Z"),
                              now=NOW)


def test_call_list_incomplete_cannot_be_used_as_evidence(monkeypatch):
    monkeypatch.setattr(m, "api", lambda c,b,k,p: {
        "result": [activity()], "next": 50})
    with httpx.Client() as client:
        with pytest.raises(m.PostCallLiveBlocked, match="CALL_LIST_INCOMPLETE"):
            m._completed_call(client, "x", 123, 42,
                              m.parse_date("2026-11-06T11:00:00Z"),
                              now=NOW)


def test_task_fields_link_only_own_card_and_no_contact_creates_no_task():
    task = m._task_fields(
        "YES", "ASK_DETAILS", NOW + timedelta(days=1),
        42, "Tb3_123", "Клиент обсуждал поддержку 1С.", 777,
    )
    assert task["RESPONSIBLE_ID"] == 42
    assert task["UF_CRM_TASK"] == ["Tb3_123"]
    assert "авторассылок" in task["DESCRIPTION"]
    with pytest.raises(m.PostCallLiveBlocked, match="NO_CONTACT_NO_TASK"):
        m._task_fields("NO", "DO_NOT_CONTACT", None, 42,
                       "Tb3_123", "Отказ", 777)


def prepare_fake_worker(monkeypatch, *, interest="YES", mutation=False):
    state = card(interest, "ASK_DETAILS")
    if interest == "NO":
        state = card("NO")
    calls = []
    def read(client, base, cid):
        return copy.deepcopy(state)
    monkeypatch.setattr(m, "_read_card", read)
    monkeypatch.setattr(
        m, "_completed_call",
        lambda *args, **kwargs: (activity(), activity()["DESCRIPTION"]),
    )
    monkeypatch.setattr(m, "_binding", lambda *args: "Tb3")
    monkeypatch.setattr(
        m, "_task_listing",
        lambda *args: calls.append("dedupe") or True,
    )
    class Fence:
        def __init__(self, *args):
            calls.append("fence-init")
        def reserve(self, aid):
            calls.append("reserve")
            return "RESERVED"
        def finalize(self, aid, outcome):
            calls.append("final-" + outcome)
            return True
    monkeypatch.setattr(m, "CallFence", Fence)
    def mock_api(client, base, method, params):
        calls.append(method)
        if method == "tasks.task.add":
            assert params["fields"]["UF_CRM_TASK"] == ["Tb3_123"]
            assert params["fields"]["RESPONSIBLE_ID"] == 42
            return {"result": {"task": {"id": 801}}}
        if method == "tasks.task.get":
            return {"result": {"task": {
                "id": 801,
                "title": f"[Сервис ИТ · звонок 777] " +
                         "Уточнить потребность клиента",
                "responsibleId": 42,
                "ufCrmTask": ["Tb3_123"],
                "deadline": "2026-11-07T14:00:00+03:00",
            }}}
        if method == "crm.item.update":
            assert params["entityTypeId"] == 1062 and params["id"] == 123
            assert set(params["fields"]) == {m.REVIEW, m.NEXT_ACTION}
            state.update(params["fields"])
            if mutation:
                state["stageId"] = "WRONG_STAGE"
            return {"result": {"item": {"id": 123}}}
        raise AssertionError("Unexpected " + method)
    monkeypatch.setattr(m, "api", mock_api)
    return calls, state


def test_realistic_one_call_creates_one_internal_task_then_updates_two_fields(
    monkeypatch,
):
    calls, state = prepare_fake_worker(monkeypatch)
    with httpx.Client() as client:
        status = m._write_one(client, "https://portal.invalid/rest",
                              {"GITHUB_TOKEN": "x",
                               "SERVICEIT_GH_LEDGER_KEY": "x"}, 123, now=NOW)
    assert status == "POSTCALL_MANAGER_RESULT_AND_TASK_VERIFIED"
    assert state[m.REVIEW] == "POSTCALL_MANAGER_CONFIRMED_YES"
    assert state[m.NEXT_ACTION].startswith("ASK_DETAILS")
    assert calls.index("reserve") < calls.index("tasks.task.add")
    assert calls.index("tasks.task.get") < calls.index("crm.item.update")
    assert calls[-1] == "final-VERIFIED"


def test_call_refusal_updates_result_without_task(monkeypatch):
    calls, state = prepare_fake_worker(monkeypatch, interest="NO")
    with httpx.Client() as client:
        status = m._write_one(client, "https://portal.invalid/rest",
                              {"GITHUB_TOKEN": "x",
                               "SERVICEIT_GH_LEDGER_KEY": "x"}, 123, now=NOW)
    assert status == "POSTCALL_NO_CONTACT_RESULT_VERIFIED"
    assert state[m.REVIEW] == "POSTCALL_MANAGER_CONFIRMED_NO"
    assert state[m.NEXT_ACTION] == "DO_NOT_CONTACT"
    assert "tasks.task.add" not in calls


def test_post_write_unexpected_stage_change_halts_after_one_use_reservation(
    monkeypatch,
):
    calls, state = prepare_fake_worker(monkeypatch, mutation=True)
    with httpx.Client() as client:
        status = m._write_one(client, "https://portal.invalid/rest",
                              {"GITHUB_TOKEN": "x",
                               "SERVICEIT_GH_LEDGER_KEY": "x"}, 123, now=NOW)
    assert status == "POSTCALL_PARTIAL_OR_UNCERTAIN_NO_RETRY"
    assert calls[-1] == "final-UNCERTAIN"


def test_readonly_uses_no_task_or_item_mutation(monkeypatch):
    from leadpilot import serviceit_postcall_live_worker as worker
    monkeypatch.setattr(worker, "field_preflight", lambda *args: True)
    monkeypatch.setattr(worker, "_card_ids", lambda *args: [123])
    monkeypatch.setattr(worker, "validate_webhook",
                        lambda x: "https://serviseit.bitrix24.ru/rest/1/x")
    with httpx.Client() as client:
        out = worker.run(client, {
            "GITHUB_REPOSITORY": m.REPOSITORY,
            "GITHUB_REF": "refs/heads/main",
            "SERVICEIT_BITRIX_WEBHOOK": "secret-hidden",
        }, write=False, now=NOW)
    assert out["status"] == "POSTCALL_READONLY_PRECHECK_PASS"
    assert out["approved_candidates"] == 1
    assert out["crm_writes"] == out["tasks_created"] == 0


def test_multiline_notes_normalized_without_leaking_or_inventing(monkeypatch):
    def fake_api(client, base, method, args):
        a = activity()
        a["DESCRIPTION"] = "Клиент спросил 1С.\nВозражение: срок внедрения."
        if method == "crm.activity.list":
            return {"result": [a]}
        if method == "crm.activity.get":
            return {"result": a}
        raise AssertionError(method)
    monkeypatch.setattr(m, "api", fake_api)
    with httpx.Client() as client:
        got, notes = m._completed_call(client, "https://example.invalid",
                                      123, 42, m.parse_date(
                                          "2026-11-06T11:00:00Z"), now=NOW)
    assert notes == "Клиент спросил 1С. Возражение: срок внедрения."


def test_deadline_mismatch_halts_pending_write(monkeypatch):
    calls, state = prepare_fake_worker(monkeypatch)
    original = m.api
    def incorrect_deadline(client, base, method, args):
        response = original(client, base, method, args)
        if method == "tasks.task.get":
            response["result"]["task"]["deadline"] = "2026-11-08T11:00:00Z"
        return response
    monkeypatch.setattr(m, "api", incorrect_deadline)
    with httpx.Client() as client:
        status = m._write_one(client, "https://portal.invalid/rest", {
            "GITHUB_TOKEN": "redacted", "SERVICEIT_GH_LEDGER_KEY": "redacted"
        }, 123, now=NOW)
    assert status == "POSTCALL_PARTIAL_OR_UNCERTAIN_NO_RETRY"
    assert "crm.item.update" not in calls
    assert calls[-1] == "final-UNCERTAIN"
