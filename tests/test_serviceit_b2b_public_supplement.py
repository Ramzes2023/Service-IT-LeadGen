"""Previously analyzed B2B cards keep original AI facts and status."""
from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX
from leadpilot.serviceit_b2b_public_supplement import supplement_existing_ai


def base():
    item={k:"Ранее подтверждённые сведения" for k in AI_FIELDS}
    item[PREFIX+"AI_STATUS"]="PUBLIC_RESEARCH_ROUTERAI_CITED"
    item[PREFIX+"AI_FULL_REPORT"]="Исторические публичные цитаты. https://tendem.ru/tenders/16548471"
    return item


def findings():
    return {
        "status":"PUBLIC_PAGES_INSPECTED",
        "pages":[{"url":"https://tendem.ru/tenders/16548471",
                  "excerpt":'Заказчик Общество с ограниченной ответственностью "ГЛОБУС-ИТ"'}],
        "independent_company_research":{
            "public_inn":{"inn":"5260365605","buyer_name":"ООО «ГЛОБУС-ИТ»",
                          "public_source":"https://globus-ltd.ru/contacts",
                          "fns_verified":False},
            "contacts":[{"type":"email","value":"contact@globus-ltd.com",
                         "url":"https://globus-ltd.ru/contacts"}],
        },
    }


def test_adds_real_public_requisites_without_replacing_earlier_ai():
    item=base()
    new=supplement_existing_ai(item, findings())
    assert new is not None
    assert set(new)==AI_FIELDS
    assert new[PREFIX+"AI_STATUS"]==item[PREFIX+"AI_STATUS"]
    assert new[PREFIX+"AI_QUOTE"]==item[PREFIX+"AI_QUOTE"]
    assert item[PREFIX+"AI_FULL_REPORT"] in new[PREFIX+"AI_FULL_REPORT"]
    assert "5260365605" in new[PREFIX+"AI_FULL_REPORT"]
    assert "contact@globus-ltd.com" in new[PREFIX+"AI_FULL_REPORT"]
    assert "НЕ ФНС" in new[PREFIX+"AI_MANAGER_BRIEF"]


def test_no_public_evidence_is_noop():
    assert supplement_existing_ai(base(), {"status":"PUBLIC_SOURCE_NOT_READABLE"}) is None


def test_addendum_can_never_truncate_cited_history():
    item=base()
    item[PREFIX+"AI_FULL_REPORT"]="Цитаты оригинала " * 800
    assert supplement_existing_ai(item,findings()) is None
