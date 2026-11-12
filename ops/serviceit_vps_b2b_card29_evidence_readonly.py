"""Card #29: production READ-ONLY diagnosis. No external API writes, no paid search.

Print only bounded status codes, booleans and counts. Never print source
texts, customer details, secrets, URLs, contact data or raw CRM fields.
"""
import os
import re

import httpx
from leadpilot.serviceit_vps_pg_queue import db, secret_file

CARD = 29
SOURCE = "B2B-CENTER-4677923"


def emit(key, value):
    s = str(value).upper()
    if re.fullmatch(r"[A-Z0-9_]{1,90}", s):
        print("CARD29_" + key + "=" + s, flush=True)


def flag(value):
    return "YES" if value else "NO"


def main():
    env = dict(os.environ)
    emit("DEEP_ENABLED", flag(env.get("SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED") == "YES"))
    emit("TDI_ENABLED", flag(env.get("SERVICEIT_VPS_TENDER_DOC_INTEL_ENABLED") == "YES"))
    emit("TAVILY_MINI_ENABLED", flag(env.get("SERVICEIT_VPS_B2B_TAVILY_RESEARCH_MINI_ENABLED") == "YES"))
    emit("BRAVE_ENABLED", flag(env.get("SERVICEIT_BRAVE_LLM_CONTEXT_ENABLED") == "YES"))
    emit("PUBLIC_FETCH_ENABLED", flag(env.get("SERVICEIT_VPS_B2B_FREE_PUBLIC_FETCH_ENABLED") == "YES"))
    from leadpilot.serviceit_b2b_hold_ai_fields import PREFIX
    from leadpilot.serviceit_kanban_presentation import SCORE_FIELD
    from leadpilot.serviceit_enrichment_worker import Rest, normalized, validate_identity

    with db(env) as conn, conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        row = conn.execute(
            "SELECT state,card_id,notice_json FROM serviceit_vps_b2b_inbox WHERE source_id=%s",
            (SOURCE,),
        ).fetchone()
        if not row:
            emit("INBOX", "ABSENT")
            return
        state, card_id, notice = row
        emit("INBOX", state)
        emit("CARD_MAPPING", flag(card_id == CARD))
        if state != "VERIFIED" or card_id != CARD:
            return
        tables = (
            ("V11", "serviceit_vps_b2b_dossier_v11_receipts"),
            ("V19", "serviceit_vps_b2b_v19_research_receipts"),
            ("V20", "serviceit_vps_b2b_v20_revision_receipts"),
            ("TDI", "serviceit_vps_b2b_tender_doc_receipts"),
        )
        for label, table in tables:
            exists = conn.execute("SELECT to_regclass(%s)", ("public." + table,)).fetchone()[0]
            if not exists:
                emit(label, "NO_TABLE")
                continue
            data = conn.execute(
                "SELECT status,EXTRACT(EPOCH FROM (now()-updated_at))::BIGINT"
                " FROM " + table + " WHERE card_id=%s",
                (CARD,),
            ).fetchone()
            emit(label, data[0] if data else "NO_RECEIPT")
            if data:
                emit(label + "_AGE_MIN", max(0, int(data[1]) // 60))
        try:
            row = conn.execute(
                "SELECT status FROM serviceit_vps_native_receipts"
                " WHERE namespace='B2B_RESEARCH_V4' AND card_id=%s",
                (CARD,),
            ).fetchone()
            emit("V4", row[0] if row else "NO_RECEIPT")
        except Exception:
            emit("V4", "UNKNOWN")

    with httpx.Client(trust_env=False, follow_redirects=False, timeout=12) as client:
        rest = Rest(secret_file(env, "SERVICEIT_BITRIX_WEBHOOK"), client)
        item = normalized(rest("crm.item.get", {
            "entityTypeId": 1062, "id": CARD, "useOriginalUfNames": "Y",
        })["item"])
        validate_identity(item, CARD)
        emit("CRM_ID", flag(item.get(PREFIX + "SIGNAL_ID") == SOURCE))
        report = item.get(PREFIX + "AI_FULL_REPORT") or ""
        brief = item.get(PREFIX + "AI_MANAGER_BRIEF") or ""
        emit("REPORT_CHARS", len(report))
        emit("BRIEF_CHARS", len(brief))
        emit("CRM_RESEARCH_STATUS", item.get(PREFIX + "AI_STATUS") or "NONE")
        emit("CRM_SCORE", item.get(SCORE_FIELD) if item.get(SCORE_FIELD) is not None else "NONE")
        emit("CRM_INN_PRESENT", flag(bool(item.get(PREFIX + "COMPANY_INN"))))
        emit("REPORT_DEADLINE_20NOV", flag("20.11.2026" in report or "20 ноября 2026" in report))
        emit("REPORT_ORGANIZER_MENTION", flag("АЗОТ-ВЗРЫВ" in report.upper()))
        emit("REPORT_INN_7727218041", flag("7727218041" in report))
        emit("REPORT_PAYMENT_TERMS", flag("отсроч" in report.lower()))
        emit("REPORT_ELECTRONIC_DELIVERY", flag("электронн" in report.lower() and "постав" in report.lower()))
        try:
            from leadpilot.serviceit_public_evidence_research import _robots, _get, VisibleText
            from leadpilot.serviceit_b2b_public_mirrors import matched_excerpt
            hosts = frozenset({"b2b-center.ru", "www.b2b-center.ru"})
            original = notice.get("source_url", "")
            _robots(client, original, hosts)
            emit("PAGE_ROBOTS", "ALLOWED")
            raw = _get(client, original, hosts)
            parser = VisibleText()
            parser.feed(raw)
            flat = " ".join(parser.parts)
            emit("PAGE_TEXT_CHARS", len(flat))
            emit("PAGE_TENDER_MATCH", flag(bool(matched_excerpt(
                flat, "4677923", notice.get("subject", "")))))
            emit("PAGE_DEADLINE", flag("20.11.2026" in flat or "20.11.26" in flat))
            emit("PAGE_ORGANIZER", flag("АЗОТ-ВЗРЫВ" in flat.upper()))
            emit("PAGE_PAYMENT_TERMS", flag("отсроч" in flat.lower()))
        except Exception as exc:
            s = str(exc)
            code = s if re.fullmatch(r"[A-Z][A-Z0-9_]{3,60}", s) else type(exc).__name__.upper()
            emit("PAGE_FAILURE", code)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        code = type(exc).__name__.upper()
        emit("AUDIT_FAILURE", code)
        raise SystemExit(2) from None
