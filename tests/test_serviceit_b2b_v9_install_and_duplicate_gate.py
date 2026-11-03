"""Isolated Gmail V9 setup: no manual per-card action, no unsafe duplicates."""
import json
from pathlib import Path

import httpx
import pytest

from leadpilot.serviceit_b2b_exact_duplicate_preflight import (
    KNOWN_EXISTING, PROBE_NONEXISTENT, verified_duplicate_lookup,
    DuplicateSearchBlocked,
)
from leadpilot.b2b_github_to_bitrix import BridgeBlocked

URL = "https://serviseit.bitrix24.ru/rest/789/LOCAL_TEST_ONLY_TOKEN"


def _handler_factory(known_items, absent_items):
    calls = []
    def handler(request):
        assert request.method == "POST"
        assert request.url.host == "serviseit.bitrix24.ru"
        assert request.url.path.endswith("/crm.item.list.json")
        params = json.loads(request.content)
        assert params["entityTypeId"] == 1062
        assert params["filter"]["categoryId"] == 21
        source = params["filter"]["UF_CRM_13_SVCIT_SIGNAL_ID"]
        calls.append(source)
        assert source in (KNOWN_EXISTING, PROBE_NONEXISTENT)
        items = known_items if source == KNOWN_EXISTING else absent_items
        return httpx.Response(200, json={"result": {"items": items, "total": len(items)}})
    return handler, calls


def _known():
    return {"id": 41, "entityTypeId": 1062, "categoryId": 21,
            "UF_CRM_13_SVCIT_SIGNAL_ID": KNOWN_EXISTING}


def test_exact_duplicate_requires_positive_and_negative_proof():
    handler, calls = _handler_factory([_known()], [])
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert verified_duplicate_lookup(client, URL) == {
            "status": "B2B_EXACT_DUPLICATE_READONLY_PASS",
            "crm_writes": 0, "gigachat_calls": 0,
            "existing_found": True, "unseen_empty": True}
    assert calls == [KNOWN_EXISTING, PROBE_NONEXISTENT]


def test_no_positive_proof_blocks_auto_creation():
    handler, calls = _handler_factory([], [])
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DuplicateSearchBlocked, match="KNOWN_DUPLICATE"):
            verified_duplicate_lookup(client, URL)
    assert calls == [KNOWN_EXISTING]


def test_nested_empty_shape_never_mistaken_for_no_duplicates():
    handler, calls = _handler_factory([[]], [])
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BridgeBlocked, match="CRM_ITEM_OUT_OF_SCOPE"):
            verified_duplicate_lookup(client, URL)
    assert calls == [KNOWN_EXISTING]


def test_negative_probe_false_positive_also_blocks():
    handler, calls = _handler_factory([_known()], [_known()])
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DuplicateSearchBlocked, match="NEGATIVE_DUPLICATE"):
            verified_duplicate_lookup(client, URL)
    assert calls == [KNOWN_EXISTING, PROBE_NONEXISTENT]


def test_v9_dedicated_project_and_approval_gates():
    source = Path("scripts/INSTALL_SERVICEIT_FREE_B2B_AUTOMATION_V9.ps1").read_text("utf-8")
    for marker in (
        "Service IT Gmail B2B Automated Intake V9",
        "Gmail-B2B-V9",
        "script-id.txt",
        "create --type standalone --title",
        "SERVICEIT_GMAIL_PROJECT=NEW_CREATED_ID_PERSISTED",
        "SERVICEIT_GMAIL_PROJECT=REUSING_PREVIOUS_V9_ONLY",
        "SERVICEIT_GMAIL_PROJECT_ID_INVALID",
        "SERVICEIT_B2B_EXACT_DUPLICATE_PROBE_BLOCKED_NO_AUTOMATION",
        "SERVICEIT_B2B_DUPLICATE_SEARCH=READONLY_VERIFIED",
        "serviceit_b2b_exact_duplicate_preflight",
        "APPS_SCRIPT_READBACK",
        "APPS_SCRIPT_READBACK_MISMATCH_",
        "ONE_TIME_INSTALL=-Apply; NO_CRM_WRITES; NO_CLOUD_ENABLE",
        "One-time Google consent for installPollingTrigger()",
    ):
        assert marker in source
    assert "UNIQUE_SERVICEIT_APPS_SCRIPT_NOT_FOUND" not in source
    assert "list-scripts" not in source
    assert "installPollingTrigger() once." not in source
    assert source.index("SERVICEIT_B2B_DUPLICATE_SEARCH=READONLY_VERIFIED") < source.index(
        "SERVICEIT_GMAIL_PROJECT=NEW_CREATED_ID_PERSISTED")
    assert source.index("APPS_SCRIPT_READBACK") < source.index(
        "Set-GhVar 'SERVICEIT_B2B_INGEST_ENABLED' 'YES'")
    assert source.index("Set-GhVar 'SERVICEIT_B2B_INGEST_ENABLED' 'NO'") < source.index(
        "serviceit_b2b_exact_duplicate_preflight")
    assert "SERVICEIT_FREE_CLOUD_ENABLED" not in source
    assert "SERVICEIT_B2B_DIRECT_AUTO_ENABLED" not in source
    assert "crm.item.update" not in source


def test_private_keys_only_owner_local_and_no_unsafe_outputs():
    source = Path("scripts/INSTALL_SERVICEIT_FREE_B2B_AUTOMATION_V9.ps1").read_text("utf-8")
    assert "Read-Dpapi" in source
    assert "ZeroFreeBSTR" in source
    assert "Write-Host $Bitrix" not in source
    assert "Write-Host $Giga" not in source
    assert "Write-Host $GitHubToken" not in source
    assert "secret set" in source
    assert "StandardInput.Write($Value)" in source
    assert "Remove-Item Env:\\SERVICEIT_BITRIX_WEBHOOK" in source
    assert "SERVICEIT_APPS_SCRIPT_V9=INSTALLED_AND_READBACK_VERIFIED" in source
