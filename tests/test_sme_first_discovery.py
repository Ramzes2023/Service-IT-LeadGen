"""Synthetic, offline FNS SME-first selection checks."""
from datetime import date
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from leadpilot.sme_first_discovery import prepare, save

NOW = date(2026, 10, 31)
OLD = "7724850252"
RECENT = "7718620740"
SMALL = "5003079216"
MICRO = "7702319337"
MEDIUM = "5005033619"


def fixture(tmp_path, rows=None):
    old, recent = tmp_path / "old.json", tmp_path / "recent.json"
    old.write_text(json.dumps({"mode": "FNS_OFFICIAL_GUI_RECONCILIATION",
        "counts": {"total": 1, "crm_ready": 0}, "crm_writes": "NO",
        "items": [{"inn": OLD, "fns_status": "SMALL",
                   "crm_ready": False, "outbound_authorized": False}]}))
    recent.write_text(json.dumps({"mode": "FNS_NEW_THREE_OFFLINE_RECONCILIATION",
        "counts": {"total": 1, "crm_ready": 0}, "crm_writes": "NO",
        "companies": [{"inn": RECENT, "fns_status": "NOT_FOUND",
                       "crm_ready": False, "outbound_authorized": False}]}))
    rows = rows if rows is not None else [
        (SMALL, "Малое предприятие", "Юридическое лицо", "50 - Московская область"),
        (MICRO, "Микропредприятие", "Юридическое лицо", "77 - г.Москва"),
        (MEDIUM, "Среднее предприятие", "Юридическое лицо", "77 - г.Москва"),
        (OLD, "Малое предприятие", "Юридическое лицо", "77 - г.Москва"),
        (RECENT, "Микропредприятие", "Юридическое лицо", "77 - г.Москва")]
    from xml.sax.saxutils import escape
    def c(col, row, value):
        return '<c r="%s%d" t="inlineStr"><is><t>%s</t></is></c>' % (
            col, row, escape(str(value)))
    headers = {"B":"Наименование / ФИО","C":"Тип субъекта","D":"Категория",
               "F":"ИНН","G":"Основной вид деятельности","H":"Регион",
               "M":"Дата включения в реестр","N":"Дата исключения из реестра"}
    title="Единый реестр субъектов малого и среднего предпринимательства по состоянию на 31.10.2026"
    data='<row r="1">'+c("A",1,title)+'</row>'
    data+='<row r="3">'+''.join(c(k,3,v) for k,v in headers.items())+'</row>'
    for r,(inn,category,kind,region) in enumerate(rows,4):
        data+=('<row r="%d">'%r+c("B",r,"Учебная компания "+inn)+
               c("C",r,kind)+c("D",r,category)+c("F",r,inn)+
               c("G",r,"47.19 Торговля розничная")+c("H",r,region)+
               c("M",r,"10.08.2020")+'</row>')
    xml='<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'+data+'</sheetData></worksheet>'
    xlsx=tmp_path/"fns.xlsx"
    with ZipFile(xlsx,"w") as z:
        z.writestr("xl/worksheets/sheet1.xml",xml.encode("utf8"))
    return xlsx,old,recent


def test_only_new_micro_and_small_selected(tmp_path):
    args=fixture(tmp_path)
    report=prepare(*args,today=NOW)
    assert {r["inn"] for r in report["companies"]}=={SMALL,MICRO}
    assert report["counts"]["already_checked_18"]==2
    assert report["counts"]["not_micro_or_small"]==1
    assert report["counts"]["size_eligible_for_research"]==2
    assert report["crm_writes"]=="NO"
    for item in report["companies"]:
        assert item["lead_score"] is None
        assert item["demand_for_1c"]=="NOT_CHECKED"
        assert item["crm_ready"] is False
        assert item["outbound_authorized"] is False
        assert "phone" not in item and "email" not in item


def test_outside_region_and_non_legal_entities_excluded(tmp_path):
    rows=[(SMALL,"Малое предприятие","Юридическое лицо","78 - Санкт-Петербург"),
          (MICRO,"Микропредприятие","Индивидуальный предприниматель","77 - г.Москва")]
    report=prepare(*fixture(tmp_path,rows),today=NOW)
    assert report["counts"]["size_eligible_for_research"]==0
    assert report["counts"]["outside_pilot_region_or_invalid"]==1
    assert report["counts"]["individual_or_unknown_subject_skipped"]==1


def test_limit_is_fail_closed(tmp_path):
    with pytest.raises(ValueError,match="narrow FNS filters"):
        prepare(*fixture(tmp_path),today=NOW,max_results=1)


def test_preapproved_old_company_is_rejected(tmp_path):
    x,old,new=fixture(tmp_path)
    data=json.loads(old.read_text())
    data["items"][0]["crm_ready"]=True
    old.write_text(json.dumps(data))
    with pytest.raises(ValueError,match="approved"):
        prepare(x,old,new,today=NOW)


def test_overlapping_historical_batches_rejected(tmp_path):
    x,old,new=fixture(tmp_path)
    data=json.loads(new.read_text())
    data["companies"][0]["inn"]=OLD
    new.write_text(json.dumps(data))
    with pytest.raises(ValueError,match="overlap"):
        prepare(x,old,new,today=NOW)


def test_future_and_stale_snapshots_rejected(tmp_path):
    args=fixture(tmp_path)
    with pytest.raises(ValueError):
        prepare(*args,today=date(2026,10,30))
    with pytest.raises(ValueError,match="stale"):
        prepare(*args,today=date(2027,1,10))


def test_no_overwrite_and_only_exports(tmp_path,monkeypatch):
    args=fixture(tmp_path)
    report=prepare(*args,today=NOW)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError,match="exports"):
        save(report,"wrong/out.json","exports/inn.txt")
    status=save(report,"exports/sme.json","exports/sme.txt")
    assert status["saved"] is True
    assert set(Path("exports/sme.txt").read_text().splitlines())=={SMALL,MICRO}
    with pytest.raises(ValueError):
        save(report,"exports/sme.json","exports/sme.txt")


def test_zero_eligible_is_not_a_fake_export(tmp_path,monkeypatch):
    args=fixture(tmp_path,[(OLD,"Малое предприятие","Юридическое лицо","77 - г.Москва")])
    result=prepare(*args,today=NOW)
    monkeypatch.chdir(tmp_path)
    status=save(result,"exports/no.json","exports/no.txt")
    assert status["saved"] is False
    assert not Path("exports/no.txt").exists()
