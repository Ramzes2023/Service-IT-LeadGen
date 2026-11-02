"""No network/Bitrix credentials: three-color kanban badge DEMO-only."""
from copy import deepcopy
from pathlib import Path
import json
import sqlite3

import pytest

from leadpilot import serviceit_kanban_score_badge as badge
from leadpilot.bitrix_1062_ai_cockpit import CockpitBlocked, State


class MockBitrix:
    def __init__(self):
        self.fields = []
        self.item = {
            "id": 3, "entityTypeId": 1062, "categoryId": 21,
            "title": "DEMO ServiceIT AI — DEMO-ITREQ-001",
            "stageId": "DT1062_21:NEW",
            "ufCrm13SvcitLeadScore": 90,
            "UF_CRM_13_SVCIT_LEAD_SCORE": 90,
            "UF_CRM_13_SVCIT_SCORE_VERSION": "mvp-2026-10-v1",
            "UF_CRM_13_SVCIT_AI_FULL_REPORT": "SYNTHETIC_GIGACHAT_REPORT_" * 10,
            "UF_CRM_13_SVCIT_STOP_REASON": "",
        }
        self.calls = []
        self.timeout = None
        self.ignore_write = False

    def __call__(self, method, params):
        self.calls.append((method, deepcopy(params)))
        if method == self.timeout:
            raise CockpitBlocked("MOCK_TIMEOUT_UNCERTAIN")
        if method == "userfieldconfig.list":
            assert params["filter"] == {"entityId": "CRM_13"}
            return {"fields": deepcopy(self.fields)}
        if method == "userfieldconfig.get":
            return {"field": deepcopy(next(f for f in self.fields if f["id"] == params["id"]))}
        if method == "userfieldconfig.add":
            f = {**deepcopy(params["field"]), "id": 77}
            self.fields.append(f)
            return {"field": deepcopy(f)}
        if method == "crm.item.get":
            assert params == badge.ITEM_SCOPE
            return {"item": deepcopy(self.item)}
        if method == "crm.item.update":
            assert params["fields"].keys() == {badge.CODE}
            assert params["id"] == 3 and params["entityTypeId"] == 1062
            if not self.ignore_write:
                self.item.update(params["fields"])
            return {"item": {"id": 3}}
        raise AssertionError("Unapproved REST method: " + method)


@pytest.fixture
def prepared(monkeypatch):
    mock = MockBitrix()
    monkeypatch.setattr(badge, "inspect_schema", lambda rest: {"missing": []})
    monkeypatch.setattr(badge, "demo_assessment",
                        lambda item, day: {"lead_score": 90, "score_version": badge.VERSION})
    return mock


@pytest.mark.parametrize("score,expected", [
    (0, "🔴 0/100"), (35, "🔴 35/100"), (59, "🔴 59/100"),
    (60, "🟡 60/100"), (65, "🟡 65/100"), (79, "🟡 79/100"),
    (80, "🟢 80/100"), (90, "🟢 90/100"), (100, "🟢 100/100"),
])
def test_traffic_light_boundaries(score, expected):
    assert badge.score_badge(score).startswith(expected)
    assert "проверки" in badge.score_badge(score)


@pytest.mark.parametrize("score", [-1, 101, 90.0, "90", True, None])
def test_bad_score_refused(score):
    with pytest.raises(CockpitBlocked, match="INVALID_SCORE"):
        badge.score_badge(score)


def test_live_readonly_has_zero_writes(prepared):
    result = badge.inspect(prepared)
    assert result["field_needed"] and result["item_write_needed"]
    assert result["expected"].startswith("🟢 90/100")
    assert set(m for m,p in prepared.calls) == {"userfieldconfig.list", "crm.item.get"}


def test_one_demo_add_and_only_one_field_write(tmp_path, prepared):
    snapshot = deepcopy(prepared.item)
    state = State(tmp_path)
    done = badge.apply_demo_badge(prepared, state=state)
    assert done["status"] == "KANBAN_BADGE_DEMO_VERIFIED"
    assert done["kanban_display_configured"] is False
    assert prepared.item[badge.CODE] == badge.score_badge(90)
    assert {k: v for k,v in prepared.item.items() if k != badge.CODE} == snapshot
    mutations = [(m,p) for m,p in prepared.calls if m in ("userfieldconfig.add", "crm.item.update")]
    assert len(mutations) == 2
    assert set(mutations[1][1]["fields"]) == {badge.CODE}
    with sqlite3.connect(state.path) as con:
        rows = con.execute("SELECT key,state FROM cockpit_ops ORDER BY key").fetchall()
    assert len(rows) == 2 and all(status == "VERIFIED" for _,status in rows)
    again = badge.apply_demo_badge(prepared, state=state)
    assert again["status"] == "KANBAN_BADGE_ALREADY_PRESENT"
    assert len([m for m,p in prepared.calls if m in ("userfieldconfig.add", "crm.item.update")]) == 2


def test_existing_unrecognized_value_never_overwritten(tmp_path, prepared):
    prepared.fields.append({"id": 77, "entityId": "CRM_13", "fieldName": badge.CODE,
                           "userTypeId": "string", "multiple": "N",
                           "settings": {"MAX_LENGTH": 120}})
    prepared.item[badge.CODE] = "someone else's value"
    with pytest.raises(CockpitBlocked, match="BADGE_EXISTING_VALUE_CONFLICT"):
        badge.apply_demo_badge(prepared, state=State(tmp_path))
    assert not [m for m,p in prepared.calls if m in ("userfieldconfig.add", "crm.item.update")]


def test_score_drift_rejected_before_write(tmp_path, prepared):
    prepared.item["UF_CRM_13_SVCIT_LEAD_SCORE"] = 70
    with pytest.raises(CockpitBlocked, match="DEMO_SCORE_VERSION_DRIFT"):
        badge.apply_demo_badge(prepared, state=State(tmp_path))
    assert not [m for m,p in prepared.calls if m in ("userfieldconfig.add", "crm.item.update")]


def test_partial_field_add_error_does_not_replay(tmp_path, prepared):
    state = State(tmp_path)
    prepared.timeout = "userfieldconfig.add"
    with pytest.raises(CockpitBlocked, match="MOCK_TIMEOUT_UNCERTAIN"):
        badge.apply_demo_badge(prepared, state=state)
    with sqlite3.connect(state.path) as con:
        assert con.execute("SELECT state FROM cockpit_ops").fetchone()[0] == "UNCERTAIN"
    prepared.timeout = None
    with pytest.raises(CockpitBlocked, match="ONE_SHOT_ALREADY_RESERVED_NO_RETRY"):
        badge.apply_demo_badge(prepared, state=state)


def test_write_readback_uncertain_and_original_fields_preserved(tmp_path, prepared):
    state = State(tmp_path)
    prepared.ignore_write = True
    with pytest.raises(CockpitBlocked, match="BADGE_READBACK_UNCERTAIN"):
        badge.apply_demo_badge(prepared, state=state)
    with sqlite3.connect(state.path) as con:
        rows = dict(con.execute("SELECT key,state FROM cockpit_ops").fetchall())
    assert rows["badge:1062:21:3:v1"] == "UNCERTAIN"
    assert rows["badge:1062:13:field:v1"] == "VERIFIED"


def test_offline_default_no_secrets_no_http(monkeypatch, capsys):
    monkeypatch.setattr(badge.httpx, "Client",
                        lambda: (_ for _ in ()).throw(AssertionError("HTTP forbidden")))
    assert badge.main([]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "KANBAN_BADGE_OFFLINE"
    assert output["crm_writes"] == 0
    assert output["examples"][0].startswith("🟢 90")


def test_exact_demo_opt_in_required_before_network(monkeypatch, capsys):
    monkeypatch.setenv("SERVICEIT_AI_COCKPIT_WEBHOOK", "SECRET_MUST_NOT_LEAK")
    assert badge.main(["--inspect-live", "--webhook-from-env", "--apply-demo"]) == 2
    output = capsys.readouterr().out
    assert "SECRET_MUST_NOT_LEAK" not in output
    assert "EXPLICIT_DEMO_BADGE_APPROVAL_REQUIRED" in output
    import os
    assert "SERVICEIT_AI_COCKPIT_WEBHOOK" not in os.environ


def test_source_has_no_task_outreach_or_other_crm_write():
    s = Path(badge.__file__).read_text(encoding="utf-8")
    for forbidden in ('tasks.task.add', 'crm.item.add', 'crm.item.delete',
                      'crm.item.details.configuration.set', 'send_email', 'getpass.getpass'):
        assert forbidden not in s
