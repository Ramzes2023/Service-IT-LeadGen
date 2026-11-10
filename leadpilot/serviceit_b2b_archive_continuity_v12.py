"""Lossless, read-only extraction of *previously verified* V10 source quotes.

This module never fetches a site and never upgrades archival information into
current factual verification. It can only recover a narrow, provenance-marked
excerpt from the V10 report archived in the independently verified V11 receipt.
It does not create buyer names, contact details or an official FNS identity.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from .serviceit_b2b_hold_ai_fields import PREFIX, AI_FIELDS

FULL = PREFIX + "AI_FULL_REPORT"
BRIEF = PREFIX + "AI_MANAGER_BRIEF"
SUMMARY = PREFIX + "AI_SUMMARY"
QUOTE = PREFIX + "AI_QUOTE"
LIMITATIONS = PREFIX + "AI_LIMITATIONS"
NEXT_ACTION = PREFIX + "AI_NEXT_ACTION"
STATUS = PREFIX + "AI_STATUS"
PUBLIC_HOSTS = frozenset((
    "www.b2b-center.ru", "b2b-center.ru", "tendem.ru",
    "zakupki.kontur.ru", "synapsenet.ru",
))
LABEL_TO_KEY = {
    "Сведения о заказчике": "BUYER",
    "Предмет и объём": "SCOPE",
    "Технологии / ТЗ": "TECHNOLOGY",
    "Срок подачи": "DEADLINE",
    "Цена / бюджет": "BUDGET",
    "Допуск / квалификация": "ELIGIBILITY",
    "Порядок подачи заявки": "SUBMISSION",
    "Документы участника": "DOCUMENTS",
    "Оплата / расчёты": "PAYMENT",
    "Обеспечение / гарантии": "GUARANTEE",
    "Критерии отбора": "EVALUATION",
    "География": "LOCATION",
    "Сроки и условия исполнения": "DELIVERY",
    "Дополнительные условия": "OTHER",
}


class ArchiveBlocked(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise ArchiveBlocked(code)


def public_url(value):
    if type(value) is not str or not 15 <= len(value) <= 650:
        return False
    parts = urlsplit(value)
    return (parts.scheme == "https" and (parts.hostname or "").lower() in PUBLIC_HOSTS
            and parts.username is None and parts.password is None and not parts.fragment
            and not any(x in value for x in ("\n", "\r", "\t", " ")))


def archived_quotes(fields):
    """Extract verbatim evidence + its exact cited URL from a V10 CRM report."""
    require(type(fields) is dict and set(fields) == AI_FIELDS,
            "ARCHIVE_NINE_FIELDS_REQUIRED")
    value = fields.get(FULL)
    require(type(value) is str and "ТЕНДЕРНОЕ ДОСЬЕ V10" in value,
            "ARCHIVED_V10_REPORT_REQUIRED")
    lines = value.splitlines()
    start = next((i for i, line in enumerate(lines)
                  if re.match(r"^07\s+ДОСЛОВНЫЕ ФРАГМЕНТЫ И ССЫЛКИ$", line)), -1)
    end = next((i for i, line in enumerate(lines)
                if i > start and re.match(r"^08\s+РЕЕСТР ИСТОЧНИКОВ$", line)), -1)
    require(0 <= start < end, "ARCHIVED_SOURCE_SECTION_REQUIRED")
    urls = {}
    for line in lines[end+1:]:
        match = re.fullmatch(
            r"\[(?:ОРИГИНАЛ\s*=\s*)?(\d{1,2})\]\s+(https://\S{5,650})",
            line.strip())
        if match and public_url(match.group(2)):
            ref = int(match.group(1))
            if 1 <= ref <= 16 and (ref not in urls or urls[ref] == match.group(2)):
                urls[ref] = match.group(2)
    rows, seen = [], set()
    for i in range(start+1, end-1):
        match = re.fullmatch(r"\d{2}\.\s+(.{5,90}?)\s+\[(\d{1,2})\]",
                             lines[i].strip())
        if match is None:
            continue
        label = match.group(1)
        kind = LABEL_TO_KEY.get(label)
        ref = int(match.group(2))
        url = urls.get(ref)
        if kind is None or url is None:
            continue
        quote_line = lines[i+1].strip()
        if not (quote_line.startswith("«") and quote_line.endswith("»")
                and 12 <= len(quote_line) <= 325):
            continue
        quote = quote_line[1:-1]
        if any(ord(ch) < 32 for ch in quote):
            continue
        key = (kind, quote, url)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"category": kind, "label": label, "quote": quote,
                     "url": url, "ref": ref})
        if len(rows) == 16:
            break
    return rows


def current_quote_count(fields):
    value = fields.get(FULL, "") if type(fields) is dict else ""
    if type(value) is not str:
        return 0
    return len(re.findall(r"(?m)^\d{2}\.\s+.{5,90}\s+\[\d{1,2}\]$", value))


def repair(current, previous_v10):
    """Keep all V11 facts/structure; restore only source-loss regressions.

    Every restored fact is explicitly labelled V10 ARCHIVE / NOT RECHECKED.
    No public-company contact or official INN may be fabricated or promoted.
    """
    require(type(current) is dict and set(current) == AI_FIELDS,
            "CURRENT_NINE_FIELDS_REQUIRED")
    require(all(type(v) is str and v.strip() for v in current.values()),
            "CURRENT_NINE_VALUES_INVALID")
    need_v11 = current[FULL]
    require("ТЕНДЕРНОЕ ДОСЬЕ V11" in need_v11,
            "CURRENT_V11_DOSSIER_REQUIRED")
    saved = archived_quotes(previous_v10)
    present = current_quote_count(current)
    if not saved or present >= len(saved):
        return dict(current), {"status": "V12_NO_SOURCE_LOSS",
                               "restored_quotes": 0}
    # V11 was re-run but public fetch failed; avoid falsely claiming the
    # old deadline or participation rules are fresh/current.
    count = min(len(saved), 16)
    for n in range(count, 0, -1):
        rows = saved[:n]
        new = dict(current)
        note = ("АРХИВ V10: ранее проверенные дословные фрагменты публичных "
                "страниц. Повторная доступность и актуальность НЕ подтверждены.")
        archive = ["", "09  ВОССТАНОВЛЕННЫЕ ДОКАЗАТЕЛЬСТВА (АРХИВ V10)",
                   note, ""]
        links = {}
        for index, row in enumerate(rows, 1):
            archive.append(f"A{index:02d}. {row['label']} [архив V10]")
            archive.append("    «" + row["quote"] + "»")
            links[row["url"]] = True
        archive += ["", "ИСТОЧНИКИ АРХИВА (проверены в V10, не перепроверены):"]
        archive += list(links)
        marker = "\nГРАНИЦЫ ДОСТОВЕРНОСТИ"
        if marker in need_v11:
            new[FULL] = need_v11.replace(marker, "\n".join(archive) + marker, 1)
        else:
            new[FULL] = need_v11 + "\n" + "\n".join(archive)
        category_replacement = {
            "DEADLINE": ("Срок подачи: не найден в доступных источниках",
                         "Срок подачи"),
            "ELIGIBILITY": ("Допуск / квалификация: не установлено в доступном тексте",
                            "Допуск / квалификация"),
            "DOCUMENTS": ("Документы участника: не установлено в доступном тексте",
                          "Документы участника"),
            "PAYMENT": ("Оплата / расчёты: не установлено в доступном тексте",
                        "Оплата / расчёты"),
            "BUDGET": ("Бюджет: в прочитанных страницах не найден", "Бюджет"),
        }
        for row in rows:
            rule = category_replacement.get(row["category"])
            if rule is not None and rule[0] in new[FULL]:
                new[FULL] = new[FULL].replace(
                    rule[0], rule[1] + ": [АРХИВ V10, сверить актуальность] «"
                    + row["quote"][:190] + "»", 1)
        selected = [row for row in rows
                    if row["category"] in ("DEADLINE", "ELIGIBILITY",
                                           "PAYMENT", "DOCUMENTS", "BUDGET")]
        route = "; ".join(row["label"] + " — «" + row["quote"][:100] + "»"
                          for row in selected[:3])
        marker = "\nАРХИВ V10 (" + str(len(rows)) + " цитат; не перепроверен): "
        new[BRIEF] += marker + (route or "см. раздел 09")
        new[SUMMARY] += marker + (route or "см. полное досье")
        if ("Публичных цитат пока нет" in new[QUOTE]):
            new[QUOTE] = ("[АРХИВ V10; актуальность не проверена] "
                          + rows[0]["quote"][:330])
        new[LIMITATIONS] += (" Архивные доказательства V10 восстановлены "
                             "без повторной проверки страниц; срок и условия "
                             "сверить с действующей редакцией закупки.")
        new[NEXT_ACTION] += (" Сверить архивные условия V10 с актуальным "
                             "извещением до коммерческого решения.")
        # Never modify status, company fields, score, stage, task or contacts.
        require(new[STATUS] == current[STATUS], "V12_STATUS_CHANGE_FORBIDDEN")
        limits_ok = all(type(v) is str and v.strip() and
                        len(v) <= (12000 if k == FULL else 4096)
                        for k, v in new.items())
        if limits_ok:
            return new, {"status": "V12_ARCHIVE_PROOF_RESTORED",
                         "restored_quotes": len(rows)}
    raise ArchiveBlocked("ARCHIVED_FACTS_DO_NOT_FIT_CRM")
