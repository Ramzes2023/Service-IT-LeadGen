"""Synthetic, offline audit of EIS buyer identities against FNS company cohort.

Mocks FNS reader intentionally: never claims an actual firm has live 1C demand.
"""
import hashlib
import json
from datetime import date

import pytest

from leadpilot import eis_buyer_review as b

TODAY = date(2026, 10, 31)
URL = "https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber=01234567890123456789"
INN = "7724850252"
SCOPE = "Оказание услуг по сопровождению конфигурации 1С:Предприятие"


def fixtures(tmp_path, monkeypatch):
    record = {
        "source_name": b.SOURCE_NAME, "source_url": URL,
        "signal_id": hashlib.sha256(URL.encode()).hexdigest()[:24],
        "title": SCOPE, "published_on": "2026-10-30",
        "buyer_inn": None, "crm_ready": False,
        "outbound_authorized": False, "lead_score": None,
    }
    source = {
        "mode": "EIS_OFFICIAL_RSS_RESEARCH_ONLY",
        "source": b.SOURCE_NAME, "checked_on": TODAY.isoformat(),
        "counts": {"candidates_for_identity_review": 1,
                   "crm_ready": 0, "qualified_small_business_leads": 0},
        "items": [record], "crm_writes": "NO", "outbound_messages": "NO",
    }
    operator = {
        "mode": "EIS_MANUAL_BUYER_FACTS",
        "items": [{
            "source_url": URL, "buyer_inn": INN, "buyer_region": "Москва",
            "observed_on": TODAY.isoformat(), "procurement_status": "ACCEPTING",
            "deadline_on": "2026-11-05", "scope_status": "EXTERNAL_1C_SERVICE",
            "buyer_identity_note": "Синтетическая проверка примера покупателя",
            "review_state": "PENDING",
        }],
    }
    eis = tmp_path / "eis.json"
    facts = tmp_path / "facts.json"
    fns = tmp_path / "fns.json"
    eis.write_text(json.dumps(source, ensure_ascii=False), encoding="utf8")
    facts.write_text(json.dumps(operator, ensure_ascii=False), encoding="utf8")
    fns.write_text("{}", encoding="utf8")
    monkeypatch.setattr(b, "_read_fns_candidates", lambda path, now: ({
        INN: {"company_name": "Синтетическая организация",
              "fns_category": "MICRO", "region": "Москва"}}, TODAY.isoformat()))
    return eis, fns, facts


def _edit(path, callback):
    obj = json.loads(path.read_text(encoding="utf8"))
    callback(obj)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf8")


def test_matching_scope_has_research_candidate_but_no_crm(tmp_path, monkeypatch):
    eis, fns, facts = fixtures(tmp_path, monkeypatch)
    result = b.review(eis, fns, facts, today=TODAY)
    assert result["counts"]["manager_research_candidates"] == 1
    assert result["counts"]["qualified_leads"] == 0
    item = result["items"][0]
    assert item["fns_size"] == "MICRO"
    assert item["source_verified_live"] is False
    assert item["status"] == "MANAGER_RESEARCH_CANDIDATE_NOT_APPROVED"
    assert "CRM_DUPLICATE_PENDING" in item["holds"]
    assert item["crm_ready"] is False and item["outbound_authorized"] is False
    assert result["crm_writes"] == "NO"


def test_no_manual_identity_never_creates_lead(tmp_path, monkeypatch):
    eis, fns, facts = fixtures(tmp_path, monkeypatch)
    _edit(facts, lambda x: x.update(items=[]))
    r = b.review(eis, fns, facts, today=TODAY)
    assert r["counts"]["operator_identity_pending"] == 1
    assert r["items"][0]["status"] == "BUYER_IDENTIFICATION_PENDING"
    assert r["counts"]["manager_research_candidates"] == 0


@pytest.mark.parametrize("field,value", [
    ("procurement_status", "CANCELLED"),
    ("procurement_status", "UNKNOWN"),
    ("deadline_on", "2026-10-30"),
    ("deadline_on", None),
    ("scope_status", "UNCERTAIN"),
    ("scope_status", "OTHER_SERVICE"),
    ("buyer_region", "Московская область"),
    ("buyer_region", "Санкт-Петербург"),
])
def test_ineligible_is_held(tmp_path, monkeypatch, field, value):
    eis, fns, facts = fixtures(tmp_path, monkeypatch)
    _edit(facts, lambda x: x["items"][0].update({field: value}))
    r = b.review(eis, fns, facts, today=TODAY)
    assert r["counts"]["manager_research_candidates"] == 0
    assert r["items"][0]["crm_ready"] is False


def test_buyer_inn_outside_fns_cohort_not_sme_lead(tmp_path, monkeypatch):
    eis, fns, facts = fixtures(tmp_path, monkeypatch)
    monkeypatch.setattr(b, "_read_fns_candidates", lambda path, now: ({}, TODAY.isoformat()))
    r = b.review(eis, fns, facts, today=TODAY)
    assert r["items"][0]["fns_size"] == "UNVERIFIED"
    assert "BUYER_NOT_IN_VERIFIED_FNS_SMALL_BUSINESS_COHORT" in r["items"][0]["holds"]


@pytest.mark.parametrize("field,value", [
    ("review_state", "APPROVED"),
    ("buyer_inn", "0000000000"),
    ("observed_on", "2026-10-01"),
    ("scope_status", "APPROVED"),
])
def test_operator_cannot_import_approval_or_fake_inn(tmp_path, monkeypatch, field, value):
    eis, fns, facts = fixtures(tmp_path, monkeypatch)
    _edit(facts, lambda x: x["items"][0].update({field: value}))
    with pytest.raises(ValueError, match="EIS_"):
        b.review(eis, fns, facts, today=TODAY)


def test_operator_cannot_add_unrelated_notice(tmp_path, monkeypatch):
    eis, fns, facts = fixtures(tmp_path, monkeypatch)
    _edit(facts, lambda x: x["items"][0].update(
        source_url="https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber=99999999999999999"))
    with pytest.raises(ValueError, match="UNKNOWN"):
        b.review(eis, fns, facts, today=TODAY)


def test_preapproved_eis_input_rejected(tmp_path, monkeypatch):
    eis, fns, facts = fixtures(tmp_path, monkeypatch)
    _edit(eis, lambda x: x["items"][0].update(crm_ready=True))
    with pytest.raises(ValueError, match="PREAPPROVED"):
        b.review(eis, fns, facts, today=TODAY)


def test_duplicate_manual_url_rejected(tmp_path, monkeypatch):
    eis, fns, facts = fixtures(tmp_path, monkeypatch)
    _edit(facts, lambda x: x["items"].append(dict(x["items"][0])))
    with pytest.raises(ValueError, match="DUPLICATE"):
        b.review(eis, fns, facts, today=TODAY)


def test_stale_eis_snapshot_rejected(tmp_path, monkeypatch):
    eis, fns, facts = fixtures(tmp_path, monkeypatch)
    _edit(eis, lambda x: x.update(checked_on="2026-09-01"))
    with pytest.raises(ValueError, match="STALE"):
        b.review(eis, fns, facts, today=TODAY)


def test_safe_save_under_exports_no_overwrite(tmp_path, monkeypatch):
    eis, fns, facts = fixtures(tmp_path, monkeypatch)
    r = b.review(eis, fns, facts, today=TODAY)
    monkeypatch.chdir(tmp_path)
    path = b.save(r, "exports/eis_qualified_review.json")
    assert json.loads((tmp_path / "exports/eis_qualified_review.json").read_text(encoding="utf8"))["counts"]["crm_ready"] == 0
    assert path.endswith("exports/eis_qualified_review.json")
    with pytest.raises(ValueError, match="EXPORTS"):
        b.save(r, "exports/eis_qualified_review.json")
    with pytest.raises(ValueError, match="EXPORTS"):
        b.save(r, "other/report.json")
