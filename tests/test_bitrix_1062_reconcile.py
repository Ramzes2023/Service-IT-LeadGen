"""Mock REST-result callback only; no actual network, webhook or writes."""
from datetime import date

import pytest

from leadpilot.bitrix_handoff_dryrun import build_offline_handoff, synthetic_fixture
from leadpilot.bitrix_1062_mapping import build_synthetic_1062_plan, SyntheticPlanRejected
from leadpilot.bitrix_1062_outbox import (
    initialize, enqueue_synthetic_plan, claim_next, mark_uncertain, overview,
)
from leadpilot.bitrix_1062_reconcile import inspect_remote_synthetic_key, reconcile_uncertain_key

NOW = date(2026, 11, 1)
KEY_FIELD = "UF_CRM_13_SVCIT_IDEMPOTENCY_KEY"


def _claim(tmp_path):
    db = tmp_path / "delivery.sqlite3"
    initialize(db)
    plan = build_synthetic_1062_plan(
        build_offline_handoff(synthetic_fixture(NOW), today=NOW), today=NOW
    )
    enqueue_synthetic_plan(db, plan)
    claim = claim_next(db)
    mark_uncertain(db, claim["idempotency_key"], reason_code="TIMEOUT")
    fields = claim["params_preview_only"]["fields"]
    item = {
        **fields,
        "id": 99, "entityTypeId": 1062, "categoryId": 21,
        "stageId": "DT1062_21:NEW", "title": fields["title"],
        KEY_FIELD: claim["idempotency_key"],
    }
    return db, claim, item


def _transport(item, *, count=1):
    seen = []
    def call(method, params):
        assert method in {"crm.item.list", "crm.item.get"}
        assert params["entityTypeId"] == 1062
        assert params["useOriginalUfNames"] == "Y"
        seen.append(method)
        if method == "crm.item.list":
            assert params["filter"]["categoryId"] == 21
            assert params["filter"][KEY_FIELD] == item[KEY_FIELD]
            return {"items": [dict(item) for _ in range(count)]}
        assert params["id"] == item["id"]
        return {"item": dict(item)}
    return call, seen


def test_readonly_remote_reconcile_exact_synthetic_item(tmp_path):
    db, claim, item = _claim(tmp_path)
    call, seen = _transport(item)
    r = reconcile_uncertain_key(db, call, claim["idempotency_key"])
    assert r == {"status": "RECONCILED_REMOTE_READBACK", "external_id": 99,
                 "crm_writes": "NO", "post_retries": 0}
    assert seen == ["crm.item.list", "crm.item.get"]
    assert overview(db)["states"]["VERIFIED"] == 1


def test_zero_visible_matches_never_resends_or_marks_verified(tmp_path):
    db, claim, item = _claim(tmp_path)
    call, seen = _transport(item, count=0)
    result = reconcile_uncertain_key(db, call, claim["idempotency_key"])
    assert result["status"] == "NO_VISIBLE_MATCH_NOT_PROOF_OF_ABSENCE"
    assert result["requires_manual_review"] is True
    assert seen == ["crm.item.list"]
    assert overview(db)["states"]["UNCERTAIN"] == 1


def test_multiple_visible_matches_fails_closed(tmp_path):
    db, claim, item = _claim(tmp_path)
    call, seen = _transport(item, count=2)
    result = reconcile_uncertain_key(db, call, claim["idempotency_key"])
    assert result["status"] == "DUPLICATE_REMOTE_CANDIDATES"
    assert seen == ["crm.item.list"]
    assert overview(db)["states"]["UNCERTAIN"] == 1


@pytest.mark.parametrize("field,value", [
    ("categoryId", 19), ("entityTypeId", 2), (KEY_FIELD, "DEMO-IDEMP-" + "a"*32),
    ("id", "123"),
])
def test_wrong_list_scope_rejected(tmp_path, field, value):
    db, claim, item = _claim(tmp_path)
    item[field] = value
    def call(method, params):
        return {"items": [item]}
    with pytest.raises(SyntheticPlanRejected):
        inspect_remote_synthetic_key(call, claim["idempotency_key"])
    assert overview(db)["states"]["UNCERTAIN"] == 1


def test_get_must_match_list_identity(tmp_path):
    db, claim, item = _claim(tmp_path)
    def call(method, params):
        if method == "crm.item.list":
            return {"items": [item]}
        return {"item": {**item, "id": 100}}
    with pytest.raises(SyntheticPlanRejected, match="REMOTE_GET_SCOPE_MISMATCH"):
        reconcile_uncertain_key(db, call, claim["idempotency_key"])
    assert overview(db)["states"]["UNCERTAIN"] == 1


def test_malformed_results_fail_closed(tmp_path):
    db, claim, item = _claim(tmp_path)
    def call(method, params):
        return {"items": None}
    with pytest.raises(SyntheticPlanRejected, match="REMOTE_ITEMS_MALFORMED"):
        reconcile_uncertain_key(db, call, claim["idempotency_key"])


def test_bad_key_before_any_call():
    called = []
    def call(method, params):
        called.append(method)
        return {}
    with pytest.raises(SyntheticPlanRejected, match="SYNTHETIC_KEY_INVALID"):
        inspect_remote_synthetic_key(call, "not-a-synthetic-key")
    assert called == []


def test_remote_item_without_demo_marker_never_matches(tmp_path):
    db, claim, item = _claim(tmp_path)
    def call(method, params):
        if method == "crm.item.list":
            return {"items": [item]}
        return {"item": {**item, "title": "Real Company"}}
    with pytest.raises(SyntheticPlanRejected, match="REMOTE_GET_SCOPE_MISMATCH"):
        reconcile_uncertain_key(db, call, claim["idempotency_key"])


def test_api_failure_does_not_reset_uncertain_state(tmp_path):
    db, claim, item = _claim(tmp_path)
    def call(method, params):
        raise TimeoutError("simulated timeout without secret")
    with pytest.raises(TimeoutError):
        reconcile_uncertain_key(db, call, claim["idempotency_key"])
    assert overview(db)["states"]["UNCERTAIN"] == 1
