"""Mock-only complete synthetic delivery; never touches real Bitrix24."""
from datetime import date
import copy
import json

import httpx
import pytest

from leadpilot.bitrix_handoff_dryrun import build_offline_handoff, synthetic_fixture
from leadpilot.bitrix_1062_mapping import build_synthetic_1062_plan, SyntheticPlanRejected
from leadpilot.bitrix_1062_outbox import (
    initialize, enqueue_synthetic_plan, claim_next, overview,
)
from leadpilot.bitrix_1062_rest_read import Bitrix1062ReadOnly
from leadpilot.bitrix_1062_synthetic_add import (
    Bitrix1062SyntheticAddOnly, CONFIRMATION,
)
from leadpilot.bitrix_1062_synthetic_delivery import deliver_one_claimed_synthetic

WEBHOOK = "https://serviseit.bitrix24.ru/rest/123/TEST_SYNTHETIC_TOKEN_123456"
KEY_FIELD = "UF_CRM_13_SVCIT_IDEMPOTENCY_KEY"


def _setup(tmp_path):
    today = date.today()
    plan = build_synthetic_1062_plan(
        build_offline_handoff(synthetic_fixture(today), today=today), today=today
    )
    db = tmp_path / "outbox.sqlite3"
    initialize(db)
    assert enqueue_synthetic_plan(db, plan)["queued"] == 2
    claimed = claim_next(db)
    assert claimed is not None
    return db, claimed


def _flags():
    return dict(approval=CONFIRMATION, rotated_webhook_confirmed=True,
                robots_disabled_confirmed=True, authorized_test_confirmed=True)


def _fake_item(claim, *, id=401):
    fields = claim["params_preview_only"]["fields"]
    return {**fields, "id": id, "entityTypeId": 1062, "categoryId": 21,
            "stageId": "DT1062_21:NEW", "title": fields["title"],
            KEY_FIELD: claim["idempotency_key"]}


def _clients(claim, *, before=None, after=None, writer_response=None):
    counts = {"read_calls": [], "add_calls": 0}
    item = _fake_item(claim)
    def read_handler(req):
        method = req.url.path.split("/")[-1]
        assert method in {"crm.item.list.json", "crm.item.get.json"}
        args = json.loads(req.content)
        assert args["entityTypeId"] == 1062
        counts["read_calls"].append(method)
        matches = before if counts["add_calls"] == 0 else after
        if matches is None:
            matches = [] if counts["add_calls"] == 0 else [item]
        if method == "crm.item.list.json":
            assert args["filter"][KEY_FIELD] == claim["idempotency_key"]
            assert args["filter"]["categoryId"] == 21
            return httpx.Response(200, json={"result": {"items": matches}})
        assert args["id"] == item["id"]
        return httpx.Response(200, json={"result": {"item": item}})
    def write_handler(req):
        counts["add_calls"] += 1
        assert req.url.path.endswith("/crm.item.add.json")
        args = json.loads(req.content)
        assert args == claim["params_preview_only"]
        assert args["fields"]["categoryId"] == 21
        if callable(writer_response):
            return writer_response(req)
        if writer_response is not None:
            return writer_response
        return httpx.Response(200, json={"result": {"item": {"id": item["id"]}}})
    return counts, httpx.Client(transport=httpx.MockTransport(read_handler)), \
        httpx.Client(transport=httpx.MockTransport(write_handler))


def _run(db, claim, r_client, w_client, flags=None):
    return deliver_one_claimed_synthetic(
        db, claim,
        read_only=Bitrix1062ReadOnly(WEBHOOK, client=r_client),
        one_shot_add=Bitrix1062SyntheticAddOnly(WEBHOOK, client=w_client),
        **(flags if flags is not None else _flags()),
    )


def test_single_valid_synthetic_post_readback_then_durable_receipt(tmp_path):
    db, claim = _setup(tmp_path)
    calls, r_client, w_client = _clients(claim)
    with r_client, w_client:
        result = _run(db, claim, r_client, w_client)
    assert result["status"] == "SYNTHETIC_POST_VERIFIED_REQUIRES_CLEANUP"
    assert result["external_id"] == 401
    assert result["requires_authorized_test_cleanup"] is True
    assert result["production_activated"] is False
    assert calls["add_calls"] == 1
    assert calls["read_calls"] == ["crm.item.list.json", "crm.item.list.json", "crm.item.get.json"]
    assert overview(db)["states"]["VERIFIED"] == 1
    assert claim_next(db)["idempotency_key"] != claim["idempotency_key"]


def test_existing_matching_item_is_verified_without_post(tmp_path):
    db, claim = _setup(tmp_path)
    item = _fake_item(claim)
    calls, r_client, w_client = _clients(claim, before=[item])
    with r_client, w_client:
        result = _run(db, claim, r_client, w_client)
    assert result["status"] == "ALREADY_PRESENT_VERIFIED_NO_POST"
    assert calls["add_calls"] == 0
    assert overview(db)["states"]["VERIFIED"] == 1


@pytest.mark.parametrize("change", [
    {"approval": "NO"}, {"rotated_webhook_confirmed": False},
    {"robots_disabled_confirmed": False}, {"authorized_test_confirmed": False},
])
def test_permission_guard_never_sends_post_or_read(tmp_path, change):
    db, claim = _setup(tmp_path)
    calls, r_client, w_client = _clients(claim)
    with r_client, w_client:
        result = _run(db, claim, r_client, w_client, {**_flags(), **change})
    assert result["status"] == "UNCERTAIN_MANUAL_RECONCILIATION"
    assert result["reason_code"] == "TEST_AUTHORIZATION_NOT_CONFIRMED"
    assert calls["add_calls"] == 0 and calls["read_calls"] == []
    assert overview(db)["states"]["UNCERTAIN"] == 1


def test_remote_duplicate_blocks_post_and_locks_outbox(tmp_path):
    db, claim = _setup(tmp_path)
    item = _fake_item(claim)
    calls, r_client, w_client = _clients(claim, before=[item, {**item, "id": 402}])
    with r_client, w_client:
        result = _run(db, claim, r_client, w_client)
    assert result["reason_code"] == "REMOTE_DUPLICATE_OR_AMBIGUOUS"
    assert calls["add_calls"] == 0
    assert overview(db)["states"]["UNCERTAIN"] == 1


def test_timeout_after_post_never_retries(tmp_path):
    db, claim = _setup(tmp_path)
    def timeout(req):
        raise httpx.ReadTimeout("private URL is in this exception", request=req)
    calls, r_client, w_client = _clients(claim, writer_response=timeout)
    with r_client, w_client:
        result = _run(db, claim, r_client, w_client)
    assert result["reason_code"] == "SYNTHETIC_ADD_RESULT_UNCERTAIN"
    assert calls["add_calls"] == 1
    assert overview(db)["states"]["UNCERTAIN"] == 1
    assert claim_next(db)["idempotency_key"] != claim["idempotency_key"]


def test_post_response_different_id_requires_manual_reconciliation(tmp_path):
    db, claim = _setup(tmp_path)
    wrong = httpx.Response(200, json={"result": {"item": {"id": 999}}})
    calls, r_client, w_client = _clients(claim, writer_response=wrong)
    with r_client, w_client:
        result = _run(db, claim, r_client, w_client)
    assert result["reason_code"] == "POST_READBACK_ID_CONFLICT"
    assert calls["add_calls"] == 1
    assert overview(db)["states"]["UNCERTAIN"] == 1


def test_post_not_visible_yet_is_not_retried(tmp_path):
    db, claim = _setup(tmp_path)
    calls, r_client, w_client = _clients(claim, after=[])
    with r_client, w_client:
        result = _run(db, claim, r_client, w_client)
    assert result["reason_code"] == "POST_READBACK_INCOMPLETE"
    assert calls["add_calls"] == 1
    assert overview(db)["states"]["UNCERTAIN"] == 1


def test_replaying_claim_without_queued_state_blocked(tmp_path):
    db, claim = _setup(tmp_path)
    calls, r_client, w_client = _clients(claim)
    with r_client, w_client:
        first = _run(db, claim, r_client, w_client)
    assert first["status"] == "SYNTHETIC_POST_VERIFIED_REQUIRES_CLEANUP"
    calls2, r2, w2 = _clients(claim)
    with r2, w2:
        with pytest.raises(SyntheticPlanRejected, match="CLAIM_NOT_DURABLE"):
            _run(db, claim, r2, w2)
    assert calls2["add_calls"] == 0


def test_claim_payload_tampering_blocked_before_requests(tmp_path):
    db, claim = _setup(tmp_path)
    changed = copy.deepcopy(claim)
    changed["params_preview_only"]["fields"]["UF_CRM_13_SVCIT_LEAD_SCORE"] = 87
    calls, r_client, w_client = _clients(claim)
    with r_client, w_client:
        with pytest.raises(SyntheticPlanRejected):
            _run(db, changed, r_client, w_client)
    assert calls["add_calls"] == 0


def test_wrong_live_transport_type_blocked(tmp_path):
    db, claim = _setup(tmp_path)
    read_client = None
    write_client = None
    with pytest.raises(SyntheticPlanRejected, match="GATED_TRANSPORTS_REQUIRED"):
        deliver_one_claimed_synthetic(
            db, claim, read_only=read_client, one_shot_add=write_client, **_flags()
        )


def test_filter_error_blocks_post_before_creation(tmp_path):
    db, claim = _setup(tmp_path)
    calls = {"read": 0, "write": 0}
    def bad_read(_req):
        calls["read"] += 1
        return httpx.Response(400, json={"error": "INVALID_ARG_VALUE",
            "error_description": "Invalid filter"})
    def forbidden_write(_req):
        calls["write"] += 1
        raise AssertionError("must not create")
    with httpx.Client(transport=httpx.MockTransport(bad_read)) as r, \
         httpx.Client(transport=httpx.MockTransport(forbidden_write)) as w:
        result = _run(db, claim, r, w)
    assert result["reason_code"] == "EXACT_KEY_READ_PREFLIGHT_FAILED"
    assert calls == {"read": 1, "write": 0}
    assert overview(db)["states"]["UNCERTAIN"] == 1


def test_read_and_write_must_use_identical_webhook_identity(tmp_path):
    db, claim = _setup(tmp_path)
    calls, r_client, w_client = _clients(claim)
    other_webhook = "https://serviseit.bitrix24.ru/rest/123/ANOTHER_SYNTHETIC_TOKEN_123456"
    with r_client, w_client:
        result = deliver_one_claimed_synthetic(
            db, claim,
            read_only=Bitrix1062ReadOnly(WEBHOOK, client=r_client),
            one_shot_add=Bitrix1062SyntheticAddOnly(other_webhook, client=w_client),
            **_flags(),
        )
    assert result["status"] == "UNCERTAIN_MANUAL_RECONCILIATION"
    assert result["reason_code"] == "WEBHOOK_IDENTITY_MISMATCH"
    assert calls["add_calls"] == 0 and calls["read_calls"] == []
    assert overview(db)["states"]["UNCERTAIN"] == 1
