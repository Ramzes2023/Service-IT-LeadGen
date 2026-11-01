"""Single DEMO-only qualification -> Bitrix24 CRM field update.

This is NOT a real-client integration. No CRM company/contact/lead list,
no manager task writes, no outgoing messages, no production worker.
An external AI call is *separate opt-in* and uses a synthetic sentence only.
"""
from __future__ import annotations

from contextlib import closing
from datetime import date, datetime
import json
import os
from pathlib import Path
import sqlite3
import httpx

from .ai import classify_authorized_signal
from .bitrix_synthetic_text_guard import synthetic_action_is_safe
from .bitrix_metadata_audit import validate_webhook
from .bitrix_1062_task_metadata import audit_synthetic_task_link
from .bitrix_1062_mapping import FIELD_MAP, EXTRA_FIELDS
from .core import SCORE_VERSION
from .serviceit_ai_lead_engine import assess_lead, plan_serviceit_ai_signals, QualificationBlocked

SCORE_FIELD = FIELD_MAP["lead_score"]
VERSION_FIELD = FIELD_MAP["score_version"]
ACTION_FIELD = FIELD_MAP["manager_next_action"]
STOP_FIELD = "UF_CRM_13_SVCIT_STOP_REASON"
ALLOWED_WRITE_FIELDS = frozenset((SCORE_FIELD, VERSION_FIELD, ACTION_FIELD, STOP_FIELD))
LEDGER_KEY = "one-1062-3-demo-score-sync-v1"
AI_TEXT = "УЧЕБНЫЙ СИГНАЛ: вымышленная компания, три торговые точки, рассматривается расширение магазина и настройка 1С и ККТ."
STOP_VALUE = "DEMO: реального клиента и согласованного основания контакта нет"
CRMSOURCE = "synthetic-fixture"
SYNTHETIC_ITEM = 3


class DemoBridgeBlocked(ValueError):
    def __init__(self, code: str, *, safe_details: dict | None = None):
        super().__init__(code)
        # Never carry remote values, Bitrix errors, names or webhook tokens.
        self.safe_details = safe_details or {}


def stop(code: str, *, safe_details: dict | None = None):
    raise DemoBridgeBlocked(code, safe_details=safe_details)


def _safe_status_shape(value: object) -> str:
    """Return only an allowlisted value category, never the value itself."""
    if value is None or value == "" or value == "0" or value == 0 or value == []:
        return "UNSELECTED"
    if isinstance(value, str):
        if value.isascii() and value.isdecimal():
            return "POSSIBLE_ENUM_ID"
        v = value.strip().casefold()
        if any(token in v for token in ("синтет", "демо", "demo", "тест")):
            return "HAS_SYNTHETIC_WORD"
        if any(token in v for token in (
                "одобр", "подтвержд", "verified", "approved", "реальн")):
            return "POSSIBLE_AFFIRMATIVE_STATUS"
        return "UNRECOGNIZED_TEXT"
    if isinstance(value, (int, float, bool)):
        return "UNRECOGNIZED_NUMERIC"
    if isinstance(value, list):
        return "NONEMPTY_LIST"
    if isinstance(value, dict):
        return "OBJECT"
    return "UNEXPECTED_TYPE"


def _date(value: object) -> date:
    if not isinstance(value, str):
        stop("DEMO_SIGNAL_DATE_INVALID")
    try:
        return date.fromisoformat(value[:10]) if value[:4].isdigit() and "-" in value[:10] else datetime.strptime(value, "%d.%m.%Y").date()
    except ValueError:
        stop("DEMO_SIGNAL_DATE_INVALID")


def _unverified_demo_review_state(value: object, expected: str) -> str:
    """Only two safe states: exact original synthetic marker or no decision yet.

    A populated unknown value could claim that evidence or a manager review
    is real/approved. Never replace or normalize it silently.
    """
    if value is None or value == "" or value == "0" or value == 0 or value == []:
        return "UNSET_UNVERIFIED"
    if isinstance(value, str):
        def normalize(text: str) -> str:
            return " ".join(
                text.strip().casefold().replace("—", "-").replace("–", "-").split()
            )
        selected = normalize(value)
        if selected == normalize(expected):
            return "EXACT_SYNTHETIC_LABEL"
        # Explicitly harmless *unverified* review labels only; never
        # infer verification or consent from a free-form Bitrix field.
        if selected in {
            "ожидает проверки", "на проверке", "требует проверки",
            "не проверено", "не подтверждено", "не рассмотрено",
            "ожидает решения", "синтетический тест", "демо", "demo",
        }:
            return "UNVERIFIED_LABEL"
    return "NONEMPTY_UNRECOGNIZED"


def _safe_demo_review_checks(item: dict) -> dict[str, str]:
    """Return static classifications, not raw Bitrix status values."""
    return {
        "evidence_status": _unverified_demo_review_state(
            item.get("UF_CRM_13_SVCIT_EVIDENCE_STATUS"),
            EXTRA_FIELDS["UF_CRM_13_SVCIT_EVIDENCE_STATUS"],
        ),
        "review_decision": _unverified_demo_review_state(
            item.get("UF_CRM_13_SVCIT_REVIEW_DECISION"),
            EXTRA_FIELDS["UF_CRM_13_SVCIT_REVIEW_DECISION"],
        ),
    }


def build_analysis(item: dict, *, today: date, hypothesis: dict | None = None) -> dict:
    """Deterministic fictional signal analysis, never real lead qualification."""
    if not isinstance(item, dict):
        stop("DEMO_ITEM_UNEXPECTED")
    if (item.get("UF_CRM_13_SVCIT_SIGNAL_SOURCE") != CRMSOURCE
            or item.get("UF_CRM_13_SVCIT_COMPANY_NAME") != "DEMO COMPANY-001"
            or item.get("UF_CRM_13_SVCIT_COMPANY_INN") != "7700000009"
            or item.get("UF_CRM_13_SVCIT_SIGNAL_ID") != "DEMO-ITREQ-001"
            or item.get("contactIds") not in (None, [])
            or item.get("companyId") not in (None, 0, "0")):
        stop("DEMO_ONLY_IDENTITY_GATE_FAILED")
    signal_date = _date(item.get(FIELD_MAP["signal_date"]))
    if not 0 <= (today - signal_date).days <= 30:
        stop("DEMO_SIGNAL_NOT_FRESH")
    if item.get(STOP_FIELD) not in (None, "", STOP_VALUE):
        stop("EXISTING_STOP_REASON_MUST_NOT_BE_OVERWRITTEN")
    review_states = _safe_demo_review_checks(item)
    if review_states["evidence_status"] == "NONEMPTY_UNRECOGNIZED":
        stop("DEMO_EVIDENCE_STATUS_NONEMPTY_REVIEW_REQUIRED", safe_details={
            "evidence_status_shape": _safe_status_shape(
                item.get("UF_CRM_13_SVCIT_EVIDENCE_STATUS")
            ),
            "review_decision_state": review_states["review_decision"],
        })
    if review_states["review_decision"] == "NONEMPTY_UNRECOGNIZED":
        stop("DEMO_REVIEW_DECISION_NONEMPTY_REVIEW_REQUIRED", safe_details={
            "review_decision_shape": _safe_status_shape(
                item.get("UF_CRM_13_SVCIT_REVIEW_DECISION")
            ),
            "evidence_status_state": review_states["evidence_status"],
        })
    # Same reusable engine as the general ServiceIT AI Signals pipeline.
    # Seven company-wide CRM/stop/legal checks intentionally stay UNKNOWN.
    # This is a synthetic demonstration, never a real qualified prospect.
    try:
        assessment = assess_lead(
            {"inn":"7700000009", "name":"DEMO COMPANY-001",
             "region":"Москва", "industry":"торговля", "phone":"", "email":""},
            [{"signal_type":"new_store",
              "signal_date":signal_date.isoformat(),
              "source_url":item.get(FIELD_MAP["signal_url"]),
              "evidence":AI_TEXT}],
            checks={}, today=today, demo=True,
        )
    except QualificationBlocked:
        stop("DEMO_GENERAL_PIPELINE_BLOCKED")
    score = assessment["lead_score"]
    recommended = assessment["suggested_service"]
    factors = assessment["score_factors"]
    if (assessment["status"] not in ("HOLD_UNVERIFIED", "DEMO_REVIEW_ONLY")
            or assessment["crm_handoff_eligible"] or factors["contact"] != 0):
        stop("DEMO_GENERAL_PIPELINE_BLOCKED")
    ai_status = "NOT_RUN"
    tag = "AI=НЕ ЗАПУЩЕН"
    if hypothesis is not None:
        if (not isinstance(hypothesis, dict) or hypothesis.get("origin") != "AI_hypothesis"
                or hypothesis.get("quality_status") != "complete"
                or hypothesis.get("service") not in ("1С", "Кассы/оборудование")
                or not isinstance(hypothesis.get("evidence_quote"), str)
                or not hypothesis.get("evidence_quote")
                or hypothesis["evidence_quote"] not in AI_TEXT):
            stop("AI_HYPOTHESIS_QUALITY_GATE_BLOCKED")
        ai_status = "HYPOTHESIS_UNVERIFIED"
        tag = "AI=ГИПОТЕЗА"
    if hypothesis is None:
        action = f"DEMO {score}/100; 1С+ККТ; AI не запускался. Проверить сигнал. Клиенту не звонить."
    else:
        need = hypothesis.get("possible_need")
        if (not isinstance(need, str) or not 8 <= len(need.strip()) <= 400
                or any(c in need for c in ("\\n", "\\r", "@"))):
            stop("AI_BRIEF_NOT_SAFE")
        # The brief is ALWAYS explicitly a hypothesis, never an established
        # buying intent. No contacts or unreviewed source URLs can enter CRM.
        concise = need.strip().replace("\\n", " ").replace("\\r", " ")[:58].rstrip()
        action = f"DEMO {score}/100; AI-гипотеза: {concise}. Не звонить клиенту."
    if not synthetic_action_is_safe(action):
        stop("DEMO_ACTION_NOT_SAFE")
    fields = {
        SCORE_FIELD: score,
        VERSION_FIELD: SCORE_VERSION,
        ACTION_FIELD: action,
        STOP_FIELD: STOP_VALUE,
    }
    assert set(fields) == ALLOWED_WRITE_FIELDS
    return {"fields": fields, "score": score, "score_version": SCORE_VERSION,
            "factors": factors, "ai_status": ai_status,
            "existing_customer_check": "NOT_PERFORMED",
            "global_crm_duplicate_check": "NOT_PERFORMED",
            "customer_contact_authorized": False,
            "real_lead_ready": False,
            "synthetic_only": True, "recommended_service_demo": recommended,
            "general_ai_lead_pipeline":plan_serviceit_ai_signals([assessment])["status"],
            "global_gates":assessment["checks"],
            "manager_brief_demo":assessment["manager_script"],
            "existing_review_states":review_states}


def _rest(client: httpx.Client, base: str, method: str, params: dict) -> dict:
    if method not in ("crm.item.get", "crm.item.update"):
        stop("REST_METHOD_NOT_ALLOWED")
    try:
        r = client.post(base + "/" + method + ".json", json=params,
                        timeout=15, follow_redirects=False)
    except (httpx.HTTPError, ValueError, TypeError):
        stop("REST_TRANSPORT_OR_TIMEOUT")
    if r.status_code != 200 or len(r.content) > 256000:
        stop("REST_HTTP_OR_SIZE_UNAVAILABLE")
    try:
        value = r.json()
    except (TypeError, ValueError):
        stop("REST_JSON_INVALID")
    if not isinstance(value, dict) or "error" in value or not isinstance(value.get("result"), dict):
        stop("REST_API_RESULT_INVALID")
    item = value["result"].get("item")
    if not isinstance(item, dict):
        stop("REST_ITEM_MISSING")
    return item



def _verify_writable_demo_fields(client: httpx.Client, base: str) -> None:
    """Never POST to fields not confirmed in *this* smart-process schema."""
    try:
        response = client.post(base + "/crm.item.fields.json",
            json={"entityTypeId":1062, "useOriginalUfNames":"Y"},
            timeout=15, follow_redirects=False)
    except (httpx.HTTPError, ValueError, TypeError):
        stop("DEMO_FIELD_SCHEMA_READ_FAILED")
    if response.status_code != 200 or len(response.content) > 256000:
        stop("DEMO_FIELD_SCHEMA_READ_FAILED")
    try:
        payload = response.json()
    except (ValueError, TypeError):
        stop("DEMO_FIELD_SCHEMA_READ_FAILED")
    if not isinstance(payload, dict) or "error" in payload:
        stop("DEMO_FIELD_SCHEMA_READ_FAILED")
    result = payload.get("result")
    fields = result.get("fields") if isinstance(result, dict) else None
    if not isinstance(fields, dict) or not ALLOWED_WRITE_FIELDS.issubset(fields):
        stop("DEMO_TARGET_FIELDS_MISSING")
    for code in ALLOWED_WRITE_FIELDS:
        details = fields[code]
        if (not isinstance(details, dict)
                or details.get("isReadOnly") in (True, "Y", "true")
                or details.get("isMultiple") in (True, "Y", "true")
                or details.get("isImmutable") in (True, "Y", "true")):
            stop("DEMO_TARGET_FIELD_NOT_SINGLE_WRITABLE")
        typ = details.get("type")
        if code == SCORE_FIELD:
            if typ not in ("integer", "double", "number", "string", "crm_userfield"):
                stop("DEMO_SCORE_FIELD_TYPE_UNEXPECTED")
        elif typ not in ("string", "text", "crm_userfield"):
            stop("DEMO_TEXT_FIELD_TYPE_UNEXPECTED")



def _id(item: dict) -> bool:
    return str(item.get("id")) == "3" and str(item.get("entityTypeId")) == "1062"


def _matches(item: dict, fields: dict) -> bool:
    return all(
        str(item.get(key)) == str(expected)
        for key,expected in fields.items()
    )


def _reserve(con: sqlite3.Connection) -> None:
    con.execute("CREATE TABLE IF NOT EXISTS demo_score_sync (key TEXT PRIMARY KEY, state TEXT NOT NULL)")
    con.execute("BEGIN IMMEDIATE")
    try:
        existing = con.execute("SELECT state FROM demo_score_sync WHERE key=?", (LEDGER_KEY,)).fetchone()
        if existing:
            con.execute("ROLLBACK")
            stop("ONE_DEMO_SCORE_SYNC_ALREADY_ATTEMPTED")
        con.execute("INSERT INTO demo_score_sync(key,state) VALUES(?,?)",
                    (LEDGER_KEY, "POST_OUTCOME_UNCERTAIN"))
        con.execute("COMMIT")
    except sqlite3.Error:
        con.execute("ROLLBACK")
        stop("DEMO_SYNC_LOCAL_LEDGER_UNAVAILABLE")


def sync_one_demo(webhook: str, ledger_path: str | Path, *,
                  apply: bool, use_ai: bool = False, client: httpx.Client | None = None,
                  ai_classifier=None, today: date | None = None) -> dict:
    base = validate_webhook(webhook)
    own = client is None
    if own:
        client = httpx.Client(timeout=15, trust_env=False, follow_redirects=False)
    try:
        try:
            gate = audit_synthetic_task_link(base, client=client)
        except (ValueError, httpx.HTTPError):
            stop("DEMO_METADATA_NOT_READY")
        if gate.get("status") != "LINKED_TASK_METADATA_READY":
            stop("DEMO_METADATA_NOT_READY")
        item = _rest(client, base, "crm.item.get", {
            "entityTypeId": 1062, "id": 3, "useOriginalUfNames": "Y"})
        if not _id(item):
            stop("DEMO_ONLY_CARD_NOT_FOUND")
        # Preflight all synthetic/stop/freshness checks BEFORE any external
        # GigaChat request. A failed CRM safety gate never sends even a
        # fictional sentence to the model or reserves a POST ledger.
        day = today or date.today()
        preview = build_analysis(item, today=day)
        # Field schema is also read-only; reject bad permission/types BEFORE
        # making an external model request or reserving a CRM mutation.
        _verify_writable_demo_fields(client, base)
        ai = None
        if use_ai:
            try:
                ai = (ai_classifier or classify_authorized_signal)(AI_TEXT)
            except (ValueError, RuntimeError, httpx.HTTPError):
                stop("AI_LIVE_REQUEST_BLOCKED")
        report = build_analysis(item, today=day, hypothesis=ai) if ai is not None else preview
        fields = report["fields"]
        already = _matches(item, fields)
        output = {
            "status": "ALREADY_MATCHES" if already else "DEMO_PREVIEW_READY",
            "portal": "serviseit.bitrix24.ru", "only_item": 3,
            "deterministic_score": report["score"], "score_version": SCORE_VERSION,
            "ai_status": report["ai_status"], "stage_policy": gate["stage_policy"],
            "synthetic_stop_gate": True,
            "global_customer_dedup": "NOT_VERIFIED",
            "review_status_classification": report["existing_review_states"],
            "real_leads_approved": False,
            "crm_writes": "NO", "task_created": False,
            "customer_messages": "NO",
        }
        if not apply or already:
            return output
        path = Path(ledger_path)
        if not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=5, isolation_level=None)) as con:
            con.execute("PRAGMA synchronous=FULL")
            try:
                _reserve(con)
            except sqlite3.Error:
                stop("DEMO_SYNC_LOCAL_LEDGER_UNAVAILABLE")
            # Exactly one REST write, to the four specified UF fields on item 3.
            _rest(client, base, "crm.item.update", {
                "entityTypeId": 1062, "id": 3,
                "useOriginalUfNames": "Y", "fields": fields})
            new_item = _rest(client, base, "crm.item.get", {
                "entityTypeId": 1062, "id": 3, "useOriginalUfNames": "Y"})
            if not _id(new_item) or not _matches(new_item, fields):
                stop("DEMO_SYNC_READBACK_UNCERTAIN_DO_NOT_RETRY")
            # Reverify no identity drift after targeted update.
            post_gate = audit_synthetic_task_link(base, client=client)
            if post_gate.get("status") != "LINKED_TASK_METADATA_READY":
                stop("DEMO_SYNC_METADATA_CHANGED_DO_NOT_RETRY")
            con.execute("UPDATE demo_score_sync SET state='VERIFIED' WHERE key=?",(LEDGER_KEY,))
        output["status"] = "DEMO_CRM_QUALIFICATION_VERIFIED"
        output["crm_writes"] = "ONE_DEMO_ITEM_UPDATE"
        return output
    finally:
        if own:
            client.close()


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply-demo", action="store_true")
    ap.add_argument("--ai-demo", action="store_true")
    args = ap.parse_args()
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        print('SERVICEIT_DEMO_QUALIFICATION=BLOCKED_NO_LOCALAPPDATA')
        raise SystemExit(2)
    ledger = Path(local) / "ServiceIT" / "One-Demo-Qualification" / "sync.sqlite3"
    try:
        data = sync_one_demo(
            os.environ.get("SERVICEIT_TASK_READONLY_WEBHOOK", ""),
            ledger, apply=args.apply_demo, use_ai=args.ai_demo)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        print("SERVICEIT_DEMO_QUALIFICATION=" + data["status"])
    except (DemoBridgeBlocked, ValueError, sqlite3.Error, httpx.HTTPError) as ex:
        allowed = {
            "DEMO_SIGNAL_DATE_INVALID", "DEMO_ONLY_IDENTITY_GATE_FAILED",
            "DEMO_SIGNAL_NOT_FRESH", "EXISTING_STOP_REASON_MUST_NOT_BE_OVERWRITTEN",
            "DEMO_EVIDENCE_STATUS_NONEMPTY_REVIEW_REQUIRED",
            "DEMO_REVIEW_DECISION_NONEMPTY_REVIEW_REQUIRED",
            "DEMO_SCORING_UNEXPECTED", "AI_HYPOTHESIS_QUALITY_GATE_BLOCKED",
            "DEMO_ACTION_TOO_LONG", "DEMO_ACTION_NOT_SAFE", "AI_BRIEF_NOT_SAFE",
            "DEMO_GENERAL_PIPELINE_BLOCKED", "REST_METHOD_NOT_ALLOWED",
            "REST_TRANSPORT_OR_TIMEOUT", "REST_HTTP_OR_SIZE_UNAVAILABLE",
            "REST_JSON_INVALID", "REST_API_RESULT_INVALID", "REST_ITEM_MISSING",
            "ONE_DEMO_SCORE_SYNC_ALREADY_ATTEMPTED",
            "DEMO_SYNC_LOCAL_LEDGER_UNAVAILABLE",
            "DEMO_METADATA_NOT_READY", "DEMO_ONLY_CARD_NOT_FOUND",
            "AI_LIVE_REQUEST_BLOCKED", "DEMO_SYNC_READBACK_UNCERTAIN_DO_NOT_RETRY",
            "DEMO_SYNC_METADATA_CHANGED_DO_NOT_RETRY",
            "DEMO_FIELD_SCHEMA_READ_FAILED", "DEMO_TARGET_FIELDS_MISSING",
            "DEMO_TARGET_FIELD_NOT_SINGLE_WRITABLE",
            "DEMO_SCORE_FIELD_TYPE_UNEXPECTED", "DEMO_TEXT_FIELD_TYPE_UNEXPECTED",
        }
        reason = str(ex) if str(ex) in allowed else "BLOCKED_UNSPECIFIED"
        failure = {"status": "BLOCKED_OR_UNCERTAIN", "reason": reason,
                   "no_blind_retry": True, "real_leads_approved": False,
                   "crm_write_confirmed": False}
        if isinstance(ex, DemoBridgeBlocked) and ex.safe_details:
            failure["safe_field_diagnostics"] = ex.safe_details
        print(json.dumps(failure, ensure_ascii=False))
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
