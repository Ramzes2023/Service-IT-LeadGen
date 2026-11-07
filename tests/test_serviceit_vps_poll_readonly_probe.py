"""No-network tests for the Bitrix 1062/21 VPS poll-only preflight."""
from datetime import datetime, timedelta, timezone

import pytest

from leadpilot import serviceit_vps_poll_readonly_probe as probe
from leadpilot.serviceit_bitrix_poll_fallback import PollBlocked


NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)


def setup(monkeypatch, *, historical=True, new_count=0, incorrect_time=False):
    calls = []

    def fake_list(client, base, method, args):
        assert client is not None
        assert base == "https://serviseit.bitrix24.ru/rest/1/offline/"
        assert method == "crm.item.list"
        assert args["entityTypeId"] == 1062
        assert args["select"] == ["id", "categoryId", "createdTime"]
        calls.append(args)
        if args["filter"] == {"id": 3}:
            if not historical:
                return {"items": [], "total": 0}
            return {
                "items": [{
                    "id": 3, "categoryId": 21,
                    "createdTime": (NOW - timedelta(days=10)).isoformat(),
                }],
                "total": 1,
            }
        assert args["filter"]["categoryId"] == 21
        assert args["start"] == 0
        created = NOW - (timedelta(days=2) if incorrect_time else timedelta(minutes=3))
        return {
            "items": [{
                "id": 123 + i,
                "categoryId": 21,
                "createdTime": created.isoformat(),
            } for i in range(new_count)],
            "total": new_count,
        }

    monkeypatch.setattr(probe, "secret_file", lambda *_: "not-a-real-webhook")
    monkeypatch.setattr(
        probe, "validate_webhook",
        lambda _: "https://serviseit.bitrix24.ru/rest/1/offline/",
    )
    monkeypatch.setattr(probe, "list_only", fake_list)
    return calls


def test_verified_positive_control_and_short_window(monkeypatch):
    calls = setup(monkeypatch, new_count=1)
    result = probe.inspect_api({}, client=object(), now=NOW)
    assert result["status"] == "BITRIX_1062_21_POLL_READONLY_COMPATIBLE"
    assert result["positive_control_card3"] == "PASS"
    assert result["new_card_candidates"] == 1
    assert result["crm_writes"] == 0
    assert result["queue_inserts"] == 0
    assert result["ai_calls"] == 0
    assert len(calls) == 2
    assert not any("url" in item or "phone" in item for item in calls)


def test_no_new_cards_requires_positive_control(monkeypatch):
    setup(monkeypatch, historical=True, new_count=0)
    result = probe.inspect_api({}, client=object(), now=NOW)
    assert result["new_card_candidates"] == 0
    assert result["positive_control_card3"] == "PASS"


def test_misleading_empty_calibration_blocks(monkeypatch):
    setup(monkeypatch, historical=False, new_count=0)
    with pytest.raises(PollBlocked, match="POLL_CALIBRATION_POSITIVE_MISSING"):
        probe.inspect_api({}, client=object(), now=NOW)


def test_ignored_time_filter_blocks_historical_cards(monkeypatch):
    setup(monkeypatch, historical=True, new_count=1, incorrect_time=True)
    with pytest.raises(PollBlocked, match="POLL_ITEM_BEFORE_ACTIVATION_OR_FUTURE"):
        probe.inspect_api({}, client=object(), now=NOW)


def test_non_utc_naive_clock_is_rejected(monkeypatch):
    setup(monkeypatch)
    with pytest.raises(ValueError, match="UTC_AWARE_TIME_REQUIRED"):
        probe.inspect_api({}, client=object(), now=NOW.replace(tzinfo=None))
