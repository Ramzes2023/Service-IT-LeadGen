"""New employer INNs are research only until independently checked in FNS."""
from datetime import date
import json
from pathlib import Path

import httpx
import pytest

from leadpilot.new_1c_fns_precheck import plan, save
from leadpilot.sources import scan

TODAY = date(2026, 10, 31)
OLD = "7724850252"
NEW = "7701234560"
ANOTHER = "5003079216"
FNS_URL = "https://rmsp.nalog.ru/search.html?mode=inn-list"


def signal(inn, region, *, kind="1c_vacancy", date_str="2026-10-29", link=None):
    return {
        "source_name": "trudvsem-open-vacancies", "source_url":
            link or f"https://trudvsem.ru/vacancy/{inn}",
        "name": "Тестовый работодатель", "region": region,
        "signal_type": kind, "evidence": "Опубликована вакансия: Программист 1С",
        "signal_date": date_str, "inn": inn,
        "phone": "+79991234567", "email": "hidden@example.org"
    }


def fixtures(tmp_path, *, only_old=False):
    folder = tmp_path / "source_exports"
    folder.mkdir()
    first = [signal(OLD, "Москва")]
    if not only_old:
        first.append(signal(NEW, "Москва"))
    second = [] if only_old else [
        signal(NEW, "Московская область", date_str="2026-10-30",
               link=f"https://trudvsem.ru/vacancy/{NEW}/new"),
        signal(ANOTHER, "Московская область", kind="it_vacancy")]
    for region, name, records in (("Москва", "77", first),
                                  ("Московская область", "50", second)):
        (folder / f"trudvsem_{name}.json").write_text(json.dumps(
            {"source": "trudvsem-open-vacancies", "region": region, "signals": records},
            ensure_ascii=False), encoding="utf-8")
    prior = tmp_path / "fns.json"
    prior.write_text(json.dumps({
        "mode": "FNS_OFFICIAL_GUI_RECONCILIATION",
        "snapshot": TODAY.isoformat(), "crm_writes": "NO",
        "outreach": "NOT_AUTHORIZED",
        "counts": {"total": 1, "small": 1, "micro": 0, "medium": 0,
                   "not_sme": 0, "not_found": 0, "crm_ready": 0},
        "items": [{"inn": OLD, "source": FNS_URL, "snapshot": TODAY.isoformat(),
                   "fns_status": "SMALL", "crm_ready": False,
                   "outbound_authorized": False}]
    }), encoding="utf-8")
    return folder, prior


def test_new_1c_inn_selected_once_even_with_multiple_vacancies(tmp_path):
    folder, prior = fixtures(tmp_path)
    before = prior.read_bytes()
    result = plan(folder, prior, today=TODAY)
    assert result["mode"] == "NEW_1C_FNS_PRECHECK_ONLY"
    assert result["counts"]["new_unique_inns"] == 1
    assert result["counts"]["selected_for_fns"] == 1
    assert result["counts"]["already_checked_fns"] == 1
    assert result["counts"]["not_1c"] == 1
    one = result["candidates"][0]
    assert one["inn"] == NEW and one["signal_date"] == "2026-10-30"
    assert "email" not in one and "phone" not in one
    assert one["business_size"] == "UNVERIFIED"
    assert one["crm_ready"] is False
    assert result["crm_writes"] == "NO"
    assert prior.read_bytes() == before


def test_no_new_inns_creates_no_false_fns_export(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    folder, prior = fixtures(tmp_path, only_old=True)
    result = plan(folder, prior, today=TODAY)
    status = save(result, "exports/batch.json", "exports/inns.txt")
    assert status["saved"] is False and status["reason"] == "NO_NEW_INNS"
    assert not Path("exports/batch.json").exists()


def test_saved_batch_contains_only_inns_and_never_overwrites(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    folder, prior = fixtures(tmp_path)
    report = plan(folder, prior, today=TODAY)
    stored = save(report, "exports/new.json", "exports/inn.txt")
    assert stored["saved"] is True
    assert Path("exports/inn.txt").read_text(encoding="utf-8") == NEW + "\n"
    with pytest.raises(ValueError):
        save(report, "exports/new.json", "exports/inn.txt")


def test_output_outside_exports_rejected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    folder, prior = fixtures(tmp_path)
    report = plan(folder, prior, today=TODAY)
    with pytest.raises(ValueError):
        save(report, "data/out.json", "exports/inn.txt")


def test_stale_fns_refused(tmp_path):
    folder, prior = fixtures(tmp_path)
    with pytest.raises(ValueError, match="stale"):
        plan(folder, prior, today=date(2027, 1, 5))


def test_tampered_existing_fns_counter_refused(tmp_path):
    folder, prior = fixtures(tmp_path)
    report = json.loads(prior.read_text("utf8"))
    report["counts"]["small"] = 99
    prior.write_text(json.dumps(report), encoding="utf8")
    with pytest.raises(ValueError, match="counters"):
        plan(folder, prior, today=TODAY)


def test_stale_or_http_signal_never_selected(tmp_path):
    folder, prior = fixtures(tmp_path)
    p = folder / "trudvsem_77.json"
    doc = json.loads(p.read_text("utf8"))
    doc["signals"][1]["signal_date"] = "2026-09-01"
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf8")
    p = folder / "trudvsem_50.json"
    doc = json.loads(p.read_text("utf8"))
    doc["signals"][0]["source_url"] = "http://trudvsem.ru/vacancy/123"
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf8")
    result = plan(folder, prior, today=TODAY)
    assert result["counts"]["selected_for_fns"] == 0
    assert result["counts"]["stale_or_untrusted_source"] == 2


@pytest.mark.parametrize("offset", [-1, 201, 1.5, True])
def test_source_scan_rejects_invalid_offset(offset):
    with pytest.raises(ValueError, match="Offset"):
        scan(offset=offset)


def test_source_scan_sends_requested_offset_to_documented_api():
    def handler(request):
        assert request.url.params["offset"] == "50"
        assert request.url.params["limit"] == "50"
        return httpx.Response(200, json={"status": "200",
            "results": {"vacancies": []}})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        result = scan(region="77", term="1С", limit=50, offset=50, client=client)
        assert result["offset"] == 50 and result["fetched"] == 0
    finally:
        client.close()


def test_missing_region_snapshot_is_not_silently_treated_as_no_new(tmp_path):
    folder, prior = fixtures(tmp_path)
    (folder / "trudvsem_50.json").unlink()
    with pytest.raises(ValueError, match="Missing, corrupted"):
        plan(folder, prior, today=TODAY)
