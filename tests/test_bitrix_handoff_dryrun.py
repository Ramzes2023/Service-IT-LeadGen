"""No Bitrix24 token, permissions, requests or production CRM for this suite."""
from datetime import date, timedelta
import json
import socket

import pytest

from leadpilot.bitrix_handoff_dryrun import (
    CONTRACT_VERSION, UNCONFIRMED_GATES,
    build_offline_handoff, synthetic_fixture, _synthetic_card_only,
)

NOW = date(2026, 11, 1)


def _cards():
    return synthetic_fixture(NOW)


def test_three_distinct_synthetic_events_not_confused_with_companies():
    report = build_offline_handoff(_cards(), today=NOW)
    assert report["contract_version"] == CONTRACT_VERSION
    assert report["mode"] == "SYNTHETIC_OFFLINE_ONLY"
    assert report["proposed_company_count"] == 1
    assert report["independent_signal_count"] == 2
    assert report["deduplicated_count"] == 1
    assert report["held_count"] == 1
    assert report["crm_writes"] == "NO"
    assert report["outbound_messages"] == 0
    assert report["production_activated"] is False
    assert report["field_mapping_ready_for_live_write"] is False
    assert len(report["unconfirmed_requirements"]) == len(UNCONFIRMED_GATES)


def test_stable_idempotency_and_duplicate_suppression():
    data = _cards()
    one = build_offline_handoff([data[0], dict(data[0])], today=NOW)
    first, repeated = one["operations"]
    assert first["status"] == "DRY_RUN_INTENT_ONLY"
    assert repeated["status"] == "DUPLICATE_SIMULATION_SKIPPED"
    assert first["idempotency_key"] == repeated["idempotency_key"]
    assert one["independent_signal_count"] == 1
    assert "UF_CRM" not in json.dumps(one)


def test_reused_signal_id_with_changed_evidence_is_held():
    item = _cards()[0]
    changed = dict(item, signal_url="https://example.invalid/serviceit-demo/request/DIFFERENT")
    result = build_offline_handoff([item, changed], today=NOW)
    assert result["independent_signal_count"] == 1
    assert result["operations"][1]["reason"] == "CONFLICTING_SAME_SIGNAL_ID"


@pytest.mark.parametrize("change,reason", [
    ({"synthetic": False}, "NOT_EXPLICITLY_SYNTHETIC"),
    ({"inn": "7700000008"}, "VALID_TEST_INN_REQUIRED"),
    ({"existing_customer": True}, "REVIEW_OR_EXISTING_CLIENT_GATE"),
    ({"manual_review": True}, "REVIEW_OR_EXISTING_CLIENT_GATE"),
    ({"review_status": "unverified"}, "REVIEW_OR_EXISTING_CLIENT_GATE"),
    ({"stops": ["all"]}, "STOP_LIST_APPLIES"),
    ({"stops": ["email"]}, "STOP_LIST_APPLIES"),
    ({"region": "Санкт-Петербург"}, "OUTSIDE_PILOT_SCOPE"),
    ({"company_name": "Real Ltd"}, "DEMO_COMPANY_NAME_REQUIRED"),
    ({"signal_source": "eis-official"}, "NON_SYNTHETIC_EVIDENCE_FORBIDDEN"),
    ({"signal_url": "https://serviseit.bitrix24.ru/crm/lead/list/"}, "NON_SYNTHETIC_EVIDENCE_FORBIDDEN"),
    ({"signal_date": "2026-01-01"}, "SIGNAL_NOT_CURRENT"),
    ({"signal_date": "2026-12-01"}, "SIGNAL_NOT_CURRENT"),
    ({"lead_score": 105}, "SCORE_NOT_VALIDATED"),
    ({"score_version": ""}, "LOGICAL_FIELD_VALIDATION_FAILED"),
    ({"suggested_service": "CRM"}, "LOGICAL_FIELD_VALIDATION_FAILED"),
    ({"phone": "+7 999 000 00 00"}, "CUSTOMER_OR_SECRET_FIELDS_FORBIDDEN"),
    ({"webhook": "TOKEN"}, "CUSTOMER_OR_SECRET_FIELDS_FORBIDDEN"),
])
def test_fail_closed_for_non_demo_data(change, reason):
    item = dict(_cards()[0], **change)
    result = _synthetic_card_only(item, NOW)
    assert result["status"] == "HOLD"
    assert result["reason"] == reason
    assert result["crm_writes"] == "NO"


def test_no_network_even_if_connect_monkeypatched(monkeypatch):
    def fail(*_, **__):
        raise AssertionError("NETWORK_FORBIDDEN")
    monkeypatch.setattr(socket.socket, "connect", fail)
    monkeypatch.setattr(socket, "create_connection", fail)
    result = build_offline_handoff(_cards(), today=NOW)
    assert result["independent_signal_count"] == 2


def test_report_contains_no_secrets_or_contacts():
    report = build_offline_handoff(_cards(), today=NOW)
    dump = json.dumps(report, ensure_ascii=False)
    assert "CRM_WRITES=YES" not in dump
    assert "@" not in dump
    assert "crm.lead.add" not in dump
    assert "crm.item.add" not in dump
    assert "/rest/" not in dump
    assert "https://example.invalid/" in dump


def test_invalid_request_batch_size_and_input_shape():
    with pytest.raises(ValueError):
        build_offline_handoff([{}] * 251, today=NOW)
    with pytest.raises(ValueError):
        build_offline_handoff({}, today=NOW)


def test_same_company_different_signal_preserves_independent_intents():
    data = _cards()
    report = build_offline_handoff([data[0], data[2]], today=NOW)
    intents = report["operations"]
    assert intents[0]["idempotency_key"] != intents[1]["idempotency_key"]
    assert intents[0]["logical_fields"]["company_inn"] == intents[1]["logical_fields"]["company_inn"]
    assert intents[0]["logical_fields"]["signal_id"] != intents[1]["logical_fields"]["signal_id"]
