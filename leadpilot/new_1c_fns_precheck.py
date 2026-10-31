"""Plan fresh 1C employer verification in FNS before Lead Score or CRM.

Reads two saved Trudvsem snapshot JSON files and a previously reconciled FNS
report; it neither scrapes FNS nor claims new businesses are SME or buyers.
No network, DB mutation, AI, CRM writes or outbound contact.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path
from urllib.parse import urlsplit

from .verified_import import read_snapshots, is_valid_inn

FNS_MODE = "FNS_OFFICIAL_GUI_RECONCILIATION"
FNS_STATUSES = {"MICRO", "SMALL", "MEDIUM", "NOT_SME", "NOT_FOUND"}
FNS_URL = "https://rmsp.nalog.ru/search.html?mode=inn-list"


def _known_inns(path: Path, today: date) -> set[str]:
    fns = json.loads(path.read_text(encoding="utf-8-sig"))
    if (not isinstance(fns, dict) or fns.get("mode") != FNS_MODE or
            fns.get("crm_writes") != "NO" or fns.get("outreach") != "NOT_AUTHORIZED"):
        raise ValueError("Expected read-only official FNS reconciliation")
    try:
        snapshot = date.fromisoformat(fns["snapshot"])
    except (KeyError, ValueError, TypeError):
        raise ValueError("Invalid FNS snapshot date") from None
    if (today - snapshot).days < 0 or (today - snapshot).days > 60:
        raise ValueError("FNS snapshot is stale or future dated")
    rows = fns.get("items")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        raise ValueError("Invalid prior FNS results")
    counts = fns.get("counts")
    if not isinstance(counts, dict) or counts.get("total") != len(rows) or counts.get("crm_ready") != 0:
        raise ValueError("Invalid FNS total or approval flag")
    inns = set()
    status = Counter()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid FNS result row")
        inn = row.get("inn")
        if (not is_valid_inn(inn) or inn in inns or
                row.get("snapshot") != snapshot.isoformat() or
                row.get("source") != FNS_URL or
                row.get("fns_status") not in FNS_STATUSES or
                row.get("crm_ready") is not False or
                row.get("outbound_authorized") is not False):
            raise ValueError("Invalid, duplicated or approved FNS result")
        inns.add(inn)
        status[row["fns_status"]] += 1
    expected = {"micro":"MICRO", "small":"SMALL", "medium":"MEDIUM",
                "not_sme":"NOT_SME", "not_found":"NOT_FOUND"}
    if any(counts.get(k) != status[v] for k,v in expected.items()):
        raise ValueError("FNS category counters inconsistent")
    return inns


def _valid_signal_url(raw: str) -> bool:
    try:
        u = urlsplit(raw)
        host = (u.hostname or "").lower()
        return (u.scheme == "https" and not u.username and not u.password
                and u.port in (None, 443) and bool(u.path.strip("/"))
                and (host == "trudvsem.ru" or host.endswith(".trudvsem.ru")))
    except (TypeError, ValueError):
        return False


def plan(snapshots: str | Path, prior_fns: str | Path, *,
         today: date | None = None, max_new: int = 60) -> dict:
    """Preview *new* INNs for an official FNS check, not qualified sales leads."""
    now = today or date.today()
    if isinstance(max_new, bool) or not isinstance(max_new, int) or not 1 <= max_new <= 100:
        raise ValueError("max_new must be 1-100")
    known = _known_inns(Path(prior_fns), now)
    signals, source_stats = read_snapshots(Path(snapshots), today=now)
    counters = Counter()
    unique = {}
    for sig in signals:
        counters["fresh_validated_signals"] += 1
        if sig.get("signal_type") != "1c_vacancy":
            counters["not_1c"] += 1
            continue
        if sig.get("region") not in ("Москва", "Московская область"):
            counters["outside_pilot_region"] += 1
            continue
        age = (now - date.fromisoformat(sig["signal_date"])).days
        if not 0 <= age <= 45 or not _valid_signal_url(sig["source_url"]):
            counters["stale_or_untrusted_source"] += 1
            continue
        inn = sig["inn"]
        if inn in known:
            counters["already_checked_fns"] += 1
            continue
        prev = unique.get(inn)
        if prev is None or sig["signal_date"] > prev["signal_date"]:
            unique[inn] = sig
    selected = sorted(unique.values(), key=lambda x: (x["signal_date"], x["inn"]), reverse=True)
    truncated = len(selected) > max_new
    selected = selected[:max_new]
    return {
        "mode": "NEW_1C_FNS_PRECHECK_ONLY",
        "date": now.isoformat(), "fns_source": FNS_URL,
        "counts": {**dict(counters),
                   "known_inn_skipped": len(known),
                   "new_unique_inns": len(unique),
                   "selected_for_fns": len(selected),
                   "truncated": truncated,
                   "ready_for_crm": 0},
        "source_audit": source_stats,
        "candidates": [{
            "inn": s["inn"], "employer": s["name"], "region": s["region"],
            "signal_url": s["source_url"], "signal_date": s["signal_date"],
            "signal": s["evidence"],
            "business_size": "UNVERIFIED",
            "next_action": "Проверить категорию организации в официальном реестре МСП по ИНН",
            "qualified_as_customer": False,
            "crm_ready": False, "outbound_authorized": False
        } for s in selected],
        "crm_writes": "NO", "outbound_messages": "NO",
        "warning": "A vacancy is a need signal, not evidence of intent to buy external 1C support."
    }


def save(report: dict, target: str | Path, inn_list: str | Path) -> dict:
    output = Path(target).resolve()
    inns_path = Path(inn_list).resolve()
    root = Path("exports").resolve()
    if (not output.is_relative_to(root) or output.suffix.lower() != ".json" or
            not inns_path.is_relative_to(root) or inns_path.suffix.lower() != ".txt" or
            output == inns_path or output.exists() or inns_path.exists()):
        raise ValueError("Both output paths must be new files under exports/")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Write only an INN list, no contacts or company names, for manual FNS search.
    candidates = report["candidates"]
    if not candidates:
        return {"saved": False, "reason": "NO_NEW_INNS", "crm_writes": "NO"}
    with output.open("x", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    with inns_path.open("x", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(c["inn"] for c in candidates) + "\n")
    return {"saved": True, "report": str(output), "inn_list": str(inns_path),
            "count": len(candidates), "crm_writes": "NO"}


def main() -> None:
    p = argparse.ArgumentParser(description="Fresh 1C source -> new employer INNs -> manual FNS")
    p.add_argument("--snapshots", required=True, help="Folder with trudvsem_77.json and trudvsem_50.json")
    p.add_argument("--prior-fns", default="exports/fns_official_reconciliation.json")
    p.add_argument("--output", default="exports/new_1c_fns_precheck.json")
    p.add_argument("--inn-list", default="exports/new_1c_fns_inns.txt")
    p.add_argument("--limit", type=int, default=60)
    args = p.parse_args()
    report = plan(args.snapshots, args.prior_fns, max_new=args.limit)
    stored = save(report, args.output, args.inn_list)
    print("NEW_1C_FNS_PRECHECK=" + json.dumps({"counts": report["counts"],
          "file_status": stored, "crm_writes": "NO"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
