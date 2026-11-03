"""Offline E2E: authenticated Bitrix ADD event -> exact ID -> manager HOLD card.

No network, live Bitrix webhook, real GigaChat calls, source modifications
or historical-card writes.
"""
from copy import deepcopy
import json
import time

import pytest
from fastapi.testclient import TestClient

from leadpilot import serviceit_bitrix_event_autofill as event_worker
from leadpilot import serviceit_bitrix_event_autofill_app as service
from leadpilot.serviceit_bitrix_new_card_events import EventRejected, intake
from leadpilot.serviceit_enrichment_store import Store
from leadpilot.serviceit_windows_autofill import FIELDS, AI_FIELDS, PREFIX


TOKEN = "SYNTHETIC-SERVICEIT-APP-TOKEN-ONLY-123456"


class BitrixFake:
    def __init__(self, *, item_id=101, category=21, update_error=False):
        self.item = dict.fromkeys(FIELDS)
        self.item.update({
            "id": item_id, "entityTypeId": 1062, "categoryId": category,
            "title": "Услуги IT-аутсорсинга",
            "stageId": "DT1062_21:NEW",
            PREFIX+"SIGNAL_ID": "NEW-EVENT-101",
            PREFIX+"SIGNAL_KIND": "it-services",
            PREFIX+"SIGNAL_SOURCE": "new-verified-source-pending",
            PREFIX+"SIGNAL_URL": "https://example.org/notice",
            PREFIX+"STOP_REASON": "",
        })
        self.calls = []
        self.update_error = update_error

    def __call__(self, method, params):
        self.calls.append(method)
        assert method != "crm.item.list", "Event consumer must never poll entire CRM"
        if method == "crm.item.get":
            assert params["id"] == self.item["id"]
            return {"item": deepcopy(self.item)}
        if method == "crm.item.update":
            assert set(params["fields"]) == AI_FIELDS
            self.item.update(params["fields"])
            if self.update_error:
                raise TimeoutError("synthetic indeterminate CRM update")
            return {"item": deepcopy(self.item)}
        raise AssertionError(method)


def event_bytes(item_id=101, *, token=TOKEN, category_type=1062):
    return json.dumps({
        "event": "ONCRMDYNAMICITEMADD",
        "auth": {
            "domain": "serviseit.bitrix24.ru",
            "application_token": token,
        },
        "data": {"FIELDS": {
            "ID": str(item_id),
            "ENTITY_TYPE_ID": str(category_type),
        }},
    }).encode()


@pytest.fixture
def schema(monkeypatch):
    import leadpilot.serviceit_windows_autofill as autofill
    monkeypatch.setattr(autofill, "schema_limits",
                        lambda rest: {key: 12000 for key in FIELDS})


def test_one_real_event_produces_nine_manager_report_fields_without_crm_listing(tmp_path, schema):
    path = tmp_path / "new-card-events.sqlite3"
    approvals = tmp_path / "approvals"
    approvals.mkdir()
    first = intake(event_bytes(), "application/json", token=TOKEN, db_file=path)
    assert first["status"] == "QUEUED_FOR_SOURCE_CHECK"
    store = Store(path)
    crm = BitrixFake()
    result = event_worker.run_event_cycle(store, crm, approvals_dir=approvals)
    assert result["event_ids_seen"] == 1
    assert result["processed"] == [{"id": 101, "status": "HOLD_9_WRITTEN"}]
    assert "crm.item.list" not in crm.calls
    assert crm.calls.count("crm.item.update") == 1
    assert all(crm.item[x] for x in AI_FIELDS)
    assert crm.item[PREFIX+"AI_STATUS"] == "NOT_RUN_SOURCE_UNVERIFIED"
    assert "Менеджер" in crm.item[PREFIX+"AI_FULL_REPORT"] or "менеджер" in crm.item[PREFIX+"AI_FULL_REPORT"]
    assert "Следующий шаг" in crm.item[PREFIX+"AI_FULL_REPORT"]
    assert crm.item[PREFIX+"LEAD_SCORE"] is None
    assert crm.item[PREFIX+"AI_SCORE_BADGE"] is None
    assert event_worker.run_event_cycle(store, crm, approvals_dir=approvals)["event_ids_seen"] == 0
    assert crm.calls.count("crm.item.update") == 1
    assert store.metrics() == {"HOLD": 1}


def test_duplicate_webhook_does_not_duplicate_manager_report(tmp_path, schema):
    path = tmp_path / "new-card-events.sqlite3"
    approvals = tmp_path / "approvals"
    approvals.mkdir()
    first = intake(event_bytes(), "application/json", token=TOKEN, db_file=path)
    duplicate = intake(event_bytes(), "application/json", token=TOKEN, db_file=path)
    assert first["queued"] is True
    assert duplicate["status"] == "DUPLICATE_EVENT_IGNORED"
    crm = BitrixFake()
    store = Store(path)
    event_worker.run_event_cycle(store, crm, approvals_dir=approvals)
    event_worker.run_event_cycle(store, crm, approvals_dir=approvals)
    assert crm.calls.count("crm.item.update") == 1


def test_ambiguous_write_is_persistently_fenced_without_retry(tmp_path, schema):
    path = tmp_path / "new-card-events.sqlite3"
    approvals = tmp_path / "approvals"
    approvals.mkdir()
    intake(event_bytes(), "application/json", token=TOKEN, db_file=path)
    store = Store(path)
    crm = BitrixFake(update_error=True)
    one = event_worker.run_event_cycle(store, crm, approvals_dir=approvals)
    assert one["processed"][0]["status"] == "UNCERTAIN"
    assert store.metrics() == {"UNCERTAIN": 1}
    two = event_worker.run_event_cycle(store, crm, approvals_dir=approvals)
    assert two["event_ids_seen"] == 0
    assert crm.calls.count("crm.item.update") == 1


def test_wrong_callback_token_rejected_before_queue_write(tmp_path):
    path = tmp_path / "new-card-events.sqlite3"
    with pytest.raises(EventRejected, match="EVENT_TOKEN_INVALID"):
        intake(event_bytes(token="WRONG-APP-TOKEN-LONG-ENOUGH-1234"),
               "application/json", token=TOKEN, db_file=path)
    assert not path.exists()


def test_out_of_scope_type_never_enqueues(tmp_path):
    path = tmp_path / "new-card-events.sqlite3"
    result = intake(event_bytes(category_type=1044), "application/json",
                    token=TOKEN, db_file=path)
    assert result["status"] == "IGNORED_OTHER_PROCESS"
    assert not path.exists()


def test_event_category_mismatch_never_updates_foreign_card(tmp_path, schema):
    path = tmp_path / "new-card-events.sqlite3"
    approvals = tmp_path / "approvals"
    approvals.mkdir()
    intake(event_bytes(), "application/json", token=TOKEN, db_file=path)
    crm = BitrixFake(category=20)
    result = event_worker.run_event_cycle(Store(path), crm, approvals_dir=approvals)
    assert result["processed"][0]["status"] == "HOLD"
    assert "crm.item.update" not in crm.calls


def test_model_not_invoked_for_unverified_card(tmp_path, schema):
    path = tmp_path / "new-card-events.sqlite3"
    approvals = tmp_path / "approvals"
    approvals.mkdir()
    intake(event_bytes(), "application/json", token=TOKEN, db_file=path)
    def model(_):
        raise AssertionError("Unverified CRM title cannot be sent as corporate-approved AI evidence")
    result = event_worker.run_event_cycle(
        Store(path), BitrixFake(), approvals_dir=approvals, model=model)
    assert result["processed"][0]["status"] == "HOLD_9_WRITTEN"


def test_authorized_cards_go_to_existing_verified_model_worker(tmp_path, monkeypatch):
    path = tmp_path / "new-card-events.sqlite3"
    approvals = tmp_path / "approvals"
    approvals.mkdir()
    intake(event_bytes(), "application/json", token=TOKEN, db_file=path)
    (approvals / "101.json").write_text(json.dumps({"owner_approved": True}))
    called = []
    class FakeWorker:
        def __init__(self, store, rest, approvals, model, *, enabled):
            assert enabled
            called.append(("constructed", model))
        def once(self, item_id):
            called.append(("once", item_id))
            return "VERIFIED"
    monkeypatch.setattr(event_worker, "Worker", FakeWorker)
    result = event_worker.run_event_cycle(
        Store(path), BitrixFake(), approvals_dir=approvals, model=lambda x: x)
    assert result["processed"] == [{"id": 101, "status": "VERIFIED"}]
    assert called[-1] == ("once", 101)


def test_cloud_runtime_is_off_by_default_even_with_no_secrets(tmp_path):
    assert service.runtime_config({}) == {"enabled": False}
    with TestClient(service.app) as client:
        r = client.get("/integrations/serviceit/bitrix24/worker-health")
        assert r.status_code == 200
        assert r.json() == {"status": "DISABLED", "cloud_worker": False}


def test_cloud_runtime_requires_durable_mount_not_ephemeral_disk(tmp_path):
    approvals = tmp_path / "approvals"
    approvals.mkdir()
    env = {
        "SERVICEIT_EVENT_AUTOFILL_ENABLED": service.LIVE_FLAG,
        "SERVICEIT_BITRIX_APPLICATION_TOKEN": TOKEN,
        "SERVICEIT_BITRIX_WEBHOOK": "https://serviseit.bitrix24.ru/rest/2/SYNTHETICONLY",
        "SERVICEIT_BITRIX_EVENT_QUEUE_DB": str(tmp_path / "new-card-events.sqlite3"),
        "SERVICEIT_APPROVAL_DIR": str(approvals),
        "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED": "NO",
    }
    with pytest.raises(service.RuntimeBlocked, match="DURABLE_MOUNT_REQUIRED"):
        service.runtime_config(env, is_mount=lambda path: False)
    accepted = service.runtime_config(env, is_mount=lambda path: True)
    assert accepted["model_enabled"] is False
    assert accepted["db_path"] == tmp_path / "new-card-events.sqlite3"


def test_cloud_runtime_requires_separate_gigachat_privacy_consent(tmp_path):
    approvals = tmp_path / "approvals"
    approvals.mkdir()
    env = {
        "SERVICEIT_EVENT_AUTOFILL_ENABLED": service.LIVE_FLAG,
        "SERVICEIT_BITRIX_APPLICATION_TOKEN": TOKEN,
        "SERVICEIT_BITRIX_WEBHOOK": "https://serviseit.bitrix24.ru/rest/2/SYNTHETICONLY",
        "SERVICEIT_BITRIX_EVENT_QUEUE_DB": str(tmp_path / "new-card-events.sqlite3"),
        "SERVICEIT_APPROVAL_DIR": str(approvals),
        "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED": "YES",
        "AI_ENABLE": "0",
    }
    with pytest.raises(service.RuntimeBlocked, match="GIGACHAT_OPT_IN_REQUIRED"):
        service.runtime_config(env, is_mount=lambda path: True)


def test_only_existing_authenticated_receiver_exposes_bitrix_event_endpoint():
    with TestClient(service.app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["production_writer_enabled"] is False
        bad = client.post("/integrations/serviceit/bitrix24/new-card",
                          json={"event": "ONCRMDYNAMICITEMADD"})
        assert bad.status_code == 503  # No approved application token.
