"""Offline card29 one-use source-backed dossier draft tests; no network."""
import importlib.util
from pathlib import Path

import pytest

from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX


def load():
    path=Path(__file__).resolve().parents[1]/"ops/serviceit_vps_b2b_card29_dossier_repair.py"
    spec=importlib.util.spec_from_file_location("card29_repair",path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


URL="https://www.b2b-center.ru/app/market-next/priobretenie-litsenzii-teams-prodlenie/tender-4677923/"
TEXT=("B2B-Center Приобретение лицензий Teams (продление) № 4677923 "
      'Организатор АО "АЗОТ-ВЗРЫВ" '
      "Окончание приёма заявок 20.11.2026 13:59 "
      "Условия оплаты предпочтительно отсрочка 30 календарных дней, "
      "предоплата возможна "
      "Условия поставки / оказания услуг электронная поставка "
      "Адрес поставки / оказания услуг Россия г. Москва "
      "121151 ул Можайский Вал д. 8 этаж 11 "
      "Количество позиций — 3 Подача альтернативных заявок "
      "Подача аналогов")
FACTS=[
    {"category":"DEADLINE","quote":"Окончание приёма заявок 20.11.2026 13:59"},
    {"category":"PAYMENT","quote":"Условия оплаты предпочтительно отсрочка 30 календарных дней"},
    {"category":"LOCATION","quote":"Условия поставки / оказания услуг электронная поставка"},
    {"category":"LOCATION","quote":"Адрес поставки / оказания услуг Россия г. Москва 121151 ул Можайский Вал д. 8"},
]


def original():
    return {key:("СЕРВИС ИТ | ТЕНДЕРНОЕ ДОСЬЕ V11 | 4677923\n"
                  + ("Прежние проверенные сведения.\n" * 35)
                  if key==PREFIX+"AI_FULL_REPORT" else "исходное подтверждённое поле")
            for key in AI_FIELDS}


def test_primary_card29_dossier_is_structured_and_preserves_history_and_nonreport_fields():
    m=load()
    base=original()
    base[PREFIX+"AI_STATUS"]="PUBLIC_RESEARCH_ROUTERAI_CITED"
    out=m.prepare(FACTS,[{"source_id":1,"url":URL,"text":TEXT}],base,URL)
    assert set(out)==AI_FIELDS
    assert "Можайский Вал" in out[PREFIX+"AI_MANAGER_BRIEF"]
    assert "20.11.2026 13:59" in out[PREFIX+"AI_SUMMARY"]
    assert "АРХИВ РАННЕГО" in out[PREFIX+"AI_FULL_REPORT"]
    assert base[PREFIX+"AI_FULL_REPORT"] in out[PREFIX+"AI_FULL_REPORT"]
    assert "3 (не количество лицензий)" in out[PREFIX+"AI_FULL_REPORT"]
    assert "заказчик/инн не подтверждены" in out[PREFIX+"AI_SUMMARY"].lower()
    assert out[PREFIX+"AI_STATUS"]==base[PREFIX+"AI_STATUS"]
    assert len(out[PREFIX+"AI_FULL_REPORT"])<=12000


def test_missing_source_deadline_or_delivery_never_produces_draft():
    m=load()
    with pytest.raises(m.Blocked):
        m.prepare(FACTS[1:],[{"source_id":1,"url":URL,"text":TEXT}],original(),URL)
    for excerpt in (
        TEXT.replace("электронная поставка", "условия требуют сверки"),
        TEXT.replace("Можайский Вал", "адрес не указан"),
        TEXT.replace("отсрочка 30 календарных дней", "условия оплаты не указаны"),
    ):
        with pytest.raises(m.Blocked):
            m.prepare(FACTS[:1],[{"source_id":1,"url":URL,"text":excerpt}],original(),URL)


def test_original_public_text_can_supply_payment_and_address_without_regex_facts():
    m=load()
    # The first V22 regex pass can miss an HTML split-heading value.
    # These literal source substrings, not model guesses, remain eligible.
    out=m.prepare(FACTS[:1],[{"source_id":1,"url":URL,"text":TEXT}],original(),URL)
    assert "Можайский Вал" in out[PREFIX+"AI_FULL_REPORT"]
    assert "отсрочка" in out[PREFIX+"AI_FULL_REPORT"].lower()
    assert "электронная поставка" in out[PREFIX+"AI_FULL_REPORT"]



def test_foreign_url_fails_closed_and_no_false_customer_inn():
    m=load()
    with pytest.raises(m.Blocked):
        m.prepare(FACTS,[{"source_id":1,"url":"https://other.invalid/","text":TEXT}],
                  original(),URL)
    out=m.prepare(FACTS,[{"source_id":1,"url":URL,"text":TEXT}],original(),URL)
    assert "ИНН заказчика: нет проверенной юридической связки" in out[PREFIX+"AI_FULL_REPORT"]
    assert "Заказчик: отдельно не подтверждён" in out[PREFIX+"AI_FULL_REPORT"]


def test_verified_v37_patch_provenance_allows_only_matching_digest_chain():
    m=load()
    start=original()
    name=PREFIX+"AI_FULL_REPORT"
    next_report="СЕРВИС ИТ | V37 · СОГЛАСОВАННЫЙ ПАСПОРТ\n" + start[name]
    before={name:start[name]}
    after={name:next_report}
    salt=b"0000000000000001"
    digest=m.digest(after,salt)
    class Cursor:
        def __init__(self,one=None,many=None):
            self.one=one
            self.many=many
        def fetchone(self):
            return self.one
        def fetchall(self):
            return self.many
    class DB:
        def __init__(self,proof):
            self.proof=proof
        def execute(self,sql,params):
            if "to_regclass" in sql:
                return Cursor((params[0] if params[0].endswith("v37_receipts") else None,))
            if "FROM serviceit_vps_b2b_dossier_consistency_v37_receipts" in sql:
                return Cursor(many=[("VERIFIED",before,after,salt,self.proof)])
            raise AssertionError("UNEXPECTED_QUERY")
    reconstructed=m.attested_current_chain(DB(digest),m.SOURCE,start)
    assert reconstructed[name]==next_report
    assert all(reconstructed[k]==start[k] for k in start if k!=name)
    with pytest.raises(m.Blocked,match="V37_RECEIPT_DIGEST_INVALID"):
        m.attested_current_chain(DB(b"x"*32),m.SOURCE,start)


def test_v37_uncertain_conflict_blocks_replaying_old_card():
    m=load()
    start=original()
    class Cursor:
        def __init__(self,one=None,many=None):
            self.one=one
            self.many=many
        def fetchone(self):
            return self.one
        def fetchall(self):
            return self.many
    class DB:
        def execute(self,sql,params):
            if "to_regclass" in sql:
                return Cursor((params[0] if params[0].endswith("v37_receipts") else None,))
            return Cursor(many=[("UNCERTAIN",{},None,None,None)])
    with pytest.raises(m.Blocked,match="V37_PRIOR_UNCERTAIN_NO_REPLAY"):
        m.attested_current_chain(DB(),m.SOURCE,start)
