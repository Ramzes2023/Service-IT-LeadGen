from datetime import date
import json
from pathlib import Path

import pytest
from leadpilot.core import initialize, ingest, qualify, suppress
from leadpilot.mvp_handoff import collect_pilot_candidates, export_review

NOW = date(2026, 10, 31)


def signal(inn="7700000001", region="Москва", kind="1c_vacancy", **kwargs):
    row = {"inn": inn, "name": "Синтетическая компания", "region": region,
           "industry": "торговля", "source_name": "synthetic-fixture",
           "source_url": f"https://example.invalid/vacancy/{inn}",
           "signal_type": kind, "evidence": "Опубликована вакансия: программист 1С",
           "signal_date": "2026-10-29"}
    row.update(kwargs)
    return row


def db_with(tmp_path, *rows):
    db = tmp_path / "research.sqlite3"
    initialize(db)
    for r in rows:
        ingest(db, r)
    qualify(db, today=NOW)
    return db


def test_1c_moscow_signal_is_not_yet_crm_ready(tmp_path):
    db = db_with(tmp_path, signal())
    before = db.read_bytes()
    r = collect_pilot_candidates(db, today=NOW)
    assert r["crm_ready"] == 0 and r["crm_writes"] == "NO"
    assert len(r["candidates"]) == 1
    c = r["candidates"][0]
    assert c["status"] == "RESEARCH_ONLY_NOT_CRM_READY"
    assert c["lead_score"] >= 80 and c["score_version"]
    assert c["crm_lifecycle"]["before_confirmed_interest"] == "CRM_LEAD"
    assert "crm_duplicate_check_pending" in c["missing_checks"]
    assert "small_business_size_unverified" in c["missing_checks"]
    assert "corporate_contact_enrichment_pending" in c["missing_checks"]
    assert c["outbound_authorized"] is False
    assert db.read_bytes() == before


def test_only_1c_moscow_and_moscow_oblast_in_first_pilot(tmp_path):
    db = db_with(tmp_path,
                 signal("7700000001", region="Московская область"),
                 signal("0600000001", region="Ингушетия"),
                 signal("7700000003", kind="it_vacancy"))
    r = collect_pilot_candidates(db, today=NOW)
    assert [x["inn"] for x in r["candidates"]] == ["7700000001"]


def test_outdated_vacancy_is_not_qualified(tmp_path):
    db = db_with(tmp_path, signal(signal_date="2026-01-01"))
    assert collect_pilot_candidates(db, today=NOW)["candidates"] == []


def test_existing_customer_and_stoplist_never_appear(tmp_path):
    db = db_with(tmp_path, signal(), signal("7700000002", existing_customer=True))
    suppress(db, "7700000001", "new_customer", "requested no prospecting")
    qualify(db, today=NOW)
    assert collect_pilot_candidates(db, today=NOW)["candidates"] == []


def test_untrusted_source_domain_rejected(tmp_path):
    db = db_with(tmp_path, signal(source_url="https://trudvsem.ru.evil.example/vacancy/123"))
    assert collect_pilot_candidates(db, today=NOW)["candidates"] == []


def test_contacts_not_exposed_without_verified_legal_basis(tmp_path):
    db = db_with(tmp_path, signal(phone="+79991234567"))
    c = collect_pilot_candidates(db, today=NOW)["candidates"][0]
    assert "phone" not in c and "email" not in c
    assert "contact_provenance_and_permission_unverified" in c["missing_checks"]


def test_export_only_to_ignored_exports_dir(tmp_path, monkeypatch):
    db = db_with(tmp_path, signal())
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError):
        export_review(db, "elsewhere/preview.json", today=NOW)
    result = export_review(db, "exports/review.json", today=NOW)
    assert result["crm_ready"] == 0
    assert len(json.loads(Path("exports/review.json").read_text(encoding="utf8"))["candidates"]) == 1


def test_bad_limit_rejected(tmp_path):
    db = db_with(tmp_path, signal())
    for value in (0, 101, True):
        with pytest.raises(ValueError):
            collect_pilot_candidates(db, today=NOW, limit=value)
