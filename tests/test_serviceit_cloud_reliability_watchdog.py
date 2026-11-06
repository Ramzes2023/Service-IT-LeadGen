"""Synthetic no-network watchdog tests: no CRM, no external messages."""
from datetime import datetime, timedelta, timezone
import re
import pytest

from leadpilot import serviceit_cloud_reliability_watchdog as w

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
TARGET = w.Target("fake-poll.yml", "Synthetic poll", 10, 120,
                  "2026-11-04T00:00:00Z")


def entry(minutes_ago, *, result="success", event="schedule", run_id=11):
    return {
        "id": run_id, "event": event, "status": "completed",
        "conclusion": result,
        "created_at": (NOW - timedelta(minutes=minutes_ago)).isoformat(),
    }


def test_scheduled_10min_heartbeat_recent_but_sparse_is_detected():
    status = w.assess(TARGET, [
        entry(50), entry(350, run_id=10),
        entry(2, event="issue_comment", run_id=99),
    ], now=NOW)
    assert status["status"] == "SPARSE"
    assert status["seen_6h"] == 2
    assert status["expected_6h"] == 36
    assert status["maximum_gap_minutes"] == 300
    assert status["run_id"] == 11


def test_stale_even_when_last_known_cron_was_green():
    state = w.assess(TARGET, [entry(180), entry(220, run_id=9)], now=NOW)
    assert state["status"] == "STALE"
    assert state["age_minutes"] == 180


def test_failed_latest_schedule_is_incident_not_masked_by_manual_success():
    state = w.assess(TARGET, [
        entry(5, result="failure", run_id=78),
        entry(60, run_id=77),
        entry(1, event="issue_comment", run_id=79),
    ], now=NOW)
    assert state["status"] == "SCHEDULE_FAILED"
    assert state["run_id"] == 78


def test_consecutive_green_ten_minute_runs_count_as_healthy():
    runs = [entry(i * 10 + 1, run_id=i + 1) for i in range(36)]
    state = w.assess(TARGET, runs, now=NOW)
    assert state["status"] == "OK"
    assert state["seen_6h"] == 36


def test_new_workflow_has_a_grace_period_and_then_warns():
    new = w.Target("new.yml", "New", 20, 180,
                   "2026-11-06T11:17:00Z")
    assert w.assess(new, [], now=NOW)["status"] == "WARMUP"
    assert w.assess(
        new, [], now=NOW + timedelta(hours=3)
    )["status"] == "NO_SCHEDULE_RUN"


def test_intentionally_gated_formal_score_is_not_monitored():
    assert all("signed-score-writer" not in x.file for x in w.TARGETS)


def test_alert_dedup_ignores_repeated_stale_age_and_new_failure_id_counts():
    row = w.assess(TARGET, [entry(170)], now=NOW)
    aging = w.assess(TARGET, [entry(170)], now=NOW + timedelta(minutes=20))
    assert row["status"] == aging["status"] == "STALE"
    assert w.signature([row]) == w.signature([aging])
    failed_1 = w.assess(TARGET, [entry(1, result="failure", run_id=13)], now=NOW)
    failed_2 = w.assess(TARGET, [entry(1, result="failure", run_id=14)], now=NOW)
    assert w.signature([failed_1]) != w.signature([failed_2])


def test_public_report_has_no_private_crm_record_or_secret():
    state = w.assess(TARGET, [entry(180)], now=NOW)
    report = w.report([state], w.signature([state]), now=NOW)
    assert "github.com" in report
    assert "Issue" in report
    assert "crm.item.update" in report  # warning NOT to re-run it
    assert "COMPANY_INN" not in report
    assert "CLIENT_PHONE" not in report
    assert re.search(r"SERVICEIT_CLOUD_WATCHDOG_STATE_SHA256:[0-9a-f]{64}", report)


def test_publish_changes_incident_once_and_emits_one_notification(monkeypatch):
    issue = {"number": 221, "state": "closed", "body": "Initial"}
    requests = []
    def fake_github(token, method, url, payload=None):
        requests.append((method, url, payload))
        if method == "GET":
            return dict(issue)
        if method == "PATCH":
            issue.update(payload)
            return dict(issue)
        if method == "POST":
            assert "comments" in url and "@Ramzes2023" in payload["body"]
            return {"id": 123}
        raise AssertionError(method)
    monkeypatch.setattr(w, "github", fake_github)
    state = w.assess(TARGET, [entry(180)], now=NOW)
    first = w.publish("token", [state], NOW)
    assert first["issue_writes"] == 2 and first["incident_open"] is True
    assert issue["state"] == "open"
    second = w.publish("token", [state], NOW + timedelta(minutes=10))
    assert second["issue_writes"] == 0
    assert [name for name, *_ in requests].count("POST") == 1

    healthy = w.assess(
        TARGET, [entry(i * 10, run_id=i + 1) for i in range(36)],
        now=NOW,
    )
    recovery = w.publish("token", [healthy], NOW)
    assert recovery["incident_open"] is False
    assert issue["state"] == "closed"
    assert [name for name, *_ in requests].count("POST") == 2


def test_authorizes_only_trusted_main_owner_or_workflow_run():
    env = {
        "GITHUB_REPOSITORY": w.REPO, "GITHUB_REF": "refs/heads/main",
        "GITHUB_EVENT_NAME": "issue_comment", "GITHUB_ACTOR": "Ramzes2023",
    }
    event = {
        "action": "created", "repository": {"full_name": w.REPO},
        "issue": {"number": 221}, "comment": {
            "body": w.OWNER_MARKER, "user": {"login": "Ramzes2023"}},
    }
    w.authorize(env, event)
    with pytest.raises(w.WatchdogBlocked):
        w.authorize(env, dict(event, issue={"number": 148}))
    env["GITHUB_EVENT_NAME"] = "workflow_run"
    good = {
        "action": "completed",
        "workflow_run": {
            "event": "schedule", "head_branch": "main",
            "head_repository": {"full_name": w.REPO},
            "name": next(iter(w.WATCHED_NAMES)),
        }
    }
    w.authorize(env, good)
    with pytest.raises(w.WatchdogBlocked):
        w.authorize(env, dict(good, workflow_run={
            **good["workflow_run"], "head_repository": {
                "full_name": "attacker/other"},
        }))


def test_no_retry_for_github_write_methods(monkeypatch):
    from urllib.error import HTTPError
    attempts = []
    def failure(request, timeout):
        attempts.append(request.method)
        raise HTTPError(request.full_url, 503, "unavailable", {}, None)
    monkeypatch.setattr(w, "urlopen", failure)
    monkeypatch.setattr(w.time, "sleep", lambda n: None)
    with pytest.raises(w.WatchdogBlocked):
        w.github("x" * 20, "PATCH", w.API + "/issues/221",
                 {"body": "new"})
    assert attempts == ["PATCH"]
    attempts.clear()
    with pytest.raises(w.WatchdogBlocked):
        w.github("x" * 20, "GET", w.API + "/issues/221")
    assert attempts == ["GET", "GET", "GET"]
