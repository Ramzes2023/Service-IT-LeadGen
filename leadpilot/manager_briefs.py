"""Read-only manager review briefs from already-qualified public/company signals.

No CRM writes, messages, phone/email extraction, third-party API calls or AI calls.
A brief is a hypothesis for a human, never permission to contact a prospect.
"""
from __future__ import annotations

from datetime import date
import json
import math
from pathlib import Path
from urllib.parse import urlparse

from .core import SCORE_VERSION, get_leads, score_company


def _eligible_ai(lead: dict, signal: dict) -> dict | None:
    ai = lead.get("ai_suggestion") or {}
    if not isinstance(ai, dict) or ai.get("quality_status") != "complete":
        return None
    need, quote = ai.get("possible_need"), ai.get("evidence_quote")
    confidence = ai.get("confidence")
    if (not isinstance(need, str) or not need.strip() or
            not isinstance(quote, str) or not quote.strip() or
            quote not in signal.get("evidence", "") or
            isinstance(confidence, bool) or
            not isinstance(confidence, (float, int)) or
            not math.isfinite(confidence) or not 0 < confidence <= 1 or
            ai.get("service") in (None, "Требуется проверка")):
        return None
    return {"origin": "AI_hypothesis_not_verified_need", "service": ai["service"],
            "possible_need": need.strip(), "confidence": confidence,
            "evidence_quote": quote}


def build_manager_briefs(db_path: str | Path, *, today: date | None = None, limit: int = 20) -> dict:
    """Create a human-review queue without changing DB or contacting external systems."""
    if not 1 <= limit <= 50:
        raise ValueError("Limit must be between 1 and 50")
    now = today or date.today()
    results: list[dict] = []
    skipped = {"stale_score_or_signal": 0, "restricted": 0, "unverified_source": 0}
    for lead in get_leads(db_path, only_queue=True):
        if len(results) >= limit:
            break
        if (lead["existing_customer"] or lead["manual_review"] or
                {"all", "new_customer"}.intersection(lead["suppressed_channels"])):
            skipped["restricted"] += 1
            continue
        score, _, explanation = score_company(lead, lead["signals"], now)
        if (score != lead["score"] or lead["score_version"] != SCORE_VERSION or
                explanation["signal_id"] is None or
                explanation["signal_id"] != lead["explanation"].get("signal_id")):
            skipped["stale_score_or_signal"] += 1
            continue
        signal = next((s for s in lead["signals"] if s["id"] == explanation["signal_id"]), None)
        if not signal:
            skipped["stale_score_or_signal"] += 1
            continue
        uri = urlparse(signal["source_url"])
        if (uri.scheme != "https" or not uri.hostname or uri.username or uri.password or
                not signal.get("evidence", "").strip() or not signal.get("signal_date")):
            skipped["unverified_source"] += 1
            continue
        ai = _eligible_ai(lead, signal)
        results.append({
            "local_company_id": lead["id"], "company": lead["name"], "inn": lead["inn"],
            "region": lead["region"], "priority": lead["priority"],
            "lead_score": score, "score_version": SCORE_VERSION,
            "rule_based_service": lead["recommended_service"],
            "source": {"name": signal["source_name"], "url": signal["source_url"],
                       "signal_date": signal["signal_date"], "evidence": signal["evidence"],
                       "signal_type": signal["signal_type"]},
            "ai_hypothesis": ai,
            "ai_review": "review_hypothesis" if ai else "human_analysis_required",
            "outreach": "NOT_AUTHORIZED_BY_THIS_BRIEF",
            "next_action": "Проверить данные и дубли в Битрикс24; подтвердить потребность вручную. "
                           "Контактировать только разрешённым законом и компанией способом.",
            "crm_writes": "NO",
        })
    return {"mode": "HUMAN_REVIEW_ONLY", "generated_on": now.isoformat(),
            "total_briefs": len(results), "skipped": skipped,
            "crm_writes": "NO", "outreach_sent": "NO", "valyqon_changes": "NONE", "briefs": results}


def export_manager_briefs(db_path: str | Path, output: str | Path, *,
                          today: date | None = None, limit: int = 20) -> dict:
    """Save only under the project's ignored exports/ directory."""
    out = Path(output).resolve()
    allowed = Path("exports").resolve()
    if not out.is_relative_to(allowed) or out.suffix != ".json":
        raise ValueError("Output must be a JSON file inside exports/")
    result = build_manager_briefs(db_path, today=today, limit=limit)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"briefs": result["total_briefs"], "output": str(out),
            "crm_writes": "NO", "outreach_sent": "NO"}
