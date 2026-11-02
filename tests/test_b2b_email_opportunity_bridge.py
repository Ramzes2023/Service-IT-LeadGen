"""Offline representative B2B email fixtures. No actual email or account data."""
from email.message import EmailMessage

import pytest

from leadpilot.b2b_email_opportunity_bridge import (
    InputRejected, build_bitrix_preview, clean_tender_url, parse_email, classify_services,
)


def mail(html, subject="Актуальные торги по вашему профилю", sender="marketplace@b2b-center.ru"):
    m = EmailMessage()
    m["From"] = sender
    m["To"] = "fixtures@example.invalid"
    m["Subject"] = subject
    m["Date"] = "Mon, 02 Nov 2026 13:15:40 +0300"
    m.set_content(html, subtype="html")
    return m.as_bytes()


def href(number, kind="app"):
    path = (f"/app/market-next/zakupka-1s/tender-{number}/" if kind == "app" else
            f"/market/zakupka-1s/tender-{number}/")
    return f"https://www.b2b-center.ru{path}?message_info=SECRET&conversion_id=3&utm_source=mail"


def bulletin(number="4661617", title="Сопровождение 1С:Бухгалтерия"):
    return (f'<div><a href="{href(number, kind="market")}">Конкурс № {number}</a>'
            f'<p>Организатор:</p><p><a href="https://www.b2b-center.ru/firms/a/12/">АО «Тестовые системы»</a></p>'
            f'<p>Предмет договора: {title}</p></div>')


def test_canonical_links_strip_tracking_and_deny_other_hosts():
    a = clean_tender_url(href("4601288"))
    assert a == ("https://www.b2b-center.ru/app/market-next/zakupka-1s/tender-4601288/", "4601288")
    assert clean_tender_url("https://malicious.example/app/market-next/x/tender-4601288/") is None
    assert clean_tender_url("http://www.b2b-center.ru/app/market-next/x/tender-4601288/") is None
    assert clean_tender_url("https://www.b2b-center.ru/app/market-next/x/tender-4601288/#frag") is None


def test_bulletin_extracts_per_procurement_service_without_cross_contamination():
    html = (bulletin() + '<div><a href="'+href("4652355")+'">Запрос предложений №4652355</a>'
            '<p>Организатор:</p><p>ООО «Стройтест»</p>'
            '<p>Предмет договора: Ремонт бетонных фундаментов</p></div>')
    report = build_bitrix_preview([mail(html)])
    assert report["emails_seen"] == 1
    assert report["tenders_unique"] == 2
    assert report["it_opportunity_previews"] == 1
    assert report["cards_not_sent"][0]["source_tender_id"] == "4661617"
    assert report["cards_not_sent"][0]["fields_not_sent"]["UF_CRM_13_SVCIT_SIGNAL_KIND"] == "tender-opportunity"
    assert "SECRET" not in str(report)
    assert report["qualified_real_leads"] == report["crm_writes"] == 0


def test_invite_parses_future_deadline_not_as_verified_publication():
    html = ('<div>Организатор закупки: АО «Тестовый заказчик»<p>Предмет закупки: '
            'Внедрение 1С ERP</p><p>Актуально до: 16.11.2026 09:00</p>'
            f'<a href="{href("4661617")}">Посмотреть процедуру №4661617</a></div>')
    rows = parse_email(mail(html, subject="Приглашение к участию в актуальных закупках"))["observations"]
    assert len(rows) == 1
    assert rows[0]["deadline_as_stated_in_email"] == "16.11.2026 09:00"
    assert rows[0]["published_at"] is None
    assert not rows[0]["deadline_verified_from_original"]
    assert rows[0]["buyer_inn"] is None


def test_distinct_revisions_preserved_but_one_tender_card():
    a = mail(bulletin(title="Сопровождение 1С:Бухгалтерия"))
    b = mail(bulletin(title="Сопровождение 1С и ЗУП"))
    report = build_bitrix_preview([a, b, a])
    assert report["emails_seen"] == 2
    assert report["tenders_unique"] == 1
    assert report["it_opportunity_previews"] == 1
    assert len(report["tenders"][0]["version_sha256"]) == 2


def test_reject_external_sender():
    with pytest.raises(InputRejected, match="SENDER_NOT_VENDOR_DOMAIN"):
        parse_email(mail(bulletin(), sender="marketplace@b2b-center.ru.attacker.invalid"))


def test_unknown_status_and_fns_are_always_hold():
    report = build_bitrix_preview([mail(bulletin())])
    row = report["tenders"][0]
    assert row["qualification_status"].startswith("HOLD_")
    assert row["buyer_fns_size"] == "UNKNOWN"
    assert row["contact_approved"] is False
    assert row["bitrix_write_allowed"] is False
    assert report["fns_micro_small_77_50"] == 0


def test_no_real_backend_or_credentials():
    report = build_bitrix_preview([mail(bulletin())])
    assert report["status"] == "B2B_EMAIL_TENDER_RESEARCH_PREVIEW_ONLY"
    assert "fields_not_sent" in report["cards_not_sent"][0]
    assert report["outreach"] == 0


@pytest.mark.parametrize("title", [
    "Внедрение системы полива сельхозполей",
    "Интеграция трубопроводов и строительных конструкций",
    "Техническое обслуживание котельной",
    "Маркировка труб и металлопроката",
    "Поставка медицинского оборудования",
    "Мониторинг загрязнения воздуха и почвы",
    "Поставка кабеля для строительных работ",
    "Система мониторинга вибрации промышленных механизмов",
])
def test_generic_non_it_tenders_never_reach_it_opportunity_queue(title):
    assert classify_services(title) == []


@pytest.mark.parametrize("title,expected", [
    ("Сопровождение 1С:Бухгалтерии и 1С ЗУП", "1C"),
    ("Внедрение Битрикс24 CRM", "CRM"),
    ("ИТ-аутсорсинг рабочих мест", "IT_SUPPORT"),
    ("Обслуживание серверов и сетевого оборудования", "SERVERS"),
    ("Внедрение электронного документооборота ЭДО", "KKT_EDO"),
    ("Поставка кассового оборудования ККТ", "KKT_EDO"),
    ("Интеграция интернет-магазина с базой данных", "INTEGRATION"),
    ("Поставка торгового оборудования и ТСД", "KKT_EDO"),
    ("Поставка компьютерных мониторов", "SERVERS"),
])
def test_strict_serviceit_categories_are_kept(title, expected):
    assert expected in classify_services(title)


def test_no_generic_integration_false_positive_in_actual_email_previews():
    a = bulletin(number="4661617", title="Сопровождение 1С:Бухгалтерии")
    b = bulletin(number="4652355", title="Интеграция строительных конструкций")
    c = bulletin(number="4661111", title="Поставка технических систем для обогрева")
    report = build_bitrix_preview([mail(a + b + c)])
    assert report["tenders_unique"] == 3
    assert report["it_opportunity_previews"] == 1
    assert [x["source_tender_id"] for x in report["cards_not_sent"]] == ["4661617"]
    assert report["qualified_real_leads"] == 0
    assert report["buyer_inn_verified"] == 0
    assert report["fns_micro_small_77_50"] == 0
    assert report["crm_writes"] == 0
