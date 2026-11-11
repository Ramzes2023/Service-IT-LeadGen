"""V23 live-four-card supplemental research: offline source and write fencing."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import ast
import httpx

from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX


def worker():
    spec=spec_from_file_location(
        "v23real","ops/serviceit_vps_b2b_v23_real_primary_backfill.py")
    mod=module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


URL="https://www.b2b-center.ru/market/zakupka-serverov/tender-4675475/"
NOTICE={"source":"B2B-Center","tender_id":"4675475",
        "source_url":URL,"subject":"Закупка серверов для МКБ"}
CARD=47
SRC="B2B-CENTER-4675475"


def nine():
    return {key:"Изначальный отчёт — без изменений" for key in AI_FIELDS}


def primary_text():
    return ("Предварительный квалификационный отбор № 4675475 "
            "Приобретение серверного оборудования для нужд МКБ. "
            "Актуально до: 16.11.2026 16:00. "
            "Общая стоимость закупки: Без указания цены. "
            "Доступно зарегистрированным пользователям: Для просмотра "
            "зарегистрируйтесь. ")


def test_source_only_new_verified_fields_zero_paid_apis(monkeypatch):
    mod=worker()
    monkeypatch.setattr(mod,"host_allowlist",lambda _:frozenset({"b2b-center.ru"}))
    monkeypatch.setattr(mod,"research_public",lambda *args,**kwargs:{
        "status":"PUBLIC_PAGES_INSPECTED","pages":[
            {"url":URL,"excerpt":primary_text()}]})
    with httpx.Client(transport=httpx.MockTransport(
            lambda _: (_ for _ in ()).throw(AssertionError("NETWORK_FORBIDDEN"))
            )) as client:
        lines,n,status=mod.verified_primary_lines(
            client,{**nine(),PREFIX+"SIGNAL_URL":URL},NOTICE,{})
    assert status=="PRIMARY_READ"
    assert n>=3 and len(lines)>=2
    assert all(URL in line and "ПРОЧИТАННЫЙ ПЕРВОИСТОЧНИК" in line for line in lines)
    assert any("предварительный" in x.lower() for x in lines)
    assert any("16.11.2026" in x for x in lines)
    assert all("0 ₽" not in line for line in lines)
    assert all("НЕ ФНС" not in line or "ОРГАНИЗАТОР" in line for line in lines)


def test_only_unseen_evidence_and_append_three_not_nine(monkeypatch):
    mod=worker()
    item=nine()
    lines=[
      "ПРОЧИТАННЫЙ ПЕРВОИСТОЧНИК / СРОК ПОДАЧИ (страница B2B): "
      "Актуально до: 16.11.2026 16:00 ["+URL+"]"
    ]
    desired=mod.bounded_draft(item,NOTICE,lines)
    assert desired is not None
    assert desired[mod.REPORT].startswith(item[mod.REPORT])
    assert "14  ПРОВЕРЕННЫЕ ПОЛЯ ПЕРВОИСТОЧНИКА V23" in desired[mod.REPORT]
    assert desired[mod.SUMMARY].endswith(item[mod.SUMMARY])
    assert desired[mod.BRIEF].endswith(item[mod.BRIEF])
    assert {k for k in AI_FIELDS if desired[k]!=item[k]}=={
        mod.REPORT,mod.SUMMARY,mod.BRIEF}
    assert mod.bounded_draft(item,NOTICE,[]) is None


def test_unrelated_source_and_missing_public_page_never_backfill(monkeypatch):
    mod=worker()
    monkeypatch.setattr(mod,"host_allowlist",lambda _:frozenset({"b2b-center.ru"}))
    monkeypatch.setattr(mod,"research_public",lambda *args,**kwargs:{
        "status":"PUBLIC_SOURCE_NOT_READABLE","pages":[]})
    with httpx.Client() as client:
        lines,n,status=mod.verified_primary_lines(client,nine(),NOTICE,{})
    assert (lines,n,status)==([],0,"PRIMARY_UNREADABLE")
    wrong={**NOTICE,"source_url":
        "https://www.b2b-center.ru/market/zakupka/tender-4679999/"}
    with httpx.Client() as client:
        try: mod.verified_primary_lines(client,nine(),wrong,{})
        except mod.BackfillBlocked: pass
        else: raise AssertionError("OTHER_TENDER_NUMBER_ACCEPTED")


def test_unknown_bitrix_update_quarantined_no_blind_retry(monkeypatch):
    mod=worker()
    item={**nine(),PREFIX+"SIGNAL_URL":URL,PREFIX+"SIGNAL_ID":SRC}
    monkeypatch.setattr(mod,"receipt_status",lambda *args:None)
    monkeypatch.setattr(mod,"prior_writes_safe",lambda *args:True)
    monkeypatch.setattr(mod,"validate_identity",lambda *args:None)
    monkeypatch.setattr(mod,"safe_notice",lambda *args:(URL,NOTICE["subject"]))
    monkeypatch.setattr(mod,"verified_primary_lines",lambda *args,**kwargs:(
        ["ПРОЧИТАННЫЙ ПЕРВОИСТОЧНИК / СРОК ПОДАЧИ: "
         "Актуально до: 16.11.2026 16:00 ["+URL+"]"],3,"PRIMARY_READ"))
    monkeypatch.setattr(mod,"fingerprint",lambda item:"SAME")
    monkeypatch.setattr(mod,"reserve",lambda *args:True)
    monkeypatch.setattr(mod,"intent",lambda *args:(b"0"*16,b"0"*32))
    status=[]
    monkeypatch.setattr(mod,"finish",lambda conn,card,state:status.append(state))
    updates=[]
    def rest(name,payload):
        if name=="crm.item.get":return {"item":item}
        if name=="crm.item.update":
            updates.append(payload)
            raise httpx.ReadTimeout("unknown CRM outcome")
        raise AssertionError(name)
    out=mod.handle(None,None,rest,{},(CARD,SRC,NOTICE),apply=True)
    assert out[0]=="UNCERTAIN"
    assert len(updates)==1 and status==["UNCERTAIN"]
    assert set(updates[0]["fields"])==AI_FIELDS


def test_owner_workflow_and_commit_pin_no_replay_without_receipt():
    flow=Path(".github/workflows/serviceit-vps-b2b-v23-four-real.yml").read_text("utf8")
    src=Path("ops/serviceit_vps_b2b_v23_real_primary_backfill.py").read_text("utf8")
    ast.parse(src)
    assert "SERVICEIT_B2B_V23_FOUR_PREVIEW_489" in flow
    assert "SERVICEIT_B2B_V23_FOUR_ONEUSE_APPLY_489" in flow
    assert "github.actor == 'Ramzes2023'" in flow
    assert "github.event.issue.number == 489" in flow
    assert "git diff --quiet" in flow
    assert "StrictHostKeyChecking=yes" in flow
    assert "pg_try_advisory_lock" in src
    assert "ON CONFLICT (card_id) DO NOTHING" in src
    assert "status='WRITE_INTENT'" in src
    assert 'finish(conn,card,"UNCERTAIN")' in src
    assert 'non_ai_unchanged(fresh,after)' in src
    assert "deep_search_enabled=False" in src
    assert "deep_llm_context_enabled=False" in src
    assert "browser_render=None" in src
    assert "investigate_routerai" not in src
    assert "crm.item.add" not in src
    assert "tasks.task.add" not in src
