"""No-network unit protection for source-loss archive repair (V12)."""
from pathlib import Path
import pytest

from leadpilot.serviceit_b2b_archive_continuity_v12 import (
    archived_quotes, current_quote_count, repair, ArchiveBlocked
)
from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX

PUBLIC="https://zakupki.kontur.ru/4672802_6"
OLD_QUOTES=[
    ("Допуск / квалификация","Требования к участникам: заполнить опросный лист"),
    ("Оплата / расчёты","Оплата в течение 30 рабочих дней после отгрузки"),
]
BASE={k:"Результат подтверждён, архив не удалять" for k in AI_FIELDS}
BASE[PREFIX+"AI_STATUS"]="PUBLIC_RESEARCH_ROUTERAI_CITED"


def source_text(rows=OLD_QUOTES,url=PUBLIC):
    lines=[
        "СЕРВИС ИТ | ТЕНДЕРНОЕ ДОСЬЕ V10 | №4672802",
        "01 ПАСПОРТ ЗАКУПКИ","Заказчик: не установлен",
        "02 ПРЕДМЕТ И ТЕХНИЧЕСКОЕ ЗАДАНИЕ",
        "03 ДОПУСК И КОММЕРЧЕСКИЕ УСЛОВИЯ",
        "Допуск / квалификация: «Требования к участникам: заполнить опросный лист» [1]",
        "Оплата / расчёты: «Оплата в течение 30 рабочих дней после отгрузки» [1]",
        "07 ДОСЛОВНЫЕ ФРАГМЕНТЫ И ССЫЛКИ"]
    for i,(label,quote) in enumerate(rows,1):
        lines += [f"{i:02d}. {label} [1]",f"«{quote}»"]
    return "\n".join(lines+["08 РЕЕСТР ИСТОЧНИКОВ","[1] "+url,
                          "ГРАНИЦЫ ДОСТОВЕРНОСТИ"])


def make_current(with_evidence=False):
    result=dict(BASE)
    report="\n".join([
        "СЕРВИС ИТ | ТЕНДЕРНОЕ ДОСЬЕ V11 | №4672802",
        "01  ПАСПОРТ ЗАКУПКИ", "Срок подачи: не найден в доступных источниках",
        "03  ДОПУСК И КОММЕРЧЕСКИЕ УСЛОВИЯ",
        "Допуск / квалификация: не установлено в доступном тексте",
        "Оплата / расчёты: не установлено в доступном тексте",
        "05  НАПРАВЛЕНИЕ И ЗАДАЧИ ДЛЯ МЕНЕДЖЕРА",
        "07  ДОСЛОВНЫЕ ФРАГМЕНТЫ И ССЫЛКИ"])
    if with_evidence:
        report+="\n01. Допуск / квалификация [1]\n«Подтверждённый опыт работы»"
        report+="\n02. Оплата / расчёты [1]\n«Отсрочка оплаты 30 дней»"
    result[PREFIX+"AI_FULL_REPORT"]=report+"\nГРАНИЦЫ ДОСТОВЕРНОСТИ\n"
    result[PREFIX+"AI_MANAGER_BRIEF"]="ТЕНДЕР №4672802\nУЧАСТИЕ  требования не найдены"
    result[PREFIX+"AI_SUMMARY"]="№4672802\nДедлайн не найден"
    result[PREFIX+"AI_QUOTE"]="Публичных цитат пока нет"
    result[PREFIX+"AI_LIMITATIONS"]="Не подтверждено: условия участия"
    result[PREFIX+"AI_NEXT_ACTION"]="Сверить сроки"
    return result


def test_extract_from_verified_v10_quote_and_reference_index():
    old={**BASE,PREFIX+"AI_FULL_REPORT":source_text()}
    rows=archived_quotes(old)
    assert len(rows)==2
    assert rows[0]["quote"]==OLD_QUOTES[0][1]
    assert rows[0]["url"]==PUBLIC
    assert rows[1]["category"]=="PAYMENT"


def test_restore_never_calls_source_or_certifies_fns():
    original=make_current()
    old={**BASE,PREFIX+"AI_FULL_REPORT":source_text()}
    fixed,info=repair(original,old)
    report=fixed[PREFIX+"AI_FULL_REPORT"]
    assert info=={"status":"V12_ARCHIVE_PROOF_RESTORED","restored_quotes":2}
    assert "09  ВОССТАНОВЛЕННЫЕ ДОКАЗАТЕЛЬСТВА" in report
    assert "НЕ подтверждены" in report or "НЕ подтверждены".lower() in report.lower() or "НЕ подтверждены" in str(fixed)
    assert "Требования к участникам" in report
    assert "Оплата в течение 30 рабочих дней" in report
    assert PUBLIC in report
    assert "[АРХИВ V10, сверить актуальность]" in report
    assert "АРХИВ V10" in fixed[PREFIX+"AI_MANAGER_BRIEF"]
    assert fixed[PREFIX+"AI_STATUS"]==original[PREFIX+"AI_STATUS"]
    assert fixed[PREFIX+"AI_HYPOTHESIS"]==original[PREFIX+"AI_HYPOTHESIS"]
    assert fixed[PREFIX+"AI_RECOMMENDATION"]==original[PREFIX+"AI_RECOMMENDATION"]
    assert set(fixed)==AI_FIELDS
    assert all(len(v)<=(12000 if k==PREFIX+"AI_FULL_REPORT" else 4096)
               for k,v in fixed.items())
    assert "ФНС подтверждено" not in str(fixed)


def test_does_not_degrade_richer_existing_v11():
    old={**BASE,PREFIX+"AI_FULL_REPORT":source_text()}
    fresh=make_current(with_evidence=True)
    updated,info=repair(fresh,old)
    assert updated==fresh
    assert info["restored_quotes"]==0
    assert current_quote_count(fresh)==2


def test_archive_rejects_private_source_and_unattributed_quotes():
    for url in ("https://127.0.0.1/private", "http://zakupki.kontur.ru/4672802",
                "https://evil.example/4672802"):
        old={**BASE,PREFIX+"AI_FULL_REPORT":source_text(url=url)}
        assert archived_quotes(old)==[]
        fixed,info=repair(make_current(),old)
        assert info["restored_quotes"]==0
    malformed={**BASE,PREFIX+"AI_FULL_REPORT":"V10 without a source archive"}
    with pytest.raises(ArchiveBlocked):
        archived_quotes(malformed)


def test_worker_is_separate_one_use_and_never_model_or_outbound_mail():
    text=Path("leadpilot/serviceit_vps_b2b_archive_v12_worker.py").read_text("utf8")
    for required in ("serviceit_vps_b2b_dossier_v11_receipts",
                     "serviceit_vps_b2b_dossier_v12_continuity_receipts",
                     "pg_try_advisory_lock", "before_fields JSONB NOT NULL",
                     "WRITE_INTENT", "V12_EXACT_NINE_READBACK_NOT_VERIFIED"):
        assert required in text
    for forbidden in ("crm.item.add","crm.deal.add","tasks.task.add",
                      "crm.contact.add","GIGACHAT_CREDENTIALS",
                      "investigate_routerai","brave_mirror_search"):
        assert forbidden not in text
