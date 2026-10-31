"""Synthetic public procurement evidence checks, without real sales outreach."""
import csv
from datetime import date
import json
from pathlib import Path

import pytest

from leadpilot.public_1c_demand_gate import review, save

NOW = date(2026, 10, 31)
SME = "7724850252"
ANOTHER = "5003079216"
OUTSIDE = "7702319337"
FNS_URL = "https://rmsp.nalog.ru/search.html?mode=inn-list"
TENDER_URL = "https://www.b2b-center.ru/search/number/TEST-123/"


def data(tmp_path):
    fns = tmp_path / "fns.json"
    companies = [
        {"inn": inn, "company_name": "Учебная организация " + inn,
         "region": "Москва", "fns_category": "MICRO",
         "fns_snapshot": NOW.isoformat(),
         "fns_source": FNS_URL, "okved_public": "46.49",
         "business_size_verified_from_export": True,
         "qualification": "SIZE_ONLY_RESEARCH", "demand_for_1c": "NOT_CHECKED",
         "lead_score": None, "crm_ready": False, "outbound_authorized": False}
        for inn in (SME, ANOTHER)]
    fns.write_text(json.dumps({
        "mode": "SME_FIRST_FNS_SIZE_ONLY",
        "snapshot": NOW.isoformat(), "counts": {"size_eligible_for_research": 2, "crm_ready": 0},
        "crm_writes": "NO", "outbound_messages": "NO", "companies": companies
    }, ensure_ascii=False), encoding="utf8")
    evidence = tmp_path / "requests.json"
    items = [{
        "buyer_inn": SME,
        "source_url": TENDER_URL,
        "source_type": "COMMERCIAL_TENDER",
        "title": "Оказание услуг по сопровождению системы 1С:Предприятие",
        "published_on": "2026-10-30",
        "deadline_on": "2026-11-05",
        "evidence_excerpt": "Публичная заявка на сопровождение 1С:Предприятие, синтетический тест",
        "verified_by_operator": False,
    }]
    evidence.write_text(json.dumps({"mode": "PUBLIC_1C_REQUESTS_MANUAL",
                                    "items": items}, ensure_ascii=False), encoding="utf8")
    return fns, evidence


def test_exact_inn_service_request_is_review_only(tmp_path):
    fns, evidence = data(tmp_path)
    before = fns.read_bytes(), evidence.read_bytes()
    result = review(fns, evidence, today=NOW)
    assert result["counts"]["size_verified_businesses"] == 2
    assert result["counts"]["source_requests_matching_fns"] == 1
    assert result["counts"]["companies_with_public_requests"] == 1
    assert result["counts"]["deadline_not_yet_passed_unverified"] == 1
    assert result["counts"]["crm_ready"] == 0
    rows = {row["inn"]: row for row in result["companies"]}
    assert rows[SME]["lead_status"] == "PUBLIC_SOURCE_MANUAL_REVIEW"
    assert rows[SME]["source_requests"][0]["source_verified_live"] is False
    assert rows[SME]["crm_ready"] is False and rows[SME]["outbound_authorized"] is False
    assert rows[ANOTHER]["lead_status"] == "NO_DOCUMENTED_EXTERNAL_REQUEST_IN_SUBMITTED_ITEMS"
    assert (fns.read_bytes(), evidence.read_bytes()) == before


def test_unmatched_buyer_is_not_promoted_to_50(tmp_path):
    fns, evidence = data(tmp_path)
    obj = json.loads(evidence.read_text("utf8"))
    obj["items"][0]["buyer_inn"] = OUTSIDE
    evidence.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf8")
    r = review(fns, evidence, today=NOW)
    assert r["counts"]["source_requests_matching_fns"] == 0
    assert r["counts"]["requests_not_matched_to_fns_50"] == 1
    assert r["unmatched_buyer_inns_to_check_fns_separately"] == [OUTSIDE]


def test_expired_request_is_not_automatically_active(tmp_path):
    fns, evidence = data(tmp_path)
    doc = json.loads(evidence.read_text("utf8"))
    doc["items"][0]["deadline_on"] = "2026-10-30"
    evidence.write_text(json.dumps(doc), encoding="utf8")
    result = review(fns, evidence, today=NOW)
    assert result["counts"]["deadline_passed"] == 1
    assert result["companies"][1]["source_requests"] or result["companies"][0]["source_requests"]
    row = next(r for r in result["companies"] if r["source_request_count"])
    assert row["source_requests"][0]["research_status"] == "DEADLINE_PASSED_NOT_ACTIVE"


def test_missing_deadline_is_unverified_not_active(tmp_path):
    fns, evidence = data(tmp_path)
    doc = json.loads(evidence.read_text("utf8"))
    doc["items"][0]["deadline_on"] = None
    evidence.write_text(json.dumps(doc), encoding="utf8")
    result = review(fns, evidence, today=NOW)
    assert result["counts"]["deadline_unknown"] == 1


def test_unrelated_1c_bitrix_not_erp_service(tmp_path):
    fns, evidence = data(tmp_path)
    doc = json.loads(evidence.read_text("utf8"))
    doc["items"][0]["title"] = "Оказание услуг по сопровождению 1С-Битрикс24"
    evidence.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf8")
    with pytest.raises(ValueError, match="explicitly"):
        review(fns, evidence, today=NOW)


@pytest.mark.parametrize("bad_url", [
    "http://www.b2b-center.ru/search/number/TEST-123/",
    "https://example.org/tenders/1c", "https://b2b-center.ru.evil.example/a",
    "https://user:pass@b2b-center.ru/a"
])
def test_untrusted_source_url_rejected(tmp_path, bad_url):
    fns, evidence = data(tmp_path)
    doc = json.loads(evidence.read_text("utf8"))
    doc["items"][0]["source_url"] = bad_url
    evidence.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf8")
    with pytest.raises(ValueError, match="HTTPS public procurement"):
        review(fns, evidence, today=NOW)


def test_duplicate_source_is_rejected(tmp_path):
    fns, evidence = data(tmp_path)
    doc = json.loads(evidence.read_text("utf8"))
    doc["items"].append(doc["items"][0])
    evidence.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf8")
    with pytest.raises(ValueError, match="Duplicate"):
        review(fns, evidence, today=NOW)


def test_outdated_or_future_published_date_rejected(tmp_path):
    fns, evidence = data(tmp_path)
    doc = json.loads(evidence.read_text("utf8"))
    doc["items"][0]["published_on"] = "2026-06-01"
    evidence.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf8")
    with pytest.raises(ValueError, match="stale"):
        review(fns, evidence, today=NOW)
    doc["items"][0]["published_on"] = "2026-11-01"
    evidence.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf8")
    with pytest.raises(ValueError, match="stale"):
        review(fns, evidence, today=NOW)


def test_import_cannot_claim_operator_verified(tmp_path):
    fns, evidence = data(tmp_path)
    doc = json.loads(evidence.read_text("utf8"))
    doc["items"][0]["verified_by_operator"] = True
    evidence.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf8")
    with pytest.raises(ValueError, match="Never assert"):
        review(fns, evidence, today=NOW)


def test_preapproved_fns_record_rejected(tmp_path):
    fns, evidence = data(tmp_path)
    doc = json.loads(fns.read_text("utf8"))
    doc["companies"][0]["crm_ready"] = True
    fns.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf8")
    with pytest.raises(ValueError, match="pre-approved"):
        review(fns, evidence, today=NOW)


def test_missing_evidence_never_generates_fake_prospects(tmp_path):
    fns, evidence = data(tmp_path)
    evidence.unlink()
    with pytest.raises(ValueError, match="EVIDENCE_MISSING"):
        review(fns, evidence, today=NOW)


def test_csv_formula_escaping_and_no_overwrite(tmp_path, monkeypatch):
    fns, evidence = data(tmp_path)
    doc = json.loads(fns.read_text("utf8"))
    doc["companies"][0]["company_name"] = "=HYPERLINK(evil)"
    fns.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf8")
    report = review(fns, evidence, today=NOW)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="exports"):
        save(report, "outside/a.json", "exports/a.csv")
    result = save(report, "exports/a.json", "exports/a.csv")
    assert result["companies"] == 2
    with Path("exports/a.csv").open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert next(row for row in rows if row["inn"] == SME)["company_name"].startswith("'=")
    with pytest.raises(ValueError, match="exports"):
        save(report, "exports/a.json", "exports/a.csv")
