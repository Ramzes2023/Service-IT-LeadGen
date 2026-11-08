"""Offline + disposable-PostgreSQL native postcall/finance receipt tests.

Never connect to real VPS, Bitrix, GitHub, accounts, or LLMs.
"""
import inspect
import os

import pytest

from leadpilot import serviceit_vps_native_actions as actions
from leadpilot import serviceit_vps_native_business as business
from leadpilot import serviceit_finance_hybrid_live_worker as finance
from leadpilot import serviceit_postcall_live_worker as postcall
from leadpilot import serviceit_vps_pg_queue as pg
from leadpilot.serviceit_vps_pg_queue import VpsBlocked


def test_business_disabled_default_never_reads_network_or_db():
    actual = business.run_once({}, client=object(), connection=object())
    assert actual == {"status": "NATIVE_BUSINESS_DISABLED", "enabled": 0,
                      "crm_writes": 0, "tasks_created": 0, "payments": 0}
    for flag in business.FLAGS.values():
        with pytest.raises(VpsBlocked, match="CUTOVER"):
            business.run_once({flag: "YES"}, client=object(), connection=object())
    with pytest.raises(VpsBlocked, match="FLAG_INVALID"):
        business.enabled({"SERVICEIT_VPS_FINANCE_ENABLED": "MAYBE"})


def test_native_postcall_and_finance_still_require_exact_scope():
    assert "NATIVE_POSTCALL_CUTOVER_NOT_READY" in inspect.getsource(postcall.run)
    assert "NATIVE_POSTCALL_HISTORIC_OR_FUTURE_CARD" in inspect.getsource(postcall._write_one)
    assert "NATIVE_FINANCE_CUTOVER_NOT_READY" in inspect.getsource(finance.run)
    assert "native_after" in inspect.signature(finance.read_deal).parameters
    assert "native_after" in inspect.signature(finance.list_won).parameters
    assert "native_connection" in inspect.signature(finance.process_deal).parameters
    assert "PostgresActionFence" in inspect.getsource(finance.action_fence)
    assert all(name in actions.OPERATIONS for name in (
        "POSTCALL", "FINANCE_REQUEST_ADD", "FINANCE_REPORT_ADD",
        "FINANCE_REPORT_UPDATE", "FINANCE_REQUEST_UPDATE"))


def test_finance_does_not_instantiate_legacy_cloud_fence_in_native_mode(monkeypatch):
    class FailCloudFence:
        def __init__(self, *args, **kwargs):
            raise AssertionError("NO GITHUB LEDGER")
    monkeypatch.setattr(finance, "FinanceFence", FailCloudFence)
    instance = finance.action_fence(
        None, {}, 15, "report-update", "a"*64, native_connection=object())
    assert isinstance(instance, actions.PostgresActionFence)
    assert instance.operation == "FINANCE_REPORT_UPDATE"
    assert instance.scope_id == 15
    assert instance.version == "a"*64


@pytest.mark.skipif(os.getenv("SERVICEIT_PG_CI_EPHEMERAL") != "YES",
                    reason="real disposable CI PostgreSQL only")
def test_native_postcall_and_finance_receipts_are_unique_and_durable(monkeypatch):
    monkeypatch.setattr(pg, "secret_file", lambda env, name: "synthetic-ci-pg-password")
    with pg.db({}) as connection:
        with connection.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS serviceit_vps_native_actions")
        actions.migrate_actions(connection)

        event = actions.PostgresActionFence(connection, "POSTCALL", 101)
        assert event.reserve(5001) == "RESERVED"
        try_same = actions.PostgresActionFence(connection, "POSTCALL", 101)
        assert try_same.reserve(5001) == "ALREADY_FENCED_NO_RETRY"
        assert event.finalize(5001, "VERIFIED") is True
        assert event.finalize(5001, "VERIFIED") is False

        same_other_card = actions.PostgresActionFence(connection, "POSTCALL", 102)
        assert same_other_card.reserve(5001) == "RESERVED"

        first_report = actions.PostgresActionFence(
            connection, "FINANCE_REPORT_UPDATE", 303, "a"*64)
        second_report = actions.PostgresActionFence(
            connection, "FINANCE_REPORT_UPDATE", 303, "b"*64)
        assert first_report.reserve(303) == "RESERVED"
        assert second_report.reserve(303) == "RESERVED"
        assert first_report.finalize(303, "UNCERTAIN")
        after_restart = actions.PostgresActionFence(
            connection, "FINANCE_REPORT_UPDATE", 303, "a"*64)
        assert after_restart.reserve(303) == "ALREADY_FENCED_NO_RETRY"
        with connection.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM serviceit_vps_native_actions")
            assert cur.fetchone()[0] == 4
            cur.execute("DROP TABLE serviceit_vps_native_actions")


def test_business_compose_opt_in_defaults_off():
    from pathlib import Path
    config=Path("deploy/serviceit-vps.compose.yml").read_text("utf8")
    for variable in business.FLAGS.values():
        assert variable + ":-NO" in config
    assert "leadpilot.serviceit_vps_native_business" in config
    assert "serviceit_gh_ledger_key" not in config
