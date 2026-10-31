"""New FNS batch must match exactly the three newly researched INNs."""
from datetime import date
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from leadpilot.fns_new_batch_reconcile import reconcile_new, save

TODAY = date(2026, 10, 31)
A, B, C = "7724850252", "7702319337", "5003079216"
FNS = "https://rmsp.nalog.ru/search.html?mode=inn-list"
NAMESPACE = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def inputs(tmp_path):
    precheck = tmp_path / "new_candidates.json"
    cands = [
        {"inn": inn, "employer": "Учебная компания " + inn,
         "region": "Москва", "business_size": "UNVERIFIED",
         "source_offsets": [0, 1],
         "signal_url": "https://trudvsem.ru/vacancy/" + inn,
         "signal_date": TODAY.isoformat(),
         "crm_ready": False, "outbound_authorized": False,
         "qualified_as_customer": False}
        for inn in (A, B, C)]
    precheck.write_text(json.dumps({
        "mode": "NEW_1C_FNS_MULTIPAGE_PRECHECK_ONLY",
        "date": TODAY.isoformat(), "fns_source": FNS,
        "crm_writes": "NO", "outbound_messages": "NO",
        "identity_conflicts_hold": [], "candidates": cands,
        "counts": {"selected_for_fns": 3, "truncated": False, "ready_for_crm": 0}
    }, ensure_ascii=False), encoding="utf-8")
    missing = tmp_path / "inn-not-found.txt"
    missing.write_text(B + "\n" + C + "\n", encoding="utf-8")
    return precheck, missing


def xlsx_export(tmp_path, rows=None):
    import xml.sax.saxutils
    rows = [(A, "Малое предприятие")] if rows is None else rows
    def cell(col, n, text):
        value = xml.sax.saxutils.escape(str(text))
        return f'<c r="{col}{n}" t="inlineStr"><is><t>{value}</t></is></c>'
    head = {"B": "Наименование / ФИО", "D": "Категория", "F": "ИНН",
            "M": "Дата включения в реестр", "N": "Дата исключения из реестра"}
    title = "Единый реестр субъектов малого и среднего предпринимательства по состоянию на 31.10.2026"
    body = '<row r="1">' + cell("A",1,title) + '</row>'
    body += '<row r="3">' + "".join(cell(c,3,t) for c,t in head.items()) + '</row>'
    for n,(inn,category) in enumerate(rows,4):
        body += f'<row r="{n}">' + cell("B",n,"Учебное ООО") + cell("D",n,category) + cell("F",n,inn) + cell("M",n,"10.08.2020") + '</row>'
    xml = f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{NAMESPACE}"><sheetData>{body}</sheetData></worksheet>'
    path = tmp_path / "fns.xlsx"
    with ZipFile(path, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", xml.encode("utf-8"))
    return path


def test_mixed_batch_no_crm_writes(tmp_path):
    precheck,missing = inputs(tmp_path)
    xlsx = xlsx_export(tmp_path)
    before = precheck.read_bytes(), missing.read_bytes(), xlsx.read_bytes()
    out = reconcile_new(precheck, xlsx=xlsx, not_found=missing, today=TODAY)
    assert out["counts"]["total"] == 3
    assert out["counts"]["small"] == 1
    assert out["counts"]["not_found"] == 2
    assert out["counts"]["size_match_only_hold"] == 1
    assert out["counts"]["crm_ready"] == 0
    assert out["crm_writes"] == "NO" and out["outbound_messages"] == "NO"
    assert all(x["crm_ready"] is False and x["outbound_authorized"] is False
               for x in out["companies"])
    assert (precheck.read_bytes(), missing.read_bytes(), xlsx.read_bytes()) == before


def test_none_found_can_be_recorded_without_fake_excel(tmp_path):
    precheck, missing = inputs(tmp_path)
    missing.write_text("\n".join((A, B, C)) + "\n", encoding="utf-8")
    out = reconcile_new(precheck, not_found=missing, checked_on="2026-10-31", today=TODAY)
    assert out["counts"]["found"] == 0
    assert out["counts"]["not_found"] == 3
    assert all(x["decision"] == "SIZE_UNKNOWN_NOT_FOUND" for x in out["companies"])
    assert "not signed" in out["origin"]


def test_all_found_can_be_recorded_without_fake_missing_txt(tmp_path):
    precheck,_ = inputs(tmp_path)
    xlsx = xlsx_export(tmp_path, rows=[
        (A, "Микропредприятие"), (B, "Малое предприятие"),
        (C, "Среднее предприятие")])
    out = reconcile_new(precheck, xlsx=xlsx, today=TODAY)
    assert out["counts"]["found"] == 3
    assert out["counts"]["not_found"] == 0
    assert out["counts"]["micro"] == 1
    assert out["counts"]["medium"] == 1
    assert out["counts"]["crm_ready"] == 0


def test_partial_batch_is_rejected(tmp_path):
    precheck, missing = inputs(tmp_path)
    missing.write_text(C + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="cover exactly"):
        reconcile_new(precheck, xlsx=xlsx_export(tmp_path), not_found=missing, today=TODAY)


def test_found_and_missing_same_inn_is_rejected(tmp_path):
    precheck, missing = inputs(tmp_path)
    missing.write_text(A + "\n" + B + "\n" + C + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="both"):
        reconcile_new(precheck, xlsx=xlsx_export(tmp_path), not_found=missing, today=TODAY)


def test_requires_source_evidence_and_date_for_missing_only(tmp_path):
    precheck, missing = inputs(tmp_path)
    with pytest.raises(ValueError, match="Supply actual"):
        reconcile_new(precheck, today=TODAY)
    with pytest.raises(ValueError, match="checked-on"):
        reconcile_new(precheck, not_found=missing, today=TODAY)


def test_rejects_old_pilot_review_mode(tmp_path):
    precheck, missing = inputs(tmp_path)
    data=json.loads(precheck.read_text("utf8"))
    data["mode"]="PILOT_SCOPE_REVIEW"
    precheck.write_text(json.dumps(data),encoding="utf8")
    with pytest.raises(ValueError, match="multi-page"):
        reconcile_new(precheck, not_found=missing, checked_on="2026-10-31", today=TODAY)


def test_rejects_truncated_or_preauthorized_candidates(tmp_path):
    precheck, missing = inputs(tmp_path)
    data=json.loads(precheck.read_text("utf8"))
    data["counts"]["truncated"]=True
    precheck.write_text(json.dumps(data),encoding="utf8")
    with pytest.raises(ValueError, match="unsafe precheck"):
        reconcile_new(precheck, not_found=missing, checked_on="2026-10-31", today=TODAY)
    data["counts"]["truncated"]=False
    data["candidates"][0]["crm_ready"]=True
    precheck.write_text(json.dumps(data),encoding="utf8")
    with pytest.raises(ValueError, match="pre-approved"):
        reconcile_new(precheck, not_found=missing, checked_on="2026-10-31", today=TODAY)


def test_excess_or_unrelated_official_record_is_rejected(tmp_path):
    precheck, missing = inputs(tmp_path)
    xlsx=xlsx_export(tmp_path, rows=[("9701112870","Малое предприятие")])
    with pytest.raises(ValueError, match="cover exactly"):
        reconcile_new(precheck, xlsx=xlsx, not_found=missing, today=TODAY)


def test_output_guard_and_no_overwrites(tmp_path, monkeypatch):
    precheck, missing = inputs(tmp_path)
    monkeypatch.chdir(tmp_path)
    result=reconcile_new(precheck, xlsx=xlsx_export(tmp_path), not_found=missing, today=TODAY)
    with pytest.raises(ValueError,match="exports"):
        save(result,"data/not_allowed.json")
    saved=save(result,"exports/next_batch.json")
    assert Path(saved).is_file()
    with pytest.raises(FileExistsError):
        save(result,"exports/next_batch.json")
