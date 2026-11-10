"""V10 source-aware Bitrix dossier layout: zero network, zero fake facts."""
from pathlib import Path

import pytest

from leadpilot.serviceit_b2b_dossier_v10 import (
    compose, evidence_rows, routing, DossierV10Blocked, original_nine,
)
from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX
from leadpilot.serviceit_b2b_routerai_deep import (
    public_documents, validate_inference,
)
from leadpilot.serviceit_vps_b2b_dossier_v10_worker import (
    compute_digest, non_ai_unchanged,
)

URL = "https://www.b2b-center.ru/market/tender-4673595/"
NOTICE = {
    "source": "B2B-Center", "tender_id": "4673595",
    "source_url": URL, "subject": "Предоставление ИТ-специалистов",
    "email_received_at": "2026-11-10T13:00:00+03:00",
    "organizer_as_named_in_email": "Тестовый организатор",
    "deadline_as_stated_in_email": "20.11.2026 16:00",
    "source_service_groups": ["IT_STAFFING"],
}
OLD = {field: "СТАРЫЙ_ОТЧЕТ_ТОЛЬКО_В_АРХИВЕ" for field in AI_FIELDS}
OLD[PREFIX + "AI_STATUS"] = "PUBLIC_RESEARCH_ROUTERAI_CITED"
PUBLIC = {
    "status": "PUBLIC_PAGES_INSPECTED",
    "checked_at": "2026-11-10T13:00:00+00:00",
    "pages": [{"url": URL, "excerpt":
        "Закупка 4673595. Предоставление ИТ-специалистов. "
        "Срок подачи заявок до 25.11.2026 18:00. "
        "Требования к участникам: опыт не менее трех лет. "
        "Предоставить подтверждающие документы. "
        "Оплата после завершения работ согласно договору. "
        "Обеспечение заявки предусмотрено документацией."
    }],
}


def inference():
    docs = public_documents(PUBLIC)
    return validate_inference({
        "overview": "Требуется ИТ-команда",
        "service_fit": "ИТ-аутстаффинг",
        "likely_need": "Возможен аутстаффинг",
        "facts": [
            {"category": "DEADLINE", "source_id": 1,
             "quote": "Срок подачи заявок до 25.11.2026 18:00",
             "finding": "заявки до 25.11"},
            {"category": "ELIGIBILITY", "source_id": 1,
             "quote": "Требования к участникам: опыт не менее трех лет",
             "finding": "стаж не менее трех лет"},
            {"category": "PAYMENT", "source_id": 1,
             "quote": "Оплата после завершения работ согласно договору",
             "finding": "оплата постфактум"},
            {"category": "GUARANTEE", "source_id": 1,
             "quote": "Обеспечение заявки предусмотрено документацией",
             "finding": "есть обеспечение заявки"},
            {"category": "BUDGET", "source_id": 1,
             "quote": "небывалый бюджет 500 млн рублей",
             "finding": "бюджет 500 млн"},
        ],
        "missing": ["бюджет и условия оплаты"],
        "manager_questions": ["Уточнить число специалистов"],
    }, docs)


def test_real_sourced_deadline_overrides_unverified_email_deadline():
    fields, coverage = compose(NOTICE, OLD, PUBLIC, inference())
    brief = fields[PREFIX + "AI_MANAGER_BRIEF"]
    summary = fields[PREFIX + "AI_SUMMARY"]
    full = fields[PREFIX + "AI_FULL_REPORT"]
    assert "25.11.2026 18:00" in brief
    assert "25.11.2026 18:00" in summary
    assert "Требования к участникам: опыт" in full
    assert "Оплата после завершения работ" in full
    assert "Обеспечение заявки" in full
    assert "500 млн" not in full and "500 млн" not in summary
    assert "СТАРЫЙ_ОТЧЕТ_ТОЛЬКО_В_АРХИВЕ" not in full
    assert full.count(URL) == 1
    assert "КОМУ ПЕРЕДАТЬ" in brief
    assert coverage["deadline_public"] is True
    assert coverage["pages"] == 1 and coverage["buyer_public"] is False
    assert set(fields) == AI_FIELDS
    assert all(len(v) <= (12000 if k.endswith("AI_FULL_REPORT") else 4096)
               for k, v in fields.items())


def test_private_vendor_email_deadline_has_visible_qualification():
    fields, coverage = compose(NOTICE, OLD, {"pages": []}, {"facts": []})
    brief = fields[PREFIX + "AI_MANAGER_BRIEF"]
    assert "20.11.2026 16:00 (только уведомление; сверить)" in brief
    assert "25.11.2026" not in brief
    assert "не найдена" in brief
    assert "не подтверждён" in brief
    assert "сначала" in brief.casefold() or "Сначала" in brief
    assert coverage["deadline_public"] is False
    assert "СТАРЫЙ_ОТЧЕТ_ТОЛЬКО_В_АРХИВЕ" not in fields[PREFIX + "AI_FULL_REPORT"]


def test_dossier_no_date_or_person_invented_if_all_sources_missing():
    notice = {**NOTICE, "deadline_as_stated_in_email": None,
              "organizer_as_named_in_email": None}
    fields, _ = compose(notice, OLD, {}, {})
    report = fields[PREFIX + "AI_FULL_REPORT"]
    assert "не найден в доступных источниках" in report
    assert "не установлен" in report
    assert "2026 16:00" not in report
    assert "Не подтверждено:" in fields[PREFIX + "AI_LIMITATIONS"]
    assert "не разрешать" not in report.lower()


def test_fake_or_uncited_model_facts_do_not_enter_dossier():
    bad = {"facts": [{"category": "DEADLINE", "quote": "до 99.99.9999",
                      "url": URL}]}
    _, rows = evidence_rows(PUBLIC, bad)
    assert rows == []
    wrong = {"facts": [{"category": "DEADLINE",
                        "quote": "Срок подачи заявок до 25.11.2026 18:00",
                        "url": "https://unrelated.example/"}]}
    assert evidence_rows(PUBLIC, wrong)[1] == []


def test_routing_is_suggested_internal_team_not_auto_contact():
    route, question = routing(["IT_STAFFING"], NOTICE["subject"])
    assert "Аутстаффинг" in route
    assert "FTE" in question
    assert "Партнёры" not in route  # no particular counterparty selected
    assert "Битрикс24" in routing(["CRM"], "Интеграция CRM")[0]
    assert "1С" in routing(["1C"], "Поддержка 1С")[0]


def test_malformed_original_and_source_rejected():
    with pytest.raises(DossierV10Blocked):
        original_nine({})
    with pytest.raises(DossierV10Blocked, match="SOURCE_URL_INVALID"):
        compose({**NOTICE, "source_url": "https://evil.example/"},
                OLD, PUBLIC, inference())


def test_digest_and_non_ai_gate():
    salt = b"0123456789abcdef"
    assert compute_digest(OLD, salt) == compute_digest(dict(OLD), salt)
    assert compute_digest(OLD, salt) != compute_digest(
        {**OLD, PREFIX + "AI_SUMMARY": "changed"}, salt)
    assert non_ai_unchanged({"id": 11, "company": "A", PREFIX+"AI_SUMMARY": "v1"},
                            {"id": 11, "company": "A", PREFIX+"AI_SUMMARY": "v2"})
    assert not non_ai_unchanged({"id": 11, "company": "A"},
                                {"id": 11, "company": "B"})


def test_vps_coordinator_calls_dossier_after_public_inn_only():
    code = Path("leadpilot/serviceit_vps_worker.py").read_text("utf8")
    assert code.index('public_inn = {"status"') < code.index(
        "from .serviceit_vps_b2b_dossier_v11_worker import worker_once")
    assert "B2B_DOSSIER_V11_CHECK_FAILED" in code
    worker = Path("leadpilot/serviceit_vps_b2b_dossier_v11_worker.py").read_text("utf8")
    assert "pg_try_advisory_lock" in worker
    assert "before_fields JSONB NOT NULL" in worker
    assert "'WRITE_INTENT'" in worker
    assert "EXACT_NINE_READBACK_NOT_VERIFIED" in worker
    for forbidden in ("tasks.task.add", "crm.contact.add", "crm.deal.add",
                      "GIGACHAT_CREDENTIALS", "crm.item.add"):
        assert forbidden not in worker
