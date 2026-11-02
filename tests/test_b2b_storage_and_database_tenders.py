"""Regression for actual procurement subject types that previously blocked intake.

All inputs are synthetic events. Never invokes GitHub, Bitrix or GigaChat.
"""
import json

import pytest

from leadpilot.b2b_email_opportunity_bridge import classify_services
from leadpilot.b2b_github_to_bitrix import BEGIN, REPO, OWNER, notice_from_event, hold_payload


SUBJECTS = (
    ("4666206",
     "Выбор контрагента на поставку Систем Хранения Данных для нужд АО «Дикси Юг»",
     "SERVERS"),
    ("4664763",
     'Поставка ноутбука-рабочей станции по техническим требованиям для ПАО "НЛМК", г. Москва, с подписанием NDA. Аналоги рассматриваются.',
     "SERVERS"),
    ("4665690",
     "Выбор поставщика ПО и технической поддержки системы объектного хранения данных S3",
     "IT_SUPPORT"),
    ("4665948",
     "Право заключения договора оказания услуг по техническому обслуживанию базы данных «Информационно-правовая система АО «СО ЕЭС» (ИПС)",
     "IT_SUPPORT"),
)


@pytest.mark.parametrize("number,subject,expected", SUBJECTS)
def test_important_storage_and_database_tenders_pass_honest_hold(number, subject, expected):
    kinds = classify_services(subject)
    assert expected in kinds
    notice = {
        "schema": "serviceit-b2b-notice-v1",
        "source": "B2B-Center",
        "email_message_id": "abcdef1234567890",
        "tender_id": number,
        "source_url": "https://www.b2b-center.ru/market/it-procurement/tender-" + number + "/",
        "subject": subject,
        "organizer": 'АО "Тест"',
        "buyer_inn": None, "fns_msp": None, "region": None,
        "original_publication_verified": False,
        "contact_approved": False,
        "qualified_lead": False,
    }
    event = {
        "action": "created",
        "repository": {"full_name": REPO},
        "issue": {"number": 66},
        "sender": {"login": OWNER},
        "comment": {"user": {"login": OWNER},
                    "body": BEGIN + json.dumps(notice, ensure_ascii=False)},
    }
    extracted, categories = notice_from_event(event)
    assert extracted["tender_id"] == number
    assert expected in categories
    payload = hold_payload(extracted, categories)
    fields = payload["fields"]
    assert fields["categoryId"] == 21
    assert fields["UF_CRM_13_SVCIT_AI_STATUS"] == "NOT_RUN_SOURCE_UNVERIFIED"
    assert fields["UF_CRM_13_SVCIT_SIGNAL_ID"] == "B2B-CENTER-" + number
    assert "UF_CRM_13_SVCIT_LEAD_SCORE" not in fields
    assert "UF_CRM_13_SVCIT_SCORE_VERSION" not in fields
    assert "UF_CRM_13_SVCIT_AI_SCORE_BADGE" not in fields
    assert "phone" not in fields and "contactIds" not in fields


@pytest.mark.parametrize("title", [
    "Техническое обслуживание складского оборудования",
    "Договор аренды площадей под склад хранения",
    "Обслуживание базы отдыха на озере",
    "Ремонт системы хранения зерна",
    "Обслуживание информационных стендов и рекламных конструкций",
])
def test_expanded_it_categories_do_not_include_generic_non_it_work(title):
    assert classify_services(title) == []
