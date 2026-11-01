"""No source/Bitrix24 involved: local manager task and follow-up demo."""
from datetime import date, timedelta
import sqlite3

import pytest

from leadpilot.core import ingest, suppress
from leadpilot import manager_workspace_demo as mw
from leadpilot.workflow_ops import review

TODAY = date(2026, 10, 31)


def setup(tmp_path, existing=False, source_name="synthetic-fixture"):
    db = tmp_path / "demo.sqlite3"
    mw.initialize(db)
    record = ingest(db, {
        "inn": "7700000009", "name": "DEMO SYNTHETIC COMPANY",
        "region": "Москва", "industry": "розничная торговля",
        "existing_customer": existing, "source_name": source_name,
        "source_url": "https://example.invalid/demo",
        "signal_type": "procurement", "evidence": "Синтетический спрос",
        "signal_date": TODAY.isoformat(),
    })
    with sqlite3.connect(db) as conn:
        signal_id = conn.execute(
            "SELECT id FROM signals WHERE company_id=?", (record["company_id"],)
        ).fetchone()[0]
    return db, record["company_id"], signal_id


def approved(db, company, signal):
    review(db, company, signal, decision="relevant", reviewer_role="manager",
           reason="Проверка синтетического сигнала менеджером", today=TODAY)


def assign(db, company, signal, due=None):
    return mw.assign(
        db, company, signal, assignee_label="DEMO-MANAGER-1",
        next_action="Синтетическая задача для демонстрации",
        due=due or TODAY + timedelta(days=1), today=TODAY,
    )


def test_synthetic_task_created_once_and_has_followup(tmp_path):
    db, cid, sid = setup(tmp_path)
    approved(db,cid,sid)
    task = assign(db,cid,sid)
    assert task["status"] == "DEMO_TASK_CREATED_LOCALLY"
    again = assign(db,cid,sid)
    assert again["status"] == "DEMO_TASK_ALREADY_EXISTS"
    assert again["task_id"] == task["task_id"]
    feedback = mw.record_simulated_result(
        db, task["task_id"], event_id="DEMO-EVENT-001",
        outcome="FOLLOW_UP", note="Синтетический запрос на повторное касание",
        next_due=TODAY+timedelta(days=3), today=TODAY,
    )
    assert feedback["state"] == "FOLLOW_UP"
    assert feedback["real_customer_contact"] is False
    assert mw.overview(db)["outcomes"]["FOLLOW_UP"] == 1
    assert mw.record_simulated_result(
        db, task["task_id"], event_id="DEMO-EVENT-001",
        outcome="FOLLOW_UP", note="Синтетический запрос на повторное касание",
        next_due=TODAY+timedelta(days=3), today=TODAY,
    )["status"] == "DEMO_EVENT_ALREADY_RECORDED"
    with pytest.raises(ValueError):
        mw.record_simulated_result(
            db, task["task_id"], event_id="DEMO-EVENT-001",
            outcome="NO_INTEREST", note="Синтетическое другое событие",
            today=TODAY)


def test_existing_customer_stop_and_unverified_source_are_blocked(tmp_path):
    db,cid,sid=setup(tmp_path,existing=True)
    approved(db,cid,sid)
    assert assign(db,cid,sid)["status"] == "HELD_BY_POLICY"

    db,cid,sid=setup(tmp_path/"real",source_name="eis-official-rss")
    approved(db,cid,sid)
    with pytest.raises(ValueError,match="Only synthetic"):
        assign(db,cid,sid)

    db,cid,sid=setup(tmp_path/"stop")
    approved(db,cid,sid)
    suppress(db,"7700000009","all","synthetic opt-out")
    assert assign(db,cid,sid)["status"] == "HELD_BY_POLICY"


def test_new_stop_prevents_simulated_feedback(tmp_path):
    db,cid,sid=setup(tmp_path)
    approved(db,cid,sid)
    task=assign(db,cid,sid)
    suppress(db,"7700000009","all","synthetic opt-out")
    assert mw.record_simulated_result(
        db,task["task_id"],event_id="DEMO-EVENT-002",outcome="INTERESTED",
        note="Синтетическая проверка запрета",today=TODAY,
    )["status"] == "DEMO_EVENT_POLICY_HOLD"
    assert not mw.overview(db)["outcomes"]


def test_no_contact_permission_from_automated_synthetic_notes(tmp_path):
    db,cid,sid=setup(tmp_path)
    approved(db,cid,sid)
    task=assign(db,cid,sid)
    out=mw.record_simulated_result(
        db,task["task_id"],event_id="DEMO-EVENT-003",
        outcome="INTERESTED",note="Синтетическая заинтересованность не реальная сделка",
        today=TODAY)
    assert out["state"] == "CLOSED_SIMULATED"
    assert out["crm_writes"] == "NO"
    assert mw.overview(db)["validated_revenue"] == 0
    assert mw.record_simulated_result(
        db,task["task_id"],event_id="DEMO-EVENT-004",
        outcome="FOLLOW_UP",note="Нельзя продолжить закрытую задачу",
        next_due=TODAY+timedelta(days=1),today=TODAY,
    )["status"] == "DEMO_TASK_ALREADY_CLOSED"


def test_wrong_parameters_rejected(tmp_path):
    db,cid,sid=setup(tmp_path)
    approved(db,cid,sid)
    with pytest.raises(ValueError):
        mw.assign(db,cid,sid,assignee_label="manager@example.com",
                  next_action="Тестовая задача менеджеру",
                  due=TODAY+timedelta(days=1),today=TODAY)
    task=assign(db,cid,sid)
    with pytest.raises(ValueError):
        mw.record_simulated_result(db,task["task_id"],event_id="DEMO-EVENT-005",
                                   outcome="FOLLOW_UP",note="Слишком короткий",today=TODAY)
