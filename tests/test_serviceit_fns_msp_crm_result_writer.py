"""No real Bitrix writes: FNS MSP result safety contracts."""
from unittest.mock import Mock
import pytest

from leadpilot.serviceit_fns_msp_crm_result_writer import (
    FIELD, MspWriteBlocked, receipt_label, result_field_verified,
    rest, write_one,
)


def receipt(kind="MICRO"):
    return {
        "msp_category": kind, "published": "2026-10-10",
        "fns_egrul_verified": False, "formal_score_authorized": False,
    }


@pytest.mark.parametrize("kind, word", [
    ("MICRO", "микропредприятие"),
    ("SMALL", "малое предприятие"),
    ("MEDIUM", "среднее предприятие"),
    (None, "не найдено"),
])
def test_official_snapshot_does_not_claim_egrul(kind, word):
    msg = receipt_label(receipt(kind))
    assert word in msg and "ЕГРЮЛ/ЕГРИП" in msg and "НЕ проверены" in msg


def test_no_fns_msp_result_from_unsigned_clearance():
    for key in ("fns_egrul_verified", "formal_score_authorized"):
        bad = receipt()
        bad[key] = True
        with pytest.raises(MspWriteBlocked):
            receipt_label(bad)


def test_no_unapproved_field_or_wrong_type(monkeypatch):
    from leadpilot import serviceit_fns_msp_crm_result_writer as module
    monkeypatch.setattr(module, "rest", lambda *_a, **_k: {
        "fields": {FIELD: {"type": "integer", "isReadOnly": False}}
    })
    with pytest.raises(MspWriteBlocked):
        result_field_verified(None, "https://example.invalid")


def test_reject_multi_field_update_before_network():
    client = Mock()
    with pytest.raises(MspWriteBlocked):
        rest(client, "https://example.invalid", "crm.item.update", {
            "entityTypeId": 1062, "id": 8, "useOriginalUfNames": "Y",
            "fields": {FIELD: "status", "title": "danger"},
        })
    client.post.assert_not_called()


def test_missing_receipt_does_not_reserve_or_write(monkeypatch):
    from leadpilot import serviceit_fns_msp_crm_result_writer as module
    # Valid checksum is not relevant here: use an established checksum-valid INN.
    from leadpilot.bitrix_1062_mapping import _is_valid_inn
    inn = next(s for s in ("7707083893", "7728168971", "7707329152") if _is_valid_inn(s))
    monkeypatch.setattr(module, "read_item", lambda *_: {
        module.PREFIX + "COMPANY_INN": inn, FIELD: None,
    })
    official = Mock()
    official.read_checked_receipt.return_value = None
    fence = Mock()
    assert write_one(Mock(), {}, "https://example.invalid",
                     fence, official, 8) == "HOLD_AWAITING_OFFICIAL_SNAPSHOT"
    fence.reserve.assert_not_called()
