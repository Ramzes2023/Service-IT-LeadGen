"""Purely synthetic contracts for one-shot deal stage SORT repair."""
import copy

import httpx
import pytest

from leadpilot import serviceit_sales_stages_repair_v2 as repair


def rows(*, short_gap=False):
    # Same 9-stage anonymous role pattern observed in real READONLY CRM:
    # PPPIPSQFF. Numeric IDs and process stage names are synthetic.
    win_sort = 42 if short_gap else 50
    values = [
        ("P0", "Вход", 10, "process"),
        ("P1", "Ожидание", 20, "process"),
        ("P2", "Подготовка", 20, "process"),
        ("INTEREST", "Есть интерес", 30, "process"),
        ("P3", "В работе", 40, "process"),
        ("WIN", "Успешно реализовано", win_sort, "success"),
        ("QUOTE", "КП/расчёт", 60, "process"),
        ("FAIL1", "Закрыто без продажи", 70, "failure"),
        ("FAIL2", "Отказ от сделки", 80, "failure"),
    ]
    return [
        {"id": i + 101, "code": "C4:" + code, "name": name,
         "sort": sort, "semantic": sem}
        for i, (code, name, sort, sem) in enumerate(values)
    ]


def test_actual_anonymous_sequence_preflight_plan_moves_only_quote():
    p = repair.build_plan(rows(), 4)
    assert p["strategy"] == "MOVE_QUOTE_SORT_ONLY"
    assert p["updates"] == [("C4:QUOTE", 41)]
    assert p["new_sorts"] == (42, 43)
    assert p["changed_existing_count"] == 1


def test_short_sort_gap_plan_only_moves_three_terminal_sort_values():
    p = repair.build_plan(rows(short_gap=True), 4)
    assert p["strategy"] == "MOVE_THREE_TERMINAL_SORTS"
    assert [c for c, _ in p["updates"]] == [
        "C4:FAIL2", "C4:FAIL1", "C4:WIN",
    ]
    assert len({s for _, s in p["updates"]} | set(p["new_sorts"])) == 5
    assert p["changed_existing_count"] == 3


def test_changed_crm_funnel_refuses_to_modify():
    fixture = rows()
    fixture[4]["name"] = "Переговоры"
    with pytest.raises(repair.RepairBlocked):
        repair.build_plan(fixture, 4)
    fixture = rows()
    fixture[0]["code"] = "C5:P0"  # Wrong sales funnel.
    with pytest.raises(repair.RepairBlocked):
        repair.build_plan(fixture, 4)
    fixture = rows()
    fixture[0]["id"] = None
    with pytest.raises(repair.RepairBlocked):
        repair.build_plan(fixture, 4)


def test_write_call_updates_exactly_sort_and_identifies_crm_permission_denied():
    recorded = []
    def handler(req):
        import json
        recorded.append((req.url.path, json.loads(req.content)))
        return httpx.Response(400, json={
            "error": "", "error_description": "Access denied.",
        })
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(repair.CrmRightsDenied):
            repair._update_sort(client, "https://serviseit.bitrix24.ru/rest/1/redacted",
                                rows()[6], 41)
    assert recorded == [
        ("/rest/1/redacted/crm.status.update.json",
         {"id": 107, "fields": {"SORT": 41}})
    ]


def _patch_live_for_synthetic(monkeypatch, state, *, denied=False, mutate_other=False):
    from leadpilot import serviceit_sales_funnel_provision as f
    from leadpilot import serviceit_sales_stages_guarded as old
    monkeypatch.setattr(f, "validate_webhook",
                        lambda _: "https://serviseit.bitrix24.ru/rest/1/redacted")
    monkeypatch.setattr(f, "rest", lambda client, base, method, args: False)
    monkeypatch.setattr(f, "categories", lambda client, base: [
        {"id": 4, "name": "Лидогенерация", "entityTypeId": 2}
    ])
    monkeypatch.setattr(f, "get_category", lambda client, base, cid: {
        "id": cid, "name": "Лидогенерация", "entityTypeId": 2
    })
    monkeypatch.setattr(old, "_read_stages",
                        lambda client, base, cid: copy.deepcopy(state))
    calls = []
    def fake_update(client, base, existing, new_sort):
        calls.append(("sort", existing["code"], new_sort))
        if denied:
            raise repair.CrmRightsDenied("CRM_SETTINGS_RIGHTS_DENIED")
        for r in state:
            if r["code"] == existing["code"]:
                r["sort"] = new_sort
        if mutate_other:
            state[0]["name"] = "Unexpected portal change"
    monkeypatch.setattr(repair, "_update_sort", fake_update)
    def fake_add(client, base, cat, code, name, sort):
        calls.append(("add", code, sort))
        state.append({"id": 900 + len(state), "code": "C4:" + code,
                      "name": name, "sort": sort, "semantic": "process"})
    monkeypatch.setattr(old, "_add_stage", fake_add)
    class Fence:
        def __init__(self, *args):
            pass
        def reserve(self, card):
            calls.append(("fence", "reserve", card))
            return "RESERVED"
        def finalize(self, card, state):
            calls.append(("fence", "finalize", state))
            return True
    monkeypatch.setattr(repair, "RepairFence", Fence)
    return calls


@pytest.mark.parametrize("short_gap,expected_changes", [(False, 3), (True, 5)])
def test_one_shot_repair_verifies_realistic_synthetic_readback(
    monkeypatch, short_gap, expected_changes
):
    state = rows(short_gap=short_gap)
    original = copy.deepcopy(state)
    calls = _patch_live_for_synthetic(monkeypatch, state)
    with httpx.Client() as client:
        result = repair.run(client, {
            "SERVICEIT_BITRIX_WEBHOOK": "not-displayed",
            "GITHUB_TOKEN": "redacted",
            "SERVICEIT_GH_LEDGER_KEY": "redacted",
        }, apply=True)
    assert result["status"] == "LEADGEN_STAGES_ORDER_AND_BOTH_NAMES_VERIFIED"
    assert result["crm_writes"] == expected_changes
    assert result["new_stages_added"] == 2
    assert result["existing_deal_updates"] == 0
    assert result["other_funnel_updates"] == 0
    assert len(state) == 11
    assert [(r["code"], r["name"], r["id"], r["semantic"]) for r in state[:9]] == [
        (r["code"], r["name"], r["id"], r["semantic"]) for r in original
    ]
    assert calls[0] == ("fence", "reserve", 79)
    assert calls[-1] == ("fence", "finalize", "VERIFIED")


def test_no_write_on_permission_denied_even_if_portal_admin_false(monkeypatch):
    state = rows()
    calls = _patch_live_for_synthetic(monkeypatch, state, denied=True)
    with httpx.Client() as client:
        result = repair.run(client, {
            "SERVICEIT_BITRIX_WEBHOOK": "redacted",
            "GITHUB_TOKEN": "redacted",
            "SERVICEIT_GH_LEDGER_KEY": "redacted",
        }, apply=True)
    assert result == {"status": "CRM_SETTINGS_PERMISSION_DENIED",
                      "crm_writes": 0}
    assert not any(x[0] == "add" for x in calls)


def test_unexpected_existing_stage_mutation_stops_and_never_adds(monkeypatch):
    state = rows()
    calls = _patch_live_for_synthetic(monkeypatch, state, mutate_other=True)
    with httpx.Client() as client:
        result = repair.run(client, {
            "SERVICEIT_BITRIX_WEBHOOK": "redacted",
            "GITHUB_TOKEN": "redacted",
            "SERVICEIT_GH_LEDGER_KEY": "redacted",
        }, apply=True)
    assert result["status"] == "REPAIR_V2_PARTIAL_OR_UNCERTAIN_NO_RETRY"
    assert result["crm_writes"] == "UNVERIFIED"
    assert not any(x[0] == "add" for x in calls)


def test_readonly_does_not_reserve_fence_or_write(monkeypatch):
    state = rows()
    calls = _patch_live_for_synthetic(monkeypatch, state)
    with httpx.Client() as client:
        result = repair.run(client, {
            "SERVICEIT_BITRIX_WEBHOOK": "redacted",
        }, apply=False)
    assert result == {
        "status": "STAGE_SORT_REPAIR_V2_READY_READONLY", "crm_writes": 0,
        "portal_admin": False,
        "crm_settings_permission": "UNKNOWN_UNTIL_SCOPED_WRITE",
        "stages_visible": 9, "planned_sort_only_updates": 1,
        "planned_new_stages": 2,
        "strategy": "MOVE_QUOTE_SORT_ONLY",
    }
    assert calls == []
