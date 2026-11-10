"""Offline protection against hardcoded/deleted Bitrix card #3 blocking real VPS polling."""
from datetime import datetime, timezone
import json

import httpx
import pytest

from leadpilot.serviceit_bitrix_poll_fallback import (
    PollBlocked, calibrate_known_card,
)
from leadpilot.serviceit_bitrix_batch_worker import list_only

BASE = "https://serviseit.bitrix24.ru/rest/2/SYNTHETIC123456"
STAMP = datetime(2026, 11, 10, tzinfo=timezone.utc).isoformat()


def item(id):
    return {"id": id, "categoryId": 21, "createdTime": STAMP}


def setup(controls):
    calls = []

    def handle(request):
        assert request.method == "POST"
        assert request.url.path.endswith("/crm.item.list.json")
        params = json.loads(request.content)
        assert params["entityTypeId"] == 1062
        assert params["useOriginalUfNames"] == "N"
        assert params["select"] == ["id", "categoryId", "createdTime"]
        filter_ = params["filter"]
        calls.append(filter_)
        result = controls.get(json.dumps(filter_, sort_keys=True))
        assert result is not None, "unexpected REST method/filter"
        return httpx.Response(200, json={"result": result})

    return httpx.Client(transport=httpx.MockTransport(handle)), calls


def mapping(*pairs):
    return {json.dumps(key, sort_keys=True): value for key, value in pairs}


def test_missing_historical_card_calibrates_current_real_card_readonly():
    client, calls = setup(mapping(
        ({"id": 3}, {"items": [], "total": 0}),
        ({"categoryId": 21}, {"items": [item(142)], "total": 1}),
        ({"id": 142}, {"items": [item(142)], "total": 1}),
    ))
    with client:
        calibrate_known_card(lambda m, p: list_only(client, BASE, m, p))
    assert calls == [{"id": 3}, {"categoryId": 21}, {"id": 142}]


def test_fallback_rejects_unrelated_exact_id_even_if_category_list_is_valid():
    client, calls = setup(mapping(
        ({"id": 3}, {"items": [], "total": 0}),
        ({"categoryId": 21}, {"items": [item(142)], "total": 1}),
        ({"id": 142}, {"items": [item(155)], "total": 1}),
    ))
    with client, pytest.raises(PollBlocked, match="POLL_CALIBRATION_IDENTITY_INVALID"):
        calibrate_known_card(lambda m, p: list_only(client, BASE, m, p))
    assert calls[-1] == {"id": 142}


def test_fallback_missing_or_malformed_real_control_is_blocked():
    for page in ({"items": [], "total": 0},
                 {"items": [{"id": 142, "categoryId": 999, "createdTime": STAMP}], "total": 1},
                 {"items": [[item(142)]], "total": 1}):
        client, calls = setup(mapping(
            ({"id": 3}, {"items": [], "total": 0}),
            ({"categoryId": 21}, page),
        ))
        with client, pytest.raises(PollBlocked):
            calibrate_known_card(lambda m, p: list_only(client, BASE, m, p))
        assert len(calls) == 2


def test_existing_id3_control_does_not_expand_rest_calls():
    client, calls = setup(mapping(
        ({"id": 3}, {"items": [item(3)], "total": 1}),
    ))
    with client:
        calibrate_known_card(lambda m, p: list_only(client, BASE, m, p))
    assert calls == [{"id": 3}]


def test_missing_historical_card_invalid_total_fails_closed():
    client, calls = setup(mapping(
        ({"id": 3}, {"items": [], "total": 4}),
    ))
    with client, pytest.raises(PollBlocked, match="POLL_CALIBRATION_PAGINATION_INVALID"):
        calibrate_known_card(lambda m, p: list_only(client, BASE, m, p))
    assert calls == [{"id": 3}]
