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


def test_missing_deadline_or_delivery_never_produces_draft():
    m=load()
    for facts in (FACTS[1:],FACTS[:2],FACTS[:3]):
        with pytest.raises(m.Blocked):
            m.prepare(facts,[{"source_id":1,"url":URL,"text":TEXT}],original(),URL)


def test_foreign_url_fails_closed_and_no_false_customer_inn():
    m=load()
    with pytest.raises(m.Blocked):
        m.prepare(FACTS,[{"source_id":1,"url":"https://other.invalid/","text":TEXT}],
                  original(),URL)
    out=m.prepare(FACTS,[{"source_id":1,"url":URL,"text":TEXT}],original(),URL)
    assert "ИНН заказчика: нет проверенной юридической связки" in out[PREFIX+"AI_FULL_REPORT"]
    assert "Заказчик: отдельно не подтверждён" in out[PREFIX+"AI_FULL_REPORT"]
