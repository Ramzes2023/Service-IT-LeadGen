"""V19 regression: MKB acronym, weak indexed provenance and mixed-source dоссier."""
from leadpilot import serviceit_b2b_tender_index_v18 as idx
from leadpilot.serviceit_b2b_dossier_v11 import compose
from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS,PREFIX

TENDER="4675475"
SUBJECT="Приобретение серверного оборудования для нужд МКБ"
URL="https://zakupki.kontur.ru/4675475_40"
ORIG="https://www.b2b-center.ru/market/4675475/"

def test_acronym_is_distinctive_root_and_same_number_tender_survives():
    terms=idx._title_terms(SUBJECT)
    assert "мкб" in terms and "сервер" in terms
    row=idx._safe_index_candidate(URL,
        "№4675475 серверы для МКБ",
        "Заявка B2B-Center №4675475. Серверное оборудование для МКБ. "
        "Заказчик: ПАО Московский Кредитный Банк. ИНН: 7734202860",
        TENDER,SUBJECT)
    assert row
    assert row["evidence_level"]=="INDEX_ONLY_UNVERIFIED"
    assert row["title_match"]=="EXACT_ID_AND_TOPIC_AND_PLATFORM"
    assert any(x["category"]=="INN" for x in row["claims"])

def test_id_topic_aggregator_index_without_platform_is_hint_not_proof():
    row=idx._safe_index_candidate(URL,
        "№4675475 Серверы для МКБ",
        "4675475. Серверное оборудование для МКБ. "
        "Заказчик: ПАО Московский Кредитный Банк. ИНН: 7734202860",
        TENDER,SUBJECT)
    assert row and row["title_match"]=="EXACT_ID_TOPIC_PLATFORM_NOT_CONFIRMED"
    assert row["evidence_level"]=="INDEX_ONLY_UNVERIFIED"
    assert idx._safe_index_candidate("https://zakupki.kontur.ru/4679999_40",
        "№4675475 Серверы для МКБ",
        "4675475 Серверное оборудование для МКБ",TENDER,SUBJECT) is None
    assert idx._safe_index_candidate(
        "https://zakupki.kontur.ru/4675475_40",
        "4675475 Мебель для библиотеки",
        "4675475 B2B-Center офисная мебель столы стулья",
        TENDER,SUBJECT) is None

def test_context_is_visible_in_both_source_rich_and_source_poor_reports():
    indexed=idx._safe_index_candidate(URL,
        "№4675475 серверы для МКБ",
        "B2B-Center №4675475 серверное оборудование для МКБ. "
        "Заказчик: ПАО Московский Кредитный Банк. ИНН: 7734202860",
        TENDER,SUBJECT)
    before={k:"PREVIOUS AI REPORT" for k in AI_FIELDS}
    before[PREFIX+"AI_STATUS"]="PUBLIC_RESEARCH_ROUTERAI_CITED"
    notice={"source":"B2B-Center","tender_id":TENDER,"subject":SUBJECT,
            "source_url":ORIG, "source_service_groups":["SERVERS"]}
    for pages in ([],[{"url":"https://tendem.ru/tenders/4675475",
                        "excerpt":"№4675475 B2B-Center. "+SUBJECT+
                        ". Условия поставки: выполнить в течение 30 дней."}]):
        findings={"status":"PUBLIC_PAGES_INSPECTED" if pages else
                  "PUBLIC_TENDER_ID_NOT_FOUND_IN_READABLE_PAGES",
                  "pages":pages,"indexed_candidates":[indexed]}
        fields,coverage=compose(notice,before,findings,{"facts":[]})
        full=fields[PREFIX+"AI_FULL_REPORT"]
        brief=fields[PREFIX+"AI_MANAGER_BRIEF"]
        assert "06A  ДОПОЛНИТЕЛЬНЫЕ НАХОДКИ BRAVE" in full
        assert "06  ЧТО ПОКА НЕ ДОКАЗАНО" in full
        assert "7734202860" in full
        assert "ИНДЕКС BRAVE" in brief
        assert "не подтвержд" in full.lower()
        assert fields[PREFIX+"AI_STATUS"]==before[PREFIX+"AI_STATUS"]
        assert set(fields)==AI_FIELDS

def test_wrong_prefix_in_inn_not_claimed_as_buyer_identity():
    row=idx._safe_index_candidate(URL,
        "№4675475 серверы для МКБ",
        "B2B-Center №4675475 серверное оборудование для МКБ. ИНН: 7734202860",
        TENDER,SUBJECT)
    assert row
    assert all(x["category"]!="BUYER" for x in row["claims"])
