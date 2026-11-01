"""Guarded DEMO cockpit. Default CLI is entirely offline; transports are injectable.

No shared layout, other-user layout, task writes or production adapter exists.
All mutations have a persistent one-shot fence, including schema/layout writes.
"""
from copy import deepcopy
from contextlib import contextmanager
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import uuid

import httpx

from .bitrix_1062_mapping import ALL_FIELD_CODES, FIELD_MAP, CONTRACT_VERSION
from .bitrix_1062_demo_qualification import AI_TEXT, build_analysis
from .bitrix_metadata_audit import validate_webhook
from .serviceit_ai_report import build_report

PREFIX = "UF_CRM_13_SVCIT_"
SPECS = {
    "AI_STATUS": ("AI — статус проверки", 2),
    "AI_SUMMARY": ("AI — краткий вывод", 4),
    "AI_QUOTE": ("Источник — дословная цитата", 4),
    "AI_HYPOTHESIS": ("Возможная потребность — гипотеза", 5),
    "AI_LIMITATIONS": ("Ограничения и допуски", 6),
    "AI_RECOMMENDATION": ("Рекомендуемая услуга — проверить", 3),
    "AI_NEXT_ACTION": ("Рекомендуемое действие", 3),
    "AI_MANAGER_BRIEF": ("Памятка менеджеру", 8),
    "AI_FULL_REPORT": ("GigaChat — полный AI-отчёт", 18),
}
WRITE_FIELDS = frozenset(PREFIX + k for k in SPECS)
LAYOUT_SCOPE = {"entityTypeId": 1062, "scope": "P", "extras": {"categoryId": 21}}
ITEM_SCOPE = {"entityTypeId": 1062, "id": 3, "useOriginalUfNames": "Y"}
READ_METHODS = frozenset({"crm.type.getByEntityTypeId", "userfieldconfig.list", "userfieldconfig.get",
                          "userfieldconfig.getTypes", "crm.item.fields", "crm.item.get", "crm.item.details.configuration.get"})
WRITE_METHODS = frozenset({"userfieldconfig.add", "crm.item.update", "crm.item.details.configuration.set"})


class CockpitBlocked(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise CockpitBlocked(code)


class Rest:
    """Bounded no-retry transport; never includes remote errors/credentials in errors."""
    def __init__(self, webhook, client):
        self.base = validate_webhook(webhook)
        self.client = client
        self.calls = 0

    def __call__(self, method, params):
        require(method in READ_METHODS | WRITE_METHODS, "METHOD_BLOCKED")
        self.calls += 1
        require(self.calls <= 200, "REST_BUDGET_EXCEEDED")
        try:
            response = self.client.post(self.base + "/" + method + ".json", json=params,
                                        timeout=15, follow_redirects=False)
            require(response.status_code == 200 and len(response.content) <= 512000, "REST_HTTP_BLOCKED")
            value = response.json()
            require(isinstance(value, dict) and "error" not in value and "result" in value, "REST_RESULT_BLOCKED")
            require("next" not in value, "PAGINATION_REQUIRES_REVIEW")
            return value["result"]
        except (httpx.HTTPError, TypeError, KeyError, json.JSONDecodeError):
            raise CockpitBlocked("REST_UNCERTAIN_NO_RETRY") from None


class State:
    """Separate cockpit database. Never opens or modifies the old score-sync ledger."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "cockpit-report-v1.sqlite3"
        with self.connect() as con:
            con.execute("CREATE TABLE IF NOT EXISTS cockpit_ops (key TEXT PRIMARY KEY, state TEXT NOT NULL, digest TEXT NOT NULL)")

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=5)
        try:
            con.execute("PRAGMA synchronous=FULL")
            with con:
                yield con
        finally:
            con.close()

    def reserve(self, key, payload):
        digest = sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            require(con.execute("SELECT state FROM cockpit_ops WHERE key=?", (key,)).fetchone() is None,
                    "ONE_SHOT_ALREADY_RESERVED_NO_RETRY")
            con.execute("INSERT INTO cockpit_ops VALUES (?, 'UNCERTAIN', ?)", (key, digest))

    def verified(self, key):
        with self.connect() as con:
            con.execute("UPDATE cockpit_ops SET state='VERIFIED' WHERE key=?", (key,))

    def backup(self, kind, value):
        # Exclusive create; never overwrite a previous snapshot. No webhook/token.
        path = self.directory / (kind + "-" + uuid.uuid4().hex + ".json")
        with path.open("x", encoding="utf-8") as out:
            json.dump(value, out, ensure_ascii=False, indent=2)
            out.flush()
            import os
            os.fsync(out.fileno())
        return path


def validate_field(field, code):
    require(isinstance(field, dict) and field.get("entityId") == "CRM_13"
            and field.get("fieldName") == code and field.get("userTypeId") == "string"
            and field.get("multiple") in ("N", False), "FIELD_TYPE_OR_SCOPE_CONFLICT")
    settings = field.get("settings", {})
    try:
        rows = int(settings.get("ROWS", 0))
        maximum = int(settings.get("MAX_LENGTH", 0) or 0)
    except (TypeError, ValueError):
        raise CockpitBlocked("FIELD_SETTINGS_INVALID") from None
    require(rows >= SPECS[code.removeprefix(PREFIX)][1] and (maximum == 0 or maximum >= 12000),
            "FIELD_MULTILINE_OR_LENGTH_CONFLICT")


def inspect_schema(rest):
    typ = rest("crm.type.getByEntityTypeId", {"entityTypeId": 1062})
    require(isinstance(typ, dict) and isinstance(typ.get("type"), dict)
            and str(typ["type"].get("id")) == "13"
            and str(typ["type"].get("entityTypeId")) == "1062", "SMART_PROCESS_TYPE_MISMATCH")
    require(typ["type"].get("isAutomationEnabled") in (False, "N")
            and typ["type"].get("isBizProcEnabled") in (False, "N"), "AUTOMATION_OR_BIZPROC_NOT_CONFIRMED_DISABLED")
    types = rest("userfieldconfig.getTypes", {"moduleId": "crm"})
    require(isinstance(types, dict) and isinstance(types.get("types"), dict), "TYPE_RESULT_INVALID")
    require(any(isinstance(t, dict) and t.get("userTypeId") == "string" for t in types["types"].values()),
            "STRING_TYPE_NOT_AVAILABLE")
    result = rest("userfieldconfig.list", {"moduleId": "crm", "select": ["*"], "filter": {"entityId": "CRM_13"}})
    require(isinstance(result, dict) and isinstance(result.get("fields"), list)
            and not result.get("next"), "FIELD_LIST_AMBIGUOUS")
    fields = result["fields"]
    require(all(isinstance(f, dict) and f.get("entityId") == "CRM_13" for f in fields), "FIELD_LIST_SCOPE_INVALID")
    names = [f.get("fieldName") for f in fields]
    require(len(names) == len(set(names)), "DUPLICATE_FIELD")
    require(ALL_FIELD_CODES.issubset(names), "ORIGINAL_FIELDS_MISSING")
    found = {}
    for field in fields:
        code = field.get("fieldName")
        if code in WRITE_FIELDS:
            detail = rest("userfieldconfig.get", {"moduleId": "crm", "id": field["id"]})
            require(isinstance(detail, dict), "FIELD_GET_INVALID")
            validate_field(detail.get("field"), code)
            found[code] = detail["field"]
    return {"existing": found, "missing": sorted(WRITE_FIELDS - found.keys()), "snapshot": result}


def provision(rest, *, state=None, apply=False):
    schema = inspect_schema(rest)
    if not apply:
        return schema
    require(state is not None, "STATE_REQUIRED")
    for code in schema["missing"]:
        # Re-read before each add; an interrupted provision can resume only absent
        # fields whose independent one-shot key has never been reserved.
        latest = inspect_schema(rest)
        if code not in latest["missing"]:
            continue
        label, rows = SPECS[code.removeprefix(PREFIX)]
        payload = {"moduleId": "crm", "field": {"entityId": "CRM_13", "fieldName": code,
                   "userTypeId": "string", "multiple": "N", "mandatory": "N",
                   "editFormLabel": {"ru": label}, "settings": {"ROWS": rows, "SIZE": 80, "MAX_LENGTH": 12000}}}
        state.backup("schema", latest["snapshot"])
        state.reserve("schema:" + code, payload)
        result = rest("userfieldconfig.add", payload)
        require(isinstance(result, dict), "ADD_UNCERTAIN_NO_RETRY")
        validate_field(result.get("field"), code)
        require(code in inspect_schema(rest)["existing"], "ADD_READBACK_UNCERTAIN")
        state.verified("schema:" + code)
    return inspect_schema(rest)


def plan_layout(current, available):
    require(isinstance(current, list) and bool(current), "LAYOUT_NULL_OR_AMBIGUOUS")
    require(all(isinstance(s, dict) and isinstance(s.get("name"), str) and s.get("name")
                and isinstance(s.get("elements"), list) for s in current), "LAYOUT_SHAPE_AMBIGUOUS")
    require(len({s["name"] for s in current}) == len(current), "LAYOUT_DUPLICATE_SECTION")
    desired = [FIELD_MAP["lead_score"], FIELD_MAP["score_version"], *(PREFIX + k for k in SPECS)]
    require(set(desired).issubset(available), "LAYOUT_FIELDS_MISSING")
    target = "serviceit_ai_analysis"
    result = deepcopy(current)
    existing = next((s for s in result if s["name"] == target), None)
    if existing is not None:
        # Never silently reshape an unknown section using our name.
        require(existing.get("title") == "Сервис ИТ | AI-анализ", "LAYOUT_SECTION_CONFLICT")
    else:
        existing = {"name": target, "title": "Сервис ИТ | AI-анализ", "type": "section", "elements": []}
        result.insert(0, existing)
    # Move ONLY the explicit AI allowlist (including score/version) into the AI
    # section. Preserve complete element objects and every other field/section.
    approved = {}
    for section in result:
        kept = []
        for element in section["elements"]:
            code = element.get("name") if isinstance(element, dict) else None
            if code in desired:
                require(code not in approved, "LAYOUT_DUPLICATE_AI_FIELD")
                approved[code] = element
            else:
                kept.append(element)
        section["elements"] = kept
    existing["elements"] = [approved.get(code, {"name": code, "optionFlags": 1}) for code in desired] + existing["elements"]
    return result


def configure_layout(rest, *, state=None, apply=False, scope="P", user_id=None):
    require(scope == "P" and user_id is None, "SHARED_OR_OTHER_USER_LAYOUT_DISABLED")
    schema = inspect_schema(rest)
    require(not schema["missing"], "PROVISION_FIRST")
    fields = rest("crm.item.fields", {"entityTypeId": 1062, "useOriginalUfNames": "Y"})
    require(isinstance(fields, dict) and isinstance(fields.get("fields"), dict), "ITEM_FIELDS_INVALID")
    before = rest("crm.item.details.configuration.get", deepcopy(LAYOUT_SCOPE))
    after = plan_layout(before, fields["fields"])
    if apply and before != after:
        require(state is not None, "STATE_REQUIRED")
        state.backup("personal-layout", {"scope": LAYOUT_SCOPE, "data": before, "proposed": after})
        require(rest("crm.item.details.configuration.get", deepcopy(LAYOUT_SCOPE)) == before, "LAYOUT_CONCURRENT_CHANGE")
        payload = {**deepcopy(LAYOUT_SCOPE), "data": after}
        state.reserve("layout:1062:21:P", payload)
        rest("crm.item.details.configuration.set", payload)
        require(rest("crm.item.details.configuration.get", deepcopy(LAYOUT_SCOPE)) == after, "LAYOUT_READBACK_UNCERTAIN_NO_RETRY")
        state.verified("layout:1062:21:P")
    return {"status": "PERSONAL_LAYOUT_APPLIED" if apply else "LAYOUT_PREVIEW", "changed": before != after,
            "before": before, "after": after}


def demo_assessment(item, today):
    require(isinstance(item, dict) and str(item.get("id")) == "3"
            and str(item.get("entityTypeId")) == "1062" and str(item.get("categoryId")) == "21"
            and item.get("title") == "DEMO ServiceIT AI — DEMO-ITREQ-001", "EXACT_DEMO_ITEM_REQUIRED")
    expected_key = "DEMO-IDEMP-" + sha256(
        f"{CONTRACT_VERSION}\x007700000009\x00DEMO-ITREQ-001".encode()).hexdigest()[:32]
    require(item.get(PREFIX + "IDEMPOTENCY_KEY") == expected_key
            and item.get(PREFIX + "SIGNAL_KIND") == "demo-synthetic", "ORIGINAL_DEMO_METADATA_MISMATCH")
    analysis = build_analysis(item, today=today)
    return {"lead_score": analysis["score"], "score_version": analysis["score_version"],
            "score_factors": analysis["factors"], "suggested_service": analysis["recommended_service_demo"]}


def report_fields(report):
    result = dict(zip((PREFIX + k for k in SPECS), (report.verification_status, report.summary,
                      report.evidence_quote, report.possible_need, report.limitations,
                      report.service_recommendation, report.next_action,
                      "\n".join(report.manager_checklist), report.full_report)))
    require(set(result) == WRITE_FIELDS and all(len(v) <= 12000 for v in result.values()), "REPORT_PAYLOAD_INVALID")
    return result


def populate(rest, *, state=None, apply=False, gigachat=False, privacy=False, classifier=None, today=None):
    day = today or date.today()
    item = rest("crm.item.get", deepcopy(ITEM_SCOPE)).get("item")
    assessment = demo_assessment(item, day)
    schema = inspect_schema(rest)
    require(not schema["missing"], "PROVISION_FIRST")
    fields = rest("crm.item.fields", {"entityTypeId": 1062, "useOriginalUfNames": "Y"}).get("fields", {})
    for code in WRITE_FIELDS:
        meta = fields.get(code, {})
        require(meta.get("type") in ("string", "crm_userfield")
                and meta.get("isReadOnly") is False and meta.get("isMultiple") is False
                and meta.get("isImmutable", False) is False, "REPORT_FIELD_NOT_WRITABLE")
    ai = None
    if apply:
        require(gigachat and privacy and state is not None, "EXPLICIT_AI_AND_SYNTHETIC_PRIVACY_OPT_IN_REQUIRED")
        require(all(item.get(code) in (None, "") for code in WRITE_FIELDS),
                "EXISTING_REPORT_REQUIRES_REVIEW_NO_OVERWRITE")
        # Reserve before model request as well: a timed-out model request is never
        # blindly repeated. This ledger is unrelated to old qualification sync.
        state.backup("demo-report-before", {k: item.get(k) for k in WRITE_FIELDS})
        state.reserve("report:1062:21:3:v1", {"source": AI_TEXT, "score": assessment})
        if classifier is None:
            from .ai import classify_cockpit_signal
            classifier = classify_cockpit_signal
        ai = classifier(AI_TEXT)
        require(isinstance(ai, dict) and ai.get("provider") == "gigachat", "GIGACHAT_ONLY")
    report = build_report(assessment, source_text=AI_TEXT, evidence_quote=AI_TEXT,
                          reference="DEMO-ITREQ-001 / synthetic-fixture (учебный источник)", ai=ai)
    if not apply:
        return {"status": "REPORT_PREVIEW_NOT_RUN", "report": report.to_dict()}
    fresh = rest("crm.item.get", deepcopy(ITEM_SCOPE)).get("item")
    demo_assessment(fresh, day)
    require(fresh == item, "ITEM_CONCURRENT_CHANGE_NO_RETRY")
    payload = report_fields(report)
    rest("crm.item.update", {**ITEM_SCOPE, "fields": payload})
    readback = rest("crm.item.get", deepcopy(ITEM_SCOPE)).get("item")
    demo_assessment(readback, day)
    require(all(readback.get(k) == v for k, v in payload.items()), "REPORT_READBACK_UNCERTAIN_NO_RETRY")
    require(all(readback.get(k) == v for k,v in item.items() if k not in WRITE_FIELDS
                and k not in {"updatedTime", "updatedBy", "movedTime"}), "PROTECTED_FIELD_DRIFT")
    state.verified("report:1062:21:3:v1")
    return {"status": "DEMO_AI_REPORT_VERIFIED", "score": report.score, "task_created": False}


def main(argv=None):
    import argparse
    import os
    parser = argparse.ArgumentParser(description="ServiceIT cockpit: offline preview by default")
    parser.add_argument("--mode", choices=["preview", "provision", "layout", "populate"], default="preview")
    parser.add_argument("--inspect-live", action="store_true")
    parser.add_argument("--webhook-from-env", action="store_true",
                        help="Explicit PowerShell SecureString handoff only; never put a URL on the command line")
    parser.add_argument("--apply-demo", action="store_true")
    parser.add_argument("--gigachat-opt-in", action="store_true")
    parser.add_argument("--synthetic-privacy-opt-in", action="store_true")
    parser.add_argument("--state-dir")
    args = parser.parse_args(argv)
    if not args.inspect_live:
        require(args.mode == "preview" and not args.apply_demo, "LIVE_INSPECTION_OPT_IN_REQUIRED")
        print(json.dumps({"status": "CODE_READY", "live_fields_created": False, "personal_layout_applied": False,
                          "ai": "NOT_RUN", "planned_fields": sorted(WRITE_FIELDS),
                          "live_preview_requires": "--inspect-live; local secure webhook prompt"}, ensure_ascii=False))
        return 0
    require(args.mode != "preview" or not args.apply_demo, "PREVIEW_CANNOT_APPLY")
    require(not args.apply_demo or args.state_dir, "STATE_DIR_REQUIRED")
    # Avoid Windows getpass console input: on the approved operator host it can
    # discard paste/keystrokes. A trusted PowerShell Read-Host -AsSecureString
    # passes the webhook only in the short-lived child process environment.
    require(args.webhook_from_env, "POWERSHELL_SECURE_WEBHOOK_REQUIRED")
    webhook = os.environ.pop("SERVICEIT_AI_COCKPIT_WEBHOOK", "")
    require(bool(webhook), "SECURE_WEBHOOK_MISSING")
    # Never accept a webhook on argv, in config files, or via Python getpass.
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        rest = Rest(webhook, client)
        state = State(args.state_dir) if args.apply_demo else None
        if args.mode == "provision":
            value = provision(rest, state=state, apply=args.apply_demo)
            output = {"status": "LIVE_FIELDS_VERIFIED" if args.apply_demo else "SCHEMA_PREVIEW", "missing": value["missing"]}
        elif args.mode == "layout":
            value = configure_layout(rest, state=state, apply=args.apply_demo)
            output = {k:v for k,v in value.items() if k not in {"before", "after"}}
        elif args.mode == "populate":
            output = populate(rest, state=state, apply=args.apply_demo, gigachat=args.gigachat_opt_in,
                              privacy=args.synthetic_privacy_opt_in)
        else:
            schema = inspect_schema(rest)
            current = rest("crm.item.details.configuration.get", deepcopy(LAYOUT_SCOPE))
            require(isinstance(current, list) and current, "LAYOUT_NULL_OR_AMBIGUOUS")
            item = rest("crm.item.get", deepcopy(ITEM_SCOPE)).get("item")
            assessment = demo_assessment(item, date.today())
            report = build_report(assessment, source_text=AI_TEXT, evidence_quote=AI_TEXT, reference="DEMO-ITREQ-001")
            output = {"status": "LIVE_READONLY_PREVIEW", "missing": schema["missing"], "ai": "NOT_RUN",
                      "report": report.to_dict(), "layout_sections": len(current)}
        print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, sqlite3.Error, httpx.HTTPError, OSError, KeyError, TypeError) as exc:
        # Only emit project-defined fixed reason codes. Never print arbitrary
        # Bitrix errors, request/response objects, webhook or source text.
        safe_codes = {
            "POWERSHELL_SECURE_WEBHOOK_REQUIRED", "SECURE_WEBHOOK_MISSING",
            "METHOD_BLOCKED", "REST_BUDGET_EXCEEDED", "REST_HTTP_BLOCKED",
            "REST_RESULT_BLOCKED", "REST_UNCERTAIN_NO_RETRY",
            "PAGINATION_REQUIRES_REVIEW", "FIELD_TYPE_OR_SCOPE_CONFLICT",
            "FIELD_SETTINGS_INVALID", "FIELD_MULTILINE_OR_LENGTH_CONFLICT",
            "SMART_PROCESS_TYPE_MISMATCH",
            "AUTOMATION_OR_BIZPROC_NOT_CONFIRMED_DISABLED",
            "TYPE_RESULT_INVALID", "STRING_TYPE_NOT_AVAILABLE",
            "FIELD_LIST_AMBIGUOUS", "FIELD_LIST_SCOPE_INVALID",
            "DUPLICATE_FIELD", "ORIGINAL_FIELDS_MISSING",
            "FIELD_GET_INVALID", "STATE_REQUIRED",
            "LAYOUT_NULL_OR_AMBIGUOUS", "LAYOUT_SHAPE_AMBIGUOUS",
            "LAYOUT_DUPLICATE_SECTION", "LAYOUT_FIELDS_MISSING",
            "LAYOUT_SECTION_CONFLICT", "LAYOUT_DUPLICATE_AI_FIELD",
            "SHARED_OR_OTHER_USER_LAYOUT_DISABLED", "PROVISION_FIRST",
            "ITEM_FIELDS_INVALID", "LAYOUT_CONCURRENT_CHANGE",
            "LAYOUT_READBACK_UNCERTAIN_NO_RETRY",
            "EXACT_DEMO_ITEM_REQUIRED", "ORIGINAL_DEMO_METADATA_MISMATCH",
            "REPORT_PAYLOAD_INVALID", "REPORT_FIELD_NOT_WRITABLE",
            "EXPLICIT_AI_AND_SYNTHETIC_PRIVACY_OPT_IN_REQUIRED",
            "EXISTING_REPORT_REQUIRES_REVIEW_NO_OVERWRITE",
            "GIGACHAT_ONLY", "ITEM_CONCURRENT_CHANGE_NO_RETRY",
            "REPORT_READBACK_UNCERTAIN_NO_RETRY", "PROTECTED_FIELD_DRIFT",
            "LIVE_INSPECTION_OPT_IN_REQUIRED", "PREVIEW_CANNOT_APPLY",
            "STATE_DIR_REQUIRED", "ONE_SHOT_ALREADY_RESERVED_NO_RETRY",
        }
        code = str(exc) if isinstance(exc, CockpitBlocked) else ""
        if code not in safe_codes:
            code = "UNCLASSIFIED_NO_REMOTE_DETAILS"
        print(json.dumps({
            "status": "BLOCKED_OR_UNCERTAIN",
            "safe_reason_code": code,
            "blind_mutation_retry": "FORBIDDEN",
            "secrets_in_output": False,
        }, ensure_ascii=False))
        raise SystemExit(2) from None
