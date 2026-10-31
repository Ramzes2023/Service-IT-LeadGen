"""Attach public research *hints* to an existing local pilot scope review.

IMPORTANT: These hints are NOT validated FNS entries or human decisions.
Nothing here approves a prospect, contacts it, or mutates the source checklist.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path
from urllib.parse import urlsplit

ALLOWED_ACTIONS = frozenset({"VERIFY_FNS", "DEFER_SCOPE", "LEGAL_REVIEW"})
ALLOWED_SOURCES = frozenset({"OFFICIAL_REGULATOR", "THIRD_PARTY",
                             "PUBLIC_INSTITUTION", "PUBLIC_LEGAL_DOCUMENT",
                             "ISSUER_DISCLOSURE"})


def _index(rows, source):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        raise ValueError(source + ": expected 1 to 100 company records")
    by_inn = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(source + ": malformed row")
        inn = row.get("inn")
        if (not isinstance(inn, str) or not inn.isascii()
                or not inn.isdigit() or len(inn) not in (10, 12)
                or inn in by_inn):
            raise ValueError(source + ": invalid or duplicate INN")
        by_inn[inn] = row
    return by_inn


def _public_url(raw):
    if not isinstance(raw, str):
        return False
    try:
        u = urlsplit(raw)
        return (u.scheme == "https" and bool(u.hostname) and
                u.port in (None, 443) and u.username is None and
                u.password is None and not u.fragment)
    except ValueError:
        return False


def preview(scope_path: str | Path, hints_path: str | Path,
            *, today: date | None = None) -> dict:
    today = today or date.today()
    scope = json.loads(Path(scope_path).read_text(encoding="utf-8-sig"))
    hints = json.loads(Path(hints_path).read_text(encoding="utf-8-sig"))
    if scope.get("mode") != "PILOT_SCOPE_REVIEW":
        raise ValueError("Expected local PILOT_SCOPE_REVIEW")
    if hints.get("mode") != "PUBLIC_RESEARCH_HINTS":
        raise ValueError("Expected PUBLIC_RESEARCH_HINTS")
    original = _index(scope.get("items"), "scope")
    sources = _index(hints.get("items"), "public research")
    if original.keys() != sources.keys():
        raise ValueError("Review hints must match the entire local INN set")
    observed = hints.get("review_date")
    try:
        snapshot = date.fromisoformat(observed)
    except (ValueError, TypeError):
        raise ValueError("Invalid research date") from None
    if snapshot > today or (today - snapshot).days > 60:
        raise ValueError("Research hints stale or future dated")
    counts = Counter()
    items = []
    for inn, manual in original.items():
        hint = sources[inn]
        action = hint.get("action")
        if action not in ALLOWED_ACTIONS or hint.get("source_level") not in ALLOWED_SOURCES:
            raise ValueError("Unsupported research action or source level")
        if not _public_url(hint.get("source_url")):
            raise ValueError("Unusable public source link")
        if hint.get("checked_on") != observed:
            raise ValueError("Evidence check date mismatch")
        for field in ("name", "note"):
            value = hint.get(field)
            if not isinstance(value, str) or not (5 <= len(value) <= 500):
                raise ValueError("Missing public company information")
        counts[action] += 1
        if manual.get("decision") != "PENDING":
            counts["manual_decision_already_present"] += 1
        items.append({
            "inn": inn,
            "public_name": hint["name"],
            "research_action": action,
            "research_note": hint["note"],
            "research_source": hint["source_url"],
            "source_level": hint["source_level"],
            "checked_on": observed,
            "existing_manual_decision": manual.get("decision"),
            "needs_human_review": True,
            "fns_current_verified": False,
            "crm_ready": False,
            "outbound_authorized": False
        })
    priority = {"LEGAL_REVIEW": 0, "VERIFY_FNS": 1, "DEFER_SCOPE": 2}
    items.sort(key=lambda x: (priority[x["research_action"]], x["inn"]))
    return {
        "mode": "PUBLIC_RESEARCH_HINTS_PREVIEW",
        "crm_writes": "NO", "outbound_messages": "NO", "crm_ready": 0,
        "counts": {"total": len(items),
                   "verify_fns": counts["VERIFY_FNS"],
                   "suggest_defer": counts["DEFER_SCOPE"],
                   "legal_review": counts["LEGAL_REVIEW"],
                   "manual_decision_already_present": counts["manual_decision_already_present"],
                   "ready_for_crm": 0},
        "items": items,
        "warning": "Research suggestions are NOT approved exclusions, FNS confirmations or outreach permission."
    }


def save_preview(report: dict, path: str | Path) -> str:
    target = Path(path).resolve()
    if target.suffix.lower() != ".json" or not target.is_relative_to(Path("exports").resolve()):
        raise ValueError("Output must be JSON under exports/")
    if target.exists():
        raise FileExistsError("Do not overwrite an earlier review report")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(target)


def main():
    p = argparse.ArgumentParser(description="Review evidence hints without changing decisions")
    p.add_argument("--scope", default="exports/mvp_scope_review.json")
    p.add_argument("--hints", default="samples/mvp_15_public_research_hints.json")
    p.add_argument("--output", default="exports/mvp_public_research_hints.json")
    args = p.parse_args()
    report = preview(args.scope, args.hints)
    file = save_preview(report, args.output)
    print("RESEARCH_TRIAGE=" + json.dumps(
        {"file": file, "counts": report["counts"], "crm_writes": "NO"},
        ensure_ascii=False))


if __name__ == "__main__":
    main()
