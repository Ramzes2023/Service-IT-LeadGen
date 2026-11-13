"""Native VPS fence and cutover: strictly offline except disposable CI PG.

No real credentials, Bitrix, GitHub, customers or AI calls.
"""
import os
import pytest

from leadpilot import serviceit_vps_native_fence as native
from leadpilot import serviceit_bitrix_dispatch_worker as exact
from leadpilot import serviceit_vps_pg_queue as pg
from leadpilot.serviceit_vps_pg_queue import VpsBlocked
from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS


def test_exact_environment_no_cloud_credential_dependency(monkeypatch):
    from leadpilot import serviceit_vps_worker as worker
    observed = []
    def fake_secret(env, name, optional=False):
        observed.append((name, optional))
        if name == "SERVICEIT_BRAVE_SEARCH_API_KEY":
            assert optional
            return ""
        return "SYNTHETIC_SECRET_FOR_TEST_ONLY"
    monkeypatch.setattr(worker, "secret_file", fake_secret)
    env = worker.exact_environment({"SERVICEIT_AI_PROVIDER": "routerai"})
    assert {name for name, _ in observed} == {
        "SERVICEIT_BITRIX_WEBHOOK", "SERVICEIT_ROUTERAI_API_KEY",
    }
    for forbidden in ("GITHUB_TOKEN", "SERVICEIT_GH_LEDGER_KEY",
                      "SERVICEIT_BITRIX_EVENT_BRIDGE_KEY"):
        assert forbidden not in env
    assert env["SERVICEIT_ROUTERAI_FALLBACK_TO_GIGACHAT"] == "NO"


def test_native_fence_is_at_most_once_and_no_user_data_durable():
    assert "PRIMARY KEY(namespace, card_id)" in native.DDL
    assert "expected_digest BYTEA" in native.DDL
    assert "field_names TEXT[]" in native.DDL
    assert "field_values" not in native.DDL
    assert "ON CONFLICT(namespace, card_id) DO NOTHING" in (
        __import__("inspect").getsource(native.PostgresNativeFence.reserve))
    assert len(AI_FIELDS) == 9
    fields = {name: "HOLD_SYNTHETIC" for name in AI_FIELDS}
    salt = b"f" * 16
    assert native.digested(fields, salt) == native.digested(dict(reversed(list(fields.items()))), salt)
    assert native.digested(fields, salt) != native.digested(fields, b"p"*16)
    for card in (3, 7, 0, 2_000_000_001, "101", True):
        with pytest.raises(VpsBlocked):
            native.PostgresNativeFence.validate_card(card)


def test_native_gate_blocks_without_cutover_before_network_or_pg(monkeypatch):
    class Refuses:
        def __getattr__(self, _):
            raise AssertionError("NO DB/HTTP/CRM ALLOWED")
    with pytest.raises(VpsBlocked, match="CUTOVER_GATES"):
        exact.run_vps_native(101, {}, client=Refuses(), connection=Refuses())
    assert "GITHUB_TOKEN" not in __import__("inspect").getsource(exact.run_vps_native)
    assert "gh_ledger_key" not in __import__("inspect").getsource(exact.run_vps_native).lower()


@pytest.mark.skipif(os.getenv("SERVICEIT_PG_CI_EPHEMERAL") != "YES",
                    reason="only disposable CI postgres")
def test_postgres_real_native_receipt_prewrite_checksum_and_replay(monkeypatch):
    monkeypatch.setattr(pg, "secret_file",
                        lambda env, name: "synthetic-ci-pg-password")
    with pg.db({}) as connection:
        with connection.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS serviceit_vps_native_receipts")
        native.migrate_native(connection)
        fields = {name: "SYNTHETIC_HOLD_FIELD" for name in AI_FIELDS}
        one = native.PostgresNativeFence(connection)
        assert one.reserve(101) == "RESERVED"
        with pytest.raises(VpsBlocked, match="REUSE"):
            one.reserve(102)
        one.checkpoint_write(101, fields)
        with connection.cursor() as cur:
            cur.execute("SELECT status, cardinality(field_names), "
                        "octet_length(expected_digest) FROM serviceit_vps_native_receipts "
                        "WHERE namespace=%s AND card_id=101", (native.NAMESPACE,))
            assert cur.fetchone() == ("WRITE_INTENT", 9, 32)
        other = native.PostgresNativeFence(connection)
        assert other.reserve(101) == "ALREADY_FENCED_NO_RETRY"
        calls = []
        def correct_read(method, payload):
            calls.append(method)
            return {"item": {"id": 101, "entityTypeId": 1062, "categoryId": 21, **fields}}
        assert other.reconcile(101, correct_read) == "EXACT_TARGET_READBACK"
        assert calls == ["crm.item.get"]
        def different_read(method, payload):
            return {"item": {"id": 101, "entityTypeId": 1062, "categoryId": 21,
                             **{**fields, sorted(fields)[0]: "DIFFERENT"}}}
        assert other.reconcile(101, different_read) == "UNCERTAIN_REMAINS"
        assert one.finalize(101, "HOLD_9_WRITTEN")
        assert not one.finalize(101, "HOLD_9_WRITTEN")
        with connection.cursor() as cur:
            cur.execute("DROP TABLE serviceit_vps_native_receipts")
