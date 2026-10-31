"""Offline acceptance of durable manager feedback/outbox. No external API."""
from datetime import date, timedelta
import sqlite3

import pytest

from leadpilot.core import ingest, suppress
from leadpilot import workflow_ops as ops

TODAY = date(2026, 10, 31)


def setup(tmp_path, *, inn="7700000009", existing=False, days=1,
          source_url="https://example.invalid/request/one"):
    db = tmp_path / "local.sqlite3"
    ops.initialize(db)
    record = ingest(db, {
        "inn": inn, "name": "Синтетическая фирма",
        "region": "Москва", "industry": "торговля",
        "existing_customer": existing, "source_name": "synthetic-fixture",
        "source_url": source_url, "signal_type": "procurement",
        "evidence": "Синтетическая заявка для проверки архитектуры",
        "signal_date": (TODAY - timedelta(days=days)).isoformat()
    })
    with sqlite3.connect(db) as con:
        sid = con.execute("SELECT id FROM signals WHERE company_id=?",
                          (record["company_id"],)).fetchone()[0]
    return db, record["company_id"], sid


def test_approval_is_feedback_not_permission_to_crm(tmp_path):
    db, cid, sid = setup(tmp_path)
    out = ops.review(db, cid, sid, decision="relevant", reviewer_role="manager",
                     reason="Подтвердить релевантность в демонстрации", today=TODAY)
    assert out["crm_writes"] == "NO"
    assert ops.stage_simulation(db, cid, sid, today=TODAY)["status"] == "QUEUED_SIMULATION_ONLY"
    summary = ops.report(db)
    assert summary["outbox"]["QUEUED"] == 1
    assert summary["customer_contact_attempts"] == 0
    assert summary["crm_writes"] == "NO"


def test_idempotent_retry_and_same_inn_new_signal(tmp_path):
    db, cid, first = setup(tmp_path)
    ops.review(db,cid,first,decision="relevant",reviewer_role="manager",
               reason="Тестовое решение по первому сигналу",today=TODAY)
    ops.stage_simulation(db,cid,first,today=TODAY)
    assert ops.deliver_simulation(db,service_available=False,today=TODAY)["simulated_retry"] == 1
    assert ops.deliver_simulation(db,service_available=True,today=TODAY)["simulated_completed"] == 1
    assert ops.deliver_simulation(db,service_available=True,today=TODAY)["simulated_completed"] == 0
    assert ops.stage_simulation(db,cid,first,today=TODAY)["crm_writes"] == "NO"
    ingest(db,{"inn":"7700000009","name":"Синтетическая фирма","region":"Москва",
               "source_name":"synthetic-fixture",
               "source_url":"https://example.invalid/request/two",
               "signal_type":"procurement","evidence":"Синтетический новый сигнал",
               "signal_date":TODAY.isoformat()})
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM companies").fetchone()[0] == 1
        new = con.execute("SELECT id FROM signals WHERE id!=?",(first,)).fetchone()[0]
    ops.review(db,cid,new,decision="relevant",reviewer_role="manager",
               reason="Тестовое решение по новому сигналу",today=TODAY)
    ops.stage_simulation(db,cid,new,today=TODAY)
    assert ops.deliver_simulation(db,service_available=True,today=TODAY)["simulated_completed"] == 1
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM workflow_outbox").fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM workflow_audit WHERE event_type='simulated_delivered'").fetchone()[0] == 2


def test_stale_signal_and_existing_customer_hold(tmp_path):
    db,cid,sid=setup(tmp_path,existing=True)
    ops.review(db,cid,sid,decision="relevant",reviewer_role="manager",
               reason="Тестовая проверка существующего клиента",today=TODAY)
    assert ops.stage_simulation(db,cid,sid,today=TODAY)["status"] == "HOLD"
    db,cid,sid=setup(tmp_path/"other",days=35)
    ops.review(db,cid,sid,decision="relevant",reviewer_role="manager",
               reason="Старый сигнал не передаём",today=TODAY)
    assert ops.stage_simulation(db,cid,sid,today=TODAY)["status"] == "HOLD"


def test_stop_before_submit_and_before_retry(tmp_path):
    db,cid,sid=setup(tmp_path)
    ops.review(db,cid,sid,decision="relevant",reviewer_role="manager",
               reason="Пример решения для запрета контакта",today=TODAY)
    ops.stage_simulation(db,cid,sid,today=TODAY)
    suppress(db,"7700000009","all","Синтетический отказ")
    result=ops.deliver_simulation(db,service_available=True,today=TODAY)
    assert result["held"] == 1
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT status FROM workflow_outbox").fetchone()[0] == "QUEUED"
    assert ops.stage_simulation(db,cid,sid,today=TODAY)["status"] == "HOLD"


def test_review_immutable_and_only_manager_or_supervisor(tmp_path):
    db,cid,sid=setup(tmp_path)
    with pytest.raises(ValueError):
        ops.review(db,cid,sid,decision="relevant",reviewer_role="researcher",
                   reason="Тестовое объяснение",today=TODAY)
    ops.review(db,cid,sid,decision="irrelevant",reviewer_role="supervisor",
               reason="Синтетическая компания нерелевантна",today=TODAY)
    assert ops.review(db,cid,sid,decision="irrelevant",reviewer_role="supervisor",
                      reason="Синтетическая компания нерелевантна",today=TODAY)["status"] == "ALREADY_RECORDED"
    with pytest.raises(ValueError):
        ops.review(db,cid,sid,decision="relevant",reviewer_role="manager",
                   reason="Пытаемся изменить историю без аудита",today=TODAY)
    assert ops.stage_simulation(db,cid,sid,today=TODAY)["status"] == "HOLD"


def test_wrong_company_signal_rejected(tmp_path):
    db,cid,sid=setup(tmp_path)
    with pytest.raises(ValueError):
        ops.review(db,cid,9999,decision="relevant",reviewer_role="manager",
                   reason="Сигнал чужой компании",today=TODAY)


def test_no_external_network_or_real_bitrix_calls(tmp_path,monkeypatch):
    db,cid,sid=setup(tmp_path)
    import socket
    monkeypatch.setattr(socket.socket,"connect",
                        lambda *args, **kwargs:pytest.fail("FORBIDDEN_EXTERNAL_NETWORK"))
    ops.review(db,cid,sid,decision="relevant",reviewer_role="manager",
               reason="Безопасный тест без сети",today=TODAY)
    ops.stage_simulation(db,cid,sid,today=TODAY)
    assert ops.deliver_simulation(db,service_available=True,today=TODAY)["mode"] == ops.MODE


def test_two_sources_one_company_never_overwrite_first_signal(tmp_path):
    db,cid,first=setup(tmp_path)
    ingest(db,{"inn":"7700000009","name":"Синтетическая фирма","region":"Москва",
               "source_name":"second-synthetic-fixture",
               "source_url":"https://example.invalid/signal/second",
               "signal_type":"new_store","evidence":"Второй синтетический сигнал",
               "signal_date":TODAY.isoformat()})
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM companies").fetchone()[0]==1
        assert con.execute("SELECT COUNT(*) FROM signals").fetchone()[0]==2


def test_no_live_crm_methods_are_present():
    import inspect
    code=inspect.getsource(ops)
    for bad in ("crm.item.add", "crm.lead.add", "api.bitrix24.ru", "httpx.post", "requests.post"):
        assert bad not in code
