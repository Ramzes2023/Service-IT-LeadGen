"""Evidence-first 3-way ServiceIT signal triage and manager-task draft.

Source-neutral. Pure Python: NO network, model call, CRM write or task.add.
Direct IT service requests, tenders and staffing vacancies are NOT equivalent
commercial leads. Unverified source text can suggest a type but NEVER prove
company contact, current deadline, consent, or sales readiness.

Manager TЗ (2026 v3): Moscow/MO first; Ingushetia second; services 1C,
outsourcing, cash registers, labeling, EDI, CryptoPro, Bitrix24, networks,
equipment/integrations. All assertions carry provenance; final Lead Score is
determined by existing company/CRM/stoplist/legal gate, not by this signal
heuristic or a free-form GigaChat response.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import re
from typing import Mapping
from urllib.parse import urlsplit

TRIAGE_VERSION = "serviceit-3-signals-2026-11-v1"
TENDER = "TENDER"
VACANCY = "VACANCY"
DIRECT = "DIRECT_SERVICE"
REVIEW = "REVIEW_REQUIRED"
BADGES = {
    TENDER: "📑 ТЕНДЕР",
    VACANCY: "👥 ВАКАНСИЯ",
    DIRECT: "🎯 ЗАПРОС УСЛУГ",
    REVIEW: "⚪ УТОЧНИТЬ ТИП",
}
CATEGORY_NAMES = {
    TENDER: "Коммерческая закупка / тендер",
    VACANCY: "Кадровый сигнал — возможная потребность",
    DIRECT: "Прямой запрос на услуги",
    REVIEW: "Тип сигнала пока не доказан",
}
SERVICES = {
    "1С": ("1с", "1c", "зуп", "бухгалтерии предприятия", "унф", "1с:ут", "1c:"),
    "IT-аутсорсинг": ("it-аутсорс", "ит-аутсорс", "администрирован", "сисадмин",
                     "системного администратора", "техподдержк", "it support"),
    "Битрикс24 / CRM": ("битрикс", "bitrix", "crm", "срм"),
    "ККТ / маркировка": ("ккт", "касс", "честный знак", "фискализац", "маркировк"),
    "ЭДО / ЭП / КриптоПро": ("эдо", "электронный документооборот", "криптопро", "эцп", "эп "),
    "Серверы / сети": ("сервер", "сетев", "wi-fi", "локальной сети", "инфраструктур"),
    "IT-оборудование": ("оборудован", "сканер", "рабочих мест", "компьютерн"),
    "Интеграции / ИИ": ("интеграц", "api", "чат-бот", "искусственн", "ии-"),
}
TENDER_WORDS = (
    "тендер", "закупк", "извещен", "конкурс", "аукцион", "запрос котиров",
    "запрос предложен", "procurement", "rfp", "rfq", "подача заявки",
)
VACANCY_WORDS = (
    "ваканси", "ищем в штат", "требуется сотрудник", "на работу",
    "программист 1с", "программист 1c", "системный администратор",
    "программиста 1с", "программиста 1c", "найм", "соискател",
    "откликнуться на ваканс", "размещена ваканси", "штатн",
)
DIRECT_WORDS = (
    "ищем подрядчика", "ищет подрядчика", "нужен подрядчик",
    "ищем поставщика услуг", "заказать сопровождение", "нужно сопровождение",
    "нужны услуги", "запрос на услуги", "заявка на обслуживание",
    "требуется аутсорсинг", "ищем исполнителя", "ищем компанию для",
)
CONTACT_PROHIBITION = (
    "услуги не предлагать", "не предлагать услуги", "подрядчикам не звонить",
    "аутсорсинг не предлагать", "просьба не беспокоить с предложениями",
    "коммерческие предложения не рассматриваем",
    "агентствам и подрядчикам не беспокоить",
)
STAFF_ONLY = (
    "только штатный сотрудник", "только в штат", "исключительно в штат",
    "не рассматриваем аутсорсинг", "без аутсорсинга",
)
KIND_HINTS = {
    TENDER: ("tender", "procurement", "b2b", "zakup", "тендер", "закупка"),
    VACANCY: ("vacancy", "job", "trudvsem", "работа", "вакансия", "1c_vacancy", "it_vacancy"),
    DIRECT: ("direct", "request", "inbound", "crm_need", "service", "лид", "заявка"),
}
MAX_TEXT = 5500


class TriageBlocked(ValueError):
    pass


def _require(ok: bool, code: str):
    if not ok:
        raise TriageBlocked(code)


def _clean(value, size: int = MAX_TEXT) -> str:
    _require(isinstance(value, str) and len(value) <= size, "UNSAFE_TEXT_LENGTH_OR_TYPE")
    _require(not any(ord(c) < 32 and c not in "\n\t" for c in value)
             and "<script" not in value.lower()
             and "{{" not in value and "}}" not in value,
             "UNSAFE_TEXT_OR_CONTROL")
    return value.strip()


def _origin(url: str) -> str:
    _require(isinstance(url, str) and 10 <= len(url) <= 700, "EVIDENCE_URL_INVALID")
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    _require(parsed.scheme == "https" and bool(re.fullmatch(
        r"[a-zA-Z0-9][a-zA-Z0-9.-]{2,250}", host))
        and "." in host and not host.endswith((".local", ".localhost", ".internal"))
        and not parsed.username and not parsed.password and parsed.port in (None, 443),
        "EVIDENCE_URL_NOT_PUBLIC_HTTPS")
    return host.lower()


def _date(value):
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return t.astimezone(timezone.utc) if t.tzinfo else None
    except ValueError:
        return None


def _type(text: str, kind: str, source: str) -> tuple[str, str]:
    raw = (text + "\n" + kind + "\n" + source).casefold().replace("ё", "е")
    declared = kind.casefold()
    hints = [
        t for t, words in KIND_HINTS.items()
        if declared and any(x in declared for x in words)
    ]
    # Explicit tender notice prevails over 'services' listed in the subject.
    tender = any(x in raw for x in TENDER_WORDS) or TENDER in hints or "b2b-center" in source.lower()
    vacancy = any(x in raw for x in VACANCY_WORDS) or VACANCY in hints
    direct = any(x in raw for x in DIRECT_WORDS) or DIRECT in hints
    if tender and vacancy:
        return REVIEW, "CONFLICTING_TENDER_AND_VACANCY_SIGNAL"
    if tender:
        return TENDER, "TENDER_NOTICE_OR_DECLARED_KIND"
    if vacancy:
        return VACANCY, "RECRUITMENT_NOTICE_OR_DECLARED_KIND"
    if direct:
        return DIRECT, "SERVICE_REQUEST_SIGNAL"
    return REVIEW, "TYPE_EVIDENCE_INSUFFICIENT"


def _public_contacts(evidence: Mapping | None, now: datetime) -> list[dict]:
    if not isinstance(evidence, Mapping):
        return []
    official = evidence.get("official_company_url")
    contacts = evidence.get("public_contacts", [])
    if not official or not contacts or not isinstance(contacts, list) or len(contacts) > 8:
        return []
    try:
        host = _origin(official)
    except TriageBlocked:
        return []
    found = []
    for c in contacts:
        if not isinstance(c, Mapping) or set(c) - {
                "kind", "value", "source_url", "verified_at", "role", "provenance"}:
            continue
        if c.get("provenance") != "OFFICIAL_COMPANY_PUBLIC":
            continue
        ts = _time(c.get("verified_at"))
        if ts is None or not timedelta(0) <= now - ts <= timedelta(days=30):
            continue
        try:
            if _origin(c.get("source_url")) != host:
                continue
        except TriageBlocked:
            continue
        kind, value = c.get("kind"), c.get("value")
        if not isinstance(value, str) or len(value) > 130:
            continue
        val = value.strip()
        if kind == "email" and re.fullmatch(
                r"[A-Za-z0-9_.+\-]{1,64}@[A-Za-z0-9.-]{3,180}\.[A-Za-z]{2,20}", val):
            # Public company domain only; HR/private mailbox may not be used.
            if val.split("@")[-1].lower() != host.removeprefix("www."):
                continue
        elif kind == "phone":
            digits = re.sub(r"[^0-9]", "", val)
            if len(digits) != 11 or digits[0] not in "78":
                continue
            val = "+7" + digits[1:]
        else:
            continue
        found.append({"kind": kind, "value": val, "source_url": c["source_url"],
                      "verified_at": ts.isoformat(), "provenance": "OFFICIAL_COMPANY_PUBLIC"})
    return found


def classify_signal(data: Mapping, *, now: datetime,
                    evidence: Mapping | None = None) -> dict:
    """Deterministic preparatory decision; never a qualified Lead Score.

    The evidence parameter is ONLY to be supplied by a separately authorised,
    audited, live public-source adapter, not from a raw CRM field or model JSON.
    """
    _require(isinstance(data, Mapping) and isinstance(now, datetime)
             and now.tzinfo is not None, "TRIAGE_INPUT_INVALID")
    now = now.astimezone(timezone.utc)
    title = _clean(data.get("title", ""), 260)
    desc = _clean(data.get("description", ""), MAX_TEXT)
    kind = _clean(data.get("signal_kind", ""), 120)
    source = _clean(data.get("source", ""), 140)
    region = _clean(data.get("region", ""), 140)
    company = _clean(data.get("company", ""), 220)
    combined = (title + "\n" + desc)[:MAX_TEXT]
    typ, reason = _type(combined, kind, source)
    lowered = combined.casefold().replace("ё", "е")
    services = [s for s, words in SERVICES.items() if any(w in lowered for w in words)]
    prohibits = any(x in lowered for x in CONTACT_PROHIBITION)
    staff_only = any(x in lowered for x in STAFF_ONLY)
    data_evidence = evidence if isinstance(evidence, Mapping) else {}
    verified = bool(data_evidence.get("source_rechecked") is True
                    and data_evidence.get("adapter_auth") == "TRUSTED_SOURCE_ADAPTER_V1"
                    and data_evidence.get("source_url"))
    if verified:
        try:
            _origin(data_evidence["source_url"])
        except TriageBlocked:
            verified = False
    published = _date(data_evidence.get("published_date")) if verified else None
    last_checked = _time(data_evidence.get("checked_at")) if verified else None
    # A stale or non-timezone-aware inspection cannot prove a notice is open.
    fresh_check = bool(last_checked and timedelta(0) <= now-last_checked <= timedelta(hours=72))
    deadline = _time(data_evidence.get("deadline_at")) if verified else None
    open_now = data_evidence.get("is_open") is True and fresh_check
    if deadline and deadline <= now:
        freshness = "EXPIRED_DEADLINE"
    elif verified and open_now and (published is None or 0 <= (now.date()-published).days <= 45):
        freshness = "SOURCE_CURRENT_VERIFIED"
    elif verified and published and (now.date()-published).days > 45:
        freshness = "POSSIBLY_STALE"
    else:
        freshness = "UNVERIFIED_CURRENT_STATUS"
    contacts = _public_contacts(data_evidence if verified else None, now)
    if not services:
        services = ["Определить услугу после проверки исходного запроса"]
    # Indicative sorting is separate from formal Lead Score: not qualified.
    factors = {
        "region": 20 if "моск" in region.casefold() else (10 if "ингуш" in region.casefold() else 0),
        "signal": {TENDER: 20, VACANCY: 10, DIRECT: 30, REVIEW: 0}[typ],
        "service_fit": 20 if services[0] != "Определить услугу после проверки исходного запроса" else 0,
        "source_freshness": 15 if freshness == "SOURCE_CURRENT_VERIFIED" else 0,
        "verified_company_contact": 10 if contacts else 0,
        "negative": -45 if (prohibits or freshness == "EXPIRED_DEADLINE") else (-15 if staff_only else 0),
    }
    priority = min(100, max(0, sum(factors.values())))
    if freshness == "EXPIRED_DEADLINE" or prohibits:
        disposition = "DO_NOT_CONTACT_REVIEW_ONLY"
    elif typ == REVIEW or freshness != "SOURCE_CURRENT_VERIFIED":
        disposition = "HOLD_VERIFY_SOURCE"
    else:
        disposition = "MANAGER_VERIFY_LEGAL_AND_CRM"
    if typ == TENDER:
        action = ("Проверить оригинальную закупку, регламент, крайний срок, условия участия, "
                  "обеспечение, комплект документов, релевантность услуг, партнёров "
                  "и ответственного за тендер. Передать на тендерную экспертизу и "
                  "уполномоченным партнёрам только по решению менеджера до дедлайна.")
    elif typ == VACANCY:
        action = ("Перепроверить вакансию и компанию; выяснить, нужен ли именно штатный "
                  "сотрудник или допустимо сопровождение 1С/ИТ по договору. "
                  "Подготовить аргумент постоянного сервиса вместо найма, но не "
                  "контактировать при запрете предложения услуг.")
    elif typ == DIRECT:
        action = ("Проверить исходный запрос, объем услуг, SLA, площадки, срок начала, "
                  "бюджет и ЛПР. После проверки стоп-листа и правового основания "
                  "передать ответственному менеджеру для согласованного контакта.")
    else:
        action = "Классифицировать первоисточник, подтвердить компанию и потребность."
    if prohibits:
        action = "В источнике найден запрет коммерческих предложений. Не звонить и не писать; только внутренняя проверка."
    if freshness == "EXPIRED_DEADLINE":
        action = "Срок закупки истёк. Не ставить срочную задачу подачи заявки; изучить историю только при отдельном основании."
    if staff_only:
        action += " В тексте указано требование штатного сотрудника — риск неприменимости аутсорсинга."
    suggested_due = None
    if typ == TENDER and freshness == "SOURCE_CURRENT_VERIFIED" and deadline:
        # Always leave time BEFORE the confirmed deadline, including
        # tenders closing in less than 24h. This is an internal verification
        # task, not automatic tender submission or supplier outreach.
        remaining = deadline - now
        if remaining > timedelta(0):
            due = min(now + timedelta(hours=24),
                      now + remaining / 2)
            if due < deadline:
                suggested_due = due.isoformat()
    if typ == DIRECT and freshness == "SOURCE_CURRENT_VERIFIED":
        suggested_due = (now + timedelta(hours=24)).isoformat()
    rationale = ("Основание классификации: " + reason + ". "
                 + ("Дата/статус проверены по первоисточнику." if freshness == "SOURCE_CURRENT_VERIFIED"
                    else "Актуальность не подтверждена."))
    return {
        "version": TRIAGE_VERSION, "type": typ,
        "badge": BADGES[typ], "kind_title": CATEGORY_NAMES[typ],
        "classification_reason": reason, "company_from_signal": company,
        "services": services, "region_from_signal": region,
        "freshness": freshness, "deadline_verified_utc": deadline.isoformat() if verified and deadline else None,
        "published_date_verified": published.isoformat() if verified and published else None,
        "source_url_verified": data_evidence.get("source_url") if verified else None,
        "contacts": contacts, "contacts_found": bool(contacts),
        "contact_prohibited_by_text": prohibits, "staff_only": staff_only,
        "indicative_priority": priority, "priority_factors": factors,
        "formal_lead_score": None, "qualification_status": disposition,
        "manager_action": action, "manager_task_due_at": suggested_due,
        "manager_task_draft_only": True, "auto_contact": False,
        "contact_legal_clearance": "NOT_PROVEN",
        "manager_task_identity_verified": False,
        "why_this_may_be_interesting": rationale,
    }


def task_draft(assessment: Mapping, card_id: int) -> dict:
    """Type-specific CRM task intent, not tasks.task.add or outreach permission."""
    _require(type(card_id) is int and card_id >= 8, "CARD_ID_HISTORIC_OR_INVALID")
    _require(isinstance(assessment, Mapping) and assessment.get("type") in BADGES,
             "TASK_CLASSIFICATION_INVALID")
    badge = assessment["badge"]
    if assessment.get("contact_prohibited_by_text"):
        headline = "Внутренняя проверка запрета контакта"
    elif assessment["type"] == TENDER:
        headline = "Проверить тендер и срок подачи"
    elif assessment["type"] == VACANCY:
        headline = "Проверить вакансию как сигнал 1С / IT-аутсорсинга"
    elif assessment["type"] == DIRECT:
        headline = "Квалифицировать прямую заявку на IT-услуги"
    else:
        headline = "Проверить источник и тип потребности"
    return {
        "title": badge + " | " + headline + f" | CRM #{card_id}",
        "description": (assessment["manager_action"] + "\n"
                        "Проверить актуальность, компанию, дубли, действующего клиента, "
                        "стоп-лист и право контакта. Источники и доказательства "
                        "должны быть сохранены в карточке. НЕ выполнять "
                        "рекламный звонок автоматически."),
        "deadline_at": assessment.get("manager_task_due_at"),
        "linked_entity_type_id": 1062,
        "linked_card_id": card_id,
        "requires_verified_responsible_user_id": True,
        "requires_tasks_scope_and_binding": True,
        "requires_durable_exact_task_dedupe": True,
        "status": "DRAFT_NEEDS_MANAGER_ID_AND_REST_GATES",
        "task_created": False,
    }
