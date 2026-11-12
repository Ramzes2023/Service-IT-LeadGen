"""Offline VPS queue/gate/official event tests; NEVER connects to VPS or Bitrix."""
from contextlib import contextmanager
from pathlib import Path
import json

import pytest
from fastapi.testclient import TestClient

from leadpilot.serviceit_vps_pg_queue import (
    postgres_settings, VpsBlocked, CARD_MIN, DDL, claim_one, insert_new,
)
from leadpilot import serviceit_vps_worker as worker
from leadpilot import serviceit_vps_ingress as ingress

TOKEN = "OFFLINE-BITRIX-APP-TOKEN-AT-LEAST-24-CHARS"


def valid_event(item_id=50, token=TOKEN):
    return {
        "event": "ONCRMDYNAMICITEMADD",
        "data": {"FIELDS": {"ID": str(item_id), "ENTITY_TYPE_ID": "1062"}},
        "auth": {"domain": "serviseit.bitrix24.ru",
                 "application_token": token},
    }


def test_local_postgres_only_and_never_remote(monkeypatch):
    import leadpilot.serviceit_vps_pg_queue as pg
    monkeypatch.setattr(pg, "secret_file", lambda env, name: "offline")
    result = postgres_settings({})
    assert result["host"] == "127.0.0.1"
    assert result["port"] == 5432
    assert result["dbname"] == "leadgen"
    assert result["user"] == "leadgen"
    for bad in [
        {"SERVICEIT_PG_HOST": "80.66.79.156"},
        {"SERVICEIT_PG_PORT": "5433"},
        {"SERVICEIT_PG_DB": "postgres"},
        {"SERVICEIT_PG_USER": "root"},
    ]:
        with pytest.raises(VpsBlocked, match="PG_LOCAL_ONLY"):
            postgres_settings(bad)


def test_secret_paths_cannot_use_inline_env_and_passwords():
    import leadpilot.serviceit_vps_pg_queue as pg
    with pytest.raises(VpsBlocked, match="SECRET_PATH_REQUIRED"):
        pg.secret_file({"SERVICEIT_PG_PASSWORD": "PASSWORD"}, "SERVICEIT_PG_PASSWORD")
    with pytest.raises(VpsBlocked, match="SECRET_PATH_REQUIRED"):
        pg.secret_file({"SERVICEIT_PG_PASSWORD_FILE": "/etc/passwd"},
                       "SERVICEIT_PG_PASSWORD")


def test_writer_off_by_default_multiple_independent_approvals():
    flag = {
        "SERVICEIT_VPS_WRITER_MODE": "LIVE",
        "SERVICEIT_VPS_CUTOVER_AUTHORIZED": "YES",
        "SERVICEIT_VPS_OLD_GH_WRITERS_DISABLED_ACK": "YES",
        "SERVICEIT_VPS_INTAKE_ENABLED": "NO",
        "SERVICEIT_VPS_POLL_ENABLED": "YES",
        "SERVICEIT_AI_PROVIDER": "routerai",
        "SERVICEIT_ROUTERAI_PRIVACY_APPROVED": "YES_1062_21",
        "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED": "YES",
    }
    assert worker.live_permitted(flag)
    for k in flag:
        altered = dict(flag)
        altered[k] = {
            "SERVICEIT_VPS_WRITER_MODE": "READONLY",
            "SERVICEIT_VPS_INTAKE_ENABLED": "YES",
            "SERVICEIT_AI_PROVIDER": "gigachat",
        }.get(k, "NO")
        assert not worker.live_permitted(altered)
    assert not worker.live_permitted({})
    assert not worker.live_permitted(dict(flag, SERVICEIT_VPS_POLL_ENABLED="NO"))
    assert not worker.live_permitted(dict(flag, SERVICEIT_VPS_INTAKE_ENABLED="YES"))
    assert not worker.live_permitted(dict(flag, SERVICEIT_AI_PROVIDER="gigachat"))


def test_pg_sql_is_at_most_once_and_no_automatic_retry():
    assert "PRIMARY KEY" in DDL
    assert "ON CONFLICT" not in DDL
    import inspect
    assert "ON CONFLICT(card_id) DO NOTHING" in inspect.getsource(insert_new)
    assert "status='PENDING' AND attempts=0" in inspect.getsource(claim_one)
    assert "FOR UPDATE SKIP LOCKED" in inspect.getsource(claim_one)
    assert "status='UNCERTAIN'" in inspect.getsource(
        __import__("leadpilot.serviceit_vps_pg_queue", fromlist=["reconcile_stale"]).reconcile_stale)


def test_vps_readonly_mode_never_calls_exact_ai_or_card_writer(monkeypatch):
    class FakeConnection: pass

    @contextmanager
    def fake_db(_):
        yield FakeConnection()

    monkeypatch.setattr(worker, "db", fake_db)
    monkeypatch.setattr(worker, "metrics", lambda _: {"PENDING": 2})
    monkeypatch.setattr(worker, "readonly_preflight", lambda env, client: {
        "status": "READONLY_CRM_FIELD_METADATA_CONFIRMED",
        "crm_writes": 0, "ai_calls": 0})
    monkeypatch.setattr(worker, "inspect_api", lambda env, client: {
        "positive_control_card3": "PASS",
        "new_card_candidates": 0})
    monkeypatch.setattr(worker, "run_vps_native", lambda *a, **kw: pytest.fail("NO CRM WRITE"))
    import httpx
    with httpx.Client() as readonly_client:
        result = worker.run_once({}, client=readonly_client)
    assert result["migration_cutover"] == "NOT_AUTHORIZED"
    assert result["queue_counts"] == {"PENDING": 2}
    assert result["poll_positive_control"] == "PASS"
    assert result["new_card_candidates_10m"] == 0


def test_ingress_default_disabled_no_postgres_calls(monkeypatch):
    monkeypatch.delenv("SERVICEIT_VPS_INTAKE_ENABLED", raising=False)
    monkeypatch.setattr(ingress, "insert_new", lambda *a: pytest.fail("NO PG CALL"))
    with TestClient(ingress.app) as client:
        response = client.post("/integrations/serviceit/bitrix24/new-card",
                               json=valid_event())
    assert response.status_code == 503
    assert response.json()["status"] == "INGRESS_DISABLED"


def test_ingress_token_and_id_only_queue(monkeypatch):
    @contextmanager
    def fake_db(_):
        yield object()
    seen = []
    monkeypatch.setenv("SERVICEIT_VPS_INTAKE_ENABLED", "YES")
    monkeypatch.setattr(ingress, "secret_file", lambda *a: TOKEN)
    monkeypatch.setattr(ingress, "db", fake_db)
    monkeypatch.setattr(ingress, "insert_new",
                        lambda _, card: seen.append(card) or True)
    with TestClient(ingress.app) as client:
        wrong = client.post("/integrations/serviceit/bitrix24/new-card",
                            json=valid_event(token="INVALID-APP-TOKEN-" + "x"*32))
        assert wrong.status_code == 403 and seen == []
        historical = client.post("/integrations/serviceit/bitrix24/new-card",
                                 json=valid_event(item_id=3))
        assert historical.status_code == 503 and seen == []
        good = client.post("/integrations/serviceit/bitrix24/new-card",
                           json=valid_event(item_id=101))
        assert good.status_code == 202 and seen == [101]
        assert good.json() == {
            "status": "NEW_CARD_QUEUED", "queued": True, "crm_writes": 0}
        assert client.get("/health").json()["crm_writer_enabled"] is False


def test_native_vps_exact_env_does_not_read_any_legacy_github_key(monkeypatch):
    import leadpilot.serviceit_vps_pg_queue as pg
    monkeypatch.setattr(pg, "secret_file", lambda env, name, optional=False:
                        "" if optional else "SYNTHETIC_ONLY_12345678901234567890")
    monkeypatch.setattr(worker, "secret_file", lambda env, name, optional=False:
                        "" if optional else "SYNTHETIC_ONLY_12345678901234567890")
    result = worker.exact_environment({"SERVICEIT_AI_PROVIDER": "routerai"})
    assert "GITHUB_REPOSITORY" not in result
    assert "SERVICEIT_GH_LEDGER_KEY" not in result
    assert "SERVICEIT_BITRIX_EVENT_BRIDGE_KEY" not in result
    assert "GITHUB_TOKEN" not in result
    assert result["SERVICEIT_BRAVE_LLM_CONTEXT_ENABLED"] == "YES"
    assert result["SERVICEIT_PUBLIC_RESEARCH_ENABLED"] == "YES"
    assert result["SERVICEIT_CORPORATE_APPROVALS_JSON"] == "{}"
    assert "GITHUB_EVENT_NAME" not in result
