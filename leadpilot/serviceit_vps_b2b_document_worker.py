"""Independent five-minute Tender Document Intelligence (TDI) sidecar step.

Only newly discovered, publicly readable attachments from signed B2B cards.
Outputs ONE source-cited Bitrix timeline note, never replaces the nine V11
dossier fields, source signal, INN, score, STOP, task or stage. Each write is
PG-fenced BEFORE REST and independently read back. Unknown outcome NO retry.
"""
from __future__ import annotations

from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
import re
import secrets

import httpx

from .ai import config_from_env
from .bitrix_metadata_audit import validate_webhook
from .serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX
from .serviceit_b2b_routerai_deep import (
    investigate_routerai, source_only_investigation,
)
from .serviceit_enrichment_worker import Rest, normalized, validate_identity
from .serviceit_vps_b2b_deep_research import (
    safe_notice, source_findings, published_buyer_inn_evidence,
)
from .serviceit_b2b_tender_documents import inspect_public_documents
from .serviceit_vps_native_fence import PostgresNativeFence
from .serviceit_vps_pg_queue import db, secret_file
from .serviceit_vps_worker import live_permitted, exact_environment

ENABLED = "SERVICEIT_VPS_TENDER_DOC_INTEL_ENABLED"
LOCK_ID = 6754819002
MAX_REPORT = 9900
DDL = """
CREATE TABLE IF NOT EXISTS serviceit_vps_b2b_tender_doc_receipts (
 card_id BIGINT PRIMARY KEY CHECK(card_id BETWEEN 8 AND 2000000000),
 source_hash CHAR(64) NOT NULL,
 status TEXT NOT NULL CHECK(status IN (
   'SCANNED_NO_DOCS','RESERVED','AI_INTENT','WRITE_INTENT',
   'VERIFIED','CONFLICT','UNCERTAIN')),
 next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 scanned_count SMALLINT NOT NULL DEFAULT 0 CHECK(scanned_count BETWEEN 0 AND 6),
 comment_id BIGINT,
 salt BYTEA,
 digest BYTEA,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CHECK ((salt IS NULL AND digest IS NULL) OR
        (octet_length(salt)=16 AND octet_length(digest)=32))
);
"""


class DocumentWorkerBlocked(ValueError):
    pass


def need(ok, why):
    if not ok:
        raise DocumentWorkerBlocked(why)


def selected(connection):
    with connection.cursor() as cur:
        cur.execute("""
          SELECT b.card_id,b.source_id,b.notice_json
          FROM serviceit_vps_b2b_inbox b
          LEFT JOIN serviceit_vps_b2b_tender_doc_receipts d
             ON d.card_id=b.card_id
          WHERE b.state='VERIFIED' AND b.card_id IS NOT NULL
            AND (d.card_id IS NULL OR
                 (d.status='SCANNED_NO_DOCS' AND d.next_attempt_at<=now()))
            AND EXISTS (
                SELECT 1 FROM serviceit_vps_b2b_dossier_v11_receipts v
                WHERE v.card_id=b.card_id AND v.status='VERIFIED')
          ORDER BY COALESCE(d.next_attempt_at,b.received_at),
                   b.received_at,b.source_id LIMIT 1
        """)
        return cur.fetchone()


def save_no_documents(connection, card, source):
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute("""
              INSERT INTO serviceit_vps_b2b_tender_doc_receipts
                  (card_id,source_hash,status,next_attempt_at)
              VALUES (%s,%s,'SCANNED_NO_DOCS',now()+interval '12 hours')
              ON CONFLICT(card_id) DO UPDATE
               SET next_attempt_at=now()+interval '12 hours',
                   updated_at=now()
               WHERE serviceit_vps_b2b_tender_doc_receipts.status='SCANNED_NO_DOCS'
            """, (card, hashlib.sha256(source.encode("ascii")).hexdigest()))


def reserve(connection, card, source, count):
    need(type(count) is int and 1 <= count <= 6, "TDI_DOCUMENT_COUNT_INVALID")
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute("""
              INSERT INTO serviceit_vps_b2b_tender_doc_receipts
                  (card_id,source_hash,status,scanned_count)
              VALUES (%s,%s,'RESERVED',%s)
              ON CONFLICT(card_id) DO UPDATE
                SET status='RESERVED',scanned_count=EXCLUDED.scanned_count,
                    next_attempt_at=now(),updated_at=now()
                WHERE serviceit_vps_b2b_tender_doc_receipts.status='SCANNED_NO_DOCS'
              RETURNING card_id
            """, (card, hashlib.sha256(source.encode("ascii")).hexdigest(), count))
            return cur.fetchone() is not None


def transition(connection, card, old, new, *, text=None):
    salt = secrets.token_bytes(16) if text is not None else None
    digest = hashlib.sha256(salt + text.encode("utf8")).digest() if salt else None
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute("""
              UPDATE serviceit_vps_b2b_tender_doc_receipts
              SET status=%s,salt=COALESCE(%s,salt),
                  digest=COALESCE(%s,digest),updated_at=now()
              WHERE card_id=%s AND status=%s RETURNING card_id
            """, (new, salt, digest, card, old))
            need(cur.fetchone() is not None, "TDI_RECEIPT_CONFLICT")
    return salt, digest


def _timeline_call(client, webhook, method, params):
    base = validate_webhook(webhook)
    need(method in ("crm.timeline.comment.add", "crm.timeline.comment.get"),
         "TDI_REST_METHOD_BLOCKED")
    try:
        response = client.post(base + "/" + method + ".json", json=params,
                               follow_redirects=False, timeout=25)
        need(response.status_code == 200 and 0 < len(response.content) < 250000,
             "TDI_REST_UNVERIFIED")
        data = response.json()
        need(type(data) is dict and "error" not in data and "result" in data,
             "TDI_REST_RESPONSE_UNVERIFIED")
        return data["result"]
    except httpx.HTTPError:
        raise DocumentWorkerBlocked("TDI_REST_UNAVAILABLE") from None


def _visible_url(url):
    """Do not post expiring auth tokens or signatures into CRM timeline."""
    from urllib.parse import urlsplit, parse_qsl
    parsed = urlsplit(url)
    params = parse_qsl(parsed.query, keep_blank_values=True)
    fail_names = ("token", "secret", "session", "cookie", "auth", "key", "sign",
                  "hash", "sid", "csrf", "jwt", "signature")
    if (len(url) > 600 or len(parsed.query) > 150
            or any(any(w in k.casefold() for w in fail_names)
                   for k, _ in params)):
        return "Ссылка на публичный файл содержит непубликуемые параметры"
    return url


def _evidence(docs):
    """Already verified downloaded bytes; never use search result snippets."""
    return {"status": "PUBLIC_PAGES_INSPECTED",
            "pages": [{"url": d["url"], "excerpt": d["text"][:4500]}
                      for d in docs[:3]]}


def _model_research(client, env, docs):
    env = exact_environment(env)
    cfg = config_from_env(env)
    need(cfg.provider == "routerai", "TDI_ROUTERAI_ONLY")
    reports = []
    for start in range(0, len(docs), 3):
        group = _evidence(docs[start:start + 3])
        try:
            reports.append(investigate_routerai(client, cfg, group))
        except Exception:
            # A failed or ungrounded model is NOT licence to invent facts.
            reports.append(source_only_investigation(group))
    return reports


def build_report(notice, docs, reports):
    need(docs and len(docs) <= 6, "TDI_SOURCE_DOCS_REQUIRED")
    tender_id = notice["tender_id"]
    titles = {
        "SCOPE": "Предмет и объём", "TECHNOLOGY": "Техническое задание",
        "BUYER": "Заказчик", "DEADLINE": "Срок подачи",
        "BUDGET": "Цена / НМЦ", "ELIGIBILITY": "Требования к участию",
        "SUBMISSION": "Подача предложения",
        "DOCUMENTS": "Состав заявки", "PAYMENT": "Оплата",
        "GUARANTEE": "Обеспечение", "EVALUATION": "Критерии выбора",
        "LOCATION": "Место исполнения", "DELIVERY": "Срок исполнения",
        "OTHER": "Прочие условия",
    }
    facts = []
    model_yes = 0
    for report in reports:
        model_yes += int(report.get("provider") == "RouterAI")
        for row in report.get("facts", [])[:16]:
            if row.get("category") not in titles:
                continue
            quote, url = row.get("quote"), row.get("url")
            if (not isinstance(quote, str) or not isinstance(url, str)
                    or not any(d["url"] == url and quote in d["text"]
                               for d in docs)):
                continue
            identity = (row["category"], quote, url)
            if identity not in facts:
                facts.append(identity)
    selected = facts[:24]
    inn_candidate = published_buyer_inn_evidence(_evidence(docs))
    lines = [
        "ТЕНДЕРНЫЕ ДОКУМЕНТЫ | ДОПОЛНЕНИЕ К ДОСЬЕ V11",
        "Закупка B2B-Center №" + tender_id,
        "Скачано и прочитано: " + str(len(docs)) + " файла(ов)",
        "Подтверждено цитатами: " + str(len(selected)),
        "Анализ RouterAI: " + ("выполнен" if model_yes else
                              "недоступен, извлечение из первоисточников"),
        "ФНС и право контакта: НЕ проверялись этим модулем.",
        "",
        "01 | ПРОЧИТАННЫЕ ПРИЛОЖЕНИЯ",
    ]
    for i, doc in enumerate(docs, 1):
        lines.append(str(i) + ". " + doc["kind"][1:].upper() + " | " +
                     str(doc["bytes"]) + " байт | sha256 " +
                     doc["sha256"][:12])
        lines.append(_visible_url(doc["url"]))
    lines += ["", "02 | УСЛОВИЯ И ТРЕБОВАНИЯ ПО ДОКУМЕНТАМ"]
    for category, title in titles.items():
        group = [(q, u) for k, q, u in selected if k == category]
        lines.append(title.upper() + ":")
        if not group:
            lines.append("Не найдено подтверждённых цитат в прочитанных документах.")
        for quote, url in group[:3]:
            idx = next(i for i, d in enumerate(docs, 1) if d["url"] == url)
            lines.append("  [" + str(idx) + "] «" + quote[:240] + "»")
    lines += ["", "03 | ЗАКАЗЧИК И ИНН"]
    if inn_candidate:
        lines += [
            "Публично названный заказчик: " + inn_candidate["buyer_name"],
            "ИНН из документа (контрольная сумма): " + inn_candidate["inn"],
            "Статус: КАНДИДАТ, не подтверждён ФНС и независимым сайтом.",
            "Компания в поле ИНН автоматически НЕ подставлялась.",
        ]
    else:
        lines += [
            "Юридические реквизиты однозначно не установлены.",
            "Не подставлять ИНН организатора, электронной площадки или посредника.",
        ]
    lines += [
        "", "04 | ИСТОЧНИК И ОГРАНИЧЕНИЯ",
        "Данные получены из самостоятельно скачанных общедоступных файлов; "
        "цитаты сверены с извлечённым текстом. Требования могут измениться.",
        "PDF-сканы без текстового слоя и документы, требующие личный кабинет, "
        "не считаются прочитанными. Условия заявки и дедлайн следует проверять "
        "по актуальной редакции извещения перед участием.",
        "Отдельное исследование: существующие поля V11, Score, ИНН, "
        "HOLD, задачи и контакты не изменялись.",
    ]
    report = "\n".join(lines)
    if len(report) > MAX_REPORT:
        # Keep source register and priority fields rather than chopping them.
        lines = [line for line in lines if not line.startswith("  [")][:]
        lines.insert(0, "Подробные цитаты сокращены из-за лимита Bitrix24.")
        report = "\n".join(lines)
    need(100 <= len(report) <= MAX_REPORT, "TDI_REPORT_OVERSIZED")
    return "".join("•" if ord(c) > 0xffff else c for c in report)


def run_once(env, client, connection, *, preview=False):
    if env.get(ENABLED, "NO") != "YES":
        return {"status": "TDI_DISABLED", "crm_writes": 0}
    need(live_permitted(env)
         and env.get("SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED") == "YES"
         and env.get("SERVICEIT_VPS_BROWSER_PROXY_ISOLATION_VERIFIED") == "YES"
         and env.get("SERVICEIT_ROUTERAI_PRIVACY_APPROVED") == "YES_1062_21",
         "TDI_DEPLOY_GATES_MISSING")
    connection.execute(DDL)
    with connection.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_ID,))
        locked = cur.fetchone()[0]
    if not locked:
        return {"status": "TDI_ANOTHER_WORKER_RUNNING", "crm_writes": 0}
    try:
        row = selected(connection)
        if row is None:
            return {"status": "TDI_NO_ELIGIBLE_CARDS", "crm_writes": 0}
        card, source, notice = row
        PostgresNativeFence.validate_card(card)
        webhook = secret_file(env, "SERVICEIT_BITRIX_WEBHOOK")
        rest = Rest(webhook, client)
        before = normalized(rest("crm.item.get", {
            "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y",
        })["item"])
        validate_identity(before, card)
        url, title = safe_notice(notice, card, source, before)
        need(bool(title) and url == notice["source_url"],
             "TDI_SOURCE_CARD_MISMATCH")
        need(all(isinstance(before.get(k), str) and before[k].strip()
                 for k in AI_FIELDS), "TDI_NINE_EXISTING_AI_REQUIRED")
        try:
            findings = source_findings(before, notice,
                                       exact_environment(env), client)
            evidence = inspect_public_documents(
                client, findings, notice["tender_id"])
        except Exception:
            evidence = {"documents": [], "discovered": 0, "rejections": {}}
        docs = evidence["documents"]
        if preview:
            return {"status": "TDI_PREVIEW", "crm_writes": 0,
                    "documents_readable": len(docs),
                    "links_found": evidence["discovered"]}
        if not docs:
            save_no_documents(connection, card, source)
            return {"status": "TDI_NO_PUBLIC_DOCUMENTS_RETRY_12H",
                    "crm_writes": 0, "documents_readable": 0,
                    "links_found": evidence["discovered"]}
        if not reserve(connection, card, source, len(docs)):
            return {"status": "TDI_ALREADY_RESERVED_NO_RETRY", "crm_writes": 0}
        phase = "RESERVED"
        try:
            transition(connection, card, phase, "AI_INTENT")
            phase = "AI_INTENT"
            reports = _model_research(client, env, docs)
            text = build_report(notice, docs, reports)
            # Prove the same source/identity is still in CRM before append.
            current = normalized(rest("crm.item.get", {
                "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y",
            })["item"])
            validate_identity(current, card)
            need(current.get(PREFIX + "SIGNAL_ID") == source and
                 current.get(PREFIX + "SIGNAL_URL") == notice["source_url"] and
                 all(current.get(k) == before.get(k) for k in AI_FIELDS),
                 "TDI_STALE_CARD_CONFLICT")
            salt, digest = transition(
                connection, card, "AI_INTENT", "WRITE_INTENT", text=text)
            phase = "WRITE_INTENT"
            ident = _timeline_call(client, webhook, "crm.timeline.comment.add", {
                "fields": {"ENTITY_ID": card,
                           "ENTITY_TYPE": "DYNAMIC_1062",
                           "COMMENT": text}
            })
            comment_id = (int(ident) if str(ident).isdecimal() else 0)
            need(comment_id > 0, "TDI_COMMENT_ID_INVALID")
            actual = _timeline_call(
                client, webhook, "crm.timeline.comment.get",
                {"id": comment_id})
            need(type(actual) is dict
                 and int(actual.get("ENTITY_ID", 0)) == card
                 and actual.get("ENTITY_TYPE", "").casefold() == "dynamic_1062"
                 and actual.get("COMMENT") == text
                 and secrets.compare_digest(
                     hashlib.sha256(salt + actual["COMMENT"].encode("utf8")).digest(),
                     digest),
                 "TDI_COMMENT_READBACK_MISMATCH")
            with connection.transaction():
                with connection.cursor() as cur:
                    cur.execute("""
                      UPDATE serviceit_vps_b2b_tender_doc_receipts
                      SET status='VERIFIED',comment_id=%s,updated_at=now()
                      WHERE card_id=%s AND status='WRITE_INTENT'
                    """, (comment_id, card))
                    need(cur.rowcount == 1, "TDI_FINALIZE_FAILED")
            return {"status": "TDI_COMMENT_VERIFIED", "crm_writes": 1,
                    "documents_readable": len(docs),
                    "routerai_used": any(x.get("provider") == "RouterAI"
                                         for x in reports)}
        except Exception:
            try:
                transition(connection, card, phase,
                           "UNCERTAIN" if phase == "WRITE_INTENT" else "CONFLICT")
            except Exception:
                pass
            return {"status": "TDI_BLOCKED_NO_BLIND_RETRY",
                    "crm_writes": "UNVERIFIED" if phase == "WRITE_INTENT" else 0}
    finally:
        with connection.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))


def main(argv=None):
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--once", action="store_true")
    group.add_argument("--batch-four", action="store_true")
    group.add_argument("--preview", action="store_true")
    a = parser.parse_args(argv)
    try:
        env = dict(os.environ)
        with db(env) as connection:
            with httpx.Client(trust_env=False, follow_redirects=False) as client:
                if a.batch_four:
                    results = [run_once(env, client, connection) for _ in range(4)]
                    out = {"status": "TDI_FOUR_BATCH", "results": results}
                else:
                    out = run_once(env, client, connection, preview=a.preview)
        print(json.dumps(out, ensure_ascii=False, sort_keys=True), flush=True)
        failures = ("TDI_BLOCKED_NO_BLIND_RETRY",)
        return 2 if (out.get("status") in failures or any(
            r.get("status") in failures for r in out.get("results", []))) else 0
    except Exception:
        print(json.dumps({"status": "TDI_GLOBAL_BLOCKED",
                          "crm_writes": 0}), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
