"""Independent five-minute Tender Document Intelligence (TDI) sidecar step.

Only newly discovered, publicly readable attachments from signed B2B cards.
Outputs ONE source-cited Bitrix timeline note, never replaces the nine V11
dossier fields, source signal, INN, score, STOP, task or stage. Each write is
PG-fenced BEFORE REST and independently read back. Unknown outcome NO retry.
"""
from __future__ import annotations

from datetime import datetime, timezone
import argparse
import base64
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
MAX_ATTACHMENTS = 4
MAX_SINGLE_ATTACHMENT_BYTES = 3_000_000
MAX_TOTAL_ATTACHMENT_BYTES = 6_000_000
ATTACHABLE_KINDS = frozenset((".pdf", ".docx", ".xlsx", ".txt"))
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


def prepare_public_attachments(docs, tender_id):
    """Encode verified public download bytes for ONE fenced Bitrix comment.

    Never upload ZIP bundles or names/paths supplied by third-party HTML.
    Large files remain source references rather than bypassing upload caps.
    Raw content stays in memory and is never persisted or logged.
    """
    need(isinstance(tender_id, str) and
         re.fullmatch(r"[0-9]{5,12}", tender_id),
         "TDI_ATTACHMENT_TENDER_ID_INVALID")
    files, manifest, total = [], [], 0
    for index, doc in enumerate(docs, 1):
        kind = doc.get("kind")
        if kind not in ATTACHABLE_KINDS:
            continue
        payload, digest, declared = (doc.get("payload"),
                                     doc.get("sha256"), doc.get("bytes"))
        need(isinstance(payload, bytes) and len(payload) >= 20
             and type(declared) is int and len(payload) == declared
             and type(digest) is str and len(digest) == 64
             and hashlib.sha256(payload).hexdigest() == digest,
             "TDI_ATTACHMENT_SOURCE_BYTES_MISMATCH")
        if (len(files) >= MAX_ATTACHMENTS or
                declared > MAX_SINGLE_ATTACHMENT_BYTES or
                total + declared > MAX_TOTAL_ATTACHMENT_BYTES):
            continue
        filename = "b2b-" + tender_id + "-document-" + str(index) + kind
        files.append([filename, base64.b64encode(payload).decode("ascii")])
        manifest.append({"name": filename, "size": declared})
        total += declared
    return files, manifest


def verify_timeline_attachments(actual, manifest):
    """Require exact downloaded file metadata after Bitrix readback.

    A partial or uncertain upload must never be blindly retried.
    """
    if not manifest:
        return 0
    data = actual.get("FILES") if type(actual) is dict else None
    need(type(data) is dict and len(data) == len(manifest),
         "TDI_BITRIX_ATTACHMENT_READBACK_MISSING")
    found = {}
    for item in data.values():
        need(type(item) is dict, "TDI_BITRIX_ATTACHMENT_SHAPE_INVALID")
        name, size, identity = (item.get("name"), item.get("size"), item.get("id"))
        need(type(name) is str and
             (type(size) is int or
              (type(size) is str and size.isdecimal())) and
             str(identity).isdecimal() and int(identity) > 0,
             "TDI_BITRIX_ATTACHMENT_METADATA_INVALID")
        need(name not in found, "TDI_BITRIX_ATTACHMENT_DUPLICATE")
        found[name] = int(size)
    need(found == {row["name"]: row["size"] for row in manifest},
         "TDI_BITRIX_ATTACHMENT_CONTENT_MISMATCH")
    return len(manifest)


def _timeline_call(client, webhook, method, params):
    base = validate_webhook(webhook)
    need(method in ("crm.timeline.comment.add", "crm.timeline.comment.get"),
         "TDI_REST_METHOD_BLOCKED")
    try:
        response = client.post(base + "/" + method + ".json", json=params,
                               follow_redirects=False,
                               timeout=60 if method.endswith(".add") else 25)
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


def _priority_excerpt(text):
    """Cover late deadlines, geography and tender conditions within model limits.

    The HTTP extractor retains up to 24k source characters; RouterAI has
    a much smaller per-document window. Sample exact source substrings
    across distinct procurement categories instead of silently reading
    only page one. All resulting quotes must still match the full text.
    """
    if len(text) <= 4500:
        return text
    patterns = (
        r"(?i)(?:предмет закупки|техническое задание|объем работ|объём работ)",
        r"(?i)(?:срок подачи|окончани[ея] подачи|дедлайн|подач[аи] заявок до)",
        r"(?i)(?:место исполнени[яю]|адрес оказания|географи[яи]|регион)",
        r"(?i)(?:критери[ий] оценки|критери[ий] отбора|порядок оценки)",
        r"(?i)(?:требования к участник|квалификационн|опыт выполнени)",
        r"(?i)(?:порядок подачи|состав заявк|перечень документов)",
        r"(?i)(?:условия оплаты|порядок оплаты|аванс|отсрочк)",
        r"(?i)(?:обеспечение заявки|банковская гарантия)",
        r"(?i)(?:заказчик|наименование организации|реквизиты)",
        r"(?i)(?:\bинн\b|\bкпп\b|огрн)",
        r"(?i)(?:срок исполнени|срок поставки|дата поставки)",
        r"(?i)(?:начальная цена|максимальная цена|стоимость договора)",
    )
    spans = [(0, 650)]
    size = 650
    for pattern in patterns:
        match = re.search(pattern, text)
        if match is None:
            continue
        left, right = max(0, match.start() - 95), min(len(text), match.end() + 255)
        if any(left < end and right > begin for begin, end in spans):
            continue
        if size + (right - left) + 5 > 4300:
            continue
        spans.append((left, right))
        size += right - left + 5
    tail = (max(0, len(text) - 320), len(text))
    if not any(tail[0] < end and tail[1] > begin for begin, end in spans):
        spans.append(tail)
    return " … ".join(text[left:right] for left, right in spans)[:4500]


def _evidence(docs):
    """Independently downloaded bytes, with late condition coverage."""
    return {"status": "PUBLIC_PAGES_INSPECTED",
            "pages": [{"url": d["url"],
                       "excerpt": _priority_excerpt(d["text"])}
                      for d in docs[:3]]}

def _model_research(client, env, docs):
    env = exact_environment(env)
    cfg = config_from_env(env)
    need(cfg.provider == "routerai", "TDI_ROUTERAI_ONLY")
    # Cover every retained character, not only keyword-selected excerpts.
    # Overlap preserves requirements spanning a window boundary. Existing
    # six-file / 24k-character caps bound this to at most 12 model calls.
    chunks = []
    for doc in docs[:6]:
        text = doc["text"][:24000]
        for start in range(0, len(text), 4000):
            chunks.append({"url": doc["url"], "excerpt": text[start:start + 4500]})
    reports = []
    for start in range(0, len(chunks), 3):
        group = {"status": "PUBLIC_PAGES_INSPECTED",
                 "pages": chunks[start:start + 3]}
        try:
            reports.append(investigate_routerai(client, cfg, group))
        except Exception:
            reports.append(source_only_investigation(group))
    return reports


def build_report(notice, docs, reports, *, attachments_planned=0):
    """Facts-first manager dossier: no repeated empty template categories."""
    need(docs and len(docs) <= 6, "TDI_SOURCE_DOCS_REQUIRED")
    tender_id = notice["tender_id"]
    titles = {
        "SCOPE": "Предмет и объём работ",
        "TECHNOLOGY": "Техническое задание / компетенции",
        "BUYER": "Заказчик по документу",
        "DEADLINE": "Срок подачи заявки",
        "LOCATION": "Регион / место исполнения",
        "DELIVERY": "Срок выполнения",
        "BUDGET": "Цена / бюджет",
        "ELIGIBILITY": "Требования к участнику",
        "SUBMISSION": "Порядок подачи предложения",
        "DOCUMENTS": "Перечень документов заявки",
        "EVALUATION": "Критерии оценки и выбора",
        "PAYMENT": "Оплата",
        "GUARANTEE": "Обеспечение / гарантии",
        "OTHER": "Дополнительные условия",
    }
    grouped = {key: [] for key in titles}
    model_used = False
    for row in reports:
        model_used = model_used or row.get("provider") == "RouterAI"
        for fact in row.get("facts", [])[:16]:
            if not isinstance(fact, dict) or fact.get("category") not in grouped:
                continue
            quote, url = fact.get("quote"), fact.get("url")
            if (not isinstance(quote, str) or not isinstance(url, str)
                    or not any(doc["url"] == url and quote in doc["text"]
                               for doc in docs)):
                continue
            category = fact["category"]
            if (quote, url) not in grouped[category] and len(grouped[category]) < 2:
                grouped[category].append((quote, url))
    covered = sum(bool(grouped[category]) for category in titles)
    evidence_count = sum(len(x) for x in grouped.values())
    inn_candidate = published_buyer_inn_evidence(_evidence(docs))
    def render(quote_limit, citations_per_group):
        lines = [
            "СЕРВИС ИТ | ДОКУМЕНТАЛЬНОЕ ДОСЬЕ B2B-CENTER",
            "Дополнение к V11 · Закупка №" + tender_id,
            "Прочитано: " + str(len(docs)) + " файла(ов) · " +
            str(covered) + " разделов с доказательствами · " +
            str(evidence_count) + " подтверждённых цитат",
            "RouterAI: " + ("цитаты проверены" if model_used
                            else "недоступен; использованы исходные тексты"),
            "Файлы для прикрепления к карточке: " +
            str(attachments_planned) + " (только общедоступные форматы)",
            "",
            "01 | ФАКТЫ, УСЛОВИЯ И ТРЕБОВАНИЯ ПО ДОКУМЕНТАМ",
        ]
        for category, title in titles.items():
            quotes = grouped[category]
            if not quotes:
                continue
            lines.append(title.upper())
            for quote, url in quotes[:citations_per_group]:
                index = next(i for i, doc in enumerate(docs, 1)
                             if doc["url"] == url)
                lines.append("  [" + str(index) + "] «" +
                             quote[:quote_limit] + "»")
        missing = [title for category, title in titles.items()
                   if not grouped[category]]
        lines += [
            "",
            "02 | ЧТО ЕЩЁ НЕ ПОДТВЕРЖДЕНО",
            ("Нет подтверждённых цитат: " + "; ".join(missing)
             if missing else
             "Все проверяемые разделы имеют цитаты; актуальность ещё требуется уточнить."),
            "Отсутствие цитаты не означает отсутствие требования в закупке.",
            "",
            "03 | ЗАКАЗЧИК И ИНН",
        ]
        if inn_candidate:
            lines += [
                "Юрлицо, указанное в документе: " + inn_candidate["buyer_name"],
                "ИНН из документа (контрольная сумма): " + inn_candidate["inn"],
                "Статус: кандидат; ФНС не проверена.",
                "В основное поле ИНН Битрикс ничего не записывалось.",
            ]
        else:
            lines += [
                "Юрлицо и достоверный ИНН документами не установлены.",
                "ИНН площадки/посредника заказчику не присваивался.",
            ]
        lines += ["", "04 | ПРОЧИТАННЫЕ ДОКУМЕНТЫ И ПЕРВОИСТОЧНИКИ"]
        for i, doc in enumerate(docs, 1):
            lines.append("[" + str(i) + "] " + doc["kind"][1:].upper() +
                         " · " + str(doc["bytes"]) + " байт · SHA256 " +
                         doc["sha256"][:12])
            lines.append(_visible_url(doc["url"]))
        lines += [
            "",
            "05 | ГРАНИЦЫ ПРОВЕРКИ",
            "Проверены дословные фрагменты скачанных публичных файлов. "
            "Сканированные PDF без текста и закрытые вложения не прочитаны. "
            "Перед участием перепроверить действующую редакцию, сроки и условия.",
            "Досье V11, Score, ФНС, ИНН, HOLD, задачи и контакты не изменялись. "
            "Никакой автоматической отправки предложений.",
        ]
        return "\n".join(lines)
    result = render(210, 2)
    if len(result) > MAX_REPORT:
        result = render(130, 1)
    if len(result) > MAX_REPORT:
        result = render(75, 1)
    need(100 <= len(result) <= MAX_REPORT, "TDI_REPORT_OVERSIZED")
    return "".join("•" if ord(char) > 0xffff else char for char in result)

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
            files, manifest = prepare_public_attachments(
                docs, notice["tender_id"])
            reports = _model_research(client, env, docs)
            text = build_report(notice, docs, reports,
                                attachments_planned=len(manifest))
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
            fields = {"ENTITY_ID": card,
                      "ENTITY_TYPE": "DYNAMIC_1062",
                      "COMMENT": text}
            if files:
                fields["FILES"] = files
            ident = _timeline_call(client, webhook, "crm.timeline.comment.add",
                                   {"fields": fields})
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
            attachment_count = verify_timeline_attachments(actual, manifest)
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
                    "attachments_verified": attachment_count,
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
