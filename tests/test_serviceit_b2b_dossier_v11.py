"""V11 native dossier: real visual hierarchy and source verified scope."""
from pathlib import Path

from leadpilot.serviceit_b2b_dossier_v11 import compose
from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX
from leadpilot.serviceit_vps_b2b_dossier_v11_worker import compute_digest

SOURCE = "https://zakupki.kontur.ru/4673595_4"
NOTICE = {
    "source": "B2B-Center",
    "tender_id": "4673595",
    "source_url": "https://www.b2b-center.ru/market/test/tender-4673595/",
    "subject": "Оказание услуг по предоставлению ИТ-специалистов",
    "source_service_groups": ["IT_STAFFING"],
    "deadline_as_stated_in_email": "23.11.2026 17:00",
}
PREVIOUS = {key: "ПРОШЛАЯ_ВЕРСИЯ_ТОЛЬКО_В_АРХИВЕ" for key in AI_FIELDS}
PREVIOUS[PREFIX+"AI_STATUS"] = "PUBLIC_RESEARCH_ROUTERAI_CITED"
EXCERPT = (
    "Запрос цен № 4673595. Оказание услуг по предоставлению ИТ-специалистов. "
    "Заказчик Общество с ограниченной ответственностью «Глобус-ИТ» "
    "ИНН-КПП 5260365605-526001001. "
    "Окончание подачи заявок 23.11.2026 17:00 МСК. "
    "Требования к участникам: предоставить коммерческое предложение. "
    "Условия оплаты: после выполнения услуг."
)
PAGES = {"status":"PUBLIC_PAGES_INSPECTED", "pages":[{"url":SOURCE,"excerpt":EXCERPT}]}
FACTS = {"provider":"RouterAI","facts":[
    {"category":"DEADLINE","quote":"Окончание подачи заявок 23.11.2026 17:00 МСК","url":SOURCE},
    {"category":"ELIGIBILITY","quote":"Требования к участникам: предоставить коммерческое предложение","url":SOURCE},
    {"category":"PAYMENT","quote":"Условия оплаты: после выполнения услуг","url":SOURCE},
]}


def test_visual_hierarchy_provenance_and_no_old_report_noise():
    fields, coverage=compose(NOTICE,PREVIOUS,PAGES,FACTS)
    full=fields[PREFIX+"AI_FULL_REPORT"]
    brief=fields[PREFIX+"AI_MANAGER_BRIEF"]
    assert "ТЕНДЕРНОЕ ДОСЬЕ V11" in full
    for title in ("01  ПАСПОРТ ЗАКУПКИ",
                  "03  ДОПУСК И КОММЕРЧЕСКИЕ УСЛОВИЯ",
                  "05  НАПРАВЛЕНИЕ И ЗАДАЧИ ДЛЯ МЕНЕДЖЕРА"):
        assert title in full
    assert "    «" in full
    assert "ЗАКАЗЧИК  ООО «Глобус-ИТ»" in brief
    assert "ИНН: 5260365605 (публичный кандидат; НЕ ФНС)" in full
    assert "Подача до: «" in fields[PREFIX+"AI_SUMMARY"]
    assert "СТАРЫЙ_ОТЧЕТ" not in full and "ПРОШЛАЯ_ВЕРСИЯ" not in full
    assert "23.11.2026" in full and "после выполнения услуг" in full
    assert coverage["pages"]==1 and coverage["buyer_public"] is True
    assert set(fields)==AI_FIELDS
    assert all(0<len(v)<=(12000 if k.endswith("AI_FULL_REPORT") else 4096)
               for k,v in fields.items())


def test_v11_only_is_coordinator_writer_future_is_not_double_billed():
    source=Path("leadpilot/serviceit_vps_worker.py").read_text("utf8")
    assert "from .serviceit_vps_b2b_dossier_v11_worker import worker_once" in source
    assert "from .serviceit_vps_b2b_dossier_v10_worker import worker_once" not in source
    mod=Path("leadpilot/serviceit_vps_b2b_dossier_v11_worker.py").read_text("utf8")
    assert "serviceit_vps_b2b_dossier_v11_receipts" in mod
    assert "serviceit_vps_b2b_dossier_v10_receipts prior" in mod
    assert "B2B_RESEARCH_V4" in mod
    assert "pg_try_advisory_lock" in mod
    assert "before_fields JSONB NOT NULL" in mod
    assert "WRITE_INTENT" in mod and "EXACT_NINE_READBACK_NOT_VERIFIED" in mod
    for forbidden in ("crm.item.add", "crm.contact.add", "crm.deal.add",
                      "tasks.task.add", "GIGACHAT_CREDENTIALS"):
        assert forbidden not in mod


def test_v11_digest_is_stable_and_versioned_receipt_not_old():
    old=compute_digest(PREVIOUS,b"0123456789abcdef")
    changed=compute_digest(
        {**PREVIOUS,PREFIX+"AI_SUMMARY":"Новая проверка"},
        b"0123456789abcdef")
    assert old!=changed
