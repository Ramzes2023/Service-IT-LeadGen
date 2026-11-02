"""Prepare complete, bounded Bitrix24 crm.item.add intents for ALL source-batch leads.

This module is strictly offline: it NEVER calls CRM, imports credentials, or
performs a write. Full field maps remain in memory; the CLI prints redacted
summaries only. Source 'VERIFIED' flags from a JSON input are NOT corporate
verification; they cannot make any intent executable.

Future production code must separately enforce portal-wide client/dedupe/stop
gates, lead-quality acceptance, durable outbox and verified readback before
one authorized crm.item.add. Never retry a POST on ambiguous outcome.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from .bitrix_1062_mapping import (
    ENTITY_TYPE_ID, CATEGORY_ID, INITIAL_STAGE, FIELD_MAP,
    IDEMPOTENCY_FIELD,
)
from .serviceit_ai_report import build_report
from .serviceit_source_batch_pipeline import (
    MAX_JSON_BYTES, assess_source_batch, BatchRejected,
)
from .serviceit_kanban_presentation import (
    BADGE_FIELD, SCORE_FIELD, VERSION_FIELD, fields_from_assessment,
)

INTENT_CONTRACT = "serviceit-all-lead-crm-create-intent-v1"
AI_PREFIX = "UF_CRM_13_SVCIT_"
AI_CODES = frozenset(AI_PREFIX + suffix for suffix in (
    "AI_STATUS", "AI_SUMMARY", "AI_QUOTE", "AI_HYPOTHESIS", "AI_LIMITATIONS",
    "AI_RECOMMENDATION", "AI_NEXT_ACTION", "AI_MANAGER_BRIEF", "AI_FULL_REPORT",
))


def _ai_fields(report, *, ai=None):
    """All nine analysis fields, without inventing a GigaChat invocation.

    ai=None means status NOT_RUN, with safe deterministic evidence summary.
    A separately authenticated provider worker may supply a validated
    GigaChat hypothesis; the workflow never treats its guess as a fact.
    """
    source = report["why_now"]
    analysis = build_report(
        report, source_text=source, evidence_quote=source,
        reference="source-fingerprint-" + report["identity_fingerprint"], ai=ai,
        timestamp=report["evidence_date"]+"T00:00:00+00:00",
    )
    fields=dict(zip((AI_PREFIX+k for k in (
        "AI_STATUS","AI_SUMMARY","AI_QUOTE","AI_HYPOTHESIS","AI_LIMITATIONS",
        "AI_RECOMMENDATION","AI_NEXT_ACTION","AI_MANAGER_BRIEF","AI_FULL_REPORT")), (
        analysis.verification_status, analysis.summary, analysis.evidence_quote,
        analysis.possible_need, analysis.limitations, analysis.service_recommendation,
        analysis.next_action, "\n".join(analysis.manager_checklist), analysis.full_report,
    )))
    _require(set(fields)==AI_CODES and all(type(x) is str and len(x)<=12000 for x in fields.values()),
             "INTENT_AI_FIELDS_INVALID")
    return fields

SOURCE_FIELD = FIELD_MAP["signal_source"]
TITLE_MAX = 120
EXACT_BADGE_ALIASES = (
    "UF_CRM_13_SVCIT_AI_SCORE_BADGE", "ufCrm13SvcitAiScoreBadge"
)
CODES = frozenset((*FIELD_MAP.values(),
    "UF_CRM_13_SVCIT_EVIDENCE_STATUS",
    "UF_CRM_13_SVCIT_REVIEW_DECISION",
    "UF_CRM_13_SVCIT_STOP_REASON",
    "UF_CRM_13_SVCIT_SIGNAL_KIND",
    IDEMPOTENCY_FIELD, BADGE_FIELD, *AI_CODES))
assert len(CODES) == 26


class CreateIntentRejected(ValueError):
    pass


def _require(yes, code):
    if not yes:
        raise CreateIntentRejected(code)


def _safe_label(text: str, max_len: int) -> str:
    _require(type(text) is str and 2 <= len(text) <= max_len, "INTENT_LABEL_INVALID")
    # Never embed HTML/linebreaks, controls, URLs, email or phone in a title.
    _require(re.fullmatch(r"[\w .()+/,&\-–А-Яа-яЁё]+", text, flags=re.UNICODE) is not None,
             "INTENT_LABEL_UNSAFE")
    return text


def _stable_key(company_inn: str, fingerprint: str, source_kind: str) -> str:
    _require(re.fullmatch(r"\d{10}|\d{12}", company_inn) is not None
             and re.fullmatch(r"[a-f0-9]{24}", fingerprint) is not None
             and source_kind in {"synthetic","research"}, "INTENT_IDENTITY_INVALID")
    digest=sha256(("serviceit-all-lead-intent-v1\x00"+source_kind+"\x00"+
                   company_inn+"\x00"+fingerprint).encode()).hexdigest()[:32]
    return ("DEMO-BATCH-" if source_kind=="synthetic" else "RESEARCH-HOLD-")+digest


def build_all_lead_intents(payload: dict, *, ai_by_fingerprint: dict | None = None) -> dict:
    """Return NOT-SENT Bitrix create-intent objects for all accepted companies.

    Even a synthetic VERIFIED input cannot grant production write authority.
    The result may contain source names and company info in memory and must not
    be printed, committed to GitHub or sent to a chat. Use safe_summary().
    """
    validated = assess_source_batch(payload)
    # The caller MUST provide a real, independently verified provider output
    # if a GigaChat hypothesis is to be marked as run. Never call a model here.
    _require(ai_by_fingerprint is None or isinstance(ai_by_fingerprint,dict),
             "INTENT_AI_INPUT_INVALID")
    kind=validated["source_kind"]
    intents=[]
    for report in validated["assessments"]:
        company=_safe_label(report["company_name"],120)
        service=_safe_label(report["suggested_service"],140)
        title=("DEMO " if kind=="synthetic" else "") + "ServiceIT AI — " + company
        _require(len(title)<=TITLE_MAX, "INTENT_TITLE_TOO_LONG")
        key=_stable_key(report["company_inn"],report["identity_fingerprint"],kind)
        signal_id=("DEMO-BATCH-" if kind=="synthetic" else "B2B-") + report["identity_fingerprint"][:20]
        scored=fields_from_assessment(report)
        ai=(ai_by_fingerprint or {}).get(report["identity_fingerprint"])
        enriched=_ai_fields(report,ai=ai)
        _require(scored[SCORE_FIELD]==report["lead_score"] and
                 scored[VERSION_FIELD]==report["score_version"] and
                 scored[BADGE_FIELD]==report["kanban_score_badge"],
                 "INTENT_SCORE_BADGE_CONFLICT")
        fields={
            "title":title, "categoryId":CATEGORY_ID, "stageId":INITIAL_STAGE,
            FIELD_MAP["company_inn"]:report["company_inn"],
            FIELD_MAP["company_name"]:company,
            FIELD_MAP["region"]:report["region"],
            FIELD_MAP["signal_id"]:signal_id,
            FIELD_MAP["signal_date"]:report["evidence_date"],
            SOURCE_FIELD:"synthetic-fixture" if kind=="synthetic" else "research-pending-verification",
            FIELD_MAP["signal_url"]:report["evidence_url"],
            FIELD_MAP["suggested_service"]:service,
            **scored,
            **enriched,
            FIELD_MAP["manager_next_action"]:"Проверить первоисточник, ИНН, клиентов и разрешение на контакт.",
            "UF_CRM_13_SVCIT_EVIDENCE_STATUS":(
                "DEMO — источник вымышленный" if kind=="synthetic"
                else "RESEARCH — потребность и источник не подтверждены в CRM"),
            "UF_CRM_13_SVCIT_REVIEW_DECISION":"Требуется решение менеджера",
            "UF_CRM_13_SVCIT_STOP_REASON":(
                "DEMO / НЕ ЗВОНИТЬ" if kind=="synthetic"
                else "HOLD — нет независимых корпоративных проверок"),
            "UF_CRM_13_SVCIT_SIGNAL_KIND":(
                "demo-synthetic" if kind=="synthetic" else "research-unverified"),
            IDEMPOTENCY_FIELD:key,
        }
        # Explicit allowlist: no contacts, companies, tasks, shared view,
        # stages beyond initial, or other CRM entity types may enter intent.
        _require(set(fields)==CODES|{"title","categoryId","stageId"},
                 "INTENT_FIELDS_SHAPE_CHANGED")
        _require(fields[BADGE_FIELD].startswith(("🟢","🟡","🔴")),
                 "INTENT_BADGE_NOT_COLORED")
        _require(all(type(v) in (str,int) for v in fields.values()),
                 "INTENT_VALUE_TYPE_INVALID")
        intents.append({
            "contract_version":INTENT_CONTRACT,
            "idempotency_key":key,
            "source_fingerprint":report["identity_fingerprint"],
            "score":report["lead_score"],
            "color_badge":report["kanban_score_badge"],
            "method_if_future_authorized":"crm.item.add",
            "params_NOT_SENT":{
                "entityTypeId":ENTITY_TYPE_ID,
                "useOriginalUfNames":"Y",
                "fields":fields,
            },
            "qualification_status":report["status"],
            "gigachat_actual_run":ai is not None and enriched[AI_PREFIX+"AI_STATUS"]=="HYPOTHESIS_UNVERIFIED",
            "ai_status":enriched[AI_PREFIX+"AI_STATUS"],
            "status":"PREVIEW_ONLY_NOT_AUTHORIZED",
            "can_execute":False,
            "crm_write_performed":False,
            "corporate_dedupe_verified":False,
        })
    _require(len(intents)==validated["unique_companies"] and
             len({i["idempotency_key"] for i in intents})==len(intents),
             "INTENT_DUPLICATE_OR_COUNT_DRIFT")
    return {
        "status":"SERVICEIT_ALL_LEAD_CRM_CREATE_INTENTS_PREVIEW",
        "source_kind":kind,
        "input_entries":validated["input_entries"],
        "unique_companies":validated["unique_companies"],
        "repeated_signals_ignored":validated["repeated_signals_ignored"],
        "intents":intents,
        "crm_writes":0,
        "real_customer_contact":False,
        "global_crm_dedupe_verified":False,
        "production_writer_activated":False,
    }


def safe_summary(plan: dict) -> dict:
    """Only non-PII status and score; never raw intent payload or webhook."""
    _require(isinstance(plan,dict) and plan.get("status") ==
             "SERVICEIT_ALL_LEAD_CRM_CREATE_INTENTS_PREVIEW",
             "INTENT_PLAN_INVALID")
    return {
        "status":plan["status"],
        "source_kind":plan["source_kind"],
        "input_entries":plan["input_entries"],
        "unique_companies":plan["unique_companies"],
        "repeated_signals_ignored":plan["repeated_signals_ignored"],
        "items":[{
            "score":i["score"],
            "color_badge":i["color_badge"],
            "qualification_status":i["qualification_status"],
            "ai_status":i["ai_status"],
            "gigachat_actual_run":i["gigachat_actual_run"],
            "field_count":len(i["params_NOT_SENT"]["fields"]),
            "method_if_future_authorized":i["method_if_future_authorized"],
            "status":i["status"], "can_execute":False
        } for i in plan["intents"]],
        "crm_writes":0,
        "global_crm_dedupe_verified":False,
        "production_writer_activated":False,
    }


def main(argv=None) -> int:
    p=argparse.ArgumentParser(description="Offline all-lead CRM creation intents. Never sends.")
    p.add_argument("--input",type=Path,default=Path(__file__).resolve().parents[1]/
                   "fixtures"/"serviceit_source_batch_demo.json")
    args=p.parse_args(argv)
    try:
        _require(args.input.is_file() and args.input.stat().st_size<=MAX_JSON_BYTES,
                 "INTENT_FILE_INVALID")
        data=json.loads(args.input.read_text(encoding="utf-8-sig"))
        print(json.dumps(safe_summary(build_all_lead_intents(data)),
                         ensure_ascii=False,indent=2))
        return 0
    except (ValueError,TypeError,KeyError,OSError,UnicodeError):
        print(json.dumps({"status":"ALL_LEAD_CRM_INTENT_BLOCKED",
                          "crm_writes":0,"secrets_in_output":False}))
        return 2


if __name__=="__main__":
    raise SystemExit(main())
