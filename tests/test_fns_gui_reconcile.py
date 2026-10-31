"""Synthetic FNS GUI export tests; no external networks, CRM changes or outreach."""
from datetime import date
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from leadpilot.fns_gui_reconcile import read_fns_xlsx, read_not_found, reconcile, save

TODAY=date(2026,10,31)
NS="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
INN_SMALL="7724850252"
INN_MEDIUM="7702319337"
INN_MISSING="9701112870"


def create_export(tmp_path, *, records=None, category=None):
    p=tmp_path/"rsmp_sample.xlsx"
    records=records or [(INN_SMALL,"Малое предприятие"),(INN_MEDIUM,"Среднее предприятие")]
    def c(column, row, text):
        import xml.sax.saxutils
        value=xml.sax.saxutils.escape(str(text))
        return f'<c r="{column}{row}" t="inlineStr"><is><t>{value}</t></is></c>'
    headers={"B":"Наименование / ФИО","D":"Категория","F":"ИНН",
             "M":"Дата включения в реестр","N":"Дата исключения из реестра"}
    head='<row r="3">'+''.join(c(col,3,value) for col,value in headers.items())+'</row>'
    rows=[]
    for index,(inn,kind) in enumerate(records,4):
        rows.append(f'<row r="{index}">'+c("B",index,"Учебная компания")+
                    c("D",index,kind)+c("F",index,inn)+
                    c("M",index,"01.08.2016")+'</row>')
    title=c("A",1,"Единый реестр субъектов малого и среднего предпринимательства по состоянию на 31.10.2026")
    xml=('<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="'+NS+'"><sheetData>'
         '<row r="1">'+title+'</row>'+head+''.join(rows)+'</sheetData></worksheet>')
    with ZipFile(p,"w") as z:
        z.writestr("xl/worksheets/sheet1.xml",xml.encode("utf-8"))
    return p


def source_files(tmp_path):
    xlsx=create_export(tmp_path)
    missing=tmp_path/"not_found.txt"
    missing.write_text(INN_MISSING+"\n",encoding="utf-8")
    scope=tmp_path/"scope.json"
    scope.write_text(json.dumps({"mode":"PILOT_SCOPE_REVIEW","items":[
        {"inn":INN_SMALL,"decision":"PENDING"},{"inn":INN_MEDIUM,"decision":"PENDING"},
        {"inn":INN_MISSING,"decision":"PENDING"}]}),encoding="utf-8")
    return xlsx,missing,scope


def test_reconciliation_never_authorizes_crm_or_contact(tmp_path):
    xlsx,missing,scope=source_files(tmp_path)
    old=scope.read_bytes()
    result=reconcile(xlsx,missing,scope,today=TODAY)
    assert result["counts"]=={"total":3,"found":2,"not_found":1,
                              "micro":0,"small":1,"medium":1,"not_sme":0,"crm_ready":0}
    assert result["crm_writes"]=="NO" and result["outreach"]=="NOT_AUTHORIZED"
    assert all(not x["crm_ready"] and not x["outbound_authorized"] for x in result["items"])
    assert scope.read_bytes()==old


def test_read_category_and_snapshot(tmp_path):
    d,found=read_fns_xlsx(create_export(tmp_path),today=TODAY)
    assert d==TODAY and found[INN_SMALL]["fns_status"]=="SMALL"
    assert found[INN_MEDIUM]["fns_status"]=="MEDIUM"


def test_unmatched_lists_fail_closed(tmp_path):
    xlsx,missing,scope=source_files(tmp_path)
    missing.write_text("5003079216\n",encoding="utf-8")
    with pytest.raises(ValueError,match="differ"):
        reconcile(xlsx,missing,scope,today=TODAY)


def test_same_inn_both_found_and_not_found_fail(tmp_path):
    xlsx,missing,scope=source_files(tmp_path)
    missing.write_text(INN_SMALL+"\n"+INN_MISSING+"\n",encoding="utf-8")
    with pytest.raises(ValueError,match="both"):
        reconcile(xlsx,missing,scope,today=TODAY)


def test_duplicate_found_in_excel_rejected(tmp_path):
    xlsx=create_export(tmp_path,records=[(INN_SMALL,"Малое предприятие"),
                                         (INN_SMALL,"Среднее предприятие")])
    with pytest.raises(ValueError,match="duplicate"):
        read_fns_xlsx(xlsx,today=TODAY)


def test_invalid_category_rejected(tmp_path):
    xlsx=create_export(tmp_path,records=[(INN_SMALL,"Предприятие неизвестного размера")])
    with pytest.raises(ValueError,match="Unexpected"):
        read_fns_xlsx(xlsx,today=TODAY)


def test_duplicate_not_found_rejected(tmp_path):
    p=tmp_path/"notfound.txt"
    p.write_text(INN_MISSING+"\n"+INN_MISSING+"\n",encoding="utf-8")
    with pytest.raises(ValueError,match="Duplicate"):
        read_not_found(p)


def test_safe_output_only_and_no_overwrite(tmp_path,monkeypatch):
    xlsx,missing,scope=source_files(tmp_path)
    monkeypatch.chdir(tmp_path)
    report=reconcile(xlsx,missing,scope,today=TODAY)
    with pytest.raises(ValueError):
        save(report,"data/unsafe.json")
    path=save(report,"exports/reconciliation.json")
    assert Path(path).exists()
    with pytest.raises(FileExistsError):
        save(report,"exports/reconciliation.json")


def test_invalid_mode_rejected(tmp_path):
    xlsx,missing,scope=source_files(tmp_path)
    data=json.loads(scope.read_text("utf8"))
    data["mode"]="CRM_APPROVED"
    scope.write_text(json.dumps(data),encoding="utf8")
    with pytest.raises(ValueError,match="PILOT_SCOPE_REVIEW"):
        reconcile(xlsx,missing,scope,today=TODAY)


def test_future_snapshot_rejected(tmp_path):
    with pytest.raises(ValueError,match="Future dated"):
        read_fns_xlsx(create_export(tmp_path),today=date(2026,10,30))
