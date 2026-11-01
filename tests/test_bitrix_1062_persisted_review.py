"""Offline persisted review adapter: synthetic SQLite only, zero Bitrix calls."""
from datetime import date, timedelta
import json
import socket
import sqlite3

import pytest

from leadpilot.core import ingest, suppress
from leadpilot.workflow_ops import initialize, review
from leadpilot.bitrix_handoff_dryrun import build_offline_handoff, synthetic_fixture
from leadpilot.bitrix_1062_mapping import SyntheticPlanRejected
from leadpilot.bitrix_1062_persisted_review import build_persisted_synthetic_manager_plan

NOW = date(2026, 11, 1)


def fixture_report():
    return build_offline_handoff(synthetic_fixture(NOW), today=NOW)


def make_db(tmp_path, *, existing=False, source_override=None, name_override=None,
            date_override=None, signal_type="procurement"):
    db = tmp_path / "workflow.sqlite3"
    initialize(db)
    item = synthetic_fixture(NOW)[0]
    stored = ingest(db, {
        "inn": item["inn"],
        "name": name_override or item["company_name"],
        "region": item["region"],
        "source_name": source_override or item["signal_source"],
        "source_url": item["signal_url"],
        "signal_type": signal_type,
        "signal_date": date_override or item["signal_date"],
        "existing_customer": existing,
        "evidence": "Only synthetic evidence for integration test",
    })
    # The canonical offline report contains a second independent synthetic
    # signal for the same company; persist it without granting a review.
    second = synthetic_fixture(NOW)[2]
    ingest(db, {
        "inn": second["inn"],
        "name": name_override or second["company_name"],
        "region": second["region"],
        "source_name": second["signal_source"],
        "source_url": second["signal_url"],
        "signal_type": "new_store",
        "signal_date": second["signal_date"],
        "existing_customer": existing,
        "evidence": "Second synthetic event, no review",
    })
    with sqlite3.connect(db) as con:
        sid = con.execute(
            "SELECT id FROM signals WHERE company_id=? AND source_url=?",
            (stored["company_id"], item["signal_url"]),
        ).fetchone()[0]
    return db, stored["company_id"], sid


def bind(db, report=None):
    return build_persisted_synthetic_manager_plan(
        db, report or fixture_report(),
        reviewer_label="DEMO-MANAGER-1",
        today=NOW,
    )


def save_review(db, cid, sid, decision="relevant",
                reason="Синтетическое решение менеджера по сигналу"):
    return review(db, cid, sid, decision=decision,
                  reviewer_role="manager", reason=reason, today=NOW)


def test_persisted_approval_maps_only_original_signal(tmp_path):
    db, cid, sid = make_db(tmp_path)
    save_review(db, cid, sid)
    result = bind(db)
    assert result["status"] == "PERSISTED_SYNTHETIC_MANAGER_PREVIEW_ONLY"
    assert result["signal_count"] == 2
    assert result["reviewed_count"] == 1
    assert result["unreviewed_count"] == 1
    assert result["policy_held_count"] == 0
    assert result["crm_writes"] == "NO"
    assert result["local_review_is_real_crm_authorization"] is False
    assert result["customer_contact_authorized"] is False
    action = result["actions_not_sent"][0]
    assert action["params_template_without_item_id"]["entityTypeId"] == 1062
    assert action["params_template_without_item_id"]["fields"]["stageId"] == "DT1062_21:SUCCESS"
    assert action["expected_category_id"] == 21
    assert action["real_manager_authorization_verified"] is False
    assert "access_token" not in json.dumps(result)


@pytest.mark.parametrize("decision,stage", [
    ("relevant", "DT1062_21:SUCCESS"),
    ("irrelevant", "DT1062_21:FAIL"),
    ("needs_research", "DT1062_21:PREPARATION"),
])
def test_each_saved_review_decision_maps_exactly(decision, stage, tmp_path):
    db, cid, sid = make_db(tmp_path)
    save_review(db, cid, sid, decision)
    p = bind(db)
    assert p["actions_not_sent"][0]["params_template_without_item_id"]["fields"]["stageId"] == stage
    assert p["crm_writes"] == "NO"


def test_missing_review_stays_unreviewed(tmp_path):
    db, _, _ = make_db(tmp_path)
    p = bind(db)
    assert p["reviewed_count"] == 0
    assert p["actions_not_sent"] == []
    assert p["unreviewed_count"] == 2


@pytest.mark.parametrize("change", [
    {"source_override": "eis-official"},
    {"name_override": "DEMO DIFFERENT COMPANY"},
    {"date_override": "2026-10-29"},
])
def test_wrong_source_company_or_date_never_joins(change, tmp_path):
    db, cid, sid = make_db(tmp_path, **change)
    save_review(db, cid, sid)
    with pytest.raises(SyntheticPlanRejected, match="PERSISTED_SIGNAL"):
        bind(db)


@pytest.mark.parametrize("channel", ["all", "new_customer", "email"])
def test_existing_stop_list_blocks_local_action(channel, tmp_path):
    db, cid, sid = make_db(tmp_path)
    save_review(db, cid, sid)
    suppress(db, "7700000009", channel, "Demo test stop")
    p = bind(db)
    assert p["reviewed_count"] == 0
    assert p["policy_held_count"] == 2
    assert p["policy_held_not_sent"][0]["reason"] == "COMPANY_POLICY_HOLD"
    assert p["crm_writes"] == "NO"


def test_existing_client_never_produces_manager_action(tmp_path):
    db, cid, sid = make_db(tmp_path, existing=True)
    save_review(db, cid, sid)
    p = bind(db)
    assert p["reviewed_count"] == 0
    assert p["policy_held_count"] == 2


def test_phone_in_saved_reason_blocked(tmp_path):
    db, cid, sid = make_db(tmp_path)
    save_review(db, cid, sid, reason="Call +7 999 000 0000 to discuss")
    with pytest.raises(SyntheticPlanRejected, match="PERSISTED_REVIEW_NOTE_UNSAFE"):
        bind(db)


def test_mismatched_inn_or_linked_signal_never_maps(tmp_path):
    db, cid, sid = make_db(tmp_path)
    save_review(db, cid, sid)
    with sqlite3.connect(db) as con:
        con.execute("UPDATE companies SET inn='7800000002' WHERE id=?", (cid,))
    with pytest.raises(SyntheticPlanRejected, match="PERSISTED_SIGNAL_IDENTITY_NOT_UNIQUE"):
        bind(db)


def test_duplicate_underlying_signal_url_halts_mapping(tmp_path):
    db, cid, sid = make_db(tmp_path)
    save_review(db, cid, sid)
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO signals(company_id,source_name,source_url,signal_type,evidence,signal_date) "
            "SELECT company_id,source_name,source_url,'it_vacancy',evidence,signal_date "
            "FROM signals WHERE id=?",
            (sid,),
        )
    with pytest.raises(SyntheticPlanRejected, match="PERSISTED_SIGNAL_IDENTITY_NOT_UNIQUE"):
        bind(db)


def test_no_database_schema_fails_closed(tmp_path):
    with pytest.raises(SyntheticPlanRejected, match="PERSISTED_WORKFLOW_NOT_READY"):
        bind(tmp_path / "empty.sqlite3")


def test_no_network_while_binding_offline_review(tmp_path, monkeypatch):
    db, cid, sid = make_db(tmp_path)
    save_review(db, cid, sid)
    def fail(*args, **kwargs):
        raise AssertionError("EXTERNAL_NETWORK_FORBIDDEN")
    monkeypatch.setattr(socket.socket, "connect", fail)
    monkeypatch.setattr(socket, "create_connection", fail)
    p = bind(db)
    assert p["reviewed_count"] == 1
    assert p["live_adapter_activated"] is False
    assert p["source_connections"] == 0
