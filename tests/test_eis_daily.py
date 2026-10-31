"""Offline EIS daily production-shape acceptance: history, errors, no fake leads."""
from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3
import hashlib

import pytest

from leadpilot import eis_daily as d
from leadpilot.eis_targeted_1c import SEARCH_PHRASES
from leadpilot.eis_rss import SOURCE_NAME

TODAY = date.today()
RSS_URL = "https://zakupki.gov.ru/epz/order/extendedsearch/rss.html?searchString=1%D0%A1&fz44=on"


def original(tmp_path):
    source = tmp_path / "eis_source_valid.json"
    source.write_text(json.dumps({
        "mode": "EIS_OFFICIAL_RSS_RESEARCH_ONLY",
        "source": SOURCE_NAME, "search_rss_url": RSS_URL,
        "counts": {"items_seen": 200},
        "crm_writes": "NO", "outbound_messages": "NO"
    }), encoding="utf-8-sig")
    return source


def notice(number):
    url = ("https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html"
           f"?regNumber={int(number):020d}")
    return {
        "source_name": SOURCE_NAME,
        "source_url": url,
        "signal_id": hashlib.sha256(url.encode()).hexdigest()[:24],
        "buyer_name_rss_unverified": "ГБУ Синтетический заказчик",
        "procurement_subject_from_rss": "Оказание услуг по сопровождению 1С",
        "title": "Оказание услуг по сопровождению 1С",
        "published_on": TODAY.isoformat(),
        "buyer_inn": None,
        "crm_ready": False, "manager_approved": False,
        "outbound_authorized": False, "lead_score": None,
    }


def report(items):
    return {
        "mode": "EIS_OFFICIAL_RSS_RESEARCH_ONLY",
        "source": SOURCE_NAME, "search_rss_url": RSS_URL,
        "counts": {"items_seen": len(items),
                   "candidates_for_identity_review": len(items),
                   "qualified_small_business_leads": 0, "crm_ready": 0},
        "items": items, "crm_writes": "NO", "outbound_messages": "NO",
    }


def batch(*numbers):
    rows = [notice(n) for n in numbers]
    return {
        "mode": "EIS_TARGETED_1C_RESEARCH_ONLY",
        "profiles": [{
            "profile_id": name, "query": phrase,
            "items_seen": len(rows) if index == 0 else 0,
            "scope_rejected": 0,
            "research_candidates": len(rows) if index == 0 else 0,
            "new_unique_notices": len(rows) if index == 0 else 0,
            "rss_report": report(rows if index == 0 else []),
        } for index, (name, phrase) in enumerate(SEARCH_PHRASES)],
        "counts": {"profiles_checked": 4,
                   "unique_research_notices": len(rows),
                   "qualified_leads": 0, "crm_ready": 0},
        "buyer_fns_status": "MANUAL_EXACT_INN_REQUIRED",
        "regions_covered": "UNVERIFIED_FROM_RSS_LINK",
        "crm_writes": "NO", "outbound_messages": "NO",
    }


def fake_download(*n):
    def downloader(url, *, today=None):
        assert url == RSS_URL
        assert today == TODAY
        return batch(*n)
    return downloader


def test_baseline_then_same_notices_then_one_genuinely_new(tmp_path):
    source = original(tmp_path)
    state = tmp_path / "state"
    first = d.run_daily(source, state, today=TODAY, downloader=fake_download(1, 2))
    assert first["counts"]["baseline_initialization"] is True
    assert first["counts"]["new_notices_since_previous_success"] == 0
    assert first["counts"]["official_research_notices_in_latest_batch"] == 2
    assert Path(first["triage_csv"]).is_file()
    assert first["crm_writes"] == "NO"
    assert "NOT_MIKHAIL_SME_LEAD" in Path(first["triage_csv"]).read_text("utf-8-sig")

    second = d.run_daily(source, state, today=TODAY, downloader=fake_download(1, 2))
    assert second["counts"]["baseline_initialization"] is False
    assert second["counts"]["new_notices_since_previous_success"] == 0
    third = d.run_daily(source, state, today=TODAY,
                        downloader=fake_download(1, 2, 3))
    assert third["counts"]["new_notices_since_previous_success"] == 1
    assert "NOT_MIKHAIL_SME_LEAD" in Path(third["new_csv"]).read_text("utf-8-sig")
    with sqlite3.connect(third["state_db"]) as db:
        assert db.execute("SELECT COUNT(*) FROM official_notices").fetchone()[0] == 3
    assert third["counts"]["qualified_small_b2b_leads"] == 0


def test_source_failure_does_not_modify_database(tmp_path):
    source = original(tmp_path)
    state = tmp_path / "persistent"
    d.run_daily(source, state, today=TODAY, downloader=fake_download(1))
    dbpath = state / "eis_official_history.sqlite3"
    before = dbpath.read_bytes()

    def fail(url, *, today):
        raise RuntimeError("EIS_HTTP_RATE_LIMIT_429")
    with pytest.raises(RuntimeError, match="RATE_LIMIT_429"):
        d.run_daily(source, state, today=TODAY, downloader=fail)
    assert dbpath.read_bytes() == before
    assert not (state / ".eis_daily.lock").exists()
    assert not list(state.glob(".pending-*"))


def test_corrupt_or_untrusted_official_source_fails_before_http(tmp_path):
    src = original(tmp_path)
    doc = json.loads(src.read_text("utf-8-sig"))
    doc["crm_writes"] = "YES"
    src.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="SOURCE_NOT_TRUSTED"):
        d.run_daily(src, tmp_path / "state", today=TODAY,
                    downloader=lambda *args, **kw: pytest.fail("NO_HTTP"))


def test_lock_blocks_concurrent_execution_without_http(tmp_path):
    src = original(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    (state / ".eis_daily.lock").write_text("other run")
    with pytest.raises(ValueError, match="ANOTHER_RUN_ACTIVE"):
        d.run_daily(src, state, today=TODAY,
                    downloader=lambda *args, **kw: pytest.fail("NO_HTTP"))
    assert (state / ".eis_daily.lock").exists()


def test_buyer_unverified_and_no_public_sector_to_sme_promotion(tmp_path):
    result = d.run_daily(original(tmp_path), tmp_path / "state",
                         today=TODAY, downloader=fake_download(1))
    snapshot = json.loads((Path(result["run_dir"]) / "eis_triage.json").read_text("utf-8"))
    assert snapshot["counts"]["qualified_b2b_leads"] == 0
    assert snapshot["counts"]["crm_ready"] == 0
    assert snapshot["rows"][0]["buyer_evidence_hint"] == "POSSIBLE_PUBLIC_SECTOR_BUYER"
    assert snapshot["rows"][0]["workstream"] == (
        "OFFICIAL_TENDER_OPPORTUNITY_REVIEW_NOT_MIKHAIL_SME_LEAD")


def test_invalid_batch_does_not_persist_and_can_retry(tmp_path):
    source = original(tmp_path)
    state = tmp_path / "state"
    bad = batch(1)
    bad["profiles"][0]["rss_report"]["items"][0]["crm_ready"] = True
    with pytest.raises(ValueError, match="PREAPPROVED"):
        d.run_daily(source, state, today=TODAY,
                    downloader=lambda url, today: bad)
    assert not (state / "eis_official_history.sqlite3").exists()
    assert not (state / ".eis_daily.lock").exists()
    good = d.run_daily(source, state, today=TODAY, downloader=fake_download(1))
    assert good["counts"]["baseline_initialization"] is True


def test_empty_feed_first_pass_is_safe(tmp_path):
    result = d.run_daily(original(tmp_path), tmp_path / "state",
                         today=TODAY, downloader=fake_download())
    assert result["counts"]["official_research_notices_in_latest_batch"] == 0
    assert result["counts"]["new_notices_since_previous_success"] == 0
    assert result["counts"]["qualified_small_b2b_leads"] == 0



def test_cli_success_reports_real_state_and_no_crm(tmp_path, monkeypatch, capsys):
    import sys
    source = original(tmp_path)
    state = tmp_path / "state"
    def local_run(source_report, state_dir):
        return d.run_daily(source_report, state_dir, today=TODAY,
                           downloader=fake_download(1))
    monkeypatch.setattr(d, "run_daily", local_run)
    monkeypatch.setattr(sys, "argv", [
        "eis_daily", "--source-report", str(source),
        "--state-dir", str(state),
    ])
    d.main()
    text = capsys.readouterr().out
    assert "EIS_DAILY_STATUS=PASS; READY_B2B_LEADS=0; CRM_WRITES=NO" in text
    assert '"new_notices_since_previous_success": 0' in text
    assert (state / "eis_official_history.sqlite3").exists()


def test_cli_failure_is_nonzero_and_does_not_claim_success(tmp_path, monkeypatch, capsys):
    import sys
    def blocked(source_report, state_dir):
        raise RuntimeError("EIS_HTTP_RATE_LIMIT_429")
    monkeypatch.setattr(d, "run_daily", blocked)
    monkeypatch.setattr(sys, "argv", [
        "eis_daily", "--source-report", str(tmp_path / "invalid"),
        "--state-dir", str(tmp_path / "state"),
    ])
    with pytest.raises(SystemExit) as error:
        d.main()
    assert error.value.code == 2
    output = capsys.readouterr().out
    assert "EIS_DAILY=BLOCKED; ERROR=EIS_HTTP_RATE_LIMIT_429" in output
    assert "EIS_DAILY_STATUS=PASS" not in output
