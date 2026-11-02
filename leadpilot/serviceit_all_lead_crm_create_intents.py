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
from .serviceit_source_batch_pipeline import (
    MAX_JSON_BYTES, assess_source_batch, BatchRejected,
)
from .serviceit_kanban_presentation import (
    BADGE_FIELD, SCORE_FIELD, VERSION_FIELD, fields_from_assessment,
)

INTENT_CONTRACT = "serviceit-all-lead-crm-create-intent-v1"
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
    IDEMPOTENCY_FIELD, BADGE_FIELD))
assert len(CODES) == 17


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


def build_all_lead_intents(payload: dict) -> dict:
    """Return NOT-SENT Bitrix create-intent objects for all accepted companies.

    Even a synthetic VERIFIED input cannot grant production write authority.
    The result may contain source names and company info in memory and must not
    be printed, committed to GitHub or sent to a chat. Use safe_summary().
    """
    validated = assess_source_batch(payload)
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
