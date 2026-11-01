"""Local SQLite-only outbox. No network or Bitrix24 writes."""
from datetime import date
import json
import socket
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from leadpilot.bitrix_handoff_dryrun import build_offline_handoff, synthetic_fixture
from leadpilot.bitrix_1062_mapping import build_synthetic_1062_plan, SyntheticPlanRejected
from leadpilot.bitrix_1062_outbox import (
    initialize, enqueue_synthetic_plan, claim_next, mark_uncertain,
    confirm_remote_readback, overview,
)

NOW = date(2026, 11, 1)


def _plan():
    base = build_offline_handoff(synthetic_fixture(NOW), today=NOW)
    return build_synthetic_1062_plan(base, today=NOW)


def _db(tmp_path):
    db = tmp_path / "synthetic-outbox.db"
    initialize(db)
    return db


def _item(claim, item_id=101):
    fields = claim["params_preview_only"]["fields"]
    return {
        "id": item_id, "entityTypeId": 1062, "categoryId": 21,
        "title": fields["title"], "stageId": "DT1062_21:NEW",
        "UF_CRM_13_SVCIT_IDEMPOTENCY_KEY": claim["idempotency_key"],
    }


def test_outbox_enqueue_replay_is_idempotent_and_durable(tmp_path):
    db = _db(tmp_path)
    assert enqueue_synthetic_plan(db, _plan()) == {"queued": 2, "duplicate_skipped": 0, "crm_writes": "NO"}
    again = enqueue_synthetic_plan(db, _plan())
    assert again["queued"] == 0 and again["duplicate_skipped"] == 2
    stats = overview(db)
    assert stats["states"]["QUEUED"] == 2
    assert stats["audit_events"] == 2
    assert stats["live_transport"] is False


def test_claim_is_one_way_until_remote_reconciliation(tmp_path):
    db = _db(tmp_path)
    enqueue_synthetic_plan(db, _plan())
    first = claim_next(db)
    second = claim_next(db)
    assert first and second
    assert first["idempotency_key"] != second["idempotency_key"]
    assert claim_next(db) is None
    assert overview(db)["states"]["CLAIMED"] == 2
    mark_uncertain(db, first["idempotency_key"], reason_code="HTTP_TIMEOUT")
    assert claim_next(db) is None
    assert overview(db)["states"]["UNCERTAIN"] == 1
    initialize(db)
    assert claim_next(db) is None
    assert overview(db)["states"]["CLAIMED"] == 1


def test_remote_readback_verification_requires_exact_scope_and_key(tmp_path):
    db = _db(tmp_path)
    enqueue_synthetic_plan(db, _plan())
    claim = claim_next(db)
    key = claim["idempotency_key"]
    fake = _item(claim)
    with pytest.raises(SyntheticPlanRejected, match="MISMATCH"):
        confirm_remote_readback(db, key, crm_item={**fake, "categoryId": 19})
    with pytest.raises(SyntheticPlanRejected, match="MISMATCH"):
        confirm_remote_readback(db, key, crm_item={**fake, "UF_CRM_13_SVCIT_IDEMPOTENCY_KEY": "wrong"})
    assert confirm_remote_readback(db, key, crm_item=fake) == {
        "state": "VERIFIED", "external_id": 101, "crm_writes": "NO",
    }
    assert confirm_remote_readback(db, key, crm_item=fake)["state"] == "VERIFIED"
    with pytest.raises(SyntheticPlanRejected, match="CONFLICT"):
        confirm_remote_readback(db, key, crm_item=_item(claim, item_id=102))
    assert overview(db)["states"]["VERIFIED"] == 1


def test_uncertain_receipt_can_be_reconciled_not_retried(tmp_path):
    db = _db(tmp_path)
    enqueue_synthetic_plan(db, _plan())
    claim = claim_next(db)
    mark_uncertain(db, claim["idempotency_key"], reason_code="HTTP_TIMEOUT")
    assert confirm_remote_readback(db, claim["idempotency_key"], crm_item=_item(claim))["state"] == "VERIFIED"
    assert claim_next(db) is not None
    assert claim_next(db) is None


def test_no_verified_receipt_without_claim(tmp_path):
    db = _db(tmp_path)
    plan = _plan()
    enqueue_synthetic_plan(db, plan)
    k = plan["requests_not_sent"][0]["idempotency_key"]
    item = {"id": 1, "entityTypeId": 1062, "categoryId": 21,
            "title": "fake", "stageId": "DT1062_21:NEW",
            "UF_CRM_13_SVCIT_IDEMPOTENCY_KEY": k}
    with pytest.raises(SyntheticPlanRejected, match="READBACK_STATE_INVALID"):
        confirm_remote_readback(db, k, crm_item=item)


@pytest.mark.parametrize("mutation", [
    lambda p: p.update({"category_id": 19}),
    lambda p: p.update({"portal": "other.bitrix24.ru"}),
    lambda p: p.update({"crm_writes": "YES"}),
    lambda p: p["requests_not_sent"][0]["params"]["fields"].update({"categoryId": 19}),
    lambda p: p["requests_not_sent"][0]["params"]["fields"].update({"leadScore": 900}),
    lambda p: p["requests_not_sent"][0]["params"]["fields"].pop("UF_CRM_13_SVCIT_COMPANY_INN"),
    lambda p: p["requests_not_sent"][0]["params"]["fields"].update({"UF_CRM_13_SVCIT_IDEMPOTENCY_KEY": "bad"}),
    lambda p: p["requests_not_sent"][0].update({"method_if_separately_authorized": "crm.deal.add"}),
])
def test_invalid_scope_never_enqueues(tmp_path, mutation):
    db = _db(tmp_path)
    p = _plan()
    mutation(p)
    with pytest.raises(SyntheticPlanRejected):
        enqueue_synthetic_plan(db, p)
    assert overview(db)["states"]["QUEUED"] == 0


def test_modified_duplicate_fails_and_does_not_alter_first(tmp_path):
    db = _db(tmp_path)
    original = _plan()
    enqueue_synthetic_plan(db, original)
    changed = _plan()
    changed["requests_not_sent"][0]["params"]["fields"]["UF_CRM_13_SVCIT_LEAD_SCORE"] = 88
    with pytest.raises(SyntheticPlanRejected, match="PAYLOAD_CONFLICT"):
        enqueue_synthetic_plan(db, changed)
    assert overview(db)["states"]["QUEUED"] == 2


def test_atomic_transaction_rolls_back_partial_insert(tmp_path):
    db = _db(tmp_path)
    original = _plan()
    enqueue_synthetic_plan(db, original)
    changed = _plan()
    changed["requests_not_sent"][0]["params"]["fields"]["UF_CRM_13_SVCIT_LEAD_SCORE"] = 89
    with pytest.raises(SyntheticPlanRejected):
        enqueue_synthetic_plan(db, changed)
    assert overview(db)["states"]["QUEUED"] == 2


def test_claims_safe_under_parallel_workers(tmp_path):
    db = _db(tmp_path)
    enqueue_synthetic_plan(db, _plan())
    with ThreadPoolExecutor(max_workers=6) as pool:
        claims = list(pool.map(lambda _: claim_next(db), range(6)))
    keys = [claim["idempotency_key"] for claim in claims if claim]
    assert len(keys) == len(set(keys)) == 2
    assert overview(db)["states"]["CLAIMED"] == 2


def test_no_network_while_processing(tmp_path, monkeypatch):
    db = _db(tmp_path)
    def fail(*args, **kwargs):
        raise AssertionError("NETWORK_FORBIDDEN")
    monkeypatch.setattr(socket.socket, "connect", fail)
    monkeypatch.setattr(socket, "create_connection", fail)
    enqueue_synthetic_plan(db, _plan())
    first = claim_next(db)
    mark_uncertain(db, first["idempotency_key"], reason_code="TEST_UNCERTAIN")
    assert "http" not in json.dumps(overview(db)).lower()
    assert overview(db)["crm_writes"] == "NO"


def test_operator_cannot_resume_uncertain_by_requeue(tmp_path):
    db = _db(tmp_path)
    enqueue_synthetic_plan(db, _plan())
    first = claim_next(db)
    mark_uncertain(db, first["idempotency_key"], reason_code="TIMEOUT")
    assert claim_next(db)["idempotency_key"] != first["idempotency_key"]
    assert claim_next(db) is None
