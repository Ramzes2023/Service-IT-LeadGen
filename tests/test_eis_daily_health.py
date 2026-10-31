"""Cross-check live-style EIS run folders, history and CSV completely offline."""
import csv
import json
from pathlib import Path
import sqlite3

import pytest

from leadpilot import eis_daily_health as h
from test_eis_daily import original, fake_download, TODAY
from leadpilot import eis_daily


def test_initial_108_style_baseline_has_zero_new(tmp_path):
    source = original(tmp_path)
    state = tmp_path / "state"
    eis_daily.run_daily(source, state, today=TODAY, downloader=fake_download(1, 2))
    report = h.verify(state)
    assert report["read_only_checks"] == "PASS"
    assert report["official_notices_latest"] == 2
    assert report["new_since_previous"] == 0
    assert report["history_unique_notices"] == 2
    assert report["baseline_initialization"] is True
    assert report["mikhail_qualified_b2b_leads"] == 0
    assert report["crm_writes"] == "NO"


def test_repeat_zero_and_third_run_one_new_notice(tmp_path):
    source = original(tmp_path)
    state = tmp_path / "state"
    eis_daily.run_daily(source, state, today=TODAY, downloader=fake_download(1))
    eis_daily.run_daily(source, state, today=TODAY, downloader=fake_download(1))
    result = h.verify(state)
    assert result["baseline_initialization"] is False
    assert result["new_since_previous"] == 0
    eis_daily.run_daily(source, state, today=TODAY, downloader=fake_download(1, 2))
    result = h.verify(state)
    assert result["official_notices_latest"] == 2
    assert result["new_since_previous"] == 1
    assert result["history_unique_notices"] == 2


def test_empty_baseline_and_next_new_notice(tmp_path):
    source = original(tmp_path)
    state = tmp_path / "state"
    eis_daily.run_daily(source, state, today=TODAY, downloader=fake_download())
    assert h.verify(state)["official_notices_latest"] == 0
    eis_daily.run_daily(source, state, today=TODAY, downloader=fake_download(3))
    result = h.verify(state)
    assert result["new_since_previous"] == 1
    assert result["history_unique_notices"] == 1


def test_sqlite_missing_or_tampered_blocks_health(tmp_path):
    source = original(tmp_path)
    state = tmp_path / "state"
    daily = eis_daily.run_daily(source, state, today=TODAY,
                               downloader=fake_download(1))
    db = Path(daily["state_db"])
    db.unlink()
    with pytest.raises(ValueError, match="STATE_DB_MISSING"):
        h.verify(state)


def test_tampered_latest_notice_set_is_rejected(tmp_path):
    source = original(tmp_path)
    state = tmp_path / "state"
    daily = eis_daily.run_daily(source, state, today=TODAY,
                               downloader=fake_download(1))
    triage = Path(daily["run_dir"]) / "eis_triage.json"
    content = json.loads(triage.read_text(encoding="utf8"))
    content["rows"][0]["crm_ready"] = True
    triage.write_text(json.dumps(content), encoding="utf8")
    with pytest.raises(ValueError, match="UNSAFE_NOTICE"):
        h.verify(state)


def test_latest_run_csv_count_tamper_detected(tmp_path):
    source = original(tmp_path)
    state = tmp_path / "state"
    daily = eis_daily.run_daily(source, state, today=TODAY,
                               downloader=fake_download(1))
    path = Path(daily["triage_csv"])
    with path.open("a", encoding="utf-8") as handle:
        handle.write("BAD,FAKE,UNTRUSTED\n")
    with pytest.raises(ValueError, match="CSV_COUNT"):
        h.verify(state)


def test_health_rejects_locked_running_source(tmp_path):
    source = original(tmp_path)
    state = tmp_path / "state"
    eis_daily.run_daily(source, state, today=TODAY, downloader=fake_download(1))
    lock = state / ".eis_daily.lock"
    lock.write_text("other instance")
    with pytest.raises(ValueError, match="RUN_IN_PROGRESS"):
        h.verify(state)


def test_cli_success_and_error_status(tmp_path, monkeypatch, capsys):
    import sys
    source = original(tmp_path)
    state = tmp_path / "state"
    eis_daily.run_daily(source, state, today=TODAY, downloader=fake_download(1))
    monkeypatch.setattr(sys, "argv", ["eis_daily_health", "--state-dir", str(state)])
    h.main()
    output = capsys.readouterr().out
    assert "EIS_HEALTH_STATUS=PASS" in output and "CRM_WRITES=NO" in output
    monkeypatch.setattr(sys, "argv", ["eis_daily_health", "--state-dir",
                                     str(tmp_path / "not_a_state")])
    with pytest.raises(SystemExit) as error:
        h.main()
    assert error.value.code == 2
    assert "EIS_HEALTH=BLOCKED" in capsys.readouterr().out
