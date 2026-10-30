from datetime import date
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
from leadpilot.core import (initialize, ingest, qualify, get_leads, score_company,
                            normalize_phone, normalize_domain, suppress, metrics, SCORE_VERSION)
from leadpilot.bitrix import export_preview, preview_payload
from leadpilot.finance import calculate_rvp
from leadpilot.ai import classify_authorized_signal


def sample(inn="7700000001", **changes):
    source = {"inn": inn, "name": "ООО Тест", "region": "Москва", "industry": "Розничная торговля",
              "phone": "+79000000001", "email": "x@example.invalid", "domain": "shop.invalid",
              "existing_customer": False, "source_name": "fictional", "source_url": "https://example.invalid/1",
              "signal_type": "1c_vacancy", "evidence": "Учебная вакансия 1С", "signal_date": "2026-10-29"}
    source.update(changes)
    return source


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "leads.sqlite3"
    initialize(p)
    return p


def test_company_upsert_by_inn(db):
    r1 = ingest(db, sample())
    r2 = ingest(db, sample(source_url="https://example.invalid/2"))
    assert r1["new_company"] and not r2["new_company"]
    assert len(get_leads_after_qualify(db)) == 1
    assert len(get_leads(db)[0]["signals"]) == 2


def get_leads_after_qualify(db):
    qualify(db, today="2026-10-30")
    return get_leads(db)


def test_repeat_same_signal_idempotent(db):
    ingest(db, sample()); r = ingest(db, sample())
    assert not r["new_signal"]
    assert len(get_leads_after_qualify(db)[0]["signals"]) == 1


def test_same_phone_different_inn_no_merge(db):
    ingest(db, sample())
    ingest(db, sample("7700000002", email="other@example.invalid", domain="other.invalid"))
    out = get_leads_after_qualify(db)
    assert len(out) == 2
    assert any(x["priority"] == "manual_review" for x in out)


def test_same_domain_not_merge(db):
    ingest(db, sample())
    ingest(db, sample("7700000002", phone="+79000000002", email="b@example.invalid"))
    out = get_leads_after_qualify(db)
    assert len(out) == 2
    assert not any(x["manual_review"] for x in out)


def test_phone_8_vs_7_and_domain_normalization():
    assert normalize_phone("8 (900) 123-45-67") == "+79001234567"
    assert normalize_phone("+7 900 1234567") == "+79001234567"
    assert normalize_domain("https://www.Example.Ru/page") == "example.ru"


def test_existing_customer_not_queue(db):
    ingest(db, sample(existing_customer=True))
    out = get_leads_after_qualify(db)
    assert out[0]["priority"] == "existing_customer"
    assert not get_leads(db, only_queue=True)


def test_suppression_all_not_queue(db):
    ingest(db, sample())
    suppress(db, "7700000001", "all", "customer refused")
    assert get_leads_after_qualify(db)[0]["priority"] == "suppressed"
    assert not get_leads(db, only_queue=True)


def test_channel_specific_opt_out_does_not_merge_or_delete(db):
    ingest(db, sample())
    suppress(db, "7700000001", "email", "no email")
    out = get_leads_after_qualify(db)
    assert out[0]["priority"] == "high"
    assert out[0]["suppressed_channels"] == ["email"]
    # A future communication planner must enforce the per-channel restriction.


def test_expired_signal_excluded(db):
    ingest(db, sample(signal_date="2025-01-01"))
    assert get_leads_after_qualify(db)[0]["priority"] == "expired_or_missing_signal"


def test_future_signal_not_considered_fresh(db):
    ingest(db, sample(signal_date="2027-01-01"))
    assert get_leads_after_qualify(db)[0]["priority"] == "expired_or_missing_signal"


def test_scoring_deterministic_versioned(db):
    ingest(db, sample())
    first = get_leads_after_qualify(db)[0]
    second = get_leads_after_qualify(db)[0]
    assert first["score"] == second["score"]
    assert first["score_version"] == second["score_version"] == SCORE_VERSION
    assert first["explanation"]["signal"] == 25


def test_score_region_priority(db):
    ingest(db, sample(region="Московская область"))
    score_mo = get_leads_after_qualify(db)[0]["score"]
    with sqlite3.connect(db) as c:
        c.execute("UPDATE companies SET region='Республика Ингушетия'")
    score_ing = get_leads_after_qualify(db)[0]["score"]
    assert score_mo - score_ing == 10


def test_finance_example_formula_and_validation():
    assert calculate_rvp(180000, software=45000, equipment=65000, direct_labor=10000) == 60000
    with pytest.raises(ValueError):
        calculate_rvp(-1)


def test_crm_preview_no_network(db, tmp_path):
    ingest(db, sample()); get_leads_after_qualify(db)
    target = tmp_path / "preview.json"
    assert export_preview(db, target) == 1
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload[0]["method"] == "crm.item.add"
    assert payload[0]["params"]["entityTypeId"] == 1
    assert payload[0]["mode"] == "preview_not_sent"


def test_unqualified_lead_not_exported(db, tmp_path):
    ingest(db, sample(existing_customer=True)); get_leads_after_qualify(db)
    assert export_preview(db, tmp_path / "preview.json") == 0
    with pytest.raises(ValueError):
        preview_payload(get_leads(db)[0])


def test_ai_off_by_default(monkeypatch):
    monkeypatch.delenv("AI_ENABLE", raising=False)
    with pytest.raises(RuntimeError, match="AI disabled"):
        classify_authorized_signal("TEST")


def test_concurrent_same_inn_single_company(db):
    with ThreadPoolExecutor(max_workers=5) as pool:
        rs = list(pool.map(lambda i: ingest(db, sample(source_url=f"https://example.invalid/{i}")), range(1, 6)))
    assert sum(1 for x in rs if x["new_company"]) == 1
    assert len(get_leads_after_qualify(db)) == 1
    assert len(get_leads(db)[0]["signals"]) == 5


def test_manual_review_not_exported(db, tmp_path):
    ingest(db, sample()); ingest(db, sample("7700000002", email="new@example.invalid"))
    get_leads_after_qualify(db)
    # Both companies are quarantined when the contact is ambiguous.
    assert export_preview(db, tmp_path / "preview.json") == 0


def test_no_secret_keys_in_storage_schema(db):
    with sqlite3.connect(db) as c:
        names = [x[0] for x in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    assert "companies" in names and "signals" in names and "audit_events" in names


def test_no_real_ai_misrepresentation(db):
    ingest(db, sample())
    assert get_leads_after_qualify(db)[0]["ai_status"] == "not_configured"
