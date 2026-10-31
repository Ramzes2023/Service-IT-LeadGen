"""Repeatable official EIS daily collection with persistent dedup and audit.

Requires an already verified EIS RSS source report. Performs four bounded,
sequential official HTTPS RSS GETs, never scrapes notice pages, never emails,
never connects to CRM, never promotes tenders into small-business B2B leads.

First successful run establishes baseline. Later runs return only new official
notice URLs. Unavailable RSS must leave the state untouched. Runs fail closed.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

from .eis_rss import SOURCE_NAME, _url
from .eis_targeted_1c import SEARCH_PHRASES, run_profiles, targeted_url
from .eis_108_review import build_queue
from .eis_108_triage import triage

MAX_INPUT_BYTES = 2 * 1024 * 1024
SOURCE_MODE = "EIS_OFFICIAL_RSS_RESEARCH_ONLY"
TRIAGE_COLUMNS = (
    "workstream", "notice_id", "published_on", "procurement_subject",
    "buyer_name_rss_unverified", "buyer_evidence_hint",
    "purchase_evidence_hint", "manual_work_queue", "official_notice_url",
    "review_holds", "qualified_lead", "crm_ready", "outbound_authorized",
)
SCHEMA = """CREATE TABLE IF NOT EXISTS official_notices (
    official_notice_url TEXT PRIMARY KEY NOT NULL,
    notice_id TEXT NOT NULL UNIQUE,
    first_seen_on TEXT NOT NULL,
    last_seen_on TEXT NOT NULL,
    procurement_subject TEXT NOT NULL,
    buyer_name_unverified TEXT NOT NULL,
    buyer_evidence_hint TEXT NOT NULL,
    purchase_evidence_hint TEXT NOT NULL,
    workstream TEXT NOT NULL
)"""


def source_url_from_report(report_path: str | Path) -> str:
    path = Path(report_path)
    if not path.is_file() or not 1 <= path.stat().st_size <= MAX_INPUT_BYTES:
        raise ValueError("EIS_DAILY_SOURCE_REPORT_MISSING")
    report = json.loads(path.read_text(encoding="utf-8-sig"))
    if (not isinstance(report, dict) or report.get("mode") != SOURCE_MODE
            or report.get("source") != SOURCE_NAME
            or report.get("crm_writes") != "NO"
            or report.get("outbound_messages") != "NO"
            or not isinstance(report.get("counts"), dict)):
        raise ValueError("EIS_DAILY_SOURCE_NOT_TRUSTED")
    url = report.get("search_rss_url")
    if not isinstance(url, str):
        raise ValueError("EIS_DAILY_SOURCE_URL_MISSING")
    _url(url, feed=True)
    targeted_url(url, SEARCH_PHRASES[0][1])
    return url


def _write_json(path: Path, data: dict) -> None:
    with path.open("x", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _safe_cell(value: object) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")) else text


def _write_csv(path: Path, items: list[dict]) -> None:
    with path.open("x", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TRIAGE_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for item in items:
            writer.writerow({key: _safe_cell(item[key]) for key in TRIAGE_COLUMNS})


def _write_batch(batch: dict, pending: Path) -> Path:
    if (batch.get("mode") != "EIS_TARGETED_1C_RESEARCH_ONLY"
            or batch.get("crm_writes") != "NO" or batch.get("outbound_messages") != "NO"):
        raise ValueError("EIS_DAILY_UNTRUSTED_BATCH")
    profiles = batch.get("profiles")
    if not isinstance(profiles, list) or len(profiles) != len(SEARCH_PHRASES):
        raise ValueError("EIS_DAILY_PROFILE_COUNT_INVALID")
    base = "eis_targeted_1c_daily"
    summary_profiles = []
    for (profile_id, phrase), profile in zip(SEARCH_PHRASES, profiles):
        if (not isinstance(profile, dict)
                or profile.get("profile_id") != profile_id
                or profile.get("query") != phrase
                or not isinstance(profile.get("rss_report"), dict)):
            raise ValueError("EIS_DAILY_PROFILE_ID_INVALID")
        report = profile["rss_report"]
        if (report.get("mode") != SOURCE_MODE
                or report.get("source") != SOURCE_NAME
                or report.get("crm_writes") != "NO"
                or report.get("outbound_messages") != "NO"):
            raise ValueError("EIS_DAILY_PROFILE_UNTRUSTED")
        _write_json(pending / (base + "_" + profile_id + ".json"), report)
        summary_profiles.append({k: v for k, v in profile.items() if k != "rss_report"})
    summary = {k: v for k, v in batch.items() if k != "profiles"}
    summary["profiles"] = summary_profiles
    summary_path = pending / (base + "_summary.json")
    _write_json(summary_path, summary)
    return summary_path


def _update_db(db: sqlite3.Connection, rows: list[dict], day: date) -> tuple[bool, list[dict]]:
    """Call only after every source, snapshot, and review has passed validation."""
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute(SCHEMA)
        db.execute("CREATE TABLE IF NOT EXISTS operation_meta "
                   "(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        # A successful *empty* initial feed is still an initialized baseline.
        # Counting notice rows alone would misclassify the next new tender.
        initialized = db.execute(
            "SELECT value FROM operation_meta WHERE key='baseline_completed'"
        ).fetchone()
        baseline = initialized is None
        new = []
        for row in rows:
            url = row["official_notice_url"]
            found = db.execute("SELECT 1 FROM official_notices WHERE official_notice_url=?", (url,)).fetchone()
            if found is None:
                if not baseline:
                    new.append(row)
                db.execute(
                    "INSERT INTO official_notices VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (url, row["notice_id"], day.isoformat(), day.isoformat(),
                     row["procurement_subject"], row["buyer_name_rss_unverified"],
                     row["buyer_evidence_hint"], row["purchase_evidence_hint"],
                     row["workstream"]),
                )
            else:
                db.execute(
                    "UPDATE official_notices SET last_seen_on=?, "
                    "procurement_subject=?, buyer_name_unverified=?, "
                    "buyer_evidence_hint=?, purchase_evidence_hint=? WHERE official_notice_url=?",
                    (day.isoformat(), row["procurement_subject"],
                     row["buyer_name_rss_unverified"], row["buyer_evidence_hint"],
                     row["purchase_evidence_hint"], url),
                )
        if baseline:
            db.execute("INSERT INTO operation_meta (key, value) VALUES (?, ?)",
                       ("baseline_completed", day.isoformat()))
        return baseline, new
    except Exception:
        db.rollback()
        raise


def run_daily(source_report: str | Path, state_dir: str | Path, *,
              today: date | None = None, downloader=run_profiles) -> dict:
    day = today or date.today()
    url = source_url_from_report(source_report)
    state = Path(state_dir).expanduser().resolve()
    if state.exists() and state.is_symlink():
        raise ValueError("EIS_DAILY_STATE_SYMLINK_NOT_ALLOWED")
    state.mkdir(parents=True, exist_ok=True)
    lock = state / ".eis_daily.lock"
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ValueError("EIS_DAILY_ANOTHER_RUN_ACTIVE") from None
    pending: Path | None = None
    final: Path | None = None
    try:
        with os.fdopen(fd, "w", encoding="ascii") as guard:
            guard.write("EIS_DAILY_LOCK_HUMAN_REMOVE_ONLY_AFTER_STOP\n")
        pending = Path(tempfile.mkdtemp(prefix=".pending-", dir=state))
        # Exactly four official GETs, no retries. Exception leaves database
        # untouched and pending directory is deleted.
        batch = downloader(url, today=day)
        summary_path = _write_batch(batch, pending)
        review = build_queue(summary_path, today=day)
        checked = triage(review)
        if (checked.get("crm_writes") != "NO"
                or checked.get("outbound_messages") != "NO"
                or checked.get("counts", {}).get("qualified_b2b_leads") != 0
                or checked.get("counts", {}).get("crm_ready") != 0):
            raise ValueError("EIS_DAILY_UNSAFE_TRIAGE_RESULT")
        rows = checked["rows"]
        _write_json(pending / "eis_review.json", review)
        _write_json(pending / "eis_triage.json", checked)
        _write_csv(pending / "eis_triage.csv", rows)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        final = state / ("run-" + timestamp)
        if final.exists():
            raise ValueError("EIS_DAILY_RUN_OUTPUT_EXISTS")
        db = sqlite3.connect(str(state / "eis_official_history.sqlite3"), timeout=3)
        try:
            baseline, fresh = _update_db(db, rows, day)
            counts = {
                "official_research_notices_in_latest_batch": len(rows),
                "new_notices_since_previous_success": len(fresh),
                "baseline_initialization": baseline,
                "public_sector_text_hints": checked["counts"]["public_sector_text_hints"],
                "qualified_small_b2b_leads": 0, "crm_ready": 0,
            }
            _write_csv(pending / "eis_new_since_previous.csv", fresh)
            report = {
                "mode": "EIS_DAILY_OFFICIAL_TENDER_RESEARCH_ONLY",
                "run_on": day.isoformat(), "counts": counts,
                "source_region": "NOT_VERIFIED_BY_TRIAL_RSS_FILTER",
                "official_deadline": "NOT_VERIFIED_FROM_RSS",
                "source": SOURCE_NAME,
                "crm_writes": "NO", "outbound_messages": "NO",
            }
            _write_json(pending / "run_summary.json", report)
            pending.rename(final)
            pending = None
            try:
                db.commit()
            except Exception:
                shutil.rmtree(final)
                raise
        finally:
            db.close()
        return {"status": "PASS", "counts": counts, "run_dir": str(final),
                "new_csv": str(final / "eis_new_since_previous.csv"),
                "triage_csv": str(final / "eis_triage.csv"),
                "state_db": str(state / "eis_official_history.sqlite3"),
                "crm_writes": "NO", "outbound_messages": "NO"}
    finally:
        if pending is not None and pending.exists():
            shutil.rmtree(pending)
        lock.unlink(missing_ok=True)


def main() -> None:
    p = argparse.ArgumentParser(description="Official EIS daily RSS -> deduped state (no CRM)")
    p.add_argument("--source-report", required=True)
    p.add_argument("--state-dir", required=True)
    args = p.parse_args()
    try:
        result = run_daily(args.source_report, args.state_dir)
    except (ValueError, RuntimeError, OSError, sqlite3.Error, UnicodeError) as exc:
        print("EIS_DAILY=BLOCKED; ERROR=" + str(exc).splitlines()[0][:90])
        raise SystemExit(2)
    print("EIS_DAILY=" + json.dumps(result, ensure_ascii=True, sort_keys=True))
    print("EIS_DAILY_STATUS=PASS; READY_B2B_LEADS=0; CRM_WRITES=NO")


if __name__ == "__main__":
    main()
