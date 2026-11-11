"""Free deterministic extraction from already verified public procurement pages.

No network/model/CRM actions. Caller must independently prove the exact tender
identity and the corporate website belonging to the identified buyer.
All unknown facts remain null, and corporate self-reported INN is NOT FNS proof.
"""
from __future__ import annotations
from datetime import datetime, timezone
from urllib.parse import urlsplit
import re

DATE = re.compile(r"(?<!\d)\d{1,2}[./]\d{1,2}[./]20\d{2}(?:\s*(?:до\s*)?\d{1,2}:\d{2})?")
AMOUNT = re.compile(r"(?<!\d)(\d{1,3}(?:[\s\u00a0]\d{3})+(?:[,.]\d{1,2})?|\d{4,12})(?!\d)")
EMAIL = re.compile(r"(?i)(?<![\w.@])[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}(?![\w.@])")
BUYER = re.compile(r"(?iu)(?:^|\n)\s*заказчик\s*[:—–-]?\s*((?:общество с ограниченной ответственностью|публичное акционерное общество|акционерное общество|ООО|ПАО|АО)\s*[«\"']?[^<>\n]{3,160})")
LABELS = {
    "submission_deadline": ("окончание приёма заявок", "окончание приема заявок", "подача заявок", "срок подачи заявок"),
    "delivery_address": ("адрес поставки / оказания услуг", "место поставки", "адрес поставки", "место оказания услуг"),
    "geography": ("регион",),
    "budget": ("общая сумма закупки", "начальная цена контракта", "начальная цена", "максимальная цена", "нмцк"),
    "payment_terms": ("условия оплаты", "порядок расчетов", "условия расчётов"),
    "delivery_terms": ("условия поставки / оказания услуг", "условия поставки", "срок исполнения"),
    "participation_terms": ("условия участия", "требования к заявке", "требования к участникам"),
}
HIDDEN = ("для просмотра зарегистрируйтесь", "доступно зарегистрированным",
          "зарегистрируйтесь или войдите", "войдите в учётную запись",
          "войдите в учетную запись", "скрыты", "не раскрываются")


def valid_inn(value: str) -> bool:
    if type(value) is not str or not value.isascii() or not value.isdigit():
        return False
    n = [int(v) for v in value]
    if len(n) == 10:
        return sum(x*y for x,y in zip(n, (2,4,10,3,5,9,4,6,8))) % 11 % 10 == n[9]
    if len(n) == 12:
        a = sum(x*y for x,y in zip(n, (7,2,4,10,3,5,9,4,6,8))) % 11 % 10
        b = sum(x*y for x,y in zip(n, (3,7,2,4,10,3,5,9,4,6,8))) % 11 % 10
        return a == n[10] and b == n[11]
    return False


def valid_ogrn(value: str) -> bool:
    if type(value) is not str or not value.isascii() or not value.isdigit():
        return False
    if len(value) == 13:
        return int(value[:12]) % 11 % 10 == int(value[-1])
    if len(value) == 15:
        return int(value[:14]) % 13 % 10 == int(value[-1])
    return False


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*#_]+", " ", value)).strip()


def _lines(text: str) -> list[str]:
    return [_clean(s) for s in text.splitlines() if _clean(s)][:3500]


def _find_label(lines: list[str], labels: tuple[str, ...]) -> str | None:
    for index, row in enumerate(lines):
        folded = row.casefold().replace("ё", "е")
        for label in labels:
            normal = label.replace("ё", "е")
            if (folded == normal or folded.startswith(normal + ":")
                    or folded.startswith(normal + " ")):
                tail = row[len(label):].strip(" :—–-")
                if tail:
                    return tail[:350]
                if index + 1 < len(lines):
                    return lines[index + 1][:350]
    return None


def _cite(value: str | None, source_url: str, status: str) -> dict | None:
    return {"value": value, "source_url": source_url, "evidence": status} if value else None


def _legal_name(value: str) -> str:
    s = re.sub(r"[^А-ЯA-Z0-9]", "", _clean(value).upper().replace("Ё", "Е"))
    return (s.replace("ОБЩЕСТВОСОГРАНИЧЕННОЙОТВЕТСТВЕННОСТЬЮ", "ООО")
             .replace("ПУБЛИЧНОЕАКЦИОНЕРНОЕОБЩЕСТВО", "ПАО")
             .replace("АКЦИОНЕРНОЕОБЩЕСТВО", "АО"))


def extract_tender_facts(*, tender_id: str, title: str, tender_text: str,
                         source_url: str, verified_source: bool) -> dict:
    """Read one PUBLIC tender detail page whose ID/title/platform are proven upstream."""
    if (verified_source is not True or type(tender_text) is not str
            or not 30 <= len(tender_text) <= 120000
            or type(tender_id) is not str or re.fullmatch(r"\d{5,12}", tender_id) is None
            or type(title) is not str or len(title) < 10
            or type(source_url) is not str or urlsplit(source_url).scheme != "https"):
        return {}
    rows = _lines(tender_text)
    for i, row in enumerate(rows):
        if row.casefold().startswith(("с этими торгами также смотрят", "похожие закупки", "смотрите также")):
            rows = rows[:i]
            break
    joined = "\n".join(rows)
    found = BUYER.search(joined)
    buyer = None
    if found:
        buyer = re.split(r"(?i:ИНН|ОГРН|Подача заявок|Срок подачи)", found.group(1))[0].strip(" :—–-")
        if not 4 <= len(buyer) <= 160 or "не указ" in buyer.casefold():
            buyer = None
    # An organizer can be an agent or ETP participant, not necessarily the
    # purchasing legal entity. Never silently convert it into buyer.
    organizer = _find_label(rows, ("организатор",))
    if not organizer or not re.match(r"(?iu)^(?:ООО|ПАО|АО|Общество|Акционерное)", organizer):
        legacy = re.search(r"(?iu)организатор\s*:\s*\|\s*((?:ООО|ПАО|АО|Общество|Акционерное)[^|\n]{3,150})\s*\|", joined)
        organizer = legacy.group(1).strip() if legacy else ""
    cutoff = _find_label(rows, ("актуально до",))
    if not cutoff:
        legacy_cutoff = re.search(r"(?iu)актуально\s+до\s*:\s*\|\s*(\d{1,2}[./]\d{1,2}[./]20\d{2}\s+\d{1,2}:\d{2})", joined)
        cutoff = legacy_cutoff.group(1) if legacy_cutoff else ""
    exact_cutoff = DATE.search(cutoff or "")
    details = {
        "tender_id": _cite(tender_id, source_url, "PUBLIC_TENDER_ID"),
        "title": _cite(title, source_url, "PUBLIC_TENDER_TITLE"),
        "buyer": _cite(buyer, source_url, "PUBLIC_PAGE_BUYER_CLAIM"),
        "organizer": _cite(organizer, source_url, "PUBLIC_PAGE_ORGANIZER_NOT_BUYER_PROOF"),
        "listed_until": _cite(exact_cutoff.group(0), source_url, "PUBLIC_LISTING_CUTOFF_NOT_APPLY_DEADLINE") if exact_cutoff else None,
        "submission_deadline": None, "budget_rub": None,
        "budget_state": "NOT_PUBLISHED", "delivery_address": None,
        "geography": None, "payment_terms": None, "delivery_terms": None,
        "participation_terms": None, "documents_access": "NOT_ESTABLISHED",
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    for field in ("submission_deadline", "delivery_address", "geography",
                  "payment_terms", "delivery_terms", "participation_terms"):
        value = _find_label(rows, LABELS[field])
        if not value or any(word in value.casefold() for word in HIDDEN):
            continue
        if field == "submission_deadline":
            match = DATE.search(value)
            if not match:
                continue
            value = match.group(0)
        if field in ("payment_terms", "delivery_terms", "participation_terms") and len(value) < 12:
            continue
        details[field] = _cite(value, source_url, "EXACT_PUBLIC_TENDER_TEXT")
    amount = _find_label(rows, LABELS["budget"])
    if amount:
        if any(s in amount.casefold() for s in ("не указ", "не задан", "не определ", "без цены")):
            details["budget_state"] = "EXPLICITLY_NOT_PUBLISHED"
        else:
            match = AMOUNT.search(amount)
            if match and ("руб" in amount.casefold() or "₽" in amount):
                details["budget_rub"] = _cite(match.group(1).replace(" ", "").replace("\u00a0", "").replace(",", "."),
                                               source_url, "EXACT_PUBLIC_TENDER_TEXT")
                details["budget_state"] = "PUBLIC_AMOUNT"
    match = re.search(r"(?iu)документация\s+от\s+организатора", joined)
    if match:
        nearby = joined[match.end():match.end() + 300].casefold()
        details["documents_access"] = ("LOGIN_REQUIRED" if any(s in nearby for s in HIDDEN)
                                       else "PUBLIC_STATUS_UNVERIFIED")
    return details


def attach_corporate_profile(tender: dict, *, company_url: str, company_text: str,
                             independently_verified_website: bool) -> dict:
    """Match legal name and checksum INN from buyer's proven corporate page.

    Self-reported website data never become FNS-confirmed data. No outreach.
    """
    out = dict(tender) if type(tender) is dict else {}
    buyer = (out.get("buyer") or {}).get("value")
    if (not independently_verified_website or type(buyer) is not str
            or type(company_url) is not str or type(company_text) is not str
            or urlsplit(company_url).scheme != "https"
            or not 30 <= len(company_text) <= 120000):
        return out
    buyer_normalized = _legal_name(buyer)
    if len(buyer_normalized) < 5:
        return out
    rows = _lines(company_text)
    for i, row in enumerate(rows):
        if buyer_normalized not in _legal_name(row):
            continue
        # Mentioning the buyer among clients or suppliers does not prove
        # that this is the buyer's own corporate identity page.
        head = row.casefold().replace("ё", "е")
        if not re.match(r"(?iu)^(?:полное наименование(?: организации)?\s*[:—-]?|(?:ооо|пао|ао|общество с ограниченной ответственностью|публичное акционерное общество)\b|©\s*\d{4}\s+(?:ооо|пао|ао)\b)", head):
            continue
        # Multiple legal entities on the same line cannot be disambiguated
        # by a nearby INN. Prefer a dedicated legal-entity header/row.
        if len(re.findall(r'(?iu)\b(?:ООО|ПАО|АО)\s*[«"]', row)) > 1:
            continue
        local_rows = rows[i:i + 7]
        nearby = " ".join(local_rows)[:900]
        inn = re.search(r"(?iu)\bИНН\s*[:№-]?\s*(\d{10}(?:\d{2})?)\b", nearby)
        ogrn = re.search(r"(?iu)\bОГРН(?:ИП)?\s*[:№-]?\s*(\d{13}(?:\d{2})?)\b", nearby)
        if not inn or not valid_inn(inn.group(1)):
            continue
        out["buyer_inn"] = _cite(inn.group(1), company_url, "WEBSITE_SELF_REPORTED_NOT_FNS_VERIFIED")
        if ogrn and valid_ogrn(ogrn.group(1)):
            out["buyer_ogrn"] = _cite(ogrn.group(1), company_url, "WEBSITE_SELF_REPORTED_NOT_FNS_VERIFIED")
        out["company_public_emails"] = [
            _cite(email.lower(), company_url, "OFFICIAL_WEBSITE_PUBLIC_CONTACT_NO_OUTREACH")
            for email in list(dict.fromkeys(EMAIL.findall(company_text)))[:6]]
        legal_address = next((entry.split(":", 1)[-1].strip()
                              for entry in local_rows
                              if entry.casefold().startswith("юридический адрес:")), "")
        if legal_address and 15 <= len(legal_address) <= 270:
            out["buyer_legal_address"] = _cite(
                legal_address, company_url, "OFFICIAL_WEBSITE_SELF_REPORTED_ADDRESS")
        phone_pattern = re.compile(
            r"(?<!\d)(?:\+7|8)[ -]*\(?\d{3}\)?[ -]*\d{3}[ -]*\d{2}[ -]*\d{2}(?!\d)")
        out["company_public_phones"] = [
            _cite(phone, company_url, "OFFICIAL_WEBSITE_PUBLIC_CONTACT_NO_OUTREACH")
            for phone in list(dict.fromkeys(phone_pattern.findall(company_text)))[:6]]
        out["fns_registry_status"] = "NOT_CHECKED"
        break
    return out
