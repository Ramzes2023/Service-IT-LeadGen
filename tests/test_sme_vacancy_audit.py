"""Strict offline 1C vacancy evidence joins; never infer an outsourcing buyer."""
import csv
from datetime import date
import json
from pathlib import Path

import pytest

from leadpilot.sme_vacancy_audit import audit, save

NOW = date(2026, 10, 31)
FIRST = "7724850252"
SECOND = "7718620740"
OTHER = "7702319337"
FNS = "https://rmsp.nalog.ru/search.html?mode=inn-list"


def setup(tmp_path, *, duplicate=True):
    fns = tmp_path / "fns.json"
    businesses = [
        {"inn": inn, "company_name": "Учебный поставщик " + inn,
         "fns_category": "MICRO", "region": "Москва",
         "okved_public": "46.49", "fns_source": FNS,
         "fns_snapshot": NOW.isoformat(),
         "business_size_verified_from_export": True,
         "qualification": "SIZE_ONLY_RESEARCH", "demand_for_1c": "NOT_CHECKED",
         "lead_score": None, "crm_ready": False, "outbound_authorized": False}
        for inn in (FIRST, SECOND)]
    fns.write_text(json.dumps({
        "mode": "SME_FIRST_FNS_SIZE_ONLY", "snapshot": NOW.isoformat(),
        "counts": {"size_eligible_for_research": 2, "crm_ready": 0},
        "crm_writes": "NO", "outbound_messages": "NO", "companies": businesses
    }, ensure_ascii=False), encoding="utf8")

    def signal(inn, name, region="Москва", day="2026-10-30", url="https://trudvsem.ru/vacancy/abc"):
        return {"source_name": "trudvsem-open-vacancies",
                "source_url": url, "name": name, "inn": inn, "region": region,
                "signal_type": "1c_vacancy", "signal_date": day,
                "evidence": "Опубликована вакансия: Программист 1С"}
    folders = []
    for page in (0, 1):
        folder = tmp_path / f"page_{page}"
        folder.mkdir()
        for code, region in (("77", "Москва"), ("50", "Московская область")):
            rows = []
            if page == 0 and code == "77":
                rows = [signal(FIRST, "Работодатель А"),
                        signal(OTHER, "Не из ФНС")]
            if page == 1 and code == "77" and duplicate:
                rows = [signal(FIRST, "Работодатель А")]
            (folder / f"trudvsem_{code}.json").write_text(json.dumps({
                "source": "trudvsem-open-vacancies", "region": region,
                "term": "1С", "offset": page, "fetched": len(rows),
                "valid_signals": len(rows), "signals": rows
            }, ensure_ascii=False), encoding="utf8")
        folders.append(folder)
    return fns, folders


def test_exact_inn_join_and_no_false_outsourcing_claim(tmp_path):
    fns, folders = setup(tmp_path)
    out = audit(fns, folders, [0, 1], today=NOW)
    assert out["counts"]["fns_size_verified_companies"] == 2
    assert out["counts"]["companies_with_1c_vacancy_signal"] == 1
    assert out["counts"]["companies_without_match_in_sample"] == 1
    assert out["counts"]["real_outsourcing_demand_confirmed"] == 0
    assert out["counts"]["crm_ready"] == 0
    assert out["crm_writes"] == "NO"
    items = {x["inn"]: x for x in out["companies"]}
    assert items[FIRST]["review_status"] == "PUBLIC_1C_VACANCY_REVIEW"
    assert items[FIRST]["vacancy_signal_count"] == 1
    assert items[FIRST]["company_identity_needs_manual_check"] is True
    assert items[SECOND]["review_status"] == "NO_MATCH_IN_SAMPLED_VACANCIES"
    assert items[SECOND]["vacancy_signal_count"] == 0
    assert all(x["crm_ready"] is False and x["outbound_authorized"] is False
               and x["lead_score"] is None for x in out["companies"])


def test_no_match_does_not_mean_no_demand(tmp_path):
    fns, folders = setup(tmp_path, duplicate=False)
    data = json.loads(fns.read_text("utf8"))
    data["companies"] = [data["companies"][1]]
    data["counts"]["size_eligible_for_research"] = 1
    fns.write_text(json.dumps(data, ensure_ascii=False), encoding="utf8")
    out = audit(fns, folders, [0, 1], today=NOW)
    assert out["counts"]["companies_with_1c_vacancy_signal"] == 0
    assert "does not imply no demand" in out["companies"][0]["next_action"]


def test_missing_source_page_is_not_zero_hits(tmp_path):
    fns, folders = setup(tmp_path)
    (folders[1] / "trudvsem_50.json").unlink()
    with pytest.raises(ValueError, match="Missing"):
        audit(fns, folders, [0, 1], today=NOW)


def test_wrong_offset_fails_closed(tmp_path):
    fns, folders = setup(tmp_path)
    data_path = folders[1] / "trudvsem_77.json"
    data = json.loads(data_path.read_text("utf8"))
    data["offset"] = 50
    data_path.write_text(json.dumps(data), encoding="utf8")
    with pytest.raises(ValueError, match="metadata"):
        audit(fns, folders, [0, 1], today=NOW)


def test_duplicate_official_inn_rejected(tmp_path):
    fns, folders = setup(tmp_path)
    doc = json.loads(fns.read_text("utf8"))
    doc["companies"][1]["inn"] = FIRST
    fns.write_text(json.dumps(doc), encoding="utf8")
    with pytest.raises(ValueError, match="Unverified"):
        audit(fns, folders, [0, 1], today=NOW)


def test_unapproved_size_or_preapproved_lead_rejected(tmp_path):
    fns, folders = setup(tmp_path)
    doc = json.loads(fns.read_text("utf8"))
    doc["companies"][0]["fns_category"] = "MEDIUM"
    fns.write_text(json.dumps(doc), encoding="utf8")
    with pytest.raises(ValueError, match="Unverified"):
        audit(fns, folders, [0, 1], today=NOW)
    doc["companies"][0]["fns_category"] = "MICRO"
    doc["companies"][0]["crm_ready"] = True
    fns.write_text(json.dumps(doc), encoding="utf8")
    with pytest.raises(ValueError, match="Unverified"):
        audit(fns, folders, [0, 1], today=NOW)


def test_csv_is_readable_and_does_not_overwrite(tmp_path, monkeypatch):
    fns, folders = setup(tmp_path)
    report = audit(fns, folders, [0, 1], today=NOW)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="exports"):
        save(report, "outside/result.json", "exports/result.csv")
    first = save(report, "exports/result.json", "exports/result.csv")
    assert first["company_count"] == 2
    with Path("exports/result.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 2
    assert all(r["crm_ready"] == "NO" for r in rows)
    with pytest.raises(ValueError, match="exports"):
        save(report, "exports/result.json", "exports/result.csv")


def test_csv_formula_injection_protection(tmp_path, monkeypatch):
    fns, folders = setup(tmp_path)
    doc = json.loads(fns.read_text("utf8"))
    doc["companies"][0]["company_name"] = "=SUM(1,2)"
    fns.write_text(json.dumps(doc), encoding="utf8")
    result = audit(fns, folders, [0, 1], today=NOW)
    monkeypatch.chdir(tmp_path)
    save(result, "exports/report.json", "exports/report.csv")
    with Path("exports/report.csv").open(encoding="utf-8-sig", newline="") as stream:
        records = list(csv.DictReader(stream))
    modified = next(row for row in records if row["inn"] == FIRST)
    assert modified["company_name"].startswith("'=")


@pytest.mark.parametrize("offsets", [[0, 0], [0, 50], [True, 1]])
def test_reject_invalid_page_indices(tmp_path, offsets):
    fns, folders = setup(tmp_path)
    with pytest.raises(ValueError, match="unique page indices"):
        audit(fns, folders, offsets, today=NOW)


def test_fns_snapshot_must_be_fresh(tmp_path):
    fns, folders = setup(tmp_path)
    with pytest.raises(ValueError, match="stale"):
        audit(fns, folders, [0, 1], today=date(2027, 1, 10))


def test_public_source_http_links_do_not_qualify(tmp_path):
    fns, folders = setup(tmp_path)
    path = folders[0] / "trudvsem_77.json"
    data = json.loads(path.read_text("utf8"))
    data["signals"][0]["source_url"] = "http://trudvsem.ru/vacancy/abc"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf8")
    result = audit(fns, folders, [0, 1], today=NOW)
    assert result["counts"]["companies_with_1c_vacancy_signal"] == 1
    assert result["counts"]["untrusted_or_http_url"] == 1
