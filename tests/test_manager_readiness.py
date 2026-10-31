"""Unit tests for strict offline join of research, enrichment, and scope decisions."""
import json
from datetime import date
from pathlib import Path

import pytest

from leadpilot.manager_readiness import assemble, write_report


def fixtures(tmp_path, monkeypatch, bad_scope=False):
    monkeypatch.chdir(tmp_path)
    export = tmp_path / "exports"
    export.mkdir(exist_ok=True)
    research = {
        "mode": "OFFLINE_MVP_RESEARCH", "crm_ready": 0,
        "candidates": [{
            "inn": "7701234560", "company_name": "Учебное ООО",
            "region": "Москва", "status": "RESEARCH_ONLY_NOT_CRM_READY",
            "lead_score": 80, "score_version": "pilot-v1",
            "signal": {"url": "https://trudvsem.ru/vacancy/synthetic"},
            "ai_status": "MISSING_OR_INCOMPLETE",
            "phone": "+79990000000"  # must NOT propagate contact fields
        }]
    }
    enrich = {"mode": "HUMAN_EVIDENCE_CHECKLIST",
              "items": [{"inn": "7701234560", "business_size": "",
                         "size_registry_url": "", "size_checked_on": "",
                         "corporate_contact_source_url": "", "contact_channel": "",
                         "contact_checked_on": "", "corporate_contact_role": "",
                         "reviewed_by_human": False,
                         "crm_duplicate_check": "NOT_PERFORMED",
                         "outreach_permission": "NOT_CONFIRMED"}]}
    scope = {"mode": "PILOT_SCOPE_REVIEW",
             "items": [{"inn": "7701234569" if bad_scope else "7701234560",
                        "decision": "PENDING", "reported_business_type": "unknown",
                        "reason": "", "evidence_url": "", "evidence_date": "",
                        "evidence_text": "", "reviewed_by": ""}]}
    paths = []
    for name, value in [("research.json", research), ("enrichment.json", enrich), ("scope.json", scope)]:
        p = export / name
        p.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        paths.append(p)
    return paths


def test_incomplete_reviews_never_promoted_and_no_contacts(tmp_path, monkeypatch):
    paths = fixtures(tmp_path, monkeypatch)
    out = assemble(*paths)
    assert out["counts"]["candidates"] == 1
    assert out["counts"]["scope_pending"] == 1
    assert out["counts"]["ready_for_crm"] == 0
    assert out["crm_writes"] == "NO" and out["outbound_messages"] == "NO"
    record = out["companies"][0]
    assert record["status"] == "HOLD_FOR_REVIEW"
    assert record["crm_ready"] is False
    assert "phone" not in record
    assert len(record["next_steps"]) >= 3


def test_mismatched_inns_fail_closed(tmp_path, monkeypatch):
    paths = fixtures(tmp_path, monkeypatch, bad_scope=True)
    with pytest.raises(ValueError, match="different companies"):
        assemble(*paths)


def test_report_only_in_exports_without_overwrite(tmp_path, monkeypatch):
    paths = fixtures(tmp_path, monkeypatch)
    out = assemble(*paths)
    with pytest.raises(ValueError):
        write_report(out, "other/report.json")
    target = write_report(out, "exports/manager-report.json")
    assert target.exists()
    with pytest.raises(FileExistsError):
        write_report(out, "exports/manager-report.json")


def test_exclusion_stays_out_of_crm_even_with_evidence(tmp_path, monkeypatch):
    paths = fixtures(tmp_path, monkeypatch)
    s = paths[2]
    obj = json.loads(s.read_text(encoding="utf8"))
    obj["items"][0].update({
        "decision": "EXCLUDE", "reported_business_type": "medium",
        "reason": "SIZE_OUT_OF_SCOPE", "evidence_url": "https://example.org/record",
        "evidence_date": date.today().isoformat(),
        "evidence_text": "Публичный справочник сообщает о среднем бизнесе.",
        "reviewed_by": "internal-reviewer"
    })
    s.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf8")
    result = assemble(*paths)
    assert result["counts"]["scope_reviewed_hold"] == 1
    assert result["counts"]["ready_for_crm"] == 0
    assert result["companies"][0]["outbound_authorized"] is False


def test_claimed_crm_duplicates_status_not_trusted(tmp_path, monkeypatch):
    paths = fixtures(tmp_path, monkeypatch)
    p = paths[1]
    obj = json.loads(p.read_text(encoding="utf8"))
    obj["items"][0]["crm_duplicate_check"] = "PASSED"
    p.write_text(json.dumps(obj), encoding="utf8")
    result = assemble(*paths)
    assert any("не подтверждено" in msg for msg in result["companies"][0]["next_steps"])
    assert result["counts"]["ready_for_crm"] == 0


def test_optional_public_hints_show_legal_hold_without_promoting_crm(tmp_path, monkeypatch):
    paths = fixtures(tmp_path, monkeypatch)
    hp = tmp_path / "research_hints.json"
    today = date.today().isoformat()
    hp.write_text(json.dumps({
        "mode": "PUBLIC_RESEARCH_HINTS", "review_date": today,
        "items": [{"inn": "7701234560", "name": "Synthetic organization",
                   "action": "LEGAL_REVIEW", "note": "Needs separate compliance review",
                   "source_url": "https://regulator.example.org/reference",
                   "source_level": "OFFICIAL_REGULATOR", "checked_on": today}]
    }), encoding="utf-8")
    result = assemble(*paths, hints=hp)
    row = result["companies"][0]
    assert row["public_research_action"] == "LEGAL_REVIEW"
    assert row["public_research_is_approved"] is False
    assert row["crm_ready"] is False
    assert row["outbound_authorized"] is False
    assert "правовая проверка" in row["next_steps"][0]


def test_optional_public_hints_must_match_exact_inn(tmp_path, monkeypatch):
    paths = fixtures(tmp_path, monkeypatch)
    hp = tmp_path / "bad_hints.json"
    today = date.today().isoformat()
    hp.write_text(json.dumps({
        "mode": "PUBLIC_RESEARCH_HINTS", "review_date": today,
        "items": [{"inn": "7701234561", "name": "Unrelated company name",
                   "action": "DEFER_SCOPE", "note": "Another company, not this one",
                   "source_url": "https://example.org/about",
                   "source_level": "THIRD_PARTY", "checked_on": today}]
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="match"):
        assemble(*paths, hints=hp)
