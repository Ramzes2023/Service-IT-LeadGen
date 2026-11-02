"""Read-only recovery diagnostics for BADGE_READBACK_UNCERTAIN. Mock only."""
from copy import deepcopy
import json
import os
import sqlite3
from pathlib import Path

import pytest

from leadpilot import serviceit_kanban_badge_recovery as rec
from leadpilot.serviceit_kanban_score_badge import CODE, score_badge
from leadpilot.bitrix_1062_mapping import FIELD_MAP
from leadpilot.bitrix_1062_ai_cockpit import PREFIX, CockpitBlocked


class Portal:
    def __init__(self, *, original=None, alternate=None, schema=True):
        self.original_value = original
        self.alternate_value = alternate
        self.schema = schema
        self.calls = []
    def __call__(self, method, params):
        self.calls.append((method, deepcopy(params)))
        if method == "crm.item.get":
            obj = {
                "id": 3, "entityTypeId": 1062, "categoryId": 21,
                "title": "DEMO ServiceIT AI — DEMO-ITREQ-001",
                FIELD_MAP["lead_score"]: 90,
                FIELD_MAP["score_version"]: rec.VERSION,
                PREFIX + "AI_FULL_REPORT": "GIGACHAT_SYNTHETIC_REPORT_" * 10
            }
            if params["useOriginalUfNames"] == "Y":
                if self.original_value is not ...:
                    obj[CODE] = self.original_value
            else:
                if self.alternate_value is not ...:
                    obj["ufCrm13SvcitAiScoreBadge"] = self.alternate_value
            return {"item": obj}
        if method == "userfieldconfig.list":
            return {"fields": [{"id":77, "entityId":"CRM_13", "fieldName":CODE}] if self.schema else []}
        if method == "userfieldconfig.get":
            return {"field": {"id":77,"entityId":"CRM_13", "fieldName":CODE,
                              "userTypeId":"string","multiple":"N","settings":{"MAX_LENGTH":120}}}
        if method == "crm.item.fields":
            key = CODE if params["useOriginalUfNames"] == "Y" else "ufCrm13SvcitAiScoreBadge"
            return {"fields":{key:{"type":"string","isReadOnly":False}}}
        raise AssertionError("Unexpected CRM operation: " + method)


@pytest.mark.parametrize("original,alternate", [
    (score_badge(90), score_badge(90)), (None, None), ("", ""),
    ("🟢 90/100", "🟢 90/100"), (..., ...),
])
def test_read_only_shapes_no_mutation(original, alternate):
    portal = Portal(original=original, alternate=alternate)
    result = rec.inspect_rest(portal)
    assert result["status"] == "BADGE_READONLY_DIAGNOSTIC"
    assert result["crm_writes"] == 0
    assert result["score_unchanged"]
    assert result["badge_schema_valid"]
    assert len(portal.calls) == 6
    assert set(m for m,p in portal.calls) == {
        "crm.item.get", "crm.item.fields", "userfieldconfig.list","userfieldconfig.get"
    }
    assert result["readback_with_original_names"]["state"] in {
        "STRING","NULL","EMPTY","KEY_ABSENT"
    }


def test_redacts_unknown_value():
    portal = Portal(original="SENSITIVE_PRODUCTION_OR_TOKEN", alternate="SENSITIVE_PRODUCTION_OR_TOKEN")
    result = rec.inspect_rest(portal)
    assert "SENSITIVE_PRODUCTION_OR_TOKEN" not in json.dumps(result, ensure_ascii=False)
    assert result["readback_with_original_names"]["state"] == "STRING"
    assert result["readback_with_original_names"]["exact_expected"] is False


def test_detects_schema_missing_but_never_adds():
    portal = Portal(original=None, alternate=None, schema=False)
    result = rec.inspect_rest(portal)
    assert result["badge_schema_exists"] is False
    assert not [m for m,p in portal.calls if m == "userfieldconfig.add"]


def test_ledger_does_not_initialize_or_write(tmp_path):
    path = tmp_path / "cockpit-report-v1.sqlite3"
    missing = rec.ledger_readonly(tmp_path)
    assert missing["state"] == "LEDGER_NOT_FOUND" and not path.exists()
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE cockpit_ops (key TEXT PRIMARY KEY,state TEXT,digest TEXT)")
        db.execute("INSERT INTO cockpit_ops VALUES (?,?,?)",
                   ("badge:1062:13:field:v1","VERIFIED","mock"))
        db.execute("INSERT INTO cockpit_ops VALUES (?,?,?)",
                   ("badge:1062:21:3:v1","UNCERTAIN","mock"))
    before = path.read_bytes()
    status = rec.ledger_readonly(tmp_path)
    assert status["items"] == {"badge:1062:13:field:v1":"VERIFIED",
                               "badge:1062:21:3:v1":"UNCERTAIN"}
    assert path.read_bytes() == before


def test_offline_cli_does_not_request_rest(tmp_path,monkeypatch,capsys):
    monkeypatch.setattr(rec.httpx, "Client",
                        lambda: (_ for _ in ()).throw(AssertionError("Network forbidden")))
    assert rec.main(["--state-dir",str(tmp_path)]) == 0
    data=json.loads(capsys.readouterr().out)
    assert data["status"] == "BADGE_LOCAL_LEDGER_AUDIT"


def test_webhook_must_be_explicit_and_is_redacted(monkeypatch,tmp_path,capsys):
    monkeypatch.setenv("SERVICEIT_AI_COCKPIT_WEBHOOK","SECRET_DO_NOT_PRINT")
    assert rec.main(["--inspect-live","--webhook-from-env","--state-dir",str(tmp_path)]) == 2
    output = capsys.readouterr().out
    assert "SECRET_DO_NOT_PRINT" not in output
    assert json.loads(output)["status"] == "BADGE_DIAGNOSTIC_BLOCKED"
    assert "SERVICEIT_AI_COCKPIT_WEBHOOK" not in os.environ


def test_source_contains_no_mutation_calls():
    s=Path(rec.__file__).read_text(encoding="utf-8")
    for forbidden in ('rest("crm.item.update"', 'rest("userfieldconfig.add"',
                      "state.reserve(", "state.backup(", "getpass.getpass(",
                      "crm.item.details.configuration.set", "tasks.task.add"):
        assert forbidden not in s
