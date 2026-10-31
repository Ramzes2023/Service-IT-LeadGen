"""Read-only independent health acceptance of persistent EIS daily history.

Verifies latest completed run, exact URL sets, new-vs-history consistency,
baseline marker, output files and hard no-CRM boundary. Makes no network
requests and never writes source DB, CSV, notices or scheduled tasks.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date
import json
from pathlib import Path
import sqlite3

from .eis_rss import _url, SOURCE_NAME

RUN_MODE = "EIS_DAILY_OFFICIAL_TENDER_RESEARCH_ONLY"
TRIAGE_MODE = "EIS_108_COMMERCIALLY_SAFE_TRIAGE_ONLY"
MAX_REPORT_BYTES = 3 * 1024 * 1024


def _json(path: Path) -> dict:
    if not path.is_file() or not 1 <= path.stat().st_size <= MAX_REPORT_BYTES:
        raise ValueError("EIS_HEALTH_MISSING_OR_OVERSIZE_REPORT")
    content = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(content, dict):
        raise ValueError("EIS_HEALTH_INVALID_JSON_REPORT")
    return content


def _date(raw: object) -> date:
    if not isinstance(raw, str):
        raise ValueError("EIS_HEALTH_INVALID_DATE")
    try:
        value = date.fromisoformat(raw)
    except ValueError:
        raise ValueError("EIS_HEALTH_INVALID_DATE") from None
    if value.isoformat() != raw:
        raise ValueError("EIS_HEALTH_INVALID_DATE")
    return value


def _rows(path: Path) -> list[dict]:
    if not path.is_file() or path.stat().st_size > MAX_REPORT_BYTES:
        raise ValueError("EIS_HEALTH_CSV_FILE_INVALID")
    with path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    if len(rows) > 500:
        raise ValueError("EIS_HEALTH_TOO_MANY_ROWS")
    return rows


def verify(state_dir: str | Path) -> dict:
    state = Path(state_dir).expanduser().resolve()
    if not state.is_dir():
        raise ValueError("EIS_HEALTH_STATE_DIR_MISSING")
    lock = state / ".eis_daily.lock"
    if lock.exists():
        raise ValueError("EIS_HEALTH_RUN_IN_PROGRESS")
    dbpath = state / "eis_official_history.sqlite3"
    if not dbpath.is_file() or dbpath.stat().st_size < 200:
        raise ValueError("EIS_HEALTH_STATE_DB_MISSING")
    run_dirs = sorted(path for path in state.iterdir()
                      if path.is_dir() and path.name.startswith("run-"))
    if not run_dirs:
        raise ValueError("EIS_HEALTH_COMPLETED_RUN_NOT_FOUND")
    run = run_dirs[-1]
    summary = _json(run / "run_summary.json")
    triage = _json(run / "eis_triage.json")
    if (summary.get("mode") != RUN_MODE or summary.get("source") != SOURCE_NAME
            or summary.get("crm_writes") != "NO"
            or summary.get("outbound_messages") != "NO"
            or triage.get("mode") != TRIAGE_MODE
            or triage.get("source") != SOURCE_NAME
            or triage.get("crm_writes") != "NO"
            or triage.get("outbound_messages") != "NO"):
        raise ValueError("EIS_HEALTH_UNTRUSTED_REPORT_MODE")
    counts = summary.get("counts")
    auditcounts = triage.get("counts")
    if not isinstance(counts, dict) or not isinstance(auditcounts, dict):
        raise ValueError("EIS_HEALTH_COUNTS_MISSING")
    now = _date(summary.get("run_on"))
    current = counts.get("official_research_notices_in_latest_batch")
    recent = counts.get("new_notices_since_previous_success")
    baseline = counts.get("baseline_initialization")
    if (type(current) is not int or type(recent) is not int
            or not 0 <= recent <= current <= 500
            or type(baseline) is not bool
            or counts.get("crm_ready") != 0
            or counts.get("qualified_small_b2b_leads") != 0
            or auditcounts.get("qualified_b2b_leads") != 0
            or auditcounts.get("crm_ready") != 0):
        raise ValueError("EIS_HEALTH_INVALID_COUNTS")
    items = triage.get("rows")
    if (not isinstance(items, list) or len(items) != current
            or auditcounts.get("unique_notices") != current
            or auditcounts.get("official_tender_research_notices") != current
            or auditcounts.get("mikhail_sme_b2b_ready") != 0):
        raise ValueError("EIS_HEALTH_TRIAGE_MISMATCH")
    all_urls = set()
    for row in items:
        if not isinstance(row, dict):
            raise ValueError("EIS_HEALTH_INVALID_NOTICE_ROW")
        url = row.get("official_notice_url")
        if not isinstance(url, str) or _url(url, feed=False) != url or url in all_urls:
            raise ValueError("EIS_HEALTH_INVALID_OR_DUPLICATE_NOTICE")
        all_urls.add(url)
        if (row.get("workstream") !=
              "OFFICIAL_TENDER_OPPORTUNITY_REVIEW_NOT_MIKHAIL_SME_LEAD"
              or row.get("crm_ready") is not False
              or row.get("qualified_lead") is not False
              or row.get("outbound_authorized") is not False):
            raise ValueError("EIS_HEALTH_UNSAFE_NOTICE")
    triage_csv = _rows(run / "eis_triage.csv")
    new_csv = _rows(run / "eis_new_since_previous.csv")
    if (len(triage_csv) != current or len(new_csv) != recent
            or {row.get("official_notice_url") for row in triage_csv} != all_urls):
        raise ValueError("EIS_HEALTH_CSV_COUNT_MISMATCH")
    new_urls = {row.get("official_notice_url") for row in new_csv}
    if (len(new_urls) != recent or not new_urls.issubset(all_urls)
            or (baseline and recent != 0)):
        raise ValueError("EIS_HEALTH_NEW_NOTICE_SET_INVALID")
    for row in new_csv:
        if (row.get("qualified_lead") != "False"
                or row.get("crm_ready") != "False"
                or row.get("outbound_authorized") != "False"):
            raise ValueError("EIS_HEALTH_NEW_CSV_UNSAFE")
    connection = sqlite3.connect(str(dbpath), timeout=2)
    try:
        connection.execute("PRAGMA query_only=ON")
        meta = connection.execute(
            "SELECT value FROM operation_meta WHERE key='baseline_completed'"
        ).fetchone()
        if meta is None:
            raise ValueError("EIS_HEALTH_BASELINE_MISSING")
        _date(meta[0])
        stored = connection.execute(
            "SELECT official_notice_url, first_seen_on, last_seen_on "
            "FROM official_notices"
        ).fetchall()
    finally:
        connection.close()
    stored_urls = set()
    for url, first, last in stored:
        if url in stored_urls or _url(url, feed=False) != url:
            raise ValueError("EIS_HEALTH_DB_NOTICE_INVALID")
        stored_urls.add(url)
        initial, lastday = _date(first), _date(last)
        if initial > lastday or lastday > now:
            raise ValueError("EIS_HEALTH_DB_DATES_INVALID")
    if not all_urls.issubset(stored_urls) or not new_urls.issubset(stored_urls):
        raise ValueError("EIS_HEALTH_HISTORY_NOT_PERSISTED")
    return {
        "mode": "EIS_DAILY_INDEPENDENT_HEALTH_CHECK",
        "run_date": now.isoformat(),
        "latest_run": str(run),
        "official_notices_latest": current,
        "new_since_previous": recent,
        "history_unique_notices": len(stored_urls),
        "baseline_initialization": baseline,
        "read_only_checks": "PASS",
        "buyer_inn_fns_deadline_status": "NOT_VERIFIED_FROM_OFFICIAL_NOTICE",
        "mikhail_qualified_b2b_leads": 0,
        "crm_writes": "NO",
    }


def main() -> None:
    cli = argparse.ArgumentParser(description="EIS persistent DB and reports: read-only audit")
    cli.add_argument("--state-dir", required=True)
    args = cli.parse_args()
    try:
        report = verify(args.state_dir)
    except (ValueError, RuntimeError, OSError, UnicodeError, sqlite3.Error) as exc:
        print("EIS_HEALTH=BLOCKED; ERROR=" + str(exc).splitlines()[0][:90])
        raise SystemExit(2)
    print("EIS_HEALTH=" + json.dumps(report, ensure_ascii=True, sort_keys=True))
    print("EIS_HEALTH_STATUS=PASS; CRM_WRITES=NO; B2B_LEADS=0")


if __name__ == "__main__":
    main()
