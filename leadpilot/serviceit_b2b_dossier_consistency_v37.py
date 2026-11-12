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
from .serviceit_kanban_presentation import SCORE_FIELD, VERSION_FIELD, BADGE_FIELD, score_label

REPORT = PREFIX + "AI_FULL_REPORT"
SUMMARY = PREFIX + "AI_SUMMARY"
BRIEF = PREFIX + "AI_MANAGER_BRIEF"
LIMITS = PREFIX + "AI_LIMITATIONS"
NEXT = PREFIX + "AI_NEXT_ACTION"
INN = PREFIX + "COMPANY_INN"
COMPANY = PREFIX + "COMPANY_NAME"
REGION = PREFIX + "REGION"
DECISION = PREFIX + "REVIEW_DECISION"
RECOMMENDATION = PREFIX + "AI_RECOMMENDATION"
MANAGER_PLACEHOLDER = "Проверить закупку и покупателя; не контактировать"
MANAGER_SCORE_VERSION = "svc-b2b-evidence-priority-v1"
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
REVIEWED.update({'4676478': {'buyer': 'АО «Тандер»',
             'inn': '2310031475',
             'region': 'Москва',
             'deadline': '18.11.2026 15:00 МСК',
             'delivery': 'Москва, Алтуфьевское шоссе, д. 33Г',
             'subject': 'Серверное оборудование для DATA',
             'organizer': 'АО «Тандер»',
             'procedure': 'Запрос предложений',
             'platform_url': 'https://www.b2b-center.ru/app/market-next/servernoe-oborudovanie-dlia-data/tender-4676478/',
             'tender_url': 'https://zakupki.kontur.ru/4676478_458',
             'scope': 'Серверное оборудование для DATA',
             'terms': '',
             'documents': 'Полное ТЗ требует сверки',
             'legal_url': 'https://zakupki.kontur.ru/4676478_458',
             'budget': 'Не раскрыт в доступной публикации'},
 '4676643': {'buyer': 'АО «Издательство „Просвещение“»',
             'inn': '7715995942',
             'region': 'Москва',
             'deadline': '24.11.2026 17:00 МСК',
             'delivery': 'Москва, ул. Краснопролетарская, д. 16 стр. 3, помещ. 1Н',
             'subject': 'Технологический аудит информационной системы на платформе 1С',
             'organizer': 'АО «Издательство „Просвещение“»',
             'procedure': 'Запрос предложений',
             'platform_url': 'https://www.b2b-center.ru/app/market-next/okazanie-uslug-po-provedeniiu-tekhnologicheskogo-audita-informatsionnoi/tender-4676643/',
             'tender_url': 'https://zakupki.kontur.ru/4676643_458',
             'scope': 'Технологический аудит информационной системы на платформе 1С',
             'terms': '',
             'documents': 'Полное ТЗ требует сверки',
             'legal_url': 'https://zakupki.kontur.ru/4676643_458',
             'budget': 'Не раскрыт в доступной публикации'},
 '4677404': {'buyer': 'ООО УК «Промышленно-металлургический холдинг»',
             'inn': '0411075740',
             'region': 'Москва',
             'deadline': '17.11.2026 16:00 МСК',
             'delivery': 'Москва, 2-й Верхний Михайловский проезд, д. 9',
             'subject': 'Приобретение лицензий 1С',
             'organizer': 'ООО УК «Промышленно-металлургический холдинг»',
             'procedure': 'Предварительный квалификационный отбор',
             'platform_url': 'https://www.b2b-center.ru/market/view.html?id=4677404',
             'tender_url': 'https://zakupki.kontur.ru/4677404_40',
             'scope': 'Приобретение лицензий 1С',
             'terms': '',
             'documents': 'Полное ТЗ требует сверки',
             'legal_url': 'https://zakupki.kontur.ru/4677404_40',
             'budget': 'Не раскрыт в доступной публикации'},
 '4678279': {'buyer': 'ПАО «Московский Кредитный Банк»',
             'inn': '7734202860',
             'region': 'Москва',
             'deadline': '18.11.2026 16:00 МСК',
             'delivery': 'Москва, 2-й проезд Перова Поля, д. 2, стр. 4',
             'subject': 'Поставка серверных комплектующих для нужд МКБ',
             'organizer': 'ПАО «Московский Кредитный Банк»',
             'procedure': 'Предварительный квалификационный отбор',
             'platform_url': 'https://www.b2b-center.ru/market/postavka-servernykh-komplektuiushchikh-dlia-nuzhd-pao-moskovskii-kreditnyi/tender-4678279/',
             'tender_url': 'https://zakupki.kontur.ru/4678279_40',
             'scope': 'Поставка серверных комплектующих для нужд МКБ',
             'terms': '',
             'documents': 'Полное ТЗ требует сверки',
             'legal_url': 'https://zakupki.kontur.ru/4678279_40',
             'budget': 'Не раскрыт в доступной публикации'},
 '4677160': {'buyer': 'ООО «Башкирэнерго»',
             'inn': '0277071467',
             'region': 'Республика Башкортостан',
             'deadline': '16.11.2026 10:00 МСК',
             'delivery': 'Уфа, бульвар Ибрагимова, д. 86',
             'subject': 'Приобретение ПО и лицензий 1С для ООО «Башкирэнерго» 26-005731',
             'organizer': 'АО «БЭСК»',
             'procedure': 'Запрос цен по 223-ФЗ',
             'platform_url': 'https://www.b2b-energo.ru/market/priobretenie-po-i-litsenzii-1s-dlia-ooo-bashkirenergo-26-005731/tender-4677160/',
             'tender_url': 'https://zakupki.kontur.ru/32616566539',
             'scope': 'Приобретение ПО и лицензий 1С для ООО «Башкирэнерго» 26-005731',
             'terms': 'Предложение на весь лот. Документы принимаются до 16.11.2026 13:00 МСК.',
             'documents': 'Полное ТЗ требует сверки',
             'legal_url': 'https://www.b2b-energo.ru/firms/ooo-bashkirenergo/49070/',
             'budget': '11 028 400 ₽ без НДС',
             'registry_id': '32616566539'}})

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
    return (p.scheme == "https" and bool(p.hostname)
            and not p.username and not p.password
            and p.hostname not in ("localhost", "127.0.0.1", "::1")
            and len(p.query) <= 100
            and re.search(r"(?i)(token|password|secret|api_key|signature)=", p.query) is None)


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
              "terms": "", "organizer": "", "documents": "", "budget": "",
              "region": "", "tender_url": "", "platform_url": notice["source_url"],
              "legal_url": ""}
    if "BUYER" in parsed:
        quote, url = parsed["BUYER"]
        # Do not elevate arbitrary words in a generic quote to a legal entity.
        legal = re.search(
            r"(?iu)\b(?:заказчик|покупатель)\s*[:—–-]\s*"
            r"((?:ПАО|АО|ООО|ОАО)\s+[^,;:]{3,100})", quote)
        if legal:
            record["buyer"] = _text(legal.group(1), 140) + " (по цитате источника)"
            record["tender_url"] = url
    if "DEADLINE" in parsed:
        quote, url = parsed["DEADLINE"]
        # A cited page may mention its publication date as well as deadline;
        # take only a date explicitly adjacent to a submission-deadline label.
        for date in DAY.finditer(quote):
            context = quote[max(0, date.start()-65):date.start()]
            if re.search(
                    r"(?iu)(?:срок\s+подачи|при[её]м\s+заяв|"
                    r"окончани[ея]|дедлайн|заявки\s+до)", context):
                record["deadline"] = (
                    date.group(1)+" "+date.group(2)+" МСК (цитата источника)")
                record["tender_url"] = record["tender_url"] or url
                break
    if "LOCATION" in parsed:
        quote, url = parsed["LOCATION"]
        if (re.search(r"(?iu)(?:место\s+поставки|адрес\s+поставки|"
                      r"место\s+исполнения|адрес\s+доставки)", quote)
                and not re.search(r"(?iu)юридическ\w*\s+адрес", quote)):
            record["delivery"] = _text(quote, 200) + " (цитата источника)"
            if re.search(r"(?iu)московск\w*\s+обл",quote):
                record["region"] = "Московская область"
            elif re.search(r"(?iu)\bг(?:ород)?[.\s]+москва\b",quote):
                record["region"] = "Москва"
            record["tender_url"] = record["tender_url"] or url
    if "PROCEDURE" in parsed:
        record["procedure"] = _text(parsed["PROCEDURE"][0], 135)
    if "ELIGIBILITY" in parsed:
        record["terms"] = _text(parsed["ELIGIBILITY"][0], 180)
    if "DOCUMENTS" in parsed:
        record["documents"] = _text(parsed["DOCUMENTS"][0], 180)
    if "BUDGET" in parsed:
        amount, ref = parsed["BUDGET"]
        if (re.search(r"(?iu)(?:начальн\w*\s+цен|стоимост|бюджет|сумм\w*\s+закупки)", amount)
                and re.search(r"\d", amount)
                and not re.search(r"(?iu)(?:не\s+указан|не\s+раскрыт|не\s+задан)", amount)):
            record["budget"] = _text(amount, 175) + " (цитата источника)"
            record["tender_url"] = record["tender_url"] or ref
    if not any((record["buyer"],record["deadline"],record["delivery"],record["budget"])):
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
              and (tid in record["tender_url"] or
                   (record.get("registry_id") and record["tender_url"].endswith("/" + record["registry_id"])))
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
        record.setdefault("budget", "")
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
    # V18 indexed leads are explicitly UNVERIFIED. Keep them visible as
    # historical leads instead of silently deleting the old 06A section.
    # Do not promote their text into the canonical facts above.
    prior_index = old_report.find("06A  ДОПОЛНИТЕЛЬНЫЕ НАХОДКИ BRAVE")
    if 0 <= prior_index < old_report.index(anchor):
        lead_text = old_report[prior_index:old_report.index(anchor)].strip()
        if lead_text:
            tail = lead_text + "\n\n" + tail
    buyer = record["buyer"] or "не установлен по проверенным источникам"
    inn = record["inn"] or (item.get(INN) if _is_valid_inn(item.get(INN)) else "")
    inn_text = (inn + " (публичные реквизиты; текущий статус ФНС отдельно)"
                if inn else "не подтверждён")
    deadline = record["deadline"] or "не установлен по проверенным источникам"
    delivery = record["delivery"] or "не установлено"
    budget = (record.get("budget") or
              ("Не раскрыт в доступной публикации" if record["reviewed"]
               else "не установлен"))
    unknown = ["Актуальность ЕГРЮЛ / ФНС-МСП и право контакта"]
    if not record["buyer"]:
        unknown.append("Юридически идентифицированный заказчик")
    if not inn:
        unknown.append("ИНН заказчика")
    if not record["deadline"]:
        unknown.append("Срок подачи")
    if not record["delivery"]:
        unknown.append("Место исполнения")
    if not record.get("budget"):
        unknown.append("Начальная цена / бюджет")
    unknown += ["Полная документация, технические условия и допуск",
                "Согласование контакта, проверка CRM-дублей и STOP-листа"]
    sections = [
        f"СЕРВИС ИТ | V37 · СОГЛАСОВАННЫЙ ПАСПОРТ | ТЕНДЕР №{tid}",
        "Актуальность фактов: проверенные публичные публикации"
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
            elif prior and key == COMPANY:
                normal = lambda val: re.sub(r"[^а-яa-z0-9]", "", val.casefold())
                approved_organizer_aliases = {
                    # Two independently reviewed sources name a procurement
                    # organizer different from the legal buying entity.
                    "4672802": (
                        "АО ГМЗ «Агат»",
                        "АО «ГМЗ Агат»",
                    ),
                    "4674861": (
                        "ООО «Группа компаний ФСК»",
                        "ООО «ГК ФСК»",
                    ),
                    "4677160": ("АО «БЭСК»", "АО «Башкирская электросетевая компания»"),
                    "4677404": ("ООО УК «ПМХ»", 'ООО «УК ПМХ»'),
                }
                if normal(prior) == normal(value):
                    continue
                aliases = approved_organizer_aliases.get(tid, ())
                if any(normal(prior) == normal(alias) for alias in aliases):
                    # The old field mistakenly named the organizer as buyer.
                    # Only this reviewed exact tender supports that correction.
                    patch[COMPANY] = value
                else:
                    raise ReconcileBlocked("EXISTING_COMPANY_CONFLICT_MANUAL_REVIEW")
            # Non-empty region stays untouched; it may be a manager-assigned
            # service region rather than the buyer's registered address.
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

# These signals were corroborated by an exact tender ID and a separate
# public company/registry record bearing the same buyer INN.
RELATED_PUBLIC = {
    "0277071467": (
        ("4672286", "Сопровождение ПО 1С 8.2",
         "https://zakupki.kontur.ru/32616553946"),
    ),
    "7734202860": (
        ("4675475", "Приобретение серверного оборудования",
         "https://zakupki.kontur.ru/4675475_40"),
        ("4678279", "Поставка серверных комплектующих",
         "https://zakupki.kontur.ru/4678279_40"),
    ),
}
MANAGER_HEADER = "КЛЮЧЕВАЯ ИНФОРМАЦИЯ"
MANAGER_END = "──────── ПОДРОБНОЕ ДОСЬЕ ────────"


def manager_score_badge(score):
    """The live Bitrix string field retains BMP priority symbols."""
    symbol = "✅" if score >= 80 else "⚠" if score >= 60 else "❌"
    return symbol + " " + score_label(score).split(" ",1)[1]


def manager_fields(notice, item, record, checks, related):
    """Build the short brief without converting unknown checks to clearance."""
    record = record or {}
    inn = record.get("inn") or item.get(INN)
    inn = inn if _is_valid_inn(inn) else ""
    buyer = record.get("buyer") or ""
    deadline = record.get("deadline") or ""
    budget = record.get("budget") or ""
    region = record.get("region") or ""
    score = item.get(SCORE_FIELD)
    try:
        if isinstance(score, str) and score.isdecimal():
            score = int(score)
    except ValueError:
        score = None
    score_valid = type(score) is int and 0 <= score <= 100
    patch = {}
    if not score_valid and all(k in item and item[k] in (None, "") for k in
                               (SCORE_FIELD, VERSION_FIELD, BADGE_FIELD)):
        score = (20 + 8 * bool(buyer and inn) + 5 * bool(deadline)
                 + 4 * bool(budget and budget[:1].isdigit()) + 3 * bool(region)
                 + 8 * bool(related))
        if region in ("Москва", "Московская область"):
            score += 25
        elif region:
            score = min(score, 49)
        patch.update({SCORE_FIELD:score, VERSION_FIELD:MANAGER_SCORE_VERSION})
        score_valid = True
    if score_valid and not item.get(BADGE_FIELD):
        patch[BADGE_FIELD] = manager_score_badge(score)
    version = patch.get(VERSION_FIELD, item.get(VERSION_FIELD)) or "не указана"
    status = item.get(PREFIX+"AI_STATUS") or "Не подтверждён"
    locality = ("Вне зоны Москва / Московская область" if region and
                region not in ("Москва", "Московская область") else "")
    if locality:
        action = "Оставить как историю спроса. Регион исполнения вне зоны отбора."
    elif checks.get("stop_hit"):
        action = "Передать на внутреннюю проверку стоп-листа."
    elif checks.get("duplicate_hit"):
        action = "Сверить совпадение в CRM и документы перед решением."
    else:
        action = "Сверить ТЗ и условия участия. Решение принимает менеджер."
    slots = {
        "заказчик":buyer, "ИНН":inn, "срок подачи":deadline,
        "бюджет":budget if budget[:1].isdigit() else "",
        "место исполнения":record.get("delivery"), "полное ТЗ":False,
    }
    known = sum(bool(value) for value in slots.values())
    unknown = ", ".join(k for k,v in slots.items() if not v)
    core = [
        MANAGER_HEADER,
        "Заказчик: " + (buyer or "не подтверждён"),
        "ИНН заказчика: " + (inn or "не подтверждён"),
        "Организатор: " + (record.get("organizer") or _text(notice.get("organizer"), 145)
                           or "не подтверждён"),
        "Предмет: " + _text(notice["subject"], 240),
        "Общая сумма закупки: " + (budget or "не подтверждена"),
        "Срок подачи заявок: " + (deadline or "не подтверждён"),
        "Место исполнения: " + (record.get("delivery") or "не подтверждено"),
        "Регион исполнения: " + (region or "не подтверждён") + (" · " + locality if locality else ""),
        "Предполагаемая услуга: " + (_text(item.get(PREFIX+"SUGGESTED_SERVICE"), 80) or "уточняется"),
        "Lead Score: " + (f"{score}/100" if score_valid else "не выставлен") + " · " + version,
        "AI-статус: " + _text(status, 140),
        "Рекомендация AI: " + action,
        f"Полнота: {known}/6. Требуют проверки: {unknown}.",
        "",
        "АВТОМАТИЧЕСКИЕ ПРОВЕРКИ",
        "Дубль закупки в видимой CRM: " + checks.get("duplicate", "не проверено"),
        "Действующий клиент: " + checks.get("customer", "не подтверждено"),
        "Стоп-лист: " + checks.get("stop", "не проверено"),
        "ФНС / ЕГРЮЛ: " + checks.get("registry", "официальная проверка не подтверждена"),
    ]
    if related:
        core += ["", "СВЯЗАННЫЕ СИГНАЛЫ ПО ИНН"] + related[:5]
    header = "\n".join(core)
    original = item[REPORT]
    if original.startswith(MANAGER_HEADER+"\n") and "\n"+MANAGER_END+"\n" in original:
        original = original.split("\n"+MANAGER_END+"\n",1)[1].lstrip("\n")
    report = header + "\n\n" + MANAGER_END + "\n\n" + original
    patch.update({SUMMARY:"\n".join(core[:10]), BRIEF:header, RECOMMENDATION:action})
    if len(report) <= MAX_REPORT:
        patch[REPORT] = report
    if item.get(DECISION) == MANAGER_PLACEHOLDER:
        patch[DECISION] = ""
    return patch
