"""Isolated, explicitly opted-in GigaChat quality preflight for synthetic ServiceIT.

No Bitrix webhook, client data, CRM writes, task calls, SQLite ledger or .env access.
Only the constant AI_TEXT can reach GigaChat. No model prose or credentials logged.
"""
from __future__ import annotations

import argparse
import json

from .bitrix_1062_demo_qualification import AI_TEXT
from .serviceit_ai_report import build_report
from .bitrix_1062_ai_cockpit import report_fields

# Exact synthetic score profile confirmed for DEMO-ITREQ-001 on 2026-11-01.
# This is NOT loaded from CRM or represented as a live score refresh.
SYNTHETIC_ASSESSMENT = {
    "lead_score": 90,
    "score_version": "mvp-2026-10-v1",
    "score_factors": {
        "region": 20,
        "segment": 15,
        "signal": 25,
        "freshness": 15,
        "service_fit": 15,
        "contact": 0,
    },
    "suggested_service": "1С + ККТ + торговое оборудование",
}
SAFE_QUALITY_REASONS = frozenset({
    "missing_possible_need", "model_abstained", "missing_verified_evidence",
    "zero_confidence", "unverified_quote",
})


def run_preflight(classifier=None):
    """A single model completion, never reserves the production one-shot ledger."""
    if classifier is None:
        from .ai import classify_cockpit_signal
        classifier = classify_cockpit_signal
    result = classifier(AI_TEXT)
    if not isinstance(result, dict) or result.get("provider") != "gigachat":
        return {"status": "GIGACHAT_PREFLIGHT_BLOCKED", "safe_reason_code": "PROVIDER_INVALID",
                "crm_writes": 0, "ledger_reservations": 0}
    if result.get("quality_status") != "complete":
        reasons = result.get("quality_reasons")
        return {"status": "GIGACHAT_PREFLIGHT_NEEDS_REVIEW",
                "quality_reasons": sorted(set(reasons) & SAFE_QUALITY_REASONS) if isinstance(reasons, list)
                                   and all(isinstance(x, str) for x in reasons) else [],
                "crm_writes": 0, "ledger_reservations": 0}
    try:
        report = build_report(SYNTHETIC_ASSESSMENT, source_text=AI_TEXT,
                              evidence_quote=AI_TEXT,
                              reference="DEMO-ITREQ-001 / synthetic-fixture",
                              ai=result)
        fields = report_fields(report)
    except (ValueError, TypeError, KeyError, AttributeError):
        return {"status": "GIGACHAT_PREFLIGHT_NEEDS_REVIEW",
                "safe_reason_code": "REPORT_QUALITY_BLOCKED",
                "crm_writes": 0, "ledger_reservations": 0}
    return {"status": "GIGACHAT_COCKPIT_PREFLIGHT_PASS",
            "provider": "gigachat", "quality": "complete",
            "score_fixture": report.score,
            "full_report_chars": len(report.full_report),
            "ai_field_count": len(fields),
            "verified_quote": result.get("evidence_quote") in AI_TEXT,
            "crm_writes": 0, "ledger_reservations": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Synthetic GigaChat preflight, never Bitrix writes")
    parser.add_argument("--live-synthetic", action="store_true")
    args = parser.parse_args(argv)
    if not args.live_synthetic:
        print(json.dumps({"status": "GIGACHAT_PREFLIGHT_OFFLINE_READY",
                          "model_called": False, "crm_writes": 0, "ledger_reservations": 0}))
        return 0
    # First check only public configuration shape; DO NOT send a paid
    # completion or OAuth request if owner GitHub variable interpolates to
    # an empty string (a common YAML env-vs-default trap).
    import os
    scope = os.environ.get("GIGACHAT_SCOPE")
    model = os.environ.get("GIGACHAT_MODEL")
    if scope not in {"GIGACHAT_API_PERS", "GIGACHAT_API_B2B", "GIGACHAT_API_CORP"}:
        output = {"status": "GIGACHAT_PREFLIGHT_BLOCKED",
                  "safe_reason_code": "GIGACHAT_SCOPE_UNSET_OR_INVALID",
                  "crm_writes": 0, "ledger_reservations": 0}
    elif not isinstance(model, str) or not 1 <= len(model.strip()) <= 80:
        output = {"status": "GIGACHAT_PREFLIGHT_BLOCKED",
                  "safe_reason_code": "GIGACHAT_MODEL_UNSET_OR_INVALID",
                  "crm_writes": 0, "ledger_reservations": 0}
    else:
        try:
            from .ai import config_from_env
            cfg = config_from_env()
            if cfg.provider != "gigachat":
                raise ValueError("PROVIDER_INVALID")
            output = run_preflight()
        except Exception:
            # Never expose remote error bodies, raw model output, OAuth
            # endpoints, credential values, or user/client data.
            output = {"status": "GIGACHAT_PREFLIGHT_BLOCKED",
                      "safe_reason_code": "MODEL_OR_CONFIG_ERROR",
                      "crm_writes": 0, "ledger_reservations": 0}
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0 if output["status"] == "GIGACHAT_COCKPIT_PREFLIGHT_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
