"""Bounded OFFLINE normalization contract, NOT Bidzaar's vendor API schema.

No transport, credentials, CRM imports or implicit trust in supplied assertions.
Only an official specification and entitlement can enable a future live adapter.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from .verified_import import is_valid_inn
from .sme_vacancy_audit import _read_fns_candidates

MODE = "BIDZAAR_AUTHORIZED_NORMALIZATION_V1"
MAX_BYTES = 1_000_000
SERVICES = {
    "1c": ("1с", "1c"),
    "it_support": ("ит-аутсорсинг", "ит аутсорсинг", "it outsourcing", "поддержка пользователей"),
    "kkt": ("ккт", "онлайн-касс", "фискальн", "маркировк"),
    "retail_equipment": ("торговое оборудование", "сканер штрих", "принтер этикет"),
    "signature_edo": ("криптопро", "cryptopro", "электронная подпись", "эдо"),
    "crm": ("битрикс24", "bitrix24", "crm"),
    "servers_network": ("администрирование сервер", "сетевое администрирование", "сервер", "сетевого оборудования"),
    "erp_api": ("erp", "интеграци", "api"),
    "chatbots_ai": ("чат-бот", "чатбот", "искусственн", "chatbot"),
}
OFFERINGS = {
    "1c": "Review scope for 1C implementation, licensing or support",
    "it_support": "Review outsourced IT and user support scope",
    "kkt": "Review cash register, fiscal and marking requirements",
    "retail_equipment": "Review retail equipment supply and installation",
    "signature_edo": "Review electronic signature, CryptoPro and EDI requirements",
    "crm": "Review Bitrix24/CRM implementation scope",
    "servers_network": "Review server and network administration scope",
    "erp_api": "Review ERP/API integration scope",
    "chatbots_ai": "Review chatbot/AI project scope",
}
FIELDS = set("id url title excerpt category origin kind paid_external status posted_at deadline_at observed_at buyer_name buyer_inn inn_role buyer_region execution_region ownership access".split())


def _exact(value, fields):
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Invalid normalization contract fields")


def _text(value, limit=1200):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError("Invalid text")
    # Reject contact/credential-bearing text rather than persisting it or errors.
    if re.search(r"@|https?://|bearer|token|password|secret|api[_ -]?key|\+\d[\d ()-]{8,}", value, re.I):
        raise ValueError("Sensitive text rejected")
    return value


def _time(value):
    try:
        if not isinstance(value, str) or len(value) > 40:
            raise ValueError()
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if d.tzinfo is None:
            raise ValueError()
        return d.astimezone(timezone.utc)
    except (TypeError, ValueError):
        raise ValueError("Expected timezone-aware timestamp") from None


def _url(value, tender_id):
    if not isinstance(value, str):
        raise ValueError("Invalid official notice URL")
    u = urlsplit(value)
    if (u.scheme != "https" or u.netloc != "bidzaar.com" or u.query or u.fragment
            or u.path not in (f"/app/process/light/{tender_id}", f"/process/light/{tender_id}")):
        raise ValueError("Invalid official notice URL")
    return value


def decode_contract(body: bytes, *, status=200, content_type="application/json"):
    """Validate a supplied response/file; never fetch, retry or echo its body."""
    if (status != 200 or not isinstance(body, bytes) or len(body) > MAX_BYTES
            or content_type.split(";")[0].strip().lower() != "application/json"):
        raise ValueError("Access or response blocked")
    if re.search(rb"captcha|<html|<!doctype|sign.?in|log.?in", body, re.I):
        raise ValueError("Access challenge blocked")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON field")
            result[key] = value
        return result
    try:
        return json.loads(body.decode("utf-8-sig"), object_pairs_hook=pairs)
    except (UnicodeError, ValueError):
        raise ValueError("Invalid JSON contract") from None


def review(doc, *, now=None, fns_file=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Timezone required")
    now = now.astimezone(timezone.utc)
    _exact(doc, {"mode", "authorization", "items"})
    if doc["mode"] != MODE or doc["authorization"] != "COMPANY_AUTHORIZED_EXPORT":
        raise ValueError("Authorized normalization contract required")
    items = doc["items"]
    if not isinstance(items, list) or len(items) > 100:
        raise ValueError("Expected at most 100 records")
    # Existing exact-INN FNS validator; authenticity still requires independent review.
    if fns_file and Path(fns_file).stat().st_size > MAX_BYTES:
        raise ValueError("FNS evidence too large")
    fns, snapshot = _read_fns_candidates(fns_file, now.date()) if fns_file else ({}, None)
    unique, histories = {}, {}
    for r in items:
        _exact(r, FIELDS)
        for key in FIELDS - {"paid_external", "posted_at", "deadline_at", "observed_at", "url"}:
            _text(r[key])
        tid = r["id"]
        if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", tid):
            raise ValueError("Invalid tender ID")
        _url(r["url"], tid)
        for key, allowed in {
            "origin": {"bidzaar_original", "aggregated_government", "aggregated_other", "unknown"},
            "kind": {"procurement", "vacancy", "article", "marketing", "supplier_registry", "unknown"},
            "status": {"open", "closed", "cancelled", "unknown"},
            "inn_role": {"buyer", "organizer", "unknown"},
            "ownership": {"private", "state", "unknown"},
            "access": {"authorized", "paid_required", "unknown"},
        }.items():
            if r[key] not in allowed:
                raise ValueError("Invalid classification")
        if type(r["paid_external"]) is not bool:
            raise ValueError("External paid request flag required")
        observed = _time(r["observed_at"])
        posted = _time(r["posted_at"]) if r["posted_at"] is not None else None
        deadline = _time(r["deadline_at"]) if r["deadline_at"] is not None else None
        if observed > now or (posted and posted > observed) or (posted and deadline and deadline <= posted):
            raise ValueError("Inconsistent timestamps")
        r = {**r, "observed_at": observed.isoformat(),
             "posted_at": posted.isoformat() if posted else None,
             "deadline_at": deadline.isoformat() if deadline else None}
        history = histories.setdefault(tid, [])
        history.append(observed)
        old = unique.get(tid)
        if old:
            if any(old[k] != r[k] for k in ("buyer_inn", "buyer_name", "origin", "url")):
                raise ValueError("Conflicting tender identity")
            if old["observed_at"] == r["observed_at"] and old != r:
                raise ValueError("Conflicting observation")
        if old is None or observed > _time(old["observed_at"]):
            unique[tid] = dict(r)
    rows, counts, firms = [], Counter(), {}
    for tid, r in sorted(unique.items()):
        holds = ["SOURCE_NOT_LIVE_VERIFIED", "STOP_LIST_PENDING", "EXISTING_CLIENTS_PENDING", "MANAGER_APPROVAL_PENDING"]
        text = (r["title"] + " " + r["excerpt"]).lower().replace("ё", "е")
        excluded = r["kind"] != "procurement" or bool(re.search(r"ваканси|требуется сотрудник|резюме|информационн.*стать|рекламн", text))
        matches = {} if excluded else {s: [w for w in words if w in text] for s, words in SERVICES.items() if any(w in text for w in words)}
        direct = r["origin"] == "bidzaar_original"
        paid = r["paid_external"] and not excluded
        deadline = _time(r["deadline_at"]) if r["deadline_at"] else None
        posted = _time(r["posted_at"]) if r["posted_at"] else None
        active = r["status"] == "open" and deadline is not None and now < deadline
        exact_inn = r["inn_role"] == "buyer" and bool(re.fullmatch(r"[0-9]{10}", r["buyer_inn"])) and is_valid_inn(r["buyer_inn"])
        company = fns.get(r["buyer_inn"]) if exact_inn else None
        region = r["buyer_region"]
        eligible = region in {"77", "50"}
        region_consistent = company and company["region"] == {"77": "Москва", "50": "Московская область"}.get(region)
        conditions = {
            "NOT_DIRECT_ORIGINAL": direct, "NOT_EXTERNAL_PAID_PROCUREMENT": paid,
            "NO_SERVICE_MATCH": bool(matches), "NOT_OPEN_BEFORE_DEADLINE": active,
            "POSTED_UNKNOWN_OR_NOT_NEW": posted is not None and timedelta(0) <= now-posted <= timedelta(days=30),
            "OBSERVATION_STALE": now-_time(r["observed_at"]) <= timedelta(days=1),
            "BUYER_LEGAL_INN_UNVERIFIED": exact_inn and bool(r["buyer_name"]),
            "BUYER_REGION_NOT_MOSCOW_MO": eligible, "FNS_EXACT_CATEGORY_PENDING": bool(company and region_consistent),
            "STATE_OR_UNKNOWN_OWNERSHIP": r["ownership"] == "private",
            "ACCESS_ENTITLEMENT_PENDING": r["access"] == "authorized",
        }
        holds.extend(k for k, ok in conditions.items() if not ok)
        if company:
            holds.append("FNS_LOCAL_EVIDENCE_AUTHENTICITY_REVIEW_PENDING")
        inn_key = r["buyer_inn"] if exact_inn else "UNKNOWN:" + tid
        firms.setdefault(inn_key, []).append(tid)
        counts.update({"direct_originals": int(direct), "aggregated_government": int(r["origin"] == "aggregated_government"),
                       "matched_nine_services": int(bool(matches)), "open_before_deadline": int(active),
                       "buyer_exact_checksum_inn": int(exact_inn), "fns_moscow_mo_local_matches": int(bool(company and region_consistent))})
        rows.append({**r, "service_evidence": matches, "service_evidence_basis": "SUPPLIED_TITLE_EXCERPT_NOT_CATEGORY_OR_VERIFIED_INTENT",
                     "mvp_priority": bool(set(matches) & {"1c", "it_support", "kkt", "retail_equipment"}),
                     "buyer_inn_status": "CHECKSUM_VALID_BUYER_ASSERTION_UNVERIFIED" if exact_inn else "UNVERIFIED",
                     "buyer_region_priority": "PRIMARY" if eligible else "SECONDARY" if region == "06" else "REVIEW",
                     "fns_category": company["fns_category"] if company else "UNKNOWN", "fns_snapshot": snapshot,
                     "first_observed_at": min(histories[tid]).isoformat(), "last_observed_at": max(histories[tid]).isoformat(),
                     "expires_at": deadline.isoformat() if deadline else None,
                     "reason_now": "Supplied open request before deadline; verify original" if active else "No verified current request",
                     "suggested_offering": [OFFERINGS[s] for s in matches],
                     "next_manager_action": "Verify official notice, buyer identity, FNS evidence, entitlement and stop/client lists",
                     "score_status": "NOT_SCORED", "holds": holds,
                     "queue": "TARGET_EVIDENCE_REVIEW" if company and region_consistent and r["ownership"] == "private" else "NON_TARGET_OR_UNKNOWN_BUYER_REVIEW",
                     "crm_ready": False, "outbound_authorized": False})
    return {"mode": "OFFLINE_BIDZAAR_RESEARCH_ONLY", "LIVE_SOURCE": "BLOCKED_NOT_CONNECTED",
            "LIVE_ACCEPTANCE": "BLOCKED", "QUALIFIED_REAL_LEADS": 0,
            "real_live_counts": {k: 0 for k in ("raw", "direct_originals", "aggregated_government", "matched_nine_services", "open_before_deadline", "buyer_exact_inn", "buyer_sme_moscow_mo", "manager_review_qualified")},
            "local_unverified_counts": {"raw": len(items), "unique_tenders": len(rows), "duplicate_observations": len(items)-len(rows), **counts, "manager_review_qualified": 0},
            "companies": firms, "items": rows, "crm_writes": "NO", "outbound_messages": "NO"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--fns", help="Existing SME_FIRST_FNS_SIZE_ONLY evidence file")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        src, dst = Path(args.input), Path(args.output).resolve()
        if src.stat().st_size > MAX_BYTES or dst.suffix != ".json" or not dst.is_relative_to(Path("exports").resolve()):
            raise ValueError("Unsafe input/output")
        report = review(decode_contract(src.read_bytes()), fns_file=args.fns)
        dst.parent.mkdir(parents=True, exist_ok=True)
        with dst.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=True, indent=2)
    except (OSError, ValueError, TypeError, KeyError):
        parser.exit(2, "BIDZAAR_IMPORT=BLOCKED_INVALID_OR_UNSAFE_INPUT\n")
    print("LIVE_SOURCE=BLOCKED_NOT_CONNECTED\nQUALIFIED_REAL_LEADS=0\nBIDZAAR_CRM_WRITES=NO")


if __name__ == "__main__":
    main()
