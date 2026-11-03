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
        params = json.loads(request.content)
        assert params["entityTypeId"] == 1062
        if request.url.path.endswith("/crm.item.get.json"):
            assert params["id"] == 41
            # Full GET includes entityTypeId; list.select does not.
            source_seen = calls[-1]
            calls.append("GET_EXACT")
            assert source_seen in (KNOWN_EXISTING, PROBE_NONEXISTENT)
            item = {**_known(), "UF_CRM_13_SVCIT_SIGNAL_ID": source_seen}
            return httpx.Response(200, json={"result": {"item": item}})
        assert request.url.path.endswith("/crm.item.list.json")
        assert params["filter"]["categoryId"] == 21
        assert params["select"] == ["id", "categoryId", "UF_CRM_13_SVCIT_SIGNAL_ID"]
        source = params["filter"]["UF_CRM_13_SVCIT_SIGNAL_ID"]
        calls.append(source)
        assert source in (KNOWN_EXISTING, PROBE_NONEXISTENT)
        items = known_items if source == KNOWN_EXISTING else absent_items
        return httpx.Response(200, json={"result": {"items": items}, "total": len(items)})
    return handler, calls


def _known():
    return {"id": 41, "entityTypeId": 1062, "categoryId": 21,
            "UF_CRM_13_SVCIT_SIGNAL_ID": KNOWN_EXISTING}


def test_exact_duplicate_requires_positive_and_negative_proof():
    # Simulate real Bitrix list dropping entityTypeId while full GET has it.
    listed = {k: v for k, v in _known().items() if k != "entityTypeId"}
    handler, calls = _handler_factory([listed], [])
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert verified_duplicate_lookup(client, URL) == {
            "status": "B2B_EXACT_DUPLICATE_READONLY_PASS",
            "crm_writes": 0, "gigachat_calls": 0,
            "existing_found": True, "unseen_empty": True}
    assert calls == [KNOWN_EXISTING, "GET_EXACT", PROBE_NONEXISTENT]


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
    impossible_duplicate = {**_known(), "UF_CRM_13_SVCIT_SIGNAL_ID": PROBE_NONEXISTENT}
    listed = {k: v for k, v in _known().items() if k != "entityTypeId"}
    handler, calls = _handler_factory([listed], [impossible_duplicate])
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DuplicateSearchBlocked, match="NEGATIVE_DUPLICATE"):
            verified_duplicate_lookup(client, URL)
    assert calls == [KNOWN_EXISTING, "GET_EXACT", PROBE_NONEXISTENT, "GET_EXACT"]


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



def test_v9_uses_existing_verified_node_bootstrap_instead_of_winget_retry():
    script = Path("scripts/INSTALL_SERVICEIT_FREE_B2B_AUTOMATION_V9.ps1").read_text("utf-8")
    bootstrap = Path("scripts/ENSURE_SERVICEIT_NODE_LTS.ps1").read_text("utf-8")
    assert "Join-Path $PSScriptRoot 'ENSURE_SERVICEIT_NODE_LTS.ps1'" in script
    assert ". $NodeBootstrap" in script
    assert "$Node = Ensure-ServiceITNode" in script
    assert "SERVICEIT_NODE_BOOTSTRAP_INCOMPLETE" in script
    assert "SERVICEIT_SHARED_NODE_BOOTSTRAP_MISSING" in script
    assert "OpenJS.NodeJS.LTS" not in script
    assert "NODE_LTS_INSTALL_FAILED" not in script
    assert "Get-Command 'npm.cmd'" in script
    assert "Get-Command 'npx.cmd'" in script
    assert "FOUND_INSTALLED_OR_CACHED" in bootstrap
    assert "node.exe" in bootstrap and "npm.cmd" in bootstrap and "npx.cmd" in bootstrap
    assert "OFFICIAL_NODE_SHA256_MISMATCH" in bootstrap


def test_v9_node_bootstrap_is_before_cloud_secrets_and_writes():
    script = Path("scripts/INSTALL_SERVICEIT_FREE_B2B_AUTOMATION_V9.ps1").read_text("utf-8")
    assert script.index("$Node = Ensure-ServiceITNode") < script.index("Set-GhSecret 'SERVICEIT_BITRIX_WEBHOOK'")
    assert script.index("$Node = Ensure-ServiceITNode") < script.index("Set-GhVar 'SERVICEIT_B2B_INGEST_ENABLED' 'YES'")
    assert script.index("$Node = Ensure-ServiceITNode") < script.index("SERVICEIT_GMAIL_PROJECT=NEW_CREATED_ID_PERSISTED")



def test_malformed_list_blocks_without_ever_fetching_untrusted_id():
    handler, calls = _handler_factory([[{"id": 41}]], [])
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BridgeBlocked, match="CRM_ITEM_OUT_OF_SCOPE"):
            verified_duplicate_lookup(client, URL)
    assert calls == [KNOWN_EXISTING]


def test_exact_get_wrong_scope_refuses_positive_duplicate():
    def handler(request):
        params = json.loads(request.content)
        assert params["entityTypeId"] == 1062
        if request.url.path.endswith("/crm.item.list.json"):
            listed = {k: v for k, v in _known().items() if k != "entityTypeId"}
            return httpx.Response(200, json={"result": {"items": [listed]}, "total": 1})
        assert request.url.path.endswith("/crm.item.get.json")
        return httpx.Response(200, json={"result": {"item": {**_known(), "categoryId": 99}}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BridgeBlocked, match="CRM_DUPLICATE_EXACT_GET_UNVERIFIED"):
            verified_duplicate_lookup(client, URL)


def test_bitrix_list_select_complies_with_official_entity_type_exclusion():
    from inspect import getsource
    from leadpilot.b2b_github_to_bitrix import exact_duplicate
    source = getsource(exact_duplicate)
    assert '"select": ["id", "categoryId", SIGNAL]' in source
    assert '"entityTypeId", "categoryId", SIGNAL' not in source
    assert '"crm.item.get"' in source
    assert 'CRM_DUPLICATE_EXACT_GET_UNVERIFIED' in source
