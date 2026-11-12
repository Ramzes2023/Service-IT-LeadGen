"""Offline, read-only B2B-Center email -> tender review -> Bitrix24 preview.

No Gmail access, website scraping, FNS API or Bitrix24 operations. Never call
crm.item.add using this output: this is a HOLD research preview, not approval.
"""
from __future__ import annotations

import argparse
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

MAX_EMAIL_BYTES = 256_000
MAX_EMAILS = 30
MAX_TENDERS_PER_EMAIL = 80
OFFICIAL_DOMAIN = "b2b-center.ru"
TENDER_PATH = re.compile(
    r"/(?:app/market-next|market)/[a-z0-9-]{1,160}/tender-(\d{5,12})/|"
    r"/search/number/l(\d{5,12})(?:-\d+){1,8}/?",
    re.I,
)
URL = re.compile(r"https://[^\s<>'\"\[\]]{1,2000}", re.I)
BULLETIN_START = re.compile(
    r"(?=(?:Запрос\s+цен|Запрос\s+предложений|Конкурс|"
    r"Предварительный\s+квалификационный\s+отбор|Процедура\s+закупки)\s*№\s*\d{5,12})",
    re.I,
)
INVITE_START = re.compile(r"(?=Организатор\s+закупки\s*:)", re.I)
# Strict ServiceIT domains. A generic "integration", "implementation",
# "monitoring", "service", "marking" or "equipment" is NOT proof of IT demand.
KINDS = {
    # Match only explicit procurement of IT personnel, not a generic vacancy
    # or recruiting advertisement. The title must state provision/outsourcing.
    "IT_STAFFING": re.compile(
        r"(?:предоставлени\w*|привлечени\w*|аутстаффинг\w*)\s+"
        r"(?:ит|it)[\s-]*специалист\w*", re.I),
    "1C": re.compile(r"(?<!\w)1[сc](?!\w)|1[сc]:", re.I),
    "IT_SUPPORT": re.compile(
        r"(?<!\w)(?:ит|it)[\s-]*(?:аутсорс\w*|поддержк\w*|обслуживан\w*|инфраструктур\w*)"
        r"|(?:технич\w*|сервисн\w*)\s+(?:поддержк\w*|обслуживан\w*)"
        r".{0,80}(?:сервер\w*|сетев\w*|компьютер\w*|информационн\w*\s+систем\w*|(?:ит|it)[\s-]*инфраструктур\w*)"
        r"|(?:технич\w*|сервисн\w*)\s+(?:обслуживан\w*|поддержк\w*)\s+"
        r"(?:баз\w*\s+данн\w*|(?<!\w)субд(?!\w))",
        re.I),
    "CRM": re.compile(r"битрикс[\s-]*24|(?<!\w)(?:crm|срм)(?!\w)|amocrm", re.I),
    "KKT_EDO": re.compile(
        r"(?<!\w)(?:ккт|эдо)(?!\w)|онлайн[\s-]*касс\w*|контрольно[\s-]*кассов\w*"
        r"|кассов\w*\s+(?:оборудован\w*|аппарат\w*|техник\w*|систем\w*)"
        r"|криптопро|диадок|электронн\w*\s+документооборот\w*"
        r"|маркировк\w*\s+(?:товар\w*|продукц\w*|остатк\w*)"
        r"|торгов\w*\s+оборудован\w*|(?<!\w)тсд(?!\w)",
        re.I),
    "SERVERS": re.compile(
        r"сервер\w*|сетев\w*\s+оборудован\w*|коммутатор\w*"
        r"|маршрутизатор\w*|ноутбук\w*|компьютер\w*"
        r"|рабоч\w*\s+станци\w*|(?<!\w)монитор(?:ы|ов|ам|ами|е|а)?(?!\w)",
        re.I),
    "CLOUD": re.compile(
        r"облачн\w*\s+(?:сервис\w*|инфраструктур\w*|"
        r"платформ\w*|хранилищ\w*|решени\w*)|"
        r"(?:основн\w*|резервн\w*)\s+облак\w*|"
        r"(?<!\w)(?:iaas|paas|saas)(?!\w)", re.I),
    "LICENSES": re.compile(
        r"(?:приобретени\w*|закупк\w*|продлени\w*|продлени\w*|"
        r"поставк\w*)\s+(?:программн\w*\s+)?лицензи\w*\s*"
        r"(?:microsoft\s*)?(?:teams|office\s*365|m365|"
        r"windows|exchange|sharepoint|microsoft\s*365)|"
        r"лицензи\w*\s+(?:на\s+)?(?:программн\w*\s+"
        r"обеспечен\w*|(?<!\w)по(?!\w))|"
        r"(?:продлени\w*|поставк\w*)\s+"
        r"(?:лицензи\w*\s+)?(?:microsoft\s*)?(?:teams|m365|office\s*365)",
        re.I),
}
IT_CONTEXT = re.compile(
    r"(?<!\w)1[сc](?!\w)|1[сc]:|битрикс[\s-]*24|(?<!\w)(?:crm|срм|api|erp|wms|эдо|ккт|ит|it|субд)(?!\w)"
    r"|программ\w*\s+обеспечен\w*|информационн\w*\s+систем\w*"
    r"|баз\w*\s+данн\w*|сайт\w*|веб[\s-]*приложен\w*"
    r"|интернет[\s-]*магазин\w*|сервер\w*|сетев\w*\s+инфраструктур\w*",
    re.I,
)
IT_INTEGRATION = re.compile(r"внедрен\w*|интеграц\w*|доработк\w*|обмен\w*\s+данными|экстрактор\w*", re.I)


def classify_services(title: str) -> list[str]:
    """Classify an individual tender SUBJECT, never the whole email.

    The output is service fit, NOT lead approval or proof of buyer identity.
    """
    if not isinstance(title, str):
        return []
    groups = [group for group, pattern in KINDS.items() if pattern.search(title)]
    if IT_CONTEXT.search(title) and IT_INTEGRATION.search(title):
        groups.append("INTEGRATION")
    return sorted(groups)



class InputRejected(ValueError):
    pass


class _TextLinks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.anchor: list[str | None] = []

    def handle_starttag(self, tag, attrs):
        if tag in {"p", "div", "tr", "td", "li", "br", "h1", "h2", "h3"}:
            self.parts.append("\n")
        if tag == "a":
            self.anchor.append(next((value for name, value in attrs if name == "href"), None))

    def handle_data(self, data):
        self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.anchor:
            href = self.anchor.pop()
            if href:
                self.parts.append(" [" + href + "] ")
        if tag in {"p", "div", "tr", "td", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def text(self):
        return "".join(self.parts)


def clean_tender_url(value: str) -> tuple[str, str] | None:
    """Return canonical public source URL and tender number, no tracking args."""
    if not isinstance(value, str) or len(value) > 2200:
        return None
    value = value.rstrip(".,;)]}»>'\"")
    try:
        u = urlsplit(value)
    except ValueError:
        return None
    if (u.scheme != "https" or (u.hostname or "").lower() not in
            {"www.b2b-center.ru", OFFICIAL_DOMAIN} or u.username or u.password
            or u.port not in {None, 443} or u.fragment):
        return None
    m = TENDER_PATH.fullmatch(u.path)
    if not m:
        return None
    number = m.group(1) or m.group(2)
    return f"https://www.b2b-center.ru{u.path}", number


def _message_body(msg):
    parts = []
    for part in msg.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        subtype = part.get_content_type()
        if subtype not in {"text/plain", "text/html"}:
            continue
        try:
            value = part.get_content()
        except (ValueError, LookupError, UnicodeError):
            continue
        if not isinstance(value, str):
            continue
        if subtype == "text/html":
            p = _TextLinks()
            p.feed(value[:180_000])
            parts.append(p.text())
        else:
            parts.append(value[:100_000])
    if not parts:
        raise InputRejected("NO_READABLE_EMAIL_CONTENT")
    return max(parts, key=lambda x: x.count("b2b-center.ru"))


def _get_field(block: str, name: str) -> str | None:
    m = re.search(rf"{re.escape(name)}\s*:\s*(.+?)\s*(?:\n|$)", block, re.I)
    if m:
        return " ".join(m.group(1).strip().split())[:320] or None
    return None


def _chunks(text: str):
    if "Организатор закупки:" in text and "Предмет закупки:" in text:
        return [x for x in INVITE_START.split(text) if "Предмет закупки:" in x]
    return [x for x in BULLETIN_START.split(text) if "Организатор:" in x and "Предмет договора:" in x]


def parse_email(raw: bytes) -> dict:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_EMAIL_BYTES:
        raise InputRejected("EMAIL_SIZE_INVALID")
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    sender = parseaddr(str(msg.get("From", "")))[1].lower()
    if not sender.endswith("@" + OFFICIAL_DOMAIN):
        raise InputRejected("SENDER_NOT_VENDOR_DOMAIN")
    subject = str(msg.get("Subject", ""))[:160]
    try:
        received = parsedate_to_datetime(str(msg.get("Date"))).isoformat()
    except (ValueError, TypeError, IndexError):
        received = None
    digest = sha256(raw).hexdigest()
    txt = _message_body(msg)
    found = []
    for block in _chunks(txt):
        candidates = [clean_tender_url(m.group(0)) for m in URL.finditer(block)]
        candidates = [x for x in candidates if x is not None]
        if not candidates:
            continue
        title = _get_field(block, "Предмет закупки") or _get_field(block, "Предмет договора")
        if not title:
            continue
        buyer = _get_field(block, "Организатор закупки") or _get_field(block, "Организатор")
        # Some authenticated B2B emails use "Актуально до", other
        # templates name the final submission time explicitly. A value from
        # the original notice remains EMAIL EVIDENCE, never verified tender
        # status until the public page/document is independently checked.
        deadline = next((
            value for label in (
                "Актуально до", "Окончание подачи заявок",
                "Окончание приёма заявок", "Срок подачи заявок",
                "Приём предложений до", "Окончание приема предложений",
                "Дата окончания подачи заявок",
            ) if (value := _get_field(block, label))
        ), None)
        head = re.search(
            r"(?:Запрос\s+цен|Запрос\s+предложений|Конкурс|Предварительный\s+квалификационный\s+отбор|Процедура\s+закупки)\s*№\s*(\d{5,12})",
            block, re.I,
        )
        item = next((x for x in candidates if head and x[1] == head.group(1)), None) or candidates[0]
        kinds = classify_services(title)
        found.append({
            "source": "B2B-Center", "tender_id": item[1], "source_url": item[0],
            "subject": title, "organizer_as_named_in_email": buyer,
            "email_received_at": received, "published_at": None,
            "deadline_as_stated_in_email": deadline,
            "deadline_verified_from_original": False,
            "buyer_inn": None, "buyer_fns_size": "UNKNOWN", "buyer_region": "UNKNOWN",
            "source_service_groups": kinds,
            "record_type": "TENDER_OPPORTUNITY_HOLD",
            "qualification_status": "HOLD_ORIGINAL_BUYER_INN_FNS_DATE_STATUS_STOPLIST",
            "contact_approved": False, "bitrix_write_allowed": False,
        })
        if len(found) >= MAX_TENDERS_PER_EMAIL:
            break
    return {
        "email_sha256": digest, "subject": subject, "email_received_at": received,
        "source_authentication": "VENDOR_DOMAIN_ONLY_NOT_DKIM_VALIDATED",
        "observations": found,
    }


def build_bitrix_preview(messages: list[bytes]) -> dict:
    if not 1 <= len(messages) <= MAX_EMAILS:
        raise InputRejected("EMAIL_COUNT_INVALID")
    seen_emails, tenders = set(), {}
    for raw in messages:
        notice = parse_email(raw)
        if notice["email_sha256"] in seen_emails:
            continue
        seen_emails.add(notice["email_sha256"])
        for row in notice["observations"]:
            key = row["source"] + ":" + row["tender_id"]
            entry = tenders.setdefault(key, {**row, "version_sha256": []})
            entry["version_sha256"].append(notice["email_sha256"])
    cards = []
    for row in tenders.values():
        if not row["source_service_groups"]:
            continue
        fields = {
            "title": "ВОЗМОЖНОСТЬ · B2B-Center №" + row["tender_id"],
            "categoryId": 21, "stageId": "DT1062_21:NEW",
            "UF_CRM_13_SVCIT_SIGNAL_KIND": "tender-opportunity",
            "UF_CRM_13_SVCIT_SIGNAL_ID": "B2B-CENTER-" + row["tender_id"],
            "UF_CRM_13_SVCIT_SIGNAL_SOURCE": "B2B-Center email subscription",
            "UF_CRM_13_SVCIT_SIGNAL_URL": row["source_url"],
            "UF_CRM_13_SVCIT_SUGGESTED_SERVICE": ", ".join(row["source_service_groups"]),
            "UF_CRM_13_SVCIT_COMPANY_NAME": row["organizer_as_named_in_email"] or "НЕ УСТАНОВЛЕН",
            "UF_CRM_13_SVCIT_EVIDENCE_STATUS": "HOLD: дата публикации, статус, ИНН/ФНС и клиент не проверены",
            "UF_CRM_13_SVCIT_REVIEW_DECISION": "Ожидает независимой проверки менеджером",
            "UF_CRM_13_SVCIT_STOP_REASON": "Не является квалифицированным B2B-лидом",
        }
        cards.append({
            "action": "PREVIEW_ONLY_NOT_EXECUTABLE", "source_tender_id": row["tender_id"],
            "entityTypeId": 1062, "fields_not_sent": fields,
            "data_quality": row["qualification_status"],
        })
    return {
        "status": "B2B_EMAIL_TENDER_RESEARCH_PREVIEW_ONLY",
        "emails_seen": len(seen_emails), "tenders_unique": len(tenders),
        "it_opportunity_previews": len(cards), "buyer_inn_verified": 0,
        "fns_micro_small_77_50": 0, "qualified_real_leads": 0,
        "crm_writes": 0, "outreach": 0,
        "cards_not_sent": cards, "tenders": list(tenders.values()),
    }


def main(argv=None):
    p = argparse.ArgumentParser(description="Offline B2B-Center email -> Bitrix preview (NEVER writes)")
    p.add_argument("email", nargs="+")
    p.add_argument("--output", required=True)
    args = p.parse_args(argv)
    target = Path(args.output).resolve()
    root = Path("exports").resolve()
    if not target.is_relative_to(root) or target.suffix != ".json" or target.exists():
        raise InputRejected("NEW_JSON_OUTPUT_UNDER_EXPORTS_REQUIRED")
    result = build_bitrix_preview([Path(name).read_bytes() for name in args.email])
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps({
        k: v for k, v in result.items() if k not in {"cards_not_sent", "tenders"}
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
