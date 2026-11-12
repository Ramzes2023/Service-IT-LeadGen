"""One-shot analysis of card 27 using only its previously attested materials."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sys
from urllib.parse import urlsplit

import httpx

from leadpilot import serviceit_b2b_v19_backfill as v19
from leadpilot import serviceit_b2b_v20_versioned as v20
from leadpilot.ai import config_from_env
from leadpilot.serviceit_b2b_dossier_v11 import LABELS, LIMIT_REPORT, REPORT, routing, tidy
from leadpilot.serviceit_b2b_routerai_deep import (
    _must_plain, _routerai_json, investigate_routerai, public_documents, source_only_investigation,
)
from leadpilot.serviceit_vps_b2b_document_worker import LOCK_ID as DOCUMENT_LOCK
from leadpilot.serviceit_vps_b2b_dossier_v11_worker import explicitly_other_tender
from leadpilot.serviceit_vps_pg_queue import db
from leadpilot.serviceit_vps_worker import exact_environment, live_permitted

CARD = 27
MARKER = "ПОВТОРНЫЙ АНАЛИЗ СОХРАНЁННЫХ МАТЕРИАЛОВ"
ROUTERAI = "https://routerai.ru/api/v1/chat/completions"
SAFE_GROUPS = {"1C", "IT_SUPPORT", "IT_STAFFING", "CRM", "KKT_EDO", "SERVERS", "INTEGRATION"}


def emit(key, value):
    print(f"CARD27_SAVED_{key}={value}", flush=True)


def read(rest):
    item = v20.normalized(rest("crm.item.get", {
        "entityTypeId": 1062, "id": CARD, "useOriginalUfNames": "Y",
    })["item"])
    v20.validate_identity(item, CARD)
    return item


def cached_findings(report, tender):
    quotes_heading = "\n07  ДОСЛОВНЫЕ ФРАГМЕНТЫ И ССЫЛКИ\n"
    sources_heading = "\n08  РЕЕСТР ИСТОЧНИКОВ\n"
    _, separator, tail = report.partition(quotes_heading)
    quotes, registry_marker, registry = tail.partition(sources_heading)
    if not separator or not registry_marker:
        return {}
    registry = registry.split("\n\n", 1)[0]
    urls = dict(re.findall(r"(?m)^\[(?:ОРИГИНАЛ = )?(\d+)\] (https://\S{1,600})$", registry))
    grouped = {}
    for ref, quote in re.findall(r"(?m)^\d+\. [^\n]+ \[(\d+)\]\n    «([^\n]{10,260})»$", quotes):
        url = urls.get(ref)
        if not url or explicitly_other_tender(url, tender):
            continue
        parsed = urlsplit(url)
        if parsed.username or parsed.password or parsed.fragment:
            continue
        if parsed.query and not (parsed.hostname in {"b2b-center.ru", "www.b2b-center.ru"}
                                 and parsed.path == "/market/view.html"
                                 and parsed.query == "id=" + tender):
            continue
        grouped.setdefault(url, []).append(quote)
    return {"status": "PUBLIC_PAGES_INSPECTED", "pages": [
        {"url": url, "excerpt": "\n".join(dict.fromkeys(quotes))}
        for url, quotes in grouped.items()
    ]}


def attested_report(conn, source_hash, report):
    for table in ("serviceit_vps_b2b_dossier_v11_receipts",
                  "serviceit_vps_b2b_v19_research_receipts", v20.TABLE):
        row = conn.execute(f"""
            SELECT after_fields,salt,digest FROM {table}
            WHERE card_id=%s AND status='VERIFIED' AND source_hash=%s
              AND after_fields->>%s=%s ORDER BY updated_at DESC LIMIT 1
        """, (CARD, source_hash, REPORT, report)).fetchone()
        if row and row[1] and row[2] and secrets.compare_digest(
                v20.compute_digest(row[0], bytes(row[1])), bytes(row[2])):
            return True
    return False


def generic_questions(client, config, groups):
    prompt = json.dumps({"service_groups": sorted(groups)}, ensure_ascii=False)
    result = _routerai_json(
        client, config,
        "Сформулируй три коротких проверочных вопроса для решения об участии "
        "в ИТ-закупке указанного типа. Требуются вопросы о составе, критериях и рисках. "
        "Не сообщай факты, цены, реквизиты, даты, контакты или названия компаний. "
        'Верни только JSON вида {"questions":["вопрос?"]}.',
        prompt, max_tokens=800, timeout=22,
    )
    questions = []
    if isinstance(result, dict) and isinstance(result.get("questions"), list):
        for value in result["questions"][:3]:
            try:
                text = _must_plain(value, 185)
                if text.endswith("?"):
                    questions.append(text)
            except ValueError:
                continue
    return questions


def prepare_note(notice, result, docs):
    raw = notice.get("source_service_groups")
    groups = set(raw) & SAFE_GROUPS if isinstance(raw, list) else set()
    route, check = routing(sorted(groups), notice.get("subject") or "")
    facts = result.get("facts", [])
    questions = result.get("manager_questions") or []
    lines = [
        MARKER,
        "Режим: один анализ сохранённых данных, без нового поиска.",
        "Кому оценить: " + route,
        "Сначала уточнить: " + check,
    ]
    deadline = tidy(notice.get("deadline_as_stated_in_email"), 100)
    if deadline:
        lines.append("Срок из уведомления, актуальность отдельно не проверена: " + deadline)
    if facts:
        lines.append("Что прямо следует из сохранённых цитат:")
        used = set()
        for fact in facts:
            category = fact["category"]
            if category in used:
                continue
            used.add(category)
            lines.append("• " + LABELS[category] + " — «" + fact["quote"] + "» [" + fact["url"] + "]")
            if len(used) >= 5:
                break
    elif not docs:
        lines.append("Сохранённых публичных цитат нет. Бюджет, заказчик, ИНН и условия участия требуют отдельного источника.")
    if questions:
        lines.append("Вопросы для решения об участии:")
        lines.extend("• " + question for question in questions[:3])
    return "\n\n" + "\n".join(lines), len(facts), len(questions)


def run(conn, client, env):
    for lock in (v20.LOCK_ID, DOCUMENT_LOCK):
        if not conn.execute("SELECT pg_try_advisory_lock(%s)", (lock,)).fetchone()[0]:
            return "LOCK_BUSY"
    row = conn.execute("""
        SELECT source_id,notice_json FROM serviceit_vps_b2b_inbox
        WHERE card_id=%s AND state='VERIFIED'
    """, (CARD,)).fetchall()
    if len(row) != 1:
        return "SIGNED_INBOX_CARD_MISSING"
    source, notice = row[0]
    rest = v20.Rest(env["SERVICEIT_BITRIX_WEBHOOK"], client)
    first = read(rest)
    url, _ = v20.safe_notice(notice, CARD, source, first)
    if url != notice["source_url"]:
        return "SIGNED_SOURCE_MISMATCH"
    before = v20.original_nine(first)
    if MARKER in before[REPORT]:
        return "ALREADY_APPLIED"
    if v19.active_card_write(conn, CARD):
        return "CARD_WRITE_IN_PROGRESS"
    source_hash = hashlib.sha256(source.encode("ascii")).hexdigest()
    for table in (v19.TABLE, v20.TABLE):
        prior = conn.execute(
            f"SELECT status,source_hash FROM {table} WHERE card_id=%s "
            "ORDER BY updated_at DESC LIMIT 1", (CARD,)).fetchone()
        if prior and (prior[0] not in {"VERIFIED", "NO_NEW_EVIDENCE", "INSUFFICIENT_CAPACITY"}
                      or prior[1] != source_hash):
            return "PRIOR_WRITE_REQUIRES_RECONCILIATION"

    proof = attested_report(conn, source_hash, before[REPORT])
    findings = cached_findings(before[REPORT], notice["tender_id"]) if proof else {}
    docs = public_documents(findings)
    groups = set(notice.get("source_service_groups") or []) & SAFE_GROUPS
    emit("CACHED_PUBLIC_SOURCES", len(docs))
    for group in sorted(groups):
        emit("SERVICE_" + group, "YES")
    emit("NOTICE_DEADLINE_PRESENT", "YES" if notice.get("deadline_as_stated_in_email") else "NO")
    attempt = v20.reserve(conn, CARD, source_hash, before)
    result = {}
    try:
        config = config_from_env(env)
        if docs:
            result = investigate_routerai(client, config, findings, timeout=45)
        else:
            result = {"manager_questions": generic_questions(client, config, groups)}
        emit("ROUTERAI_RETURNED", "YES")
    except (ValueError, httpx.HTTPError):
        result = source_only_investigation(findings) if docs else {}
        emit("ROUTERAI_RETURNED", "NO")
    addition, facts, questions = prepare_note(notice, result, docs)
    emit("CITED_FINDINGS", facts)
    for category in sorted({fact["category"] for fact in result.get("facts", [])} & LABELS.keys()):
        emit("CITED_" + category, "YES")
    emit("MODEL_QUESTIONS", questions)
    if len(before[REPORT]) + len(addition) > LIMIT_REPORT:
        v20.transition(conn, CARD, attempt, "RESERVED", "INSUFFICIENT_CAPACITY")
        return "REPORT_CAPACITY"
    fresh = read(rest)
    if v20.fingerprint(first) != v20.fingerprint(fresh):
        v20.transition(conn, CARD, attempt, "RESERVED", "CONFLICT")
        return "CARD_CHANGED"
    desired = {**before, REPORT: before[REPORT] + addition}
    salt, digest = v20.transition(conn, CARD, attempt, "RESERVED", "WRITE_INTENT", desired=desired)
    try:
        rest("crm.item.update", {
            "entityTypeId": 1062, "id": CARD, "useOriginalUfNames": "Y", "fields": desired,
        })
        after = read(rest)
        if (v20.original_nine(after) != desired or not v20.non_ai_unchanged(fresh, after)
                or not secrets.compare_digest(v20.compute_digest(desired, salt), digest)):
            raise ValueError("READBACK_FAILED")
    except Exception:
        v20.transition(conn, CARD, attempt, "WRITE_INTENT", "UNCERTAIN")
        return "WRITE_UNCERTAIN"
    v20.transition(conn, CARD, attempt, "WRITE_INTENT", "VERIFIED")
    return "WRITTEN_AND_READ_BACK"


def main():
    env = dict(os.environ)
    assert live_permitted(env)
    exact = exact_environment(env)
    rest_base = v20.validate_webhook(exact["SERVICEIT_BITRIX_WEBHOOK"])
    allowed = {ROUTERAI, *(rest_base + "/" + method + ".json"
                           for method in ("crm.item.get", "crm.item.update"))}

    def allow_only_crm_and_model(request):
        if request.method != "POST" or str(request.url) not in allowed:
            raise httpx.RequestError("EXTERNAL_RESEARCH_DISABLED", request=request)

    with db(env) as conn, httpx.Client(
            trust_env=False, follow_redirects=False, timeout=20,
            event_hooks={"request": [allow_only_crm_and_model]}) as client:
        status = run(conn, client, exact)
    emit("STATUS", status)
    emit("EXTERNAL_SEARCH_CALLS", 0)
    return 0 if status in {"WRITTEN_AND_READ_BACK", "ALREADY_APPLIED"} else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        emit("FAILURE_CLASS", type(error).__name__.upper())
        sys.exit(2)
