"""Compact, evidence-led B2B dossier V10 for plain-text Bitrix24 AI fields.

Formatting only: no network, secrets, model calls, database or CRM mutation.
The writer archives the nine previous CRM fields separately before replacement.
Brave discovers URLs; only independently fetched text supplies quoted facts.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from urllib.parse import urlsplit

from .serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX
from .serviceit_b2b_routerai_deep import public_documents
from .serviceit_vps_b2b_deep_research import (
    public_buyer_candidate, published_buyer_inn_evidence,
)

REPORT = PREFIX + "AI_FULL_REPORT"
BRIEF = PREFIX + "AI_MANAGER_BRIEF"
STATUS = PREFIX + "AI_STATUS"
LIMIT_TEXT = 4096
LIMIT_REPORT = 12000
MAX_EVIDENCE = 16
LABELS = {
    "SCOPE": "Предмет и объём",
    "TECHNOLOGY": "Технологии / ТЗ",
    "BUYER": "Сведения о заказчике",
    "DEADLINE": "Срок подачи",
    "BUDGET": "Цена / бюджет",
    "ELIGIBILITY": "Допуск / квалификация",
    "SUBMISSION": "Порядок подачи заявки",
    "DOCUMENTS": "Документы участника",
    "PAYMENT": "Оплата / расчёты",
    "GUARANTEE": "Обеспечение / гарантии",
    "EVALUATION": "Критерии отбора",
    "LOCATION": "География",
    "DELIVERY": "Сроки и условия исполнения",
    "OTHER": "Дополнительные условия",
}
CATEGORIES = frozenset(LABELS)


class DossierV10Blocked(ValueError):
    pass


def need(value, code):
    if not value:
        raise DossierV10Blocked(code)


def tidy(value, limit=240):
    if type(value) is not str:
        return ""
    value = " ".join(value.split())
    value = "".join("•" if ord(ch) > 0xFFFF else ch
                    for ch in value if ord(ch) >= 32
                    and ord(ch) not in (0xFE0F, 0x200D, 0x20E3))
    return value[:limit]


def original_nine(item):
    value = {key: item.get(key) for key in AI_FIELDS}
    need(len(value) == 9 and all(type(v) is str and v.strip()
         and len(v) <= (LIMIT_REPORT if k == REPORT else LIMIT_TEXT)
         for k, v in value.items()), "NINE_AI_FIELDS_MISSING")
    return value


def routing(groups, title):
    """Requested department or potential partner category, NOT a real assignment."""
    g = set(x for x in (groups or []) if type(x) is str)
    s = title.casefold()
    if "IT_STAFFING" in g or re.search(r"(?i)(?:ит|it)[-\s]*специалист", s):
        return ("Аутстаффинг / подбор ИТ-команд; при необходимости партнёры 1С, "
                "DevOps, ИБ", "Уточнить роли, грейды, FTE/часы, ставки, "
                "сроки привлечения, допуск субподряда и требования ИБ.")
    if "1C" in g or re.search(r"(?i)\b1[сc]\b", s):
        return ("Команда 1С / профильный партнёр по внедрению",
                "Уточнить конфигурацию, число лицензий, интеграции, объём, "
                "SLA, права на исходный код и критерии приёмки.")
    if "SERVERS" in g or "сервер" in s:
        return ("Инфраструктура и серверы / партнёр-поставщик",
                "Уточнить BOM, CPU/RAM/диски, количество, лицензии, "
                "монтаж, гарантию, место и график поставки.")
    if "CRM" in g or "битрикс" in s or "INTEGRATION" in g:
        return ("Команда CRM/Битрикс24 и интеграций",
                "Уточнить системы, API, сроки миграции, пользователей, "
                "обмен данными, тестирование и поддержку.")
    if "KKT_EDO" in g:
        return ("ККТ, маркировка и ЭДО / специализированный партнёр",
                "Уточнить оборудование, ОФД/оператора ЭДО, лицензии, "
                "число объектов, интеграцию с учётной системой.")
    return ("Технический руководитель «Сервис ИТ» — первичная оценка",
            "Установить точный предмет и технологии до назначения исполнителя.")


def checked_public_contacts(findings, buyer, public_inn):
    """Use only independent company self-declarations matching buyer and INN."""
    if not buyer or not public_inn or type(findings) is not dict:
        return []
    ind = findings.get("independent_company_research")
    if type(ind) is not dict:
        return []
    identity = ind.get("public_inn")
    if (type(identity) is not dict
            or identity.get("buyer_name") != buyer
            or identity.get("inn") != public_inn.get("inn")
            or identity.get("provenance") != "PUBLIC_CORPORATE_SELF_DECLARATION"):
        return []
    values = []
    for row in (ind.get("contacts") or [])[:12]:
        if type(row) is not dict or row.get("origin") != "LEGAL_NAME_AND_INN_ON_PUBLIC_PAGE_NOT_FNS":
            continue
        label, val, url = row.get("type"), row.get("value"), row.get("url")
        if type(val) is not str or type(url) is not str:
            continue
        u = urlsplit(url)
        if u.scheme != "https" or not u.hostname or u.username or u.password:
            continue
        if label == "email" and re.fullmatch(
                r"[A-Za-z0-9_.+-]{1,64}@[A-Za-z0-9.-]{2,100}\.[A-Za-z]{2,15}", val):
            values.append(("email", val, url))
        if label == "phone" and re.fullmatch(r"[+\d() -]{8,26}", val):
            values.append(("телефон", val, url))
    return list(dict.fromkeys(values))[:5]


def evidence_rows(findings, investigation):
    """Independent readback of verbatim quote and fetched URL; ignore AI prose."""
    docs = public_documents(findings)
    map_by_url = {row["url"]: row for row in docs}
    urls = [row["url"] for row in docs]
    accepted, seen = [], set()
    raw = investigation.get("facts") if type(investigation) is dict else None
    for row in raw[:24] if type(raw) is list else []:
        if type(row) is not dict or row.get("category") not in CATEGORIES:
            continue
        quote, url = row.get("quote"), row.get("url")
        if (type(quote) is not str or type(url) is not str
                or url not in map_by_url or not 10 <= len(quote) <= 300
                or quote not in map_by_url[url]["text"]):
            continue
        if (url, quote) in seen:
            continue
        seen.add((url, quote))
        accepted.append({"category": row["category"], "quote": tidy(quote, 260),
                         "url": url, "ref": urls.index(url) + 1})
        if len(accepted) >= MAX_EVIDENCE:
            break
    return docs, accepted


def _fact(facts, category, limit=175):
    for row in facts:
        if row["category"] == category:
            return "«" + tidy(row["quote"], limit) + "» [" + str(row["ref"]) + "]"
    return ""


def compose(notice, old_item, findings, investigation):
    """Nine legible fields with sourced deadlines/participation and routing.

    Never label a supplier-email deadline as independently verified.
    Never claim missing public evidence means that the tender doesn't exist.
    """
    old = original_nine(old_item)
    need(type(notice) is dict and notice.get("source") == "B2B-Center",
         "SIGNED_NOTICE_REQUIRED")
    tid = notice.get("tender_id")
    need(type(tid) is str and re.fullmatch(r"\d{5,12}", tid), "TENDER_ID_INVALID")
    title = tidy(notice.get("subject"), 240)
    need(bool(title), "SIGNED_TITLE_REQUIRED")
    source_url = notice.get("source_url")
    need(type(source_url) is str and
         re.fullmatch(r"https://www\.b2b-center\.ru/[^\s]{1,650}", source_url),
         "SOURCE_URL_INVALID")
    groups = notice.get("source_service_groups")
    route, questions = routing(groups if type(groups) is list else [], title)
    docs, facts = evidence_rows(findings, investigation)
    unique_urls = [row["url"] for row in docs]
    buyer = public_buyer_candidate(findings)
    buyer_inn = published_buyer_inn_evidence(findings) if buyer else None
    contacts = checked_public_contacts(findings, buyer, buyer_inn)
    organizer = tidy(notice.get("organizer_as_named_in_email"), 165)
    email_deadline = tidy(notice.get("deadline_as_stated_in_email"), 100)
    email_time = tidy(notice.get("email_received_at"), 90)

    public_deadline = _fact(facts, "DEADLINE")
    deadline = (public_deadline + " (текст публичной страницы)"
                if public_deadline else
                email_deadline + " (только уведомление; сверить)" if email_deadline
                else "не найден в доступных источниках")
    buyer_text = buyer + " (из публичного текста, не проверено ФНС)" if buyer else "не установлен"
    inn_text = (buyer_inn["inn"] + " (публичный кандидат; НЕ ФНС)"
                if buyer_inn else "нет подтверждённой связки покупатель—ИНН")
    kind_categories = ("ELIGIBILITY", "SUBMISSION", "DOCUMENTS",
                       "PAYMENT", "GUARANTEE", "EVALUATION")
    active = {x["category"] for x in facts}
    absence = [LABELS[c] for c in ("BUYER", "DEADLINE", "BUDGET", *kind_categories)
               if c not in active and not (c == "BUYER" and buyer)
               and not (c == "DEADLINE" and email_deadline)]
    if not buyer_inn:
        absence.insert(0, "проверенный ИНН / ФНС-МСП")
    if not contacts:
        absence.append("проверенные публичные контакты")
    # A public quote is always tied to one URL; ten quotes from one URL
    # count as ONE distinct source, never ten independent sources.
    core = [
        f"ТЕНДЕР №{tid}  |  АНАЛИТИЧЕСКАЯ КАРТОЧКА V10",
        "ПРЕДМЕТ  " + title,
        "ЗАКАЗЧИК  " + buyer_text,
        "ДЕДЛАЙН  " + deadline,
        "СУММА  " + (_fact(facts, "BUDGET") or "не найдена"),
        "УЧАСТИЕ  " + (_fact(facts, "ELIGIBILITY") or "требования не найдены"),
        "КОНТАКТ  " + (", ".join(v for _, v, _ in contacts) if contacts else "не подтверждён"),
        "КОМУ ПЕРЕДАТЬ  " + route,
        "РЕШЕНИЕ  " + ("Изучить подтверждённые условия и ТЗ"
                       if facts else "Сначала получить первоисточник / ТЗ"),
    ]
    brief = "\n".join(core) + ("\n\nПРОБЕЛЫ  " + "; ".join(absence[:6]) if absence else "") + (
        f"\nДОКАЗАТЕЛЬСТВА  {len(facts)} цитат / {len(unique_urls)} разных URL. "
        "HOLD ограничивает контакт, НЕ исследование.")
    summary = "\n".join([f"№{tid} · {title}",
        "Подача до: " + deadline,
        "Заказчик: " + buyer_text,
        "Деньги / участие: " + (_fact(facts, "BUDGET", 100) or "сумма не найдена")
        + " / " + (_fact(facts, "ELIGIBILITY", 100) or "условия не найдены"),
        "Маршрут: " + route])

    sections = [
        f"СЕРВИС ИТ  |  ТЕНДЕРНОЕ ДОСЬЕ V10  |  №{tid}",
        "═" * 48,
        f"Предмет: {title}",
        "Исследовано: " + datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC"),
        f"Доказательств: {len(facts)} цитат / {len(unique_urls)} самостоятельных URL",
        "Статус: исследование открыто; коммерческий контакт — отдельное разрешение",
        "",
        "01  ПАСПОРТ ЗАКУПКИ",
        "Предмет: " + title,
        "Официальная ссылка (доступность отдельно проверяется): [ОРИГИНАЛ]",
        "Получено уведомление: " + (email_time or "дата не указана"),
        "Заказчик: " + buyer_text,
        "Организатор (из email, НЕ заказчик): " + (organizer or "не указан"),
        "ИНН: " + inn_text,
        "Срок подачи: " + deadline,
        "Бюджет: " + (_fact(facts, "BUDGET") or "в прочитанных страницах не найден"),
        "",
        "02  ПРЕДМЕТ И ТЕХНИЧЕСКОЕ ЗАДАНИЕ",
        "Что закупается: " + (_fact(facts, "SCOPE", 230) or title + " (по уведомлению)"),
        "Требования и компетенции: " + (_fact(facts, "TECHNOLOGY", 230)
                                      or "детальное ТЗ не обнаружено"),
        "География: " + (_fact(facts, "LOCATION") or "не установлена"),
        "Исполнение / поставка: " + (_fact(facts, "DELIVERY") or "не установлено"),
        "",
        "03  ДОПУСК И КОММЕРЧЕСКИЕ УСЛОВИЯ",
    ]
    for category in kind_categories:
        sections.append(LABELS[category] + ": " +
                        (_fact(facts, category, 205)
                         or "не установлено в доступном тексте"))
    sections += [
        "",
        "04  КОНТАКТЫ И ЮРИДИЧЕСКАЯ ПРОВЕРКА",
        "Публичный заказчик: " + buyer_text,
        "ИНН: " + inn_text,
        "Реестр ФНС-МСП и актуальный статус ЕГРЮЛ: требуют отдельной проверки",
    ]
    if contacts:
        for kind, value, url in contacts:
            mark = unique_urls.index(url) + 1 if url in unique_urls else None
            sections.append(kind + ": " + value
                            + (" [" + str(mark) + "]" if mark else " (см. источники)"))
    else:
        sections.append("Контакт заказчика: нет независимой проверенной связки")
    sections += [
        "",
        "05  НАПРАВЛЕНИЕ И ЗАДАЧИ ДЛЯ МЕНЕДЖЕРА",
        "Рекомендуемое подразделение / тип партнёра: " + route,
        "Сверить с фактическими компетенциями и доступностью сотрудников.",
        "Технический запрос: " + questions,
        "Проверить допуск внешнего подрядчика и ограничения публикации ТЗ.",
        "Проверить STOP-лист, дубли CRM и право контакта перед любым обращением.",
        "Не пересылать данные третьим лицам без согласования и проверки условий.",
        "",
        "06  ЧТО ПОКА НЕ ДОКАЗАНО",
        *["• " + x for x in absence[:12]],
        ("• В открытом доступе не найден текст закупки: письмо B2B остаётся "
         "источником самого сигнала." if not docs else
         "• Цитаты одной страницы — один независимый URL, не несколько источников."),
        "",
        "07  ДОСЛОВНЫЕ ФРАГМЕНТЫ И ССЫЛКИ",
    ]
    for n, row in enumerate(facts, 1):
        sections.append(f"{n:02d}. {LABELS[row['category']]} [{row['ref']}]")
        sections.append("    «" + row["quote"] + "»")
    if not facts:
        sections.append("Нет пригодных дословных цитат; не подменять их догадками ИИ.")
    sections += ["", "08  РЕЕСТР ИСТОЧНИКОВ"]
    # One URL is listed exactly once even when it is both the signed
    # marketplace link and the independently fetched public source.
    if source_url in unique_urls:
        ref = unique_urls.index(source_url) + 1
        sections.append(f"[ОРИГИНАЛ = {ref}] " + source_url)
    else:
        sections.append("[ОРИГИНАЛ] " + source_url)
    for i, url in enumerate(unique_urls, 1):
        if url != source_url:
            sections.append("[" + str(i) + "] " + url)
    for kind, value, url in contacts:
        if url not in unique_urls:
            sections.append("[КОНТАКТ " + kind + "] " + url)
    sections += ["", "ГРАНИЦЫ ДОСТОВЕРНОСТИ",
                 "Публичные страницы, уведомление и выводы модели имеют разные уровни "
                 "подтверждения. Brave — только поиск URL, RouterAI — разбор "
                 "дословно подтверждённых фрагментов. Не подтверждены "
                 "покупка/право контакта/статус ЕГРЮЛ без отдельной проверки."]
    full = "\n".join(sections)
    # Prefer shedding secondary proof quotes over silently chopping URL, deadline,
    # amount, buyer or manager action mid-word. Preserve the footer/source list.
    if len(full) > LIMIT_REPORT:
        for n in range(MAX_EVIDENCE, 5, -1):
            short = [x for x in sections]
            mark = short.index("07  ДОСЛОВНЫЕ ФРАГМЕНТЫ И ССЫЛКИ")
            footer = short.index("08  РЕЕСТР ИСТОЧНИКОВ")
            short = short[:mark + 1] + short[mark + 1:footer][:2*n] + short[footer:]
            if len("\n".join(short)) <= LIMIT_REPORT:
                full = "\n".join(short)
                break
    need(len(full) <= LIMIT_REPORT, "PUBLIC_DOSSIER_TOO_LARGE_FOR_CRM")
    result = dict(old)
    result[PREFIX + "AI_SUMMARY"] = summary[:LIMIT_TEXT]
    result[PREFIX + "AI_QUOTE"] = (
        "Публичный источник [" + str(facts[0]["ref"]) + "]: " + facts[0]["quote"]
        if facts else "Публичных цитат пока нет. Исходный сигнал: " + title)
    result[PREFIX + "AI_HYPOTHESIS"] = (
        "Рекомендуемое направление (предварительно): " + route)
    result[PREFIX + "AI_LIMITATIONS"] = (
        "Не подтверждено: " + "; ".join(absence[:12]) +
        ". Если условия доступны только после входа, их нельзя подменять гипотезами.")
    result[PREFIX + "AI_RECOMMENDATION"] = (
        "Предложить на рассмотрение: " + route + ". " + questions
        + " Не направлять автоматически подрядчикам/заказчику.")
    result[PREFIX + "AI_NEXT_ACTION"] = (
        "Сначала сверить дедлайн и действующую редакцию извещения; "
        + questions + " Затем подтвердить допуск, STOP/CRM и согласовать исполнителя.")
    result[BRIEF] = brief[:LIMIT_TEXT]
    result[REPORT] = full
    # Preserve original research status; it is evidence/processing provenance,
    # not a commercial approval. Never overwrite source or user manager fields.
    result[STATUS] = old[STATUS]
    result = {name: "\n".join(tidy(part, len(part)) for part in value.splitlines())
              for name, value in result.items()}
    need(set(result) == AI_FIELDS and all(type(v) is str and v.strip()
         and len(v) <= (LIMIT_REPORT if k == REPORT else LIMIT_TEXT)
         for k, v in result.items()), "BITRIX_V10_NINE_FIELDS_INVALID")
    return result, {"pages": len(docs), "citations": len(facts),
                    "buyer_public": bool(buyer), "deadline_public": bool(public_deadline),
                    "contacts": len(contacts)}
