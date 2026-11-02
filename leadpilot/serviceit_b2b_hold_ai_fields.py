"""Fail-closed manager-readable analysis for each newly delivered B2B HOLD.

Generated from unverified supplier-email subject, NOT GigaChat inference.
Automatically populates the SAME NINE existing AI fields as DEMO #3,
but never manufactures a score, citation, buyer INN, proof of purchasing
intent, GigaChat completion, contact permission or human approval.

This is safe analysis metadata, not a lead or a model-generated report.
"""
from __future__ import annotations

import re
import math

from .b2b_email_opportunity_bridge import KINDS

PREFIX="UF_CRM_13_SVCIT_"
AI_SUFFIXES=(
    "AI_STATUS","AI_SUMMARY","AI_QUOTE","AI_HYPOTHESIS","AI_LIMITATIONS",
    "AI_RECOMMENDATION","AI_NEXT_ACTION","AI_MANAGER_BRIEF","AI_FULL_REPORT",
)
AI_FIELDS=frozenset(PREFIX+x for x in AI_SUFFIXES)
assert len(AI_FIELDS)==9


class HoldAnalysisRejected(ValueError):
    pass


def _check(value, maximum=240):
    if (type(value) is not str or not 1<=len(value)<=maximum
            or re.search(r"[<>\x00-\x1f\x7f]|https?://|mailto:|@|\{\{|\}\}",value,re.I)):
        raise HoldAnalysisRejected("SOURCE_TITLE_UNSAFE_OR_EMPTY")
    return value


def hold_analysis_fields(notice: dict, services: list[str], *, ai: dict | None = None) -> dict[str,str]:
    """Safe descriptive fields for *every* exact unverified B2B notice.

    GigaChat must be started by a separate authorized enrichment worker only
    after checking source content and privacy constraints. This creates NO score.
    """
    if not isinstance(notice,dict):
        raise HoldAnalysisRejected("NOTICE_INVALID")
    if (notice.get("source")!="B2B-Center"
            or notice.get("original_publication_verified") is not False
            or notice.get("qualified_lead") is not False
            or notice.get("contact_approved") is not False
            or notice.get("buyer_inn") is not None
            or notice.get("fns_msp") is not None):
        raise HoldAnalysisRejected("NOT_UNVERIFIED_B2B_HOLD")
    tid=notice.get("tender_id")
    if not isinstance(tid,str) or not re.fullmatch(r"\d{5,12}",tid):
        raise HoldAnalysisRejected("TENDER_ID_INVALID")
    subject=_check(notice.get("subject"),240)
    if (not isinstance(services,list) or not 1<=len(services)<=5
            or any(x not in set(KINDS)|{"INTEGRATION"} for x in services)):
        raise HoldAnalysisRejected("SERVICES_INVALID")
    service_text=", ".join(services)
    model="NOT_RUN"
    if ai is not None:
        from .ai import ALLOWED
        confidence=ai.get("confidence") if isinstance(ai,dict) else None
        quote=ai.get("evidence_quote") if isinstance(ai,dict) else None
        if (not isinstance(ai,dict) or ai.get("provider")!="gigachat"
                or ai.get("origin")!="AI_hypothesis"
                or ai.get("quality_status")!="complete"
                or ai.get("service") not in ALLOWED
                or ai.get("service")=="Требуется проверка"
                or isinstance(confidence,bool) or not isinstance(confidence,(int,float))
                or not math.isfinite(confidence) or not 0<confidence<=1
                or not isinstance(quote,str) or not quote.strip()
                or quote not in subject or not isinstance(ai.get("model"),str)):
            raise HoldAnalysisRejected("UNVERIFIED_MODEL_OUTPUT")
        model=_check(ai["model"],80)
    status="HYPOTHESIS_UNVERIFIED_SOURCE_UNVERIFIED" if ai else "NOT_RUN_SOURCE_UNVERIFIED"
    summary=("Получено уведомление B2B-Center №"+tid+
             (". Гипотеза GigaChat получена, но потребность и заказчик не подтверждены."
              if ai else ". Потребность и заказчик не подтверждены; AI-анализ не запускался."))
    quote="Тема письма поставщика: "+subject
    hypothesis=(("Не проверенная моделью гипотеза: возможна закупка по направлению "+
                 service_text+". Требуется изучить оригинальное извещение.")
                if ai is None else
                ("GigaChat высказал НЕПРОВЕРЕННУЮ гипотезу о применимости услуги «"+
                 _check(ai["service"],80)+"». Дословная цитата из темы уведомления: «"+
                 _check(ai["evidence_quote"],240)+"». Покупка и первоисточник не подтверждены."))
    limits=("Не проверены оригинал закупки, дата, актуальность, ИНН заказчика, ФНС, "
            "существующие клиенты, CRM-дубли, стоп-лист и право контакта. "
            "Тема письма не доказывает реальную потребность или согласие.")
    rec="Услуга для проверки по ключевым словам: "+service_text
    next_action=("Сверить оригинальное извещение и заказчика, статус и сроки. "
                 "Не звонить и не создавать коммерческий лид до независимой проверки.")
    brief=("Статус: HOLD, не квалифицированный лид.\n"
           "1. Открыть первоисточник, проверить статус и дату.\n"
           "2. Установить заказчика и его ИНН, сверить ФНС.\n"
           "3. Проверить клиентскую базу, дубли и стоп-лист.\n"
           "4. Проверить применимость услуг, договор и правовое основание связи.\n"
           "5. Только после проверок направить на реальный Score и GigaChat.")
    full=(
        "Сервис ИТ | Автоматический предварительный разбор HOLD\n"+
        "Источник: уведомление B2B-Center, закупка №"+tid+"\n"+
        ("AI-провайдер: GigaChat / "+model+" (гипотеза, не доказательство)\\n"
         if ai else "AI-провайдер: NOT_RUN (GigaChat не вызывался)\\n")+
        "Lead Score: НЕ РАССЧИТАН — отсутствуют подтверждённые факты\n"+
        "Цветной приоритет: НЕ НАЗНАЧЕН\n\n"+
        "Краткий вывод\n"+summary+"\n\n"+
        "Цитата только из темы уведомления (не первоисточник)\n"+quote+"\n\n"+
        "Гипотеза, НЕ факт\n"+hypothesis+"\n\n"+
        "Ограничения\n"+limits+"\n\n"+
        "Рекомендуемое направление\n"+rec+"\n\n"+
        "План менеджера\n"+brief+"\n\n"+
        "Следующее действие\n"+next_action
    )
    vals=(status,summary,quote,hypothesis,limits,rec,next_action,brief,full)
    fields={PREFIX+key: val for key,val in zip(AI_SUFFIXES,vals)}
    if (set(fields)!=AI_FIELDS or any(type(v) is not str or
          not 1<=len(v)<=12000 for v in fields.values())):
        raise HoldAnalysisRejected("AI_HOLD_FIELDS_INVALID")
    return fields
