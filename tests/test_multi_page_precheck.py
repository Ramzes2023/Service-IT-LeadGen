"""Offline multi-page aggregation never produces CRM-ready leads."""
from datetime import date
import json
from pathlib import Path
import pytest

from leadpilot.multi_page_precheck import combine_pages
from leadpilot.new_1c_fns_precheck import save

NOW = date(2026, 10, 31)
OLD = "7724850252"
NEW = "7701234560"
THIRD = "7702319337"
FNS_URL = "https://rmsp.nalog.ru/search.html?mode=inn-list"


def sig(inn, region, name="Тестовая фирма", day="2026-10-30", suffix="a"):
    return {"source_name":"trudvsem-open-vacancies",
            "source_url":f"https://trudvsem.ru/vacancy/{inn}/{suffix}",
            "name":name, "inn":inn, "region":region, "signal_type":"1c_vacancy",
            "signal_date":day, "evidence":"Опубликована вакансия: Программист 1С"}


def pages(tmp_path, *, second=True):
    prior=tmp_path/"fns.json"
    prior.write_text(json.dumps({
        "mode":"FNS_OFFICIAL_GUI_RECONCILIATION",
        "snapshot":NOW.isoformat(), "crm_writes":"NO", "outreach":"NOT_AUTHORIZED",
        "counts":{"total":1,"found":1,"not_found":0,"micro":0,"small":1,
                  "medium":0,"not_sme":0,"crm_ready":0},
        "items":[{"inn":OLD,"fns_status":"SMALL",
                  "source":FNS_URL,"snapshot":NOW.isoformat(),
                  "crm_ready":False,"outbound_authorized":False}]
    }),encoding="utf8")
    directories=[]
    for offset in ([0, 50] if second else [0]):
        folder=tmp_path/f"page_{offset}"
        folder.mkdir()
        for code,region in (("77","Москва"),("50","Московская область")):
            if offset==0 and code=="77":
                entries=[sig(OLD,region),sig(NEW,region,suffix="1",day="2026-10-29")]
            elif offset==50 and code=="50":
                entries=[sig(NEW,region,suffix="2"),sig(THIRD,region)]
            else:
                entries=[]
            (folder/f"trudvsem_{code}.json").write_text(json.dumps({
                "source":"trudvsem-open-vacancies","region":region,
                "term":"1С","offset":offset,
                "fetched":len(entries),"valid_signals":len(entries),
                "signals":entries
            },ensure_ascii=False),encoding="utf8")
        directories.append(folder)
    return directories,prior


def test_two_pages_join_and_dedupe_with_newest_date(tmp_path):
    folders, fns = pages(tmp_path)
    old=fns.read_bytes()
    r=combine_pages(folders,[0,50],fns,today=NOW)
    assert r["counts"]["pages_checked"]==2
    assert r["counts"]["unique_new_inns"]==2
    assert r["counts"]["selected_for_fns"]==2
    assert r["counts"]["duplicate_employer_between_pages"]==1
    assert r["counts"]["already_checked_fns"]==1
    assert r["crm_writes"]=="NO"
    assert all(c["crm_ready"] is False and c["business_size"]=="UNVERIFIED"
               for c in r["candidates"])
    candidate=next(c for c in r["candidates"] if c["inn"]==NEW)
    assert candidate["signal_url"].endswith("/2")
    assert candidate["source_offsets"]==[0,50]
    assert "phone" not in candidate and "email" not in candidate
    assert fns.read_bytes()==old


def test_metadata_offset_mismatch_is_fatal(tmp_path):
    folders,fns=pages(tmp_path)
    path=folders[1]/"trudvsem_50.json"
    data=json.loads(path.read_text("utf8"))
    data["offset"]=0
    path.write_text(json.dumps(data),encoding="utf8")
    with pytest.raises(ValueError,match="metadata"):
        combine_pages(folders,[0,50],fns,today=NOW)


def test_missing_regional_snapshot_is_fatal(tmp_path):
    folders,fns=pages(tmp_path)
    (folders[0]/"trudvsem_77.json").unlink()
    with pytest.raises(ValueError,match="Missing"):
        combine_pages(folders,[0,50],fns,today=NOW)


def test_conflicting_company_identity_is_on_hold(tmp_path):
    folders,fns=pages(tmp_path)
    path=folders[1]/"trudvsem_50.json"
    doc=json.loads(path.read_text("utf8"))
    doc["signals"][0]["name"]="Совершенно другое юридическое лицо"
    path.write_text(json.dumps(doc,ensure_ascii=False),encoding="utf8")
    result=combine_pages(folders,[0,50],fns,today=NOW)
    assert result["identity_conflicts_hold"]==[NEW]
    assert result["counts"]["held_identity_conflicts"]==1
    assert [c["inn"] for c in result["candidates"]]==[THIRD]


def test_only_old_records_return_no_new(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    folders,fns=pages(tmp_path,second=False)
    p=folders[0]/"trudvsem_77.json"
    x=json.loads(p.read_text("utf8"))
    x["signals"]=x["signals"][:1]
    x["fetched"]=1
    x["valid_signals"]=1
    p.write_text(json.dumps(x,ensure_ascii=False),encoding="utf8")
    report=combine_pages(folders,[0],fns,today=NOW)
    assert report["counts"]["selected_for_fns"]==0
    out=save(report,"exports/pilot.json","exports/inn.txt")
    assert out["reason"]=="NO_NEW_INNS"
    assert not Path("exports/inn.txt").exists()


def test_save_writes_legal_inns_only_without_overwrite(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    folders,fns=pages(tmp_path)
    r=combine_pages(folders,[0,50],fns,today=NOW)
    out=save(r,"exports/next_fns.json","exports/next_inns.txt")
    assert out["saved"] is True
    assert set(Path("exports/next_inns.txt").read_text("utf8").splitlines())=={NEW,THIRD}
    with pytest.raises(ValueError):
        save(r,"exports/next_fns.json","exports/next_inns.txt")


@pytest.mark.parametrize("offsets", [[0,0], [0,25], [0,201], [0,True]])
def test_reject_invalid_offset_sets(tmp_path,offsets):
    folders,fns=pages(tmp_path)
    with pytest.raises(ValueError,match="offsets"):
        combine_pages(folders,offsets,fns,today=NOW)


def test_count_exceeding_50_is_rejected(tmp_path):
    folders,fns=pages(tmp_path)
    path=folders[0]/"trudvsem_77.json"
    obj=json.loads(path.read_text("utf8"))
    obj["fetched"]=51
    path.write_text(json.dumps(obj),encoding="utf8")
    with pytest.raises(ValueError,match="metadata"):
        combine_pages(folders,[0,50],fns,today=NOW)


def test_single_offset_page_supported(tmp_path):
    folders,fns=pages(tmp_path,second=False)
    r=combine_pages(folders,[0],fns,today=NOW)
    assert r["counts"]["pages_checked"]==1
    assert r["counts"]["selected_for_fns"]==1
