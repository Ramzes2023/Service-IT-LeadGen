"""Real generic B2B service titles must corroborate exact public page and ID."""
from leadpilot.serviceit_b2b_public_mirrors import matched_excerpt

TITLE = "Оказание услуг по предоставлению ИТ-специалистов"
ID = "4673595"


def public_page(title=TITLE, label="Номер у источника 4673595",
                source="Запрос котировок B2B-Center"):
    intro = (source + " опубликовано 09.11.2026. " +
             title + ". Заказчик Общество с ограниченной ответственностью " +
             '"ГЛОБУС-ИТ". Подача заявок до 23.11.2026. ')
    return intro + ("Системный аналитик 1С, DevOps инженер. " * 190) + label


def test_full_real_generic_title_exact_labeled_source_id():
    excerpt=matched_excerpt(public_page(), ID, TITLE)
    assert excerpt
    assert TITLE in excerpt
    assert "ГЛОБУС-ИТ" in excerpt
    assert "4673595" in excerpt
    assert "Номер у источника" in excerpt


def test_generic_page_must_not_match_unrelated_id_or_title():
    assert not matched_excerpt(
        public_page(label="Номер у источника 5673595"),ID,TITLE)
    assert not matched_excerpt(
        public_page(title="Оказание услуг по поставке офисных кресел"),ID,TITLE)
    assert not matched_excerpt(
        public_page(label="Похожие закупки 4673595"),ID,TITLE)
    assert not matched_excerpt(
        public_page(source="Запрос котировок другой площадки"),ID,TITLE)


def test_real_title_requires_explicit_public_source_not_search_index():
    text=("Текст из индекса: " + TITLE +
          ". Номер у источника 4673595, но нет B2B-Center.")
    assert not matched_excerpt(text,ID,TITLE)
