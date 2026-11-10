"""V14 historical real B2B public addendum is additive and write-fenced."""
import importlib.util
from pathlib import Path
import re
import pytest
from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS,PREFIX

SCRIPT=Path("ops/serviceit_vps_b2b_crossportal_four_v14.py")
WORKFLOW=Path(".github/workflows/serviceit-vps-b2b-crossportal-four-v14.yml")


def mod():
    spec=importlib.util.spec_from_file_location("crossportal_v14",SCRIPT)
    obj=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def fields():
    x={name:"Старый AI текст V12. Историческое свидетельство останется."
       for name in AI_FIELDS}
    x[PREFIX+"AI_FULL_REPORT"]=(
        "СЕРВИС ИТ | ТЕНДЕРНОЕ ДОСЬЕ V11\n"
        "07  ДОСЛОВНЫЕ ФРАГМЕНТЫ\n"
        "09  ВОССТАНОВЛЕННЫЕ ДОКАЗАТЕЛЬСТВА\n"
        "Историческое подтверждение. Не удалять.")
    return x


def test_adds_new_source_facts_contacts_but_preserves_exact_old_v12():
    m=mod()
    old=fields()
    facts=[
        {"category":"PAYMENT","quote":"Условия оплаты: 100% постоплата",
         "url":"https://zakupki.kontur.ru/4673595_4"},
        {"category":"LOCATION","quote":"Место поставки Нижний Новгород",
         "url":"https://zakupki.kontur.ru/4673595_4"},
    ]
    target,meta=m.draft_addendum(
        old,facts,"ООО «Глобус-ИТ»",
        {"inn":"5260365605","public_source":"https://zakupki.kontur.ru/4673595_4"},
        [("телефон","+78007751396","https://globus-ltd.ru/contacts"),
         ("email","contact@globus-ltd.com","https://globus-ltd.ru/contacts")])
    assert meta["new_rows"]>=5
    assert target is not None
    full=target[PREFIX+"AI_FULL_REPORT"]
    assert full.startswith(old[PREFIX+"AI_FULL_REPORT"])
    assert "09  ВОССТАНОВЛЕННЫЕ ДОКАЗАТЕЛЬСТВА" in full
    assert "10  ПУБЛИЧНАЯ ПРОВЕРКА V14" in full
    assert "100% постоплата" in full
    assert "5260365605" in full
    assert "не ответственного за тендер" in full
    assert "+78007751396" in full
    assert target[PREFIX+"AI_MANAGER_BRIEF"].endswith(
        old[PREFIX+"AI_MANAGER_BRIEF"])
    for key in AI_FIELDS-{
        PREFIX+"AI_FULL_REPORT",PREFIX+"AI_MANAGER_BRIEF",PREFIX+"AI_SUMMARY"
    }:
        assert target[key]==old[key]


def test_no_evidence_means_no_crm_write():
    m=mod()
    before=fields()
    assert m.draft_addendum(before,[], "",None,[])[0] is None
    assert before==fields()


def test_existing_quote_is_not_duplicated():
    m=mod()
    old=fields()
    quote="Условия участия: все участники должны иметь допуск"
    old[PREFIX+"AI_FULL_REPORT"]+="\n"+quote
    same=[{"category":"ELIGIBILITY","quote":quote,
           "url":"https://zakupki.kontur.ru/tender"}]
    target,meta=m.draft_addendum(old,same,"",None,[])
    assert target is None
    assert meta["new_rows"]==0


def test_large_historical_report_must_not_be_truncated():
    m=mod()
    old=fields()
    old[PREFIX+"AI_FULL_REPORT"]="X"*11990
    target,meta=m.draft_addendum(old,[{
        "category":"PAYMENT","quote":"Постоплата 30 календарных дней",
        "url":"https://zakupki.kontur.ru/tender",
    }],"",None,[])
    assert target is None and meta["new_rows"]==0
    assert old[PREFIX+"AI_FULL_REPORT"]=="X"*11990


def test_receipt_and_crm_write_are_one_use_versioned_limited_scope():
    code=SCRIPT.read_text("utf8")
    assert "serviceit_vps_b2b_public_v14_receipts" in code
    assert "pg_try_advisory_lock" in code
    assert "LOCK_ID = 6754819001" in code
    assert "v12.status IN ('VERIFIED','NO_CHANGE')" in code
    assert "v11.status='VERIFIED'" in code
    assert "b.state='VERIFIED'" in code
    assert "len(four)==4" in code
    assert "V12_OR_MANUAL_CRM_NINE_FIELDS_DRIFT" in code
    assert "CRM_CONCURRENT_EDIT_NO_OVERWRITE" in code
    assert "WRITE_INTENT" in code
    assert "non_ai_unchanged(fresh,after)" in code
    assert '"fields":update' in code
    assert '"fields":change' not in code
    assert "FULL_NINE_EXACT_OTHER_AI_PRESERVATION" in code
    assert "PREVIOUS_UNCERTAIN_INTENT_HASH_INVALID" in code
    assert "PREVIOUS_UNCERTAIN_CRM_NOT_OLD" in code
    assert "PREVIOUS_UNCERTAIN_EVIDENCE_NOT_ADDITIVE" in code
    assert "serviceit_vps_b2b_public_v13_receipts prior13" in code
    assert "10  ПУБЛИЧНАЯ ПРОВЕРКА V13" in code
    assert 'and all(update[k]==old[k] for k in' in code
    assert "PENDING_AI" not in code
    assert "crm.item.add" not in code
    assert "crm.timeline.comment.add" not in code
    assert "tasks.task.add" not in code
    assert "investigate_routerai(" not in code
    assert "crm.item.update" in code
    assert "serviceit_vps_b2b_tender_doc_receipts" in code
    assert "LIVE_CODE_SHA_CHANGED" in code


def test_owner_only_guarded_workflow():
    w=WORKFLOW.read_text("utf8")
    assert "github.actor == 'Ramzes2023'" in w
    assert "github.event.issue.number == 368" in w
    assert "SERVICEIT_B2B_FOUR_V14_PUBLIC_BACKFILL_368" in w
    assert "workflow_dispatch" not in w
    assert "StrictHostKeyChecking=yes" in w
    assert "UserKnownHostsFile=" in w
    assert "docker exec -i serviceit-leadgen-vps-coordinator-1 python -B -" in w
    assert "serviceit_vps_b2b_crossportal_four_v14.py" in w
