"""V37 offline source safety and CRM-only contract tests."""
import re
import pytest

from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS,PREFIX
from leadpilot.serviceit_b2b_dossier_consistency_v37 import (
    REVIEWED,REPORT,INN,REGION,COMPANY,prepare,ReconcileBlocked,
    extracted_verified,reviewed_or_verified,
)
from leadpilot.bitrix_1062_mapping import _is_valid_inn


TAIL = """07  ДОСЛОВНЫЕ ФРАГМЕНТЫ И ССЫЛКИ
01. Закупка [1]
    «Источник доступен»
08  РЕЕСТР ИСТОЧНИКОВ
[1] https://www.b2b-center.ru
12 РЕАЛЬНЫЕ СВЕДЕНИЯ (архивная внешняя проверка)
Эта проверка сохранена без изменений.
"""


def _fixture(tid, **overrides):
    item={x:"Ожидает проверки первоисточника" for x in AI_FIELDS}
    item[REPORT]=(
        "СЕРВИС ИТ  |  ТЕНДЕРНОЕ ДОСЬЕ V11 | №"+tid+"\n"
        "01  ПАСПОРТ ЗАКУПКИ\n"
        "Заказчик: не установлен\n"
        "ИНН: нет подтверждения\n"
        "Срок подачи: [АРХИВ V10]\n"
        "02  ПРЕДМЕТ И ТЕХНИЧЕСКОЕ ЗАДАНИЕ\n"
        "География: не установлена\n"
        "06  ЧТО ПОКА НЕ ДОКАЗАНО\n"
        "• Срок подачи\n"
        "• Сведения о заказчике\n"+TAIL
    )
    item[PREFIX+"AI_MANAGER_BRIEF"]="Действия Михаила: проверить закупку"
    item[PREFIX+"AI_SUMMARY"]="Для Михаила: задание"
    item[COMPANY]=None
    item[INN]=None
    item[REGION]=None
    item.update(overrides)
    return item


def _notice(tid):
    return {
        "source":"B2B-Center",
        "tender_id":tid,
        "subject":REVIEWED[tid]["subject"],
        "source_url":REVIEWED[tid]["platform_url"],
    }


@pytest.mark.parametrize("tid,inn,deadline,buyer", [
    ("4673971","7702077840","17.11.2026 18:00","Московская Биржа"),
    ("4672802","7401000191","16.11.2026 10:00","Агрегат"),
    ("4674861","7708335695","17.11.2026 12:00","Ева"),
    ("4675475","7734202860","16.11.2026 16:00","Московский Кредитный Банк"),
])
def test_reconcile_all_four_signed_verified_cards(tid,inn,deadline,buyer):
    assert _is_valid_inn(inn)
    item=_fixture(tid)
    patch,stats=prepare(_notice(tid),item,[])
    assert stats["status"]=="DRAFT" and stats["reviewed"] is True
    assert patch[INN]==inn
    assert patch[COMPANY] == REVIEWED[tid]["buyer"]
    assert patch[REGION]==REVIEWED[tid]["region"]
    assert deadline+" МСК" in patch[REPORT]
    assert buyer in patch[REPORT]
    assert "Бюджет: Не раскрыт" in patch[REPORT]
    assert "Что пока не доказано" not in patch[REPORT]
    assert "06  ЧТО ЕЩЁ ТРЕБУЕТ ПОДТВЕРЖДЕНИЯ" in patch[REPORT]
    assert "• Срок подачи\n" not in patch[REPORT].split("07  ")[0]
    assert "• Юридически идентифицированный заказчик" not in patch[REPORT]
    assert patch[REPORT].endswith(TAIL)
    assert patch[PREFIX+"AI_MANAGER_BRIEF"].startswith("КАРТОЧКА ЗАКУПКИ")
    assert "Михаил" not in str(patch)
    assert set(patch).issubset(AI_FIELDS | {INN,COMPANY,REGION})
    assert len(patch[REPORT]) <= 12000
    patch2,stats2=prepare(_notice(tid),{**item,**patch},[])
    assert patch2 is None and stats2["status"]=="ALREADY_MATCHING"


def test_card15_buyer_is_not_organizer():
    patch,_=prepare(_notice("4674861"),_fixture("4674861"),[])
    section=patch[REPORT].split("02  ")[0]
    assert "Заказчик: ООО «Ева»" in section
    assert "Организатор: ООО «Группа компаний ФСК» (не заказчик)" in section
    assert "ИНН заказчика: 7708335695" in section


def test_card17_place_of_delivery_is_not_registered_address():
    patch,_=prepare(_notice("4675475"),_fixture("4675475"),[])
    assert "111141, Москва, 2-й проезд Перова Поля" in patch[REPORT]
    assert "юридический адрес" not in patch[REPORT].split("02  ")[0].lower()


def test_no_source_id_match_prevents_reviewed_seed():
    notice=_notice("4673971")
    notice["tender_id"]="46739710"
    patch,meta=prepare(notice,_fixture("4673971"),[])
    assert patch is None and meta["status"]=="NO_ATTESTED_NEW_FACTS"


def test_nonmatching_signed_subject_is_not_promoted():
    notice=_notice("4673971")
    notice["subject"]="Покупка детских игрушек по конкурсу"
    with pytest.raises(ReconcileBlocked,match="SUBJECT"):
        prepare(notice,_fixture("4673971"),[])


def test_existing_different_inn_never_overwritten():
    with pytest.raises(ReconcileBlocked,match="EXISTING_INN_CONFLICT"):
        prepare(_notice("4675475"),_fixture("4675475",
            **{INN:"7702077840"}),[])


def test_wrong_organizer_company_fixed_only_for_exact_reviewed_card():
    item=_fixture("4674861",**{
        COMPANY:"ООО «ГРУППА КОМПАНИЙ ФСК»",REGION:"Московская область",
    })
    patch,meta=prepare(_notice("4674861"),item,[])
    assert patch[COMPANY]=="ООО «Ева»"
    assert REGION not in patch
    assert patch[INN]=="7708335695"
    assert "Заказчик: ООО «Ева»" in patch[REPORT]


def test_arbitrary_existing_company_conflict_blocks_all_writes():
    item=_fixture("4674861", **{COMPANY:"ООО «Незнакомый заказчик»"})
    with pytest.raises(ReconcileBlocked,match="EXISTING_COMPANY_CONFLICT"):
        prepare(_notice("4674861"),item,[])


def test_no_unproven_index_quote_promoted():
    tid="4679999"
    notice={"source":"B2B-Center","tender_id":tid,
            "subject":"Тендер на поддержку серверов",
            "source_url":"https://www.b2b-center.ru/market/support/tender-"+tid+"/"}
    item=_fixture(tid)
    patch,meta=prepare(notice,item,[
        "ИНДЕКС BRAVE / BUYER: ПАО НЕПРОВЕРЕННОЕ https://example.com",
    ])
    assert patch is None and meta["status"]=="NO_ATTESTED_NEW_FACTS"


def test_future_card_with_signed_verified_deadline_and_location():
    tid="4679999"
    notice={"source":"B2B-Center","tender_id":tid,
            "subject":"Сопровождение серверного оборудования",
            "source_url":"https://www.b2b-center.ru/market/support/tender-"+tid+"/"}
    item=_fixture(tid)
    lines=[
        "ПРОЧИТАННЫЙ ИСТОЧНИК / DEADLINE: Окончание подачи 20.11.2026 18:30 [https://example.com/tenders/4679999]",
        "ПРОЧИТАННЫЙ ИСТОЧНИК / LOCATION: Место поставки: Москва, ул. Нагатинская, д. 1 [https://example.com/tenders/4679999]",
    ]
    patch,meta=prepare(notice,item,lines)
    assert meta["reviewed"] is False
    assert "20.11.2026 18:30 МСК" in patch[REPORT]
    assert "Москва, ул. Нагатинская" in patch[REPORT]
    assert "ИНН: не подтверждён" in patch[REPORT]
    assert INN not in patch


def test_a_verified_receipt_line_must_have_exact_source_url():
    assert not extracted_verified([
        "ПРОЧИТАННЫЙ ИСТОЧНИК / DEADLINE: 20.11.2026 18:30 [http://example.com]",
    ])
    ok=extracted_verified([
        "ПРОЧИТАННЫЙ ИСТОЧНИК / DEADLINE: 20.11.2026 18:30 [https://example.com/t/4679999]",
    ])
    assert "DEADLINE" in ok


def test_unusual_report_does_not_overwrite_original():
    item=_fixture("4675475",**{REPORT:"Ручное нестандартное досье"})
    patch,meta=prepare(_notice("4675475"),item,[])
    assert patch is None and meta["status"]=="NONSTANDARD_REPORT_MANUAL_REVIEW"


def test_unverified_brave_index_section_preserved_as_unverified_not_misreported():
    item=_fixture("4675475")
    item[REPORT]=item[REPORT].replace(
        "07  ДОСЛОВНЫЕ ФРАГМЕНТЫ И ССЫЛКИ",
        "06A  ДОПОЛНИТЕЛЬНЫЕ НАХОДКИ BRAVE — НЕ ПРОВЕРЕНЫ\n"
        "Индекс может содержать другие процедуры. Не переносить в поля.\n\n"
        "07  ДОСЛОВНЫЕ ФРАГМЕНТЫ И ССЫЛКИ")
    patch,_=prepare(_notice("4675475"),item,[])
    assert "06A  ДОПОЛНИТЕЛЬНЫЕ НАХОДКИ BRAVE" in patch[REPORT]
    assert patch[REPORT].index("06A  ")<patch[REPORT].index("07  ")
    assert "Индекс может содержать другие процедуры" in patch[REPORT]
    assert "Индекс может содержать другие процедуры" not in patch[PREFIX+"AI_SUMMARY"]


def test_publication_date_not_promoted_to_deadline():
    tid="4679999"
    notice={"source":"B2B-Center","tender_id":tid,
            "subject":"Сопровождение серверного оборудования",
            "source_url":"https://www.b2b-center.ru/market/support/tender-"+tid+"/"}
    item=_fixture(tid)
    lines=[
        "ПРОЧИТАННЫЙ ИСТОЧНИК / DEADLINE: Опубликовано 10.11.2026 14:00. Окончание подачи 20.11.2026 18:30 [https://example.com/tenders/4679999]"
    ]
    patch,meta=prepare(notice,item,lines)
    assert "20.11.2026 18:30 МСК" in patch[REPORT]
    assert "10.11.2026 14:00 МСК" not in patch[REPORT].split("07  ")[0]


def test_legal_address_cannot_be_promoted_to_delivery():
    tid="4679999"
    notice={"source":"B2B-Center","tender_id":tid,
            "subject":"Сопровождение серверного оборудования",
            "source_url":"https://www.b2b-center.ru/market/support/tender-"+tid+"/"}
    item=_fixture(tid)
    lines=["ПРОЧИТАННЫЙ ИСТОЧНИК / LOCATION: Юридический адрес компании: Москва, ул. Ленина, 1 [https://example.com/tenders/4679999]"]
    patch,meta=prepare(notice,item,lines)
    assert patch is None and meta["status"]=="NO_ATTESTED_NEW_FACTS"


def test_supplier_company_name_not_promoted_to_buyer():
    tid="4679999"
    notice={"source":"B2B-Center","tender_id":tid,
            "subject":"Сопровождение серверного оборудования",
            "source_url":"https://www.b2b-center.ru/market/support/tender-"+tid+"/"}
    item=_fixture(tid)
    lines=["ПРОЧИТАННЫЙ ИСТОЧНИК / BUYER: Организатор ООО «Поставщик», заказчик не указан [https://example.com/tenders/4679999]"]
    patch,meta=prepare(notice,item,lines)
    assert patch is None and meta["status"]=="NO_ATTESTED_NEW_FACTS"


def test_verified_amount_removes_false_budget_gap():
    tid="4679999"
    notice={"source":"B2B-Center","tender_id":tid,
            "subject":"Сопровождение серверного оборудования",
            "source_url":"https://www.b2b-center.ru/market/support/tender-"+tid+"/"}
    item=_fixture(tid)
    lines=["ПРОЧИТАННЫЙ ИСТОЧНИК / BUDGET: Начальная цена закупки: 1 250 000 руб. с НДС [https://example.com/tenders/4679999]"]
    patch,meta=prepare(notice,item,lines)
    assert "Бюджет: Начальная цена закупки: 1 250 000 руб." in patch[REPORT]
    before_gaps=patch[REPORT].split("06  ЧТО ЕЩЁ ТРЕБУЕТ ПОДТВЕРЖДЕНИЯ")[1].split("07  ")[0]
    assert "• Начальная цена / бюджет" not in before_gaps
    assert "не считать цену нулевой" in patch[REPORT]


def test_card13_exact_published_organizer_can_be_corrected_to_legal_buyer():
    item=_fixture("4672802",**{
        COMPANY:"АО ГМЗ «Агат»",
    })
    patch,meta=prepare(_notice("4672802"),item,[])
    assert patch[COMPANY]=="ПАО «Агрегат»"
    assert patch[INN]=="7401000191"
    assert "Заказчик: ПАО «Агрегат»" in patch[REPORT]


def test_card13_arbitrary_different_company_stays_blocked():
    item=_fixture("4672802",**{
        COMPANY:"ООО «Другая организация»",
    })
    with pytest.raises(ReconcileBlocked,match="EXISTING_COMPANY_CONFLICT"):
        prepare(_notice("4672802"),item,[])
