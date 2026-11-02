"""Offline source-batch -> company aggregate -> Lead Score -> colored Kanban plan.

Runs for any number of input source candidates (bounded); no Bitrix REST,
credentials, subscriptions, model calls, emails, or CRM mutations. Source
and customer-check evidence is NOT independently verified in this module.
The output is a preview, never a production CRM handoff authorization.
"""
from __future__ import annotations

import argparse
from collections import OrderedDict
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from .core import normalize_inn
from .serviceit_ai_lead_engine import (
    MAX_SIGNALS, assess_lead, plan_serviceit_ai_signals,
)
from .serviceit_kanban_presentation import fields_from_assessment

CONTRACT = "serviceit-source-batch-v1"
COMPANY_KEYS = frozenset(("inn", "name", "region", "industry"))
SIGNAL_KEYS = frozenset(("signal_type", "signal_date", "source_url", "evidence"))
GATE_KEYS = frozenset((
    "company_registry", "source", "existing_customer", "crm_dedup",
    "stop_list", "legal_basis", "contact",
))
ALLOWED_STATES = frozenset(("VERIFIED", "BLOCKED", "UNKNOWN"))
MAX_ENTRIES = 250
MAX_COMPANIES = 200
MAX_JSON_BYTES = 262144


class BatchRejected(ValueError):
    pass


def _require(ok: bool, code: str) -> None:
    if not ok:
        raise BatchRejected(code)


def _clean_signal(row: Any) -> dict:
    _require(isinstance(row, dict) and set(row) == SIGNAL_KEYS, "SIGNAL_SHAPE_INVALID")
    _require(all(isinstance(row[k], str) for k in SIGNAL_KEYS), "SIGNAL_TYPE_INVALID")
    _require(all(len(row[k]) <= (1200 if k == "evidence" else 512)
                 for k in SIGNAL_KEYS), "SIGNAL_TOO_LONG")
    return {k: row[k] for k in sorted(SIGNAL_KEYS)}


def _clean_company(row: Any) -> dict:
    _require(isinstance(row, dict) and set(row) == COMPANY_KEYS, "COMPANY_SHAPE_INVALID")
    _require(all(isinstance(row[k], str) for k in COMPANY_KEYS), "COMPANY_TYPE_INVALID")
    _require(all(len(row[k]) <= 140 for k in COMPANY_KEYS), "COMPANY_TOO_LONG")
    try:
        inn = normalize_inn(row["inn"])
    except (ValueError, TypeError):
        raise BatchRejected("COMPANY_INN_INVALID") from None
    return {**row, "inn": inn, "phone": "", "email": ""}


def _clean_checks(row: Any) -> dict:
    _require(isinstance(row, dict) and set(row).issubset(GATE_KEYS), "GATE_SHAPE_INVALID")
    _require(all(type(v) is str and v in ALLOWED_STATES for v in row.values()),
             "GATE_STATE_INVALID")
    # Explicitly fill the missing gates as UNKNOWN rather than pretending VERIFIED.
    return {name: row.get(name, "UNKNOWN") for name in sorted(GATE_KEYS)}


def assess_source_batch(payload: dict) -> dict:
    """Validated in-memory assessment only. Keeps company identity out of stdout."""
    _require(isinstance(payload, dict)
             and set(payload) == {"contract_version", "as_of", "source_kind", "entries"}
             and payload["contract_version"] == CONTRACT, "BATCH_CONTRACT_INVALID")
    _require(payload["source_kind"] in ("synthetic", "research"),
             "SOURCE_KIND_INVALID")
    _require(isinstance(payload["as_of"], str), "DATE_INVALID")
    try:
        when = date.fromisoformat(payload["as_of"])
    except ValueError:
        raise BatchRejected("DATE_INVALID") from None
    entries = payload["entries"]
    _require(isinstance(entries, list) and 1 <= len(entries) <= MAX_ENTRIES,
             "ENTRY_COUNT_INVALID")

    companies: OrderedDict[str, dict] = OrderedDict()
    repeated = 0
    for entry in entries:
        _require(isinstance(entry, dict)
                 and set(entry) == {"company", "signals", "checks"},
                 "ENTRY_SHAPE_INVALID")
        company = _clean_company(entry["company"])
        checks = _clean_checks(entry["checks"])
        signals = entry["signals"]
        _require(isinstance(signals, list) and 1 <= len(signals) <= MAX_SIGNALS,
                 "ENTRY_SIGNALS_INVALID")
        inn = company["inn"]
        if inn not in companies:
            _require(len(companies) < MAX_COMPANIES, "COMPANY_COUNT_INVALID")
            companies[inn] = {"company": company, "checks": checks,
                              "signals": [], "seen": {}}
        target = companies[inn]
        _require(target["company"] == company, "COMPANY_IDENTITY_CONFLICT")
        _require(target["checks"] == checks, "COMPANY_CHECKS_CONFLICT")
        for source in signals:
            signal = _clean_signal(source)
            if payload["source_kind"] == "synthetic":
                _require(re.fullmatch(r"https://example\.invalid/[A-Za-z0-9/_-]+",
                                      signal["source_url"]) is not None,
                         "SYNTHETIC_SOURCE_REQUIRED")
            key = (signal["source_url"], signal["signal_type"])
            if key in target["seen"]:
                _require(target["seen"][key] == signal, "DUPLICATE_SOURCE_CONFLICT")
                repeated += 1
            else:
                target["seen"][key] = signal
                target["signals"].append(signal)
            _require(len(target["signals"]) <= MAX_SIGNALS, "COMPANY_SIGNALS_LIMIT")

    # Stable ordering for repeated runs regardless of provider response order.
    reports = []
    for inn in sorted(companies):
        target = companies[inn]
        signals = sorted(target["signals"], key=lambda x: (
            x["source_url"], x["signal_type"], x["signal_date"]))
        result = assess_lead(target["company"], signals,
                             checks=target["checks"], today=when,
                             demo=payload["source_kind"] == "synthetic")
        reports.append(result)

    return {
        "source_kind": payload["source_kind"],
        "as_of": when.isoformat(),
        "input_entries": len(entries),
        "unique_companies": len(companies),
        "repeated_signals_ignored": repeated,
        "assessments": reports,
    }


def plan_source_batch(payload: dict) -> dict:
    validated = assess_source_batch(payload)
    when = date.fromisoformat(validated["as_of"])
    entries = payload["entries"]
    companies = {x["company_inn"]: x for x in validated["assessments"]}
    repeated = validated["repeated_signals_ignored"]
    reports = validated["assessments"]
    planned = plan_serviceit_ai_signals(reports)
    masked = []
    for report, item in zip(reports, planned["items"]):
        fields = fields_from_assessment(report)
        masked.append({
            "company_fingerprint": sha256(report["company_inn"].encode()).hexdigest()[:20],
            "signal_fingerprint": report["identity_fingerprint"],
            "score": report["lead_score"],
            "score_version": report["score_version"],
            "color_badge": report["kanban_score_badge"],
            "crm_fields_preview": fields,
            "source_count": report["source_count"],
            "qualification_status": report["status"],
            "crm_handoff_eligible_by_supplied_checks": report["crm_handoff_eligible"],
            "crm_operation": item["crm_operation"],
            "customer_contacted": False,
        })
    batch_hash = sha256(json.dumps({
        "as_of":when.isoformat(), "source_kind":payload["source_kind"],
        "items":masked
    }, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
    return {
        "status": "SERVICEIT_ALL_LEAD_BATCH_PREVIEW",
        "batch_fingerprint": batch_hash,
        "source_kind": payload["source_kind"],
        "as_of": when.isoformat(),
        "input_entries": len(entries),
        "unique_companies": len(companies),
        "repeated_signals_ignored": repeated,
        "items": masked,
        "crm_writes": 0,
        "automatic_production_worker": False,
        "corporate_checks_independently_verified": False,
        "global_crm_dedupe_verified": False,
        "outbound_contact_authorized": False,
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Offline multi-source color scoring. NO CRM writes.")
    p.add_argument("--input", type=Path, default=Path(__file__).resolve().parents[1] /
                   "fixtures" / "serviceit_source_batch_demo.json")
    args = p.parse_args(argv)
    try:
        _require(args.input.is_file(), "INPUT_NOT_FOUND")
        _require(args.input.stat().st_size <= MAX_JSON_BYTES, "INPUT_TOO_LARGE")
        data = json.loads(args.input.read_text(encoding="utf-8-sig"))
        result = plan_source_batch(data)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (BatchRejected, ValueError, KeyError, TypeError, UnicodeError, OSError):
        # Avoid exposing source-specific contents or local paths in console.
        print(json.dumps({"status":"SOURCE_BATCH_BLOCKED","crm_writes":0,
                          "secrets_in_output":False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
