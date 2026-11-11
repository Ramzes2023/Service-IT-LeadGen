"""V21: source-number-bound indexed conditions never become verified tender facts."""
from leadpilot.serviceit_b2b_tender_index_v18 import _safe_index_candidate
from leadpilot.serviceit_b2b_v19_backfill import novel_evidence

ID="4675475"
TITLE="Приобретение серверного оборудования для нужд МКБ"
URL="https://zakupki.kontur.ru/4675475_40"


def fixture():
    title="Предварительный квалификационный отбор №4675475: "+TITLE
    text=("B2B-Center Предварительный квалификационный отбор №4675475. "
          + TITLE + ".\n"
          "Заказчик: ПАО «Московский Кредитный Банк»\n"
          "ИНН: 7734202860\n"
          "Начальная цена: сумма не задана\n"
          "Условия участия: требуется регистрация и подтверждённая квалификация\n"
          "Критерии отбора: релевантный опыт поставок серверного оборудования\n"
          "Порядок подачи заявок: в электронной форме через площадку B2B-Center\n"
          "Перечень документов: анкета участника и сведения о квалификации\n"
          "Условия оплаты: определяются в рамках будущих процедур\n")
    return title,text


def test_index_procedure_and_condition_categories_are_explicitly_unverified():
    title,text=fixture()
    row=_safe_index_candidate(URL,title,text,ID,TITLE)
    assert row is not None and row["evidence_level"]=="INDEX_ONLY_UNVERIFIED"
    cats={c["category"] for c in row["claims"]}
    assert {"PROCEDURE","BUYER","INN","BUDGET","ELIGIBILITY","EVALUATION",
            "SUBMISSION","DOCUMENTS","PAYMENT"} <= cats
    assert all("quote" in x and len(x["quote"])<=250 for x in row["claims"])
    assert any("Предварительный квалификационный отбор" in x["quote"]
               for x in row["claims"] if x["category"]=="PROCEDURE")
    assert any("сумма не задана" in x["quote"]
               for x in row["claims"] if x["category"]=="BUDGET")


def test_previous_v19_facts_do_not_hide_new_eligibility_in_v20():
    title,text=fixture()
    row=_safe_index_candidate(URL,title,text,ID,TITLE)
    assert row
    old_report=("Предварительный квалификационный отбор №4675475. "
                "Заказчик: ПАО «Московский Кредитный Банк». "
                "ИНН: 7734202860. Начальная цена: сумма не задана")
    findings={"status":"PUBLIC_TENDER_ID_NOT_FOUND_IN_READABLE_PAGES",
              "pages":[],"indexed_candidates":[row]}
    lines=novel_evidence(findings,old_report)
    assert lines and all("ИНДЕКС BRAVE" in line for line in lines)
    assert any("ELIGIBILITY" in line and "НЕ ФНС" in line for line in lines)
    assert any("EVALUATION" in line for line in lines)
    assert all(URL in line for line in lines)
    assert len(lines)<=6


def test_different_tender_same_number_does_not_leak_other_conditions():
    title,text=fixture()
    text += ("\nДокументы для участия: секретный пакет для 8888888 "
             "и совершенно другой процедуры")
    row=_safe_index_candidate(URL,title,text,ID,TITLE)
    assert row
    assert all("секретный пакет" not in c["quote"] for c in row["claims"])
    assert _safe_index_candidate("https://zakupki.kontur.ru/8888888_40",
                                 title,text,ID,TITLE) is None


def test_untrusted_unlabelled_boilerplate_is_not_eligibility_or_price():
    title="№4675475 "+TITLE
    text=("B2B-Center №4675475 "+TITLE+" Документы для участия "
          "вы можете прочитать на сайте; условия участия не раскрыты")
    row=_safe_index_candidate(URL,title,text,ID,TITLE)
    assert row
    assert not any(c["category"] in {"ELIGIBILITY","DOCUMENTS","BUDGET"}
                   for c in row["claims"])
