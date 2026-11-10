"""Offline contract: positive source proof may not silently turn false empty into safe POST."""
import json

import httpx
import pytest

from leadpilot.serviceit_b2b_positive_control import positive_duplicate_control
from leadpilot.b2b_github_to_bitrix import BridgeBlocked


def client_for(*, stale=False, missing=False, false_empty=False, bad_negative=False):
    calls = []
    def respond(request):
        method = request.url.path.rsplit("/", 1)[-1].removesuffix(".json")
        data = json.loads(request.content)
        assert method in ("crm.item.get", "crm.item.list")
        calls.append(method)
        if method == "crm.item.get":
            if data["id"] == 3:
                return httpx.Response(200, json={"result": {"item": {
                    "id": 3, "entityTypeId": 1062, "categoryId": 21,
                    "UF_CRM_13_SVCIT_SIGNAL_ID": None if missing else "SVCIT-KNOWN-3",
                }}})
            if data["id"] == 10:
                return httpx.Response(200, json={"result": {"item": {
                    "id": 10, "entityTypeId": 1062, "categoryId": 21,
                    "UF_CRM_13_SVCIT_SIGNAL_ID": "B2B-CENTER-999999999999",
                }}})
            raise AssertionError("unexpected GET")
        mode = data["useOriginalUfNames"]
        field = ("UF_CRM_13_SVCIT_SIGNAL_ID" if mode == "Y"
                 else "ufCrm13SvcitSignalId")
        source = data["filter"][field]
        if source == "SVCIT-KNOWN-3":
            items = [] if false_empty else [{"id": 3, "categoryId": 21, field: source}]
        elif source == "B2B-CENTER-999999999999":
            items = ([{"id": 10, "categoryId": 21, field: source}]
                     if bad_negative else [])
        else:
            raise AssertionError("wrong source")
        return httpx.Response(200, json={"result": {"items": items},
                                         "total": len(items)})
    return httpx.Client(transport=httpx.MockTransport(respond)), calls


def test_live_canary_checks_both_name_modes_and_absent_source():
    client, calls = client_for()
    with client:
        assert positive_duplicate_control(client, "https://example.invalid/rest")
    assert calls == ["crm.item.get", "crm.item.list", "crm.item.list", "crm.item.list"]


@pytest.mark.parametrize("kwargs", [
    {"missing": True},
    {"false_empty": True},
    {"bad_negative": True},
])
def test_unverifiable_positive_or_negative_never_opens_write_gate(kwargs):
    client, _ = client_for(**kwargs)
    with client, pytest.raises(BridgeBlocked):
        positive_duplicate_control(client, "https://example.invalid/rest")
