"""Public research suggestions must never alter human review or write to CRM."""
from datetime import date
import json
from pathlib import Path

import pytest
from leadpilot.research_triage import preview, save_preview

TODAY = date(2026, 10, 31)


def inputs(tmp_path):
    scope = tmp_path / "scope.json"
    hints = tmp_path / "hints.json"
    scope.write_text(json.dumps({
        "mode": "PILOT_SCOPE_REVIEW", "items": [
            {"inn": "7701234560", "decision": "PENDING"},
            {"inn": "7701234561", "decision": "PENDING"}
        ]}), encoding="utf8")
    hints.write_text(json.dumps({
        "mode": "PUBLIC_RESEARCH_HINTS", "review_date": "2026-10-31",
        "items": [
            {"inn": "7701234560", "name": "Example business 1",
             "action": "VERIFY_FNS", "note": "Small company status not yet verified",
             "source_url": "https://company.example.org/about", "source_level": "THIRD_PARTY",
             "checked_on": "2026-10-31"},
            {"inn": "7701234561", "name": "Example business 2",
             "action": "LEGAL_REVIEW", "note": "Possible compliance issue requires human review",
             "source_url": "https://agency.example.org/status",
             "source_level": "OFFICIAL_REGULATOR", "checked_on": "2026-10-31"}
        ]}), encoding="utf8")
    return scope, hints


def test_research_preview_no_contact_or_crm_write(tmp_path):
    scope, hints = inputs(tmp_path)
    before = scope.read_bytes()
    obj = preview(scope, hints, today=TODAY)
    assert scope.read_bytes() == before
    assert obj["counts"]["total"] == 2
    assert obj["counts"]["verify_fns"] == 1
    assert obj["counts"]["legal_review"] == 1
    assert obj["counts"]["ready_for_crm"] == 0
    assert obj["crm_writes"] == "NO"
    assert obj["items"][0]["research_action"] == "LEGAL_REVIEW"
    assert all(x["needs_human_review"] and not x["outbound_authorized"] for x in obj["items"])


def test_mismatched_lists_rejected(tmp_path):
    scope, hints = inputs(tmp_path)
    obj = json.loads(hints.read_text("utf8"))
    obj["items"].pop()
    hints.write_text(json.dumps(obj), encoding="utf8")
    with pytest.raises(ValueError, match="match"):
        preview(scope, hints, today=TODAY)


def test_invalid_evidence_url_rejected(tmp_path):
    scope, hints = inputs(tmp_path)
    obj = json.loads(hints.read_text("utf8"))
    obj["items"][0]["source_url"] = "http://localhost:5000/"
    hints.write_text(json.dumps(obj), encoding="utf8")
    with pytest.raises(ValueError, match="source"):
        preview(scope, hints, today=TODAY)


def test_old_source_rejected(tmp_path):
    scope, hints = inputs(tmp_path)
    with pytest.raises(ValueError, match="stale"):
        preview(scope, hints, today=date(2027, 3, 1))


def test_does_not_overwrite_or_write_outside_exports(tmp_path, monkeypatch):
    scope, hints = inputs(tmp_path)
    monkeypatch.chdir(tmp_path)
    report = preview(scope, hints, today=TODAY)
    with pytest.raises(ValueError):
        save_preview(report, "data/companies.json")
    path = save_preview(report, "exports/triage.json")
    assert Path(path).exists()
    with pytest.raises(FileExistsError):
        save_preview(report, "exports/triage.json")


def test_existing_manual_decision_is_preserved(tmp_path):
    scope, hints = inputs(tmp_path)
    data = json.loads(scope.read_text("utf8"))
    data["items"][0]["decision"] = "DEFER"
    scope.write_text(json.dumps(data), encoding="utf8")
    out = preview(scope, hints, today=TODAY)
    assert out["counts"]["manual_decision_already_present"] == 1
    assert json.loads(scope.read_text("utf8"))["items"][0]["decision"] == "DEFER"
