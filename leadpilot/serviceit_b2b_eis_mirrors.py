"""Resolve public 223-FZ records from the verified B2B publication."""
import re
from urllib.parse import urlsplit

import httpx

from .bitrix_1062_mapping import _is_valid_inn
from .serviceit_public_evidence_research import ResearchBlocked, _host, _page, _safe_model_excerpt
from .serviceit_vps_b2b_deep_research import MAX_PAGES, public_buyer_candidate


EIS_NOTICE_NUMBER = re.compile(
    r"(?iu)\bномер\s+извещения\s+(?:на|в)\s+ЕИС\s*[:№]?\s*(3\d{10})(?!\d)"
)
EIS_MIRRORS = (
    ("tendik.ru", "/tender/eis223/{number}"),
    ("sprosradar.ru", "/zakupki/223/{number}/"),
)


def _source_eis_number(findings, notice, client):
    url, tid = notice["source_url"], notice["tender_id"]
    hosts = frozenset({"b2b-center.ru"})
    parsed = urlsplit(url)
    match = re.fullmatch(r"/(?:app/market-next|market)/[a-z0-9-]+/tender-(\d{5,12})/?",
                         parsed.path)
    if (_host(url, hosts) not in {"www.b2b-center.ru", "b2b-center.ru"}
            or not match or match[1] != tid or parsed.query):
        return None
    texts = []
    for row in findings.get('pages', []):
        if not isinstance(row,dict) or not isinstance(row.get('excerpt'),str):
            continue
        candidate=urlsplit(row.get('url',''))
        if (candidate.scheme=='https' and candidate.hostname==parsed.hostname
                and not candidate.query and not candidate.fragment
                and re.fullmatch(r'/(?:app/market-next|market)/[a-z0-9-]+/tender-'+re.escape(tid)+r'/?',candidate.path)):
            texts.append(row['excerpt'])
    for text in texts:
        numbers = set(EIS_NOTICE_NUMBER.findall(text))
        if len(numbers) == 1 and re.search(r"№\s*" + re.escape(tid) + r"(?!\d)", text):
            return next(iter(numbers))
    # Search extracts are often truncated before the EIS label. Read the
    # canonical signed page once with the shared public/robots policy.
    page = _page(client, url, hosts)
    text = " ".join(page["text"].split())
    numbers = set(EIS_NOTICE_NUMBER.findall(text))
    if len(numbers) == 1 and re.search(r"№\s*" + re.escape(tid) + r"(?!\d)", text):
        return next(iter(numbers))
    return None


def _registry_buyer(rows):
    """Require both exact registry mirrors to agree on the named buyer."""
    pages = {urlsplit(row["url"]).hostname: row for row in rows}
    if not {"tendik.ru", "sprosradar.ru"} <= set(pages):
        return None
    try:
        reference = public_buyer_candidate({
            "status": "PUBLIC_PAGES_INSPECTED", "pages": [pages["tendik.ru"]],
        })
    except ValueError:
        return None
    if not reference:
        return None
    row = pages["sprosradar.ru"]
    body = row["excerpt"]
    name = re.search(r'[«"]([^»"]+)[»"]', reference)
    if not name:
        return None
    # The registry mirror can print only the distinctive name, while the
    # other mirror spells out the legal form. Match the same full name and
    # require a single checksum-valid INN in that labelled buyer field.
    tokens = re.findall(r"[^\W_]+", name[1])
    if len(tokens) < 2:
        return None
    label = r"[^\w]+".join(map(re.escape, tokens))
    matches = re.findall(
        r"(?iu)\bзаказчик\s*[:—–-]?\s*"
        r"(?:ООО\s+|АО\s+|ПАО\s+|общество\s+с\s+ограниченной\s+ответственностью\s+)?"
        r'[«"]?' + label + r'[»"]?\s*[·,;:—–-]?\s*'
        r"ИНН\s*[:№]?\s*(\d{10}|\d{12})(?!\d)",
        body,
    )
    validated = {inn for inn in matches if _is_valid_inn(inn)}
    if len(validated) != 1:
        return None
    return {"buyer": reference, "inn": next(iter(validated)),
            "url": row["url"], "reference": pages["tendik.ru"]["url"]}


def supplement_eis_mirrors(findings, notice, client, *, verified_number=None):
    """Cross-check exact registry-linked public pages, with no provider credits."""
    try:
        number = _source_eis_number(findings, notice, client)
    except (ResearchBlocked, httpx.HTTPError, KeyError, TypeError):
        number = None
    if not number and isinstance(verified_number,str) and re.fullmatch(r"3\d{10}",verified_number):
        number = verified_number
    if not number:
        return findings
    tid = notice["tender_id"]
    tender_number = re.compile(r"(?<!\d)" + re.escape(tid) + r"(?!\d)")
    eis_number = re.compile(r"(?<!\d)" + re.escape(number) + r"(?!\d)")
    words = re.findall(r"[^\W_]{4,}", notice["subject"].casefold())
    words = set(words) - {"закупка", "тендер", "поставка", "приобретение"}
    prior = list(findings.get("pages") or [])
    known = {p["url"] for p in prior if isinstance(p, dict) and "url" in p}
    added = []
    for host, path in EIS_MIRRORS:
        url = "https://" + host + path.format(number=number)
        if url in known:
            continue
        try:
            text = " ".join(_page(client, url, frozenset({host}))["text"].split())
        except (ResearchBlocked, httpx.HTTPError):
            continue
        match = tender_number.search(text)
        if not match or not eis_number.search(text):
            continue
        window = text[max(0, match.start()-300):match.end()+300].casefold()
        if len(words & set(re.findall(r"[^\W_]{4,}", window))) < 2:
            continue
        added.append({"url": url, "excerpt": text[:7000], "eis_notice_number": number})
    if not added:
        return findings
    result = {**findings, "status": "PUBLIC_PAGES_INSPECTED",
            "pages": (prior[:1] + added + prior[1:])[:MAX_PAGES],
            "model_excerpt": findings.get("model_excerpt") or _safe_model_excerpt(added[0]["excerpt"]),
            "eis_mirror_count": len(added)}
    registry_buyer = _registry_buyer(added)
    if registry_buyer:
        result["eis_registry_buyer"] = registry_buyer
    return result




def registry_buyer_note(findings):
    evidence = findings.get("eis_registry_buyer")
    if not evidence:
        return ""
    return (
        "\n\nЗАКАЗЧИК ПО ИЗВЕЩЕНИЮ ЕИС\n"
        f"{evidence['buyer']}, ИНН {evidence['inn']}.\n"
        f"Реквизиты: {evidence['url']}\n"
        f"Сверка наименования: {evidence['reference']}"
    )
