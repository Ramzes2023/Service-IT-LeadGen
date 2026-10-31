"""Offline manager queue contract: never promote tender research into CRM."""
import csv
import hashlib
import json
from datetime import date

import pytest

from leadpilot.eis_manager_queue import assemble, save

DAY = date(2026, 10, 31)
URL = "https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber=01234567890123456789"
INN = "7724850252"


def _record(url=URL):
    return {
        "signal_id": hashlib.sha256(url.encode()).hexdigest()[:24],
        "source_url": url,
        "title": "Оказание услуг по сопровождению 1С:Предприятие",
        "published_on": "2026-10-30",
        "buyer_inn_operator_supplied": INN,
        "buyer_region_operator_supplied": "Москва",
        "fns_company_name": "СИНТЕТИЧЕСКИЙ ПОКУПАТЕЛЬ",
        "fns_size": "MICRO",
        "procurement_status_operator_supplied": "ACCEPTING",
        "deadline_operator_supplied": "2026-11-05",
        "scope_operator_supplied": "EXTERNAL_1C_SERVICE",
        "status": "MANAGER_RESEARCH_CANDIDATE_NOT_APPROVED",
        "holds": ["CRM_DUPLICATE_PENDING", "LEGAL_CONTACT_BASIS_PENDING",
                  "MANAGER_APPROVAL_PENDING", "LIVE_SOURCE_RECHECK_REQUIRED"],
        "source_verified_live": False,
        "manager_approved": False,
        "lead_score": None,
        "crm_ready": False,
        "outbound_authorized": False,
    }


def _audit(tmp_path, rows=None):
    rows = [_record()] if rows is None else rows
    path = tmp_path / "eis_buyer_review.json"
    path.write_text(json.dumps({
        "mode": "EIS_BUYER_FNS_REVIEW_ONLY",
        "checked_on": DAY.isoformat(),
        "counts": {"rss_records": len(rows), "qualified_leads": 0, "crm_ready": 0},
        "items": rows,
        "crm_writes": "NO", "outbound_messages": "NO",
    }, ensure_ascii=False), encoding="utf8")
    return path


def test_manager_queue_preserves_all_holds_and_no_crm(tmp_path):
    report = assemble(_audit(tmp_path), today=DAY)
    assert report["counts"]["manager_review_companies"] == 1
    assert report["counts"]["qualified_leads"] == 0
    c = report["companies"][0]
    assert c["buyer_inn"] == INN
    assert c["contact"] is None and c["lead_score"] is None
    assert c["crm_ready"] is False and c["outbound_authorized"] is False
    assert "MANAGER_APPROVAL_PENDING" in c["holds"]


@pytest.mark.parametrize("field,value", [
    ("status", "OUTSIDE_PILOT_OR_NEEDS_REVIEW"),
    ("crm_ready", True),
    ("manager_approved", True),
    ("outbound_authorized", True),
    ("lead_score", 99),
    ("fns_size", "MEDIUM"),
    ("procurement_status_operator_supplied", "CLOSED"),
    ("scope_operator_supplied", "OTHER_SERVICE"),
    ("deadline_operator_supplied", "2026-10-30"),
    ("source_verified_live", True),
])
def test_invalid_review_never_becomes_manager_card(tmp_path, field, value):
    record = _record()
    record[field] = value
    path = _audit(tmp_path, [record])
    if field in ("crm_ready", "manager_approved", "outbound_authorized", "lead_score"):
        with pytest.raises(ValueError, match="PREAPPROVAL"):
            assemble(path, today=DAY)
    else:
        report = assemble(path, today=DAY)
        assert report["counts"]["manager_review_companies"] == 0


def test_duplicate_same_source_rejected(tmp_path):
    with pytest.raises(ValueError, match="DUPLICATE"):
        assemble(_audit(tmp_path, [_record(), _record()]), today=DAY)


def test_second_eis_notice_same_inn_aggregated(tmp_path):
    other = URL.replace("01234567890123456789", "01234567890123456780")
    result = assemble(_audit(tmp_path, [_record(), _record(other)]), today=DAY)
    assert result["counts"]["manager_review_companies"] == 1
    assert result["counts"]["manager_review_notices"] == 2


def test_stale_audit_rejected(tmp_path):
    path = _audit(tmp_path)
    doc = json.loads(path.read_text(encoding="utf8"))
    doc["checked_on"] = "2026-10-01"
    path.write_text(json.dumps(doc), encoding="utf8")
    with pytest.raises(ValueError, match="STALE"):
        assemble(path, today=DAY)


def test_csv_formula_and_exclusive_write(tmp_path, monkeypatch):
    rec = _record()
    rec["fns_company_name"] = "=HYPERLINK(1)"
    report = assemble(_audit(tmp_path, [rec]), today=DAY)
    monkeypatch.chdir(tmp_path)
    save(report, "exports/report.json", "exports/report.csv")
    with (tmp_path / "exports/report.csv").open(encoding="utf-8-sig", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["company_name"].startswith("'=")
    with pytest.raises(ValueError, match="NEW_FILES"):
        save(report, "exports/report.json", "exports/report.csv")
    assert report["crm_writes"] == "NO"
