"""Structural tests only; no Bitrix24, no credentials, no network."""
from datetime import date
from hashlib import sha256
import json
import socket

import pytest

from leadpilot.bitrix_1062_mapping import (
    build_synthetic_1062_plan, SyntheticPlanRejected, CONTRACT_VERSION,
    ALL_FIELD_CODES, FIELD_MAP, EXTRA_FIELDS, IDEMPOTENCY_FIELD,
)
from leadpilot.bitrix_handoff_dryrun import build_offline_handoff, synthetic_fixture

NOW = date(2026, 11, 1)


def report():
    inn, sid = "7700000009", "DEMO-ITREQ-001"
    key = "DEMO-IDEMP-" + sha256(f"{CONTRACT_VERSION}\x00{inn}\x00{sid}".encode()).hexdigest()[:32]
    logical = {
        "company_inn": inn, "company_name": "DEMO COMPANY-001", "region": "Москва", "signal_id": sid,
        "signal_date": "2026-10-31", "signal_source": "synthetic-fixture",
        "signal_url": "https://example.invalid/serviceit-demo/request/001", "suggested_service": "1С",
        "lead_score": 86, "score_version": "demo-v1",
        "manager_next_action": "Проверить синтетический запрос 1С",
    }
    op = {"status": "DRY_RUN_INTENT_ONLY", "idempotency_key": key, "logical_fields": logical,
          "crm_entity_type": "UNCONFIRMED", "actual_crm_field_codes": "UNCONFIRMED",
          "crm_writes": "NO", "real_customer_contact": False}
    return {"contract_version": CONTRACT_VERSION, "mode": "SYNTHETIC_OFFLINE_ONLY",
            "portal": "serviseit.bitrix24.ru", "crm_writes": "NO",
            "production_activated": False, "outbound_messages": 0, "real_customer_records_read": 0,
            "operations": [op]}


def test_preview_exact_1062_mapping_and_sixteen_fields():
    p = build_synthetic_1062_plan(report(), today=NOW)
    assert p["status"] == "BITRIX_1062_MAPPING_PREVIEW_ONLY"
    assert p["operation_count"] == 1 and p["field_count"] == 16
    cmd = p["requests_not_sent"][0]
    assert cmd["params"]["entityTypeId"] == 1062
    assert cmd["params"]["useOriginalUfNames"] == "Y"
    fields = cmd["params"]["fields"]
    assert fields["categoryId"] == 21
    assert fields["stageId"] == "DT1062_21:NEW"
    assert {k for k in fields if k.startswith("UF_CRM")} == ALL_FIELD_CODES
    assert len(ALL_FIELD_CODES) == len(set(FIELD_MAP.values())) + len(EXTRA_FIELDS) + 1 == 16
    assert fields[IDEMPOTENCY_FIELD] == report()["operations"][0]["idempotency_key"]
    assert fields["UF_CRM_13_SVCIT_LEAD_SCORE"] == 86
    assert fields["UF_CRM_13_SVCIT_REVIEW_DECISION"] == "Ожидает проверки менеджером"
    assert p["crm_writes"] == "NO" and p["live_adapter_activated"] is False
    assert p["source_connections"] == 0


def test_offline_dryrun_contract_matches_live_verified_schema_preview():
    intents = build_offline_handoff(synthetic_fixture(NOW), today=NOW)
    p = build_synthetic_1062_plan(intents, today=NOW)
    assert p["operation_count"] == 2
    assert len(p["requests_not_sent"]) == 2
    assert p["crm_writes"] == "NO"
    assert all(item["params"]["entityTypeId"] == 1062 for item in p["requests_not_sent"])


def test_synthetic_preview_without_any_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("NETWORK_FORBIDDEN")
    monkeypatch.setattr(socket.socket, "connect", fail)
    monkeypatch.setattr(socket, "create_connection", fail)
    p = build_synthetic_1062_plan(report(), today=NOW)
    assert p["operation_count"] == 1
    assert "webhook" not in json.dumps(p).lower()
    assert "/rest/" not in json.dumps(p)


def test_duplicate_is_never_scheduled_twice():
    r = report()
    r["operations"].append({"status": "DUPLICATE_SIMULATION_SKIPPED", "crm_writes": "NO"})
    assert build_synthetic_1062_plan(r, today=NOW)["operation_count"] == 1


def test_duplicate_unfiltered_intent_rejected():
    r = report()
    r["operations"].append(dict(r["operations"][0]))
    with pytest.raises(SyntheticPlanRejected, match="REPEATED_INTENT"):
        build_synthetic_1062_plan(r, today=NOW)


@pytest.mark.parametrize("change", [
    {"contract_version": "wrong"}, {"mode": "LIVE"}, {"crm_writes": "YES"},
    {"production_activated": True}, {"outbound_messages": 1},
    {"portal": "other.bitrix24.ru"}, {"real_customer_records_read": 1},
    {"operations": "not-list"},
])
def test_report_scope_fails_closed(change):
    r = report()
    r.update(change)
    with pytest.raises(SyntheticPlanRejected):
        build_synthetic_1062_plan(r, today=NOW)


@pytest.mark.parametrize("change", [
    {"company_inn": "7700000008"}, {"signal_id": "REAL-ABC"},
    {"signal_source": "eis-official"}, {"company_name": "Real Customer"},
    {"signal_url": "https://serviseit.bitrix24.ru/rest/demo/"},
    {"signal_date": "2026-06-01"}, {"signal_date": "2026-11-02"},
    {"lead_score": True}, {"suggested_service": "Foreign field"},
    {"manager_next_action": "email customer@example.com"},
    {"phone": "+7-999-000"},
])
def test_mutated_logical_fields_fail_closed(change):
    r = report()
    r["operations"][0]["logical_fields"].update(change)
    with pytest.raises(SyntheticPlanRejected):
        build_synthetic_1062_plan(r, today=NOW)


def test_tampered_idempotency_rejected():
    r = report()
    r["operations"][0]["idempotency_key"] = "DEMO-IDEMP-FAKE"
    with pytest.raises(SyntheticPlanRejected, match="IDEMPOTENCY_MISMATCH"):
        build_synthetic_1062_plan(r, today=NOW)


def test_customer_or_auth_fields_rejected():
    for field in ("companyId", "contactIds", "webhook", "access_token"):
        r = report()
        r["operations"][0][field] = "1"
        with pytest.raises(SyntheticPlanRejected):
            build_synthetic_1062_plan(r, today=NOW)


def test_hold_cannot_claim_write():
    r = report()
    r["operations"].append({"status": "HOLD", "crm_writes": "YES"})
    with pytest.raises(SyntheticPlanRejected):
        build_synthetic_1062_plan(r, today=NOW)
