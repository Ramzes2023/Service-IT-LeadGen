"""Evidence-led, deterministic Bitrix B2B dossier passport reconciliation.

No network, model, CRM, secret or database access. Never manufacture FNS,
budget, contacts or contract award. Four reviewed signed tenders use a dated
public-source snapshot, NOT a claim of current live registration status.
Future tenders use only explicitly receipt-verified source quotation rows.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from .bitrix_1062_mapping import _is_valid_inn
from .serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX

REPORT = PREFIX + "AI_FULL_REPORT"
SUMMARY = PREFIX + "AI_SUMMARY"
BRIEF = PREFIX + "AI_MANAGER_BRIEF"
LIMITS = PREFIX + "AI_LIMITATIONS"
NEXT = PREFIX + "AI_NEXT_ACTION"
INN = PREFIX + "COMPANY_INN"
COMPANY = PREFIX + "COMPANY_NAME"
REGION = PREFIX + "REGION"
MAX_REPORT = 12000
MAX_TEXT = 4096
# Reviewed 2026-11-11: published tender detail + a separate legal source,
# grounded to the exact immutable B2B tender number. This is not a FNS
# enquiry nor a dynamically refreshed marketplace API.
REVIEWED = {
    "4673971": {
        "buyer": "ПАО «Московская Биржа»", "inn": "7702077840",
        "region": "Москва", "deadline": "17.11.2026 18:00 МСК",
        "delivery": "125009, Москва, Большой Кисловский пер., д. 13",
        "procedure": "Запрос цен",
        "subject": "Закупка серверов ЗОКИИ с ПНР и услугами расширенной ТП на 3 года",
        "scope": "Два лота: серверы ЗОКИИ, пусконаладочные работы и расширенная техническая поддержка на 3 года",
        "terms": "Лот 1 — 100% постоплата; лот 2 — 100% аванс с банковской гарантией и сопровождением",
        "organizer": "ПАО «Московская Биржа»",
        "documents": "Документы и детали требований следует сверить на площадке",
        "tender_url": "https://zakupki.kontur.ru/4673971_459",
        "platform_url": "https://www.b2b-center.ru/app/market-next/zakupka-serverov-zokii-s-pnr-i-uslugami-rasshirennoi-tp-na-3-goda/tender-4673971/",
        "legal_url": "https://www.cbr.ru/registries/rcb/ecb/orginfo/?id=11123764",
    },
    "4672802": {
        "buyer": "ПАО «Агрегат»", "inn": "7401000191",
        "region": "Челябинская область",
        "deadline": "16.11.2026 10:00 МСК",
        "delivery": "456020, Челябинская область, Ашинский район, г. Сим, ул. Пушкина, д. 1",
        "procedure": "Запрос предложений",
        "subject": "Приобретение лицензий для предоставления доступа к серверам 1С",
        "scope": "Лицензии 1С для новых рабочих мест и покрытия нехватки действующих лицензий",
        "terms": "Подача предложений по всему лоту; точный состав лицензий требует сверки с документацией",
        "organizer": "АО ГМЗ «Агат» (не заказчик)",
        "documents": "По открытой карточке документы пока не загружены",
        "tender_url": "https://zakupki.kontur.ru/4672802_4",
        "platform_url": "https://www.b2b-center.ru/market/priobretenie-litsenzii-programmnogo-obespecheniia-dlia-predostavleniia/tender-4672802/",
        "legal_url": "https://e-disclosure.ru/portal/company.aspx?id=9992",
    },
    "4674861": {
        "buyer": "ООО «Ева»", "inn": "7708335695",
        "region": "Москва", "deadline": "17.11.2026 12:00 МСК",
        "delivery": "101000, Москва (адрес поставки из опубликованной карточки)",
        "procedure": "Запрос предложений",
        "subject": "Поставка сервера, заявка 9236",
        "scope": "Сервер Dell PowerEdge R770 — 2 шт.; коммутатор Dell EMC Connectrix DS-6620B — 2 шт.; СХД Dell PowerVault ME5224 — 4 шт.",
        "terms": "Цена с НДС; применимость аналогов и детальные требования — по документации",
        "organizer": "ООО «Группа компаний ФСК» (не заказчик)",
        "documents": "Документы и договорные условия необходимо сверить на площадке",
        "tender_url": "https://zakupki.kontur.ru/4674861_4",
        "platform_url": "https://www.b2b-center.ru/market/postavka-servera-zaiavka-9236/tender-4674861/",
        "legal_url": "https://www.tbank.ru/business/contractor/legal/1187746878934/",
    },
    "4675475": {
        "buyer": "ПАО «Московский Кредитный Банк»", "inn": "7734202860",
        "region": "Москва", "deadline": "16.11.2026 16:00 МСК",
        "delivery": "111141, Москва, 2-й проезд Перова Поля, д. 2, стр. 4",
        "procedure": "Предварительный квалификационный отбор (не договор поставки)",
        "subject": "Приобретение серверного оборудования для нужд МКБ",
        "scope": "Квалификационный отбор потенциальных поставщиков серверного оборудования; состав ТЗ пока не подтверждён",
        "terms": "Подача предложений электронным документом; допуск и критерии — по документации",
        "organizer": "ПАО «Московский Кредитный Банк»",
        "documents": "Файлы закупочной документации требуют доступа к площадке",
        "tender_url": "https://zakupki.kontur.ru/4675475_40",
        "platform_url": "https://www.b2b-center.ru/market/priobretenie-servernogo-oborudovaniia-dlia-nuzhd-mkb/tender-4675475/",
        "legal_url": "https://cbr.ru/registries/rcb/ecb/orginfo/?id=9460",
    },
}
PROVEN_QUOTE = re.compile(
    r"^ПРОЧИТАННЫЙ ИСТОЧНИК / ([A-Z_]{3,25}): (.{10,300}) \[(https://[^\]\s]{8,700})\]$"
)
DAY = re.compile(r"(?<!\d)(\d{2}\.\d{2}\.20\d{2})\s*(\d{2}:\d{2})(?!\d)")


class ReconcileBlocked(ValueError):
    pass


def _need(ok, label):
    if not ok:
        raise ReconcileBlocked(label)


def _text(s, limit=300):
    return " ".join(str(s or "").split())[:limit]


def _safe_url(s):
    if not isinstance(s, str) or len(s) > 800:
        return False
    p = urlsplit(s)
    return p.scheme == "https" and bool(p.hostname) and not p.username and not p.password


def _named_manager(text):
    # Replace only internal workflow phrasing, never indiscriminately remove a
    # person's name from a source citation or contractual party name.
    text = re.sub(r"(?iu)\bдля Михаила\b", "для ответственного менеджера", text)
    text = re.sub(r"(?iu)\bДействия Михаила\b", "Рекомендуемые действия", text)
    text = re.sub(r"(?iu)\bДействие Михаила\b", "Следующее действие", text)
    text = re.sub(r"(?iu)\bМихаилу:\s*", "Ответственному менеджеру: ", text)
    return text


def extracted_verified(verified_lines):
    """Only source lines already attested by signed immutable V19/V20 receipts.

    A raw CRM appendix or Brave index snippet is NOT an attested source line.
    Verified lines are supplied by the signed receipt reader, not users/LLM.
    """
    found = {}
    for line in verified_lines[:32] if isinstance(verified_lines, list) else ():
        if not isinstance(line, str):
            continue
        match = PROVEN_QUOTE.fullmatch(line.strip())
        if match is None:
            continue
        category, quote, url = match.groups()
        if not _safe_url(url):
            continue
        if category in ("DEADLINE", "LOCATION", "BUYER", "BUDGET",
                        "PROCEDURE", "ELIGIBILITY", "SUBMISSION", "DOCUMENTS"):
            found.setdefault(category, (quote, url))
    return found


def _record_from_verified(notice, item, verified):
    parsed = extracted_verified(verified)
    if not parsed:
        return None
    tid = notice["tender_id"]
    record = {"reviewed": False, "buyer": "", "inn": "",
              "deadline": "", "delivery": "", "subject": _text(notice["subject"]),
              "scope": _text(notice["subject"]), "procedure": "",
              "terms": "", "organizer": "", "documents": "",
              "region": "", "tender_url": "", "platform_url": notice["source_url"],
              "legal_url": ""}
    if "BUYER" in parsed:
        quote, url = parsed["BUYER"]
        # Do not elevate arbitrary words in a generic quote to a legal entity.
        if re.search(r"(?iu)\b(?:ПАО|АО|ООО|ОАО)\b", quote):
            record["buyer"] = _text(quote, 150) + " (из цитаты источника)"
            record["tender_url"] = url
    if "DEADLINE" in parsed:
        quote, url = parsed["DEADLINE"]
        date = DAY.search(quote)
        if date:
            record["deadline"] = date.group(1)+" "+date.group(2)+" МСК (цитата источника)"
            record["tender_url"] = record["tender_url"] or url
    if "LOCATION" in parsed:
        quote, url = parsed["LOCATION"]
        record["delivery"] = _text(quote, 200) + " (цитата источника)"
        record["tender_url"] = record["tender_url"] or url
    if "PROCEDURE" in parsed:
        record["procedure"] = _text(parsed["PROCEDURE"][0], 135)
    if "ELIGIBILITY" in parsed:
        record["terms"] = _text(parsed["ELIGIBILITY"][0], 180)
    if "DOCUMENTS" in parsed:
        record["documents"] = _text(parsed["DOCUMENTS"][0], 180)
    if not any((record["buyer"],record["deadline"],record["delivery"])):
        return None
    return record


def reviewed_or_verified(notice, item, verified_lines):
    _need(isinstance(notice, dict) and notice.get("source") == "B2B-Center",
          "SIGNED_NOTICE_REQUIRED")
    tid = notice.get("tender_id")
    _need(isinstance(tid, str) and re.fullmatch(r"\d{5,12}", tid),
          "SIGNED_TENDER_ID_REQUIRED")
    if tid in REVIEWED:
        record = dict(REVIEWED[tid])
        _need(_is_valid_inn(record["inn"])
              and record["platform_url"].find(tid) >= 0
              and record["tender_url"].find(tid) >= 0
              and _safe_url(record["legal_url"])
              and _safe_url(record["platform_url"])
              and _safe_url(record["tender_url"]),
              "REVIEWED_PUBLIC_PROVENANCE_INVALID")
        original_subject = _text(notice.get("subject"), 240).casefold()
        expected = record["subject"].casefold()
        # Subject in signed supplier email can be longer/shorter, but the
        # distinctive core must correspond to the same known tender.
        words = [s for s in re.findall(r"[а-яa-z]{5,}", expected)
                 if s not in {"закупка", "поставка", "лицензий", "серверного"}]
        _need(sum(int(w in original_subject) for w in words[:8]) >= 1,
              "REVIEWED_TENDER_SUBJECT_MISMATCH")
        record["reviewed"] = True
        return record
    return _record_from_verified(notice, item, verified_lines)


def prepare(notice, item, verified_lines):
    """Only the AI report/brief/limits/action and safe empty company fields.

    Immutable source sections 07 onward are preserved *byte-for-byte* except
    tightly scoped staff-facing wording. No no-evidence CRM write.
    """
    _need(isinstance(item, dict) and all(
        isinstance(item.get(k), str) and item[k].strip() for k in AI_FIELDS),
        "NINE_AI_BASE_REQUIRED")
    record = reviewed_or_verified(notice, item, verified_lines)
    if record is None:
        return None, {"status": "NO_ATTESTED_NEW_FACTS"}
    tid = notice["tender_id"]
    old_report = item[REPORT]
    anchor = "07  ДОСЛОВНЫЕ ФРАГМЕНТЫ И ССЫЛКИ"
    if old_report.count(anchor) != 1:
        return None, {"status": "NONSTANDARD_REPORT_MANUAL_REVIEW"}
    tail = old_report[old_report.index(anchor):]
    if ("V37 · СОГЛАСОВАННЫЙ ПАСПОРТ" in old_report[:old_report.index(anchor)]):
        return None, {"status": "ALREADY_CANONICAL"}
    buyer = record["buyer"] or "не установлен по проверенным источникам"
    inn = record["inn"] or (item.get(INN) if _is_valid_inn(item.get(INN)) else "")
    inn_text = (inn + " (публичные реквизиты; текущий статус ФНС отдельно)"
                if inn else "не подтверждён")
    deadline = record["deadline"] or "не установлен по проверенным источникам"
    delivery = record["delivery"] or "не установлено"
    budget = "Не раскрыт в доступной публикации" if record["reviewed"] else "не установлен"
    unknown = ["Актуальность ЕГРЮЛ / ФНС-МСП и право контакта"]
    if not record["buyer"]:
        unknown.append("Юридически идентифицированный заказчик")
    if not inn:
        unknown.append("ИНН заказчика")
    if not record["deadline"]:
        unknown.append("Срок подачи")
    if not record["delivery"]:
        unknown.append("Место исполнения")
    unknown += ["Начальная цена / бюджет",
                "Полная документация, технические условия и допуск",
                "Согласование контакта, проверка CRM-дублей и STOP-листа"]
    sections = [
        f"СЕРВИС ИТ | V37 · СОГЛАСОВАННЫЙ ПАСПОРТ | ТЕНДЕР №{tid}",
        "Актуальность фактов: публичные публикации, проверка от 11.11.2026"
        if record["reviewed"] else
        "Факты из ранее проверенных и подписанных цитат источников",
        "Коммерческий контакт: требуется отдельное разрешение",
        "",
        "01  ПАСПОРТ ЗАКУПКИ",
        "Предмет: " + _text(notice["subject"], 240),
        "Заказчик: " + buyer,
        "ИНН заказчика: " + inn_text,
        "Организатор: " + (record["organizer"] or "не установлен отдельно"),
        "Тип процедуры: " + (record["procedure"] or "уточнить по оригиналу"),
        "Срок окончания приёма заявок: " + deadline,
        "Бюджет: " + budget + " (не считать цену нулевой)",
        "Место исполнения / поставки: " + delivery,
        "Регион заказчика: " + (record["region"] or "не установлен"),
        "",
        "02  ПРЕДМЕТ И ПАРАМЕТРЫ",
        "Объём / спецификация: " + (record["scope"] or "детальное ТЗ не получено"),
        "Особые требования: " + (record["terms"] or "не установлены"),
        "Документы: " + (record["documents"] or "требуется проверить"),
        "",
        "03  ПОРЯДОК УЧАСТИЯ",
        "Проверить критерии допуска, документы участника и правила подачи",
        "Проверить наличие аванса, банковских гарантий, условия оплаты и санкции",
        "Срок подачи заявок и закупочные файлы перепроверить на площадке",
        "",
        "04  ЮРИДИЧЕСКИЕ РЕКВИЗИТЫ И ИСТОЧНИКИ",
        "Покупатель / заказчик: " + buyer,
        "ИНН: " + inn_text,
        "Статус юридического лица в ЕГРЮЛ / включение в реестр МСП: отдельно проверить",
        "Оригинал извещения: " + notice["source_url"],
    ]
    for label, url in (
        ("Публичная карточка процедуры", record["tender_url"]),
        ("Публикация B2B-Center", record["platform_url"]),
        ("Независимые публичные реквизиты", record["legal_url"])):
        if _safe_url(url):
            sections.append(label+": "+url)
    sections += [
        "",
        "05  ДЕЙСТВИЯ ОТВЕТСТВЕННОГО МЕНЕДЖЕРА",
        "1. Сверить актуальную версию закупки и скачать разрешённые документы",
        "2. Проверить юридические реквизиты, дубли CRM и STOP-лист",
        "3. Получить ТЗ и оценить техническую и коммерческую применимость",
        "4. Согласовать право контакта до любого обращения или пересылки сведений",
        "",
        "06  ЧТО ЕЩЁ ТРЕБУЕТ ПОДТВЕРЖДЕНИЯ",
        *("• " + x for x in unknown),
        "",
    ]
    report = "\n".join(sections) + "\n" + tail
    summary = "\n".join([
        "№" + tid + " · " + _text(notice["subject"], 170),
        "Заказчик: " + buyer,
        "ИНН: " + inn_text,
        "Срок подачи: " + deadline,
        "География поставки: " + delivery,
        "Бюджет: " + budget,
        "Статус: проверка допуска и права контакта до обращения",
    ])
    brief = "\n".join([
        "КАРТОЧКА ЗАКУПКИ №"+tid,
        "Заказчик: " + buyer,
        "ИНН: " + inn_text,
        "Срок подачи: " + deadline,
        "Место исполнения: " + delivery,
        "Тип процедуры: " + (record["procedure"] or "уточняется"),
        "Бюджет: " + budget,
        "Далее: получить ТЗ, проверить условия участия, CRM-дубли, STOP и право контакта.",
    ])
    patch = {
        REPORT: report,
        SUMMARY: summary,
        BRIEF: brief,
        LIMITS: "Подтверждения ещё требуют: " + "; ".join(unknown),
        NEXT: "Сверить первоисточник и актуальный срок; изучить документацию и допуск. "
              "Проверить дубли, STOP и согласовать контакт до любых обращений.",
    }
    for name in AI_FIELDS:
        if name not in patch and isinstance(item.get(name), str):
            revised = _named_manager(item[name])
            if revised != item[name]:
                patch[name] = revised
    patch = {key: _named_manager(value) for key,value in patch.items()}
    if record["reviewed"]:
        for key,value in ((COMPANY,record["buyer"]), (INN,record["inn"]),
                          (REGION,record["region"])):
            prior = _text(item.get(key),250)
            if not prior and value:
                patch[key] = value
            elif prior and key == INN and prior != value:
                raise ReconcileBlocked("EXISTING_INN_CONFLICT_MANUAL_REVIEW")
            # Other populated company/region fields are intentionally left
            # intact. A non-empty different legal name needs human review.
    if any(key not in AI_FIELDS | {COMPANY,INN,REGION} for key in patch):
        raise ReconcileBlocked("DISALLOWED_CRM_FIELD")
    if len(patch[REPORT])>MAX_REPORT or any(len(v)>MAX_TEXT for k,v in patch.items()
                                           if k in AI_FIELDS and k!=REPORT):
        return None, {"status": "CRM_FIELD_CAPACITY_INSUFFICIENT"}
    if all(item.get(k)==v for k,v in patch.items()):
        return None, {"status": "ALREADY_MATCHING"}
    return {k:v for k,v in patch.items() if item.get(k)!=v}, {
        "status": "DRAFT", "reviewed": record["reviewed"],
        "fact_fields": sum(bool(record[k]) for k in ("buyer","inn","deadline","delivery")),
        "unknown_count": len(unknown),
        "structured_candidates": sum(int(k in patch) for k in (COMPANY,INN,REGION)),
    }
