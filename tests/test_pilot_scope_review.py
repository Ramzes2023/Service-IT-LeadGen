"""Scope checks do not contact companies or write to Bitrix24."""
import json
from datetime import date
from pathlib import Path
import pytest

from leadpilot.pilot_scope_review import audit, prepare

TODAY = date(2026, 10, 31)


def original(tmp_path):
    p = tmp_path / "checklist.json"
    p.write_text(json.dumps({
        "mode": "HUMAN_EVIDENCE_CHECKLIST", "items": [
            {"inn": "7701234560", "company_name": "Synthetic 1"},
            {"inn": "7701234561", "company_name": "Synthetic 2"}]
    }), encoding="utf-8")
    return p


def build(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = original(tmp_path)
    before = source.read_bytes()
    output = prepare(source, "exports/scope_review.json")
    assert source.read_bytes() == before
    assert output["crm_writes"] == "NO"
    p = Path("exports/scope_review.json")
    return p


def test_prepares_two_pending_reviews(tmp_path, monkeypatch):
    p = build(tmp_path, monkeypatch)
    r = audit(p, today=TODAY)
    assert r["counts"]["total"] == 2
    assert r["counts"]["pending"] == 2
    assert r["counts"]["crm_ready"] == 0
    assert r["outreach"] == "NOT_AUTHORIZED"


def test_existing_notes_not_overwritten(tmp_path, monkeypatch):
    p = build(tmp_path, monkeypatch)
    with pytest.raises(FileExistsError):
        prepare(original(tmp_path), p)


def test_dangerous_path_outside_exports_rejected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError):
        prepare(original(tmp_path), "other/scope.json")


def test_exclusion_needs_evidence(tmp_path, monkeypatch):
    p = build(tmp_path, monkeypatch)
    data = json.loads(p.read_text(encoding="utf-8"))
    row = data["items"][0]
    row.update(decision="EXCLUDE", reported_business_type="medium",
               reason="SIZE_OUT_OF_SCOPE")
    p.write_text(json.dumps(data), encoding="utf-8")
    res = audit(p, today=TODAY)
    assert res["counts"]["invalid_review"] == 1
    assert res["counts"]["exclusion_reviewed"] == 0
    assert res["counts"]["crm_ready"] == 0


def test_verified_exclusion_stays_local_and_no_crm_writes(tmp_path, monkeypatch):
    p = build(tmp_path, monkeypatch)
    data = json.loads(p.read_text(encoding="utf-8"))
    row = data["items"][0]
    row.update(decision="EXCLUDE", reported_business_type="medium",
               reason="SIZE_OUT_OF_SCOPE", evidence_url="https://companies.example/record/123",
               evidence_date="2026-10-30", reviewed_by="internal_reviewer",
               evidence_text="The company is classified as a medium-size business.")
    p.write_text(json.dumps(data), encoding="utf-8")
    res = audit(p, today=TODAY)
    assert res["counts"]["exclusion_reviewed"] == 1
    assert res["details"][0]["status"] == "EXCLUSION_REVIEWED_NOT_CRM_ACTION"
    assert res["counts"]["crm_writes"] == "NO"
    assert res["counts"]["crm_ready"] == 0


def test_provisional_small_cannot_become_crm_ready(tmp_path, monkeypatch):
    p = build(tmp_path, monkeypatch)
    data = json.loads(p.read_text(encoding="utf-8"))
    row = data["items"][0]
    row.update(decision="PROVISIONAL_SMALL", reported_business_type="small",
               evidence_url="https://rmsp.nalog.ru/", evidence_date="2026-10-30",
               reviewed_by="internal_reviewer", evidence_text="INN 7701234560: small company, pending independent verification.")
    p.write_text(json.dumps(data), encoding="utf-8")
    res = audit(p, today=TODAY)
    assert res["counts"]["provisional_small"] == 1
    assert res["details"][0]["status"] == "SMALL_CLAIM_REQUIRES_FNS_VERIFICATION"
    assert res["counts"]["crm_ready"] == 0


def test_untrusted_url_never_counts_as_evidence(tmp_path, monkeypatch):
    p = build(tmp_path, monkeypatch)
    data = json.loads(p.read_text(encoding="utf-8"))
    row = data["items"][0]
    row.update(decision="EXCLUDE", reported_business_type="large",
               reason="SIZE_OUT_OF_SCOPE",
               evidence_url="https://example.com.evil.test", evidence_date="2026-10-30",
               reviewed_by="internal_reviewer", evidence_text="This company is large according to the public profile.")
    p.write_text(json.dumps(data), encoding="utf-8")
    # Domain authenticity requires independent review; URL syntax alone is not enough.
    res = audit(p, today=TODAY)
    assert res["counts"]["crm_ready"] == 0
    assert res["note"].startswith("Evidence reviewed")


def test_unknown_decision_rejected(tmp_path, monkeypatch):
    p = build(tmp_path, monkeypatch)
    data = json.loads(p.read_text(encoding="utf-8"))
    data["items"][0]["decision"] = "AUTO_SEND_TO_CRM"
    p.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        audit(p, today=TODAY)
