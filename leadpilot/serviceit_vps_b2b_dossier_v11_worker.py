"""VPS-native V11 evidence dossier worker for historical four and every future B2B.

One verifiable CRM update per exact source-backed card. Previous nine fields
archived in private PostgreSQL; uncertain writes are NEVER replayed. The
worker is called by the existing five-minute coordinator after native V4
research, not by GitHub/cloud CRM writers.
"""
from __future__ import annotations

from contextlib import nullcontext

from datetime import datetime, timezone
import hashlib
import json
import os
import re
import secrets
from urllib.parse import parse_qs, urlsplit

import httpx

from .ai import config_from_env
from .bitrix_metadata_audit import validate_webhook
from .serviceit_b2b_eis_mirrors import registry_buyer_note, supplement_eis_mirrors
from .serviceit_b2b_dossier_v11 import REPORT, compose, evidence_rows, original_nine, DossierV11Blocked
from .serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX
from .serviceit_b2b_routerai_deep import (
    MAX_PAGES, investigate_routerai, plan_research_step, public_documents,
    source_only_investigation,
)
from .serviceit_enrichment_worker import (
    Rest, normalized, validate_identity, fingerprint,
)
from .serviceit_vps_b2b_deep_research import (
    published_buyer_inn_evidence, safe_notice, source_findings,
)
from .serviceit_vps_native_fence import PostgresNativeFence
from .serviceit_vps_pg_queue import db, secret_file
from .serviceit_vps_worker import live_permitted

RESEARCH_STEPS = {
    "search_kontur": "zakupki.kontur.ru",
    "search_synapse": "synapsenet.ru",
    "search_tendem": "tendem.ru",
}
MAX_RESEARCH_STEPS = 2
DEADLINE_VALUE = re.compile(r"(?<!\d)(\d{1,2})[./](\d{1,2})[./](\d{4})[\s,]+(\d{1,2}):(\d{2})(?!\d)")


def explicitly_other_tender(url, tender_id):
    """Reject foreign procurement URLs even when related listings mention our ID."""
    try:
        parsed = urlsplit(url)
    except (TypeError, ValueError):
        return True
    host = (parsed.hostname or "").removeprefix("www.")
    if host == "b2b-center.ru":
        match = re.search(r"/tender-(\d{5,12})(?:/|$)", parsed.path)
        ids = ([match[1]] if match else
               parse_qs(parsed.query).get("id", []) if parsed.path == "/market/view.html" else [])
    elif host == "zakupki.kontur.ru":
        match = re.match(r"^/(\d{5,12})_\d+/?$", parsed.path)
        ids = [match[1]] if match else []
    elif host == "synapsenet.ru":
        match = re.match(r"^/zakupki/b2b/(\d{5,12})(?:--|/|$)", parsed.path)
        ids = [match[1]] if match else []
    else:
        ids = []
    return any(value != tender_id for value in ids)


def tender_scoped_findings(findings, tender_id):
    pages = findings.get("pages") or []
    valid = [page for page in pages if isinstance(page, dict)
             and isinstance(page.get("url"), str)
             and not explicitly_other_tender(page["url"], tender_id)]
    if valid == pages:
        return findings
    return {**findings, "pages": valid, "contacts": [], "model_excerpt": "",
            "structured_facts": [], "independent_company_research": None}


def research_with_routerai(client, config, env, notice, findings):
    """Two model-directed steps inside the existing per-card search budget."""
    from .serviceit_b2b_public_mirrors import brave_mirror_search

    tender_id = notice["tender_id"]
    findings = tender_scoped_findings(findings, tender_id)
    steps = dict(RESEARCH_STEPS)
    can_read_documents = env.get("SERVICEIT_VPS_TENDER_DOC_INTEL_ENABLED") == "YES"
    history = []
    originals = {}
    if can_read_documents:
        steps["read_documents"] = None
    for _ in range(MAX_RESEARCH_STEPS):
        try:
            action = plan_research_step(client, config, tender_id, findings, tuple(steps), history)
        except Exception:
            break
        if action == "stop":
            break
        domain = steps.pop(action)
        count_before = len(public_documents(findings))
        if action == "read_documents":
            findings, originals = dossier_document_sources(client, findings, tender_id)
        else:
            try:
                key = secret_file(env, "SERVICEIT_BRAVE_SEARCH_API_KEY", optional=True)
                result = brave_mirror_search(
                    client, tender_id, notice["subject"], key,
                    allow_mirrors=frozenset({domain}),
                    index={"candidates": findings.get("indexed_candidates", [])},
                )
            except Exception:
                result = None
            if result:
                result = tender_scoped_findings(result, tender_id)
                prior = findings.get("pages") or []
                known = {page["url"] for page in prior}
                added = [page for page in result.get("pages", []) if page["url"] not in known]
                if added:
                    findings = {**findings, "status": "PUBLIC_PAGES_INSPECTED",
                                "pages": (prior[:1] + added + prior[1:])[:MAX_PAGES]}
        history.append({"step": action, "new_sources": max(0, len(public_documents(findings)) - count_before)})
    return findings, originals, "read_documents" not in steps, len(history)


def withhold_conflicting_deadlines(report):
    """Keep other verified facts when two sources give distinct deadlines."""
    values = {}
    for fact in report.get("facts", []):
        if fact.get("category") != "DEADLINE":
            continue
        dates = DEADLINE_VALUE.findall(fact.get("quote", ""))
        if len(dates) == 1:
            values.setdefault(fact["url"], set()).add(tuple(map(int, dates[0])))
    stable = {url: next(iter(dates)) for url, dates in values.items() if len(dates) == 1}
    if len(set(stable.values())) < 2:
        return report, []
    return {**report, "facts": [f for f in report["facts"] if f.get("category") != "DEADLINE"]}, list(stable)

def dossier_document_sources(client, findings, tender_id):
    """Add up to two readable public files to the initial dossier input."""
    from urllib.parse import urlsplit
    from .serviceit_b2b_tender_documents import inspect_public_documents
    from .serviceit_b2b_routerai_deep import MAX_PAGES
    from .serviceit_vps_b2b_document_worker import _priority_excerpt

    if findings.get("status") != "PUBLIC_PAGES_INSPECTED":
        return findings, {}
    original = list(findings.get("pages") or [])
    try:
        evidence = inspect_public_documents(
            client, findings, tender_id, max_pages=4, max_downloads=2)
    except Exception:
        return findings, {}
    additions = []
    originals = {}
    known = {p.get("url") for p in original if isinstance(p, dict)}
    for document in evidence["documents"]:
        url = document["url"]
        parsed = urlsplit(url)
        # Never expose signed or expiring links in a dossier.
        if parsed.query or parsed.fragment or len(url) > 600 or url in known:
            continue
        text = document["text"]
        additions.append({"url": url, "excerpt": _priority_excerpt(text)})
        originals[url] = text
        known.add(url)
    if not additions:
        return findings, {}
    return {**findings, "pages": original[:MAX_PAGES - len(additions)] + additions}, originals


VERSION = "B2B_DOSSIER_V11"
MAX_CARDS_PER_BATCH = 4
LOCK_ID = 6754819001
DDL = """
CREATE TABLE IF NOT EXISTS serviceit_vps_b2b_dossier_v11_receipts (
  card_id BIGINT PRIMARY KEY CHECK(card_id BETWEEN 8 AND 2000000000),
  source_hash CHAR(64) NOT NULL,
  status TEXT NOT NULL CHECK(status IN (
    'RESERVED','WRITE_INTENT','VERIFIED','NO_CHANGE','CONFLICT','UNCERTAIN')),
  before_fields JSONB NOT NULL,
  after_fields JSONB,
  salt BYTEA,
  digest BYTEA,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK ((salt IS NULL AND digest IS NULL) OR
         (octet_length(salt)=16 AND octet_length(digest)=32))
);
"""


class V11Blocked(ValueError):
    pass


def need(value, reason):
    if not value:
        raise V11Blocked(reason)


def compute_digest(fields, salt):
    need(type(fields) is dict and set(fields) == AI_FIELDS and
         type(salt) is bytes and len(salt) == 16, "DIGEST_INPUT_INVALID")
    raw = json.dumps(fields, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":")).encode("utf8")
    return hashlib.sha256(salt + raw).digest()


def eligible_one(connection):
    """Selection is source-verified; no unrelated CRM records or new cards."""
    with connection.cursor() as cur:
        cur.execute("""
            SELECT b.card_id,b.source_id,b.notice_json
            FROM serviceit_vps_b2b_inbox b
            WHERE b.state='VERIFIED' AND b.card_id IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM serviceit_vps_b2b_dossier_v11_receipts r
                WHERE r.card_id=b.card_id)
              AND (
                EXISTS (
                  SELECT 1 FROM serviceit_vps_b2b_dossier_v10_receipts prior
                  WHERE prior.card_id=b.card_id AND prior.status='VERIFIED'
                )
                OR EXISTS (
                  SELECT 1 FROM serviceit_vps_native_receipts research
                  WHERE research.card_id=b.card_id
                    AND research.namespace='B2B_RESEARCH_V4'
                    AND research.status IN ('VERIFIED','HOLD')
                )
              )
            ORDER BY b.received_at,b.source_id LIMIT 1
        """)
        return cur.fetchone()


def reserve(connection, card, source, old):
    need(type(source) is str and
         re.fullmatch(r"B2B-CENTER-\d{5,12}", source),
         "SIGNED_SOURCE_ID_REQUIRED")
    PostgresNativeFence.validate_card(card)
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute("""
              INSERT INTO serviceit_vps_b2b_dossier_v11_receipts(
                card_id,source_hash,status,before_fields)
              VALUES (%s,%s,'RESERVED',%s::jsonb)
              ON CONFLICT(card_id) DO NOTHING RETURNING card_id
            """, (card, hashlib.sha256(source.encode("ascii")).hexdigest(),
                  json.dumps(old, ensure_ascii=False)))
            return cur.fetchone() is not None


def update_state(connection, card, prev, next_):
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute("""
               UPDATE serviceit_vps_b2b_dossier_v11_receipts
               SET status=%s,updated_at=now()
               WHERE card_id=%s AND status=%s RETURNING card_id
            """, (next_, card, prev))
            need(cur.fetchone() is not None, "RECEIPT_STATE_CONFLICT")


def before_write(connection, card, fields):
    salt = secrets.token_bytes(16)
    digest = compute_digest(fields, salt)
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute("""
                UPDATE serviceit_vps_b2b_dossier_v11_receipts
                SET status='WRITE_INTENT', after_fields=%s::jsonb,
                    salt=%s,digest=%s,updated_at=now()
                WHERE card_id=%s AND status='RESERVED'
                RETURNING card_id
            """, (json.dumps(fields, ensure_ascii=False), salt, digest, card))
            need(cur.fetchone() is not None, "WRITE_INTENT_NOT_DURABLE")
    return salt, digest


def non_ai_unchanged(before, after):
    ignored = set(AI_FIELDS) | {
        "updatedTime","updatedBy","lastActivityTime","lastActivityBy",
    }
    return all(after.get(k) == v for k, v in before.items()
               if k not in ignored)


def reserve_research_needed(findings, report, coverage):
    """Evaluate independently sourced business fields after the primary pass."""
    if not coverage["deadline_public"]:
        return True
    if not (published_buyer_inn_evidence(findings) or findings.get("eis_registry_buyer")):
        return True
    _, facts = evidence_rows(findings, report)
    categories = {fact["category"] for fact in facts}
    return "BUDGET" not in categories or not categories.intersection(
        {"ELIGIBILITY", "DOCUMENTS", "SUBMISSION"}
    )


def worker_once(env, client, connection, *, preview=False, search_scope=None):
    """No payment/outreach/score/stage/task/contacts. Old fields copied into PG."""
    need(live_permitted(env)
         and env.get("SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED") == "YES"
         and env.get("SERVICEIT_VPS_BROWSER_RESEARCH_ENABLED") == "YES"
         and env.get("SERVICEIT_VPS_BROWSER_PROXY_ISOLATION_VERIFIED") == "YES"
         and env.get("SERVICEIT_ROUTERAI_FALLBACK_TO_GIGACHAT", "NO") == "NO",
         "V11_LIVE_PRIVACY_AND_RESEARCH_GATES_REQUIRED")
    connection.execute(DDL)
    # Real PG connection advisory lock serializes explicit 4-card batch and
    # ongoing five-minute coordinator; no overlap/duplicate paid call.
    with connection.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_ID,))
        locked = cur.fetchone()[0]
    if not locked:
        return {"status": "V11_ANOTHER_WORKER_ACTIVE", "crm_writes": 0}
    try:
        row = eligible_one(connection)
        if row is None:
            return {"status": "V11_NO_ELIGIBLE_CARDS", "crm_writes": 0}
        card, source, notice = row
        PostgresNativeFence.validate_card(card)
        webhook = secret_file(env, "SERVICEIT_BITRIX_WEBHOOK")
        validate_webhook(webhook)
        rest = Rest(webhook, client)
        first = normalized(rest("crm.item.get", {
            "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y",
        })["item"])
        validate_identity(first, card)
        old = original_nine(first)
        source_url, title = safe_notice(notice, card, source, first)
        need(bool(title) and source_url == notice["source_url"],
             "ORIGINAL_TENDER_IDENTITY_UNVERIFIED")
        if preview:
            return {"status": "V11_ELIGIBLE_READONLY", "crm_writes": 0,
                    "prior_ai_field_count": len(old)}

        # Preserve previous report BEFORE any external model call or CRM write.
        # If anything after this point is ambiguous, the receipt blocks replay.
        if not reserve(connection, card, source, old):
            return {"status": "V11_ALREADY_RESERVED_NO_RETRY", "crm_writes": 0}
        phase = "RESERVED"
        invoked = False
        try:
            from .serviceit_vps_worker import exact_environment
            exact = exact_environment(env)
            document_texts = {}
            documents_tried = False
            research_steps = 0
            try:
                with search_scope(card, source) if search_scope else nullcontext(True) as can_search:
                    options = {"enable_v18": True}
                    if can_search and (
                            exact.get("SERVICEIT_VPS_B2B_TAVILY_SEARCH_ENABLED") == "YES"
                            or exact.get("SERVICEIT_VPS_B2B_TAVILY_RESEARCH_MINI_ENABLED") == "YES"):
                        options["connection"] = connection
                    findings = source_findings(first, notice, exact, client, **options)
                    findings = tender_scoped_findings(findings, notice["tender_id"])
                    if search_scope and can_search:
                        findings = supplement_eis_mirrors(findings, notice, client)
                        try:
                            findings, document_texts, documents_tried, research_steps = research_with_routerai(
                                client, config_from_env(exact), exact, notice, findings,
                            )
                        except Exception:
                            pass  # Retain the already fetched evidence.
            except Exception:
                findings = {"status": "PUBLIC_SOURCE_UNVERIFIED", "pages": [],
                            "checked_at": datetime.now(timezone.utc).isoformat()}
            if not documents_tried and env.get("SERVICEIT_VPS_TENDER_DOC_INTEL_ENABLED") == "YES":
                findings, document_texts = dossier_document_sources(
                    client, findings, notice["tender_id"])
            report = source_only_investigation(findings)
            if public_documents(findings):
                try:
                    cfg = config_from_env(exact)
                    need(cfg.provider == "routerai", "ROUTERAI_ONLY")
                    invoked = True
                    report = investigate_routerai(client, cfg, findings)
                except Exception:
                    # An invalid model response is not authorization to guess.
                    # Use only independently read literal source phrases.
                    report = source_only_investigation(findings)
            if document_texts:
                report = {**report, "facts": [
                    fact for fact in report.get("facts", [])
                    if fact.get("url") not in document_texts
                    or fact["quote"] in document_texts[fact["url"]]
                ]}
            report, deadline_conflicts = withhold_conflicting_deadlines(report)
            fields, coverage = compose(
                notice, first, {**findings, "indexed_candidates": []}, report)
            registry_note = registry_buyer_note(findings)
            if registry_note and len(fields[REPORT]) + len(registry_note) <= 12000:
                fields[REPORT] += registry_note
            if deadline_conflicts:
                note = "\n\nСВЕРКА ИСТОЧНИКОВ\nСроки подачи различаются. Уточнить актуальный срок на площадке.\n"
                note += "\n".join(deadline_conflicts[:3])
                if len(fields[REPORT]) + len(note) <= 12000:
                    fields[REPORT] += note
            fresh = normalized(rest("crm.item.get", {
                "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y",
            })["item"])
            validate_identity(fresh, card)
            need(fingerprint(first) == fingerprint(fresh) and
                 all(fresh.get(k) == old[k] for k in AI_FIELDS),
                 "CRM_STALE_PREWRITE_CONFLICT")
            if fields == old:
                update_state(connection, card, "RESERVED", "NO_CHANGE")
                return {"status": "V11_NO_CHANGE", "crm_writes": 0,
                        "routerai_invoked": invoked}
            salt, proof = before_write(connection, card, fields)
            phase = "WRITE_INTENT"
            rest("crm.item.update", {
                "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y",
                "fields": fields,
            })
            result = normalized(rest("crm.item.get", {
                "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y",
            })["item"])
            validate_identity(result, card)
            observed = {key: result.get(key) for key in AI_FIELDS}
            need(observed == fields and
                 secrets.compare_digest(compute_digest(observed, salt), proof)
                 and non_ai_unchanged(fresh, result),
                 "EXACT_NINE_READBACK_NOT_VERIFIED")
            update_state(connection, card, "WRITE_INTENT", "VERIFIED")
            reserve_status = "SKIPPED"
            if env.get("SERVICEIT_VPS_B2B_TAVILY_RESEARCH_MINI_ENABLED") == "YES":
                try:
                    from .serviceit_b2b_tavily_research_mini_v47 import nominate_gap
                    queued = nominate_gap(
                        connection, env, card, source,
                        missing=reserve_research_needed(findings, report, coverage),
                    )
                    reserve_status = "QUEUED" if queued else "NOT_NEEDED_OR_OUT_OF_SCOPE"
                except Exception:
                    reserve_status = "QUEUE_UNAVAILABLE"
            return {"status": "V11_NINE_FIELDS_VERIFIED", "crm_writes": 1,
                    "routerai_invoked": invoked,
                    "routerai_research_steps": research_steps,
                    "tavily_reserve": reserve_status,
                    "source_pages": min(coverage["pages"], 9),
                    "quoted_facts": min(coverage["citations"], 16),
                    "deadline_public": coverage["deadline_public"],
                    "public_buyer": coverage["buyer_public"],
                    "public_contacts": coverage["contacts"]}
        except Exception:
            try:
                update_state(connection, card, phase,
                             "UNCERTAIN" if phase == "WRITE_INTENT" else "CONFLICT")
            except Exception:
                pass
            return {"status": "V11_BLOCKED_NO_BLIND_REPLAY",
                    "crm_writes": "UNVERIFIED" if phase == "WRITE_INTENT" else 0,
                    "routerai_invoked": invoked}
    finally:
        with connection.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))


def verify_all(env, client, connection):
    """Independent existing CRM GET only, no model, source or writes."""
    base = secret_file(env, "SERVICEIT_BITRIX_WEBHOOK")
    validate_webhook(base)
    rest = Rest(base, client)
    with connection.cursor() as cur:
        cur.execute("""
            SELECT card_id,after_fields,salt,digest
            FROM serviceit_vps_b2b_dossier_v11_receipts
            WHERE status='VERIFIED' ORDER BY card_id
        """)
        rows = cur.fetchall()
    need(len(rows) > 0, "NO_VERIFIED_V11_RECEIPTS")
    verified = 0
    for card, expected, salt, proof in rows:
        item = normalized(rest("crm.item.get", {
            "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y",
        })["item"])
        validate_identity(item, card)
        values = {key: item.get(key) for key in AI_FIELDS}
        need(values == expected and
             secrets.compare_digest(compute_digest(values, bytes(salt)), bytes(proof)),
             "V11_READBACK_MISMATCH")
        verified += 1
    return {"status": "V11_VERIFIED_READBACK", "cards_verified": verified,
            "crm_writes": 0, "routerai_calls": 0}


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser()
    mx = p.add_mutually_exclusive_group(required=True)
    mx.add_argument("--once", action="store_true")
    mx.add_argument("--batch-four", action="store_true")
    mx.add_argument("--preview", action="store_true")
    mx.add_argument("--verify", action="store_true")
    a = p.parse_args(argv)
    env = dict(os.environ)
    try:
        with db(env) as c:
            with httpx.Client(trust_env=False, follow_redirects=False, timeout=24) as client:
                if a.verify:
                    outcome = verify_all(env, client, c)
                elif a.batch_four:
                    outcomes = [worker_once(env, client, c) for _ in range(MAX_CARDS_PER_BATCH)]
                    outcome = {"status": "V11_BATCH",
                               "outcomes": outcomes,
                               "crm_writes": sum(x.get("crm_writes")==1 for x in outcomes)}
                else:
                    outcome = worker_once(env, client, c, preview=a.preview)
        print(json.dumps(outcome, sort_keys=True, ensure_ascii=False))
        return 0 if outcome.get("status") not in (
            "V11_BLOCKED_NO_BLIND_REPLAY",) and all(
            row.get("status") not in ("V11_BLOCKED_NO_BLIND_REPLAY",)
            for row in outcome.get("outcomes", [])) else 2
    except Exception:
        print(json.dumps({"status": "V11_GLOBAL_BLOCKED",
                          "no_blind_retry": True}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
