"""Isolated Gmail V9 setup: no manual per-card action, no unsafe duplicates."""
import json
from pathlib import Path

import httpx
import pytest


URL = "https://serviseit.bitrix24.ru/rest/789/LOCAL_TEST_ONLY_TOKEN"


def test_v9_positive_control_uses_current_crm_item_and_no_historical_id():
    from inspect import getsource
    from leadpilot.serviceit_b2b_positive_control import positive_duplicate_control
    source = getsource(positive_duplicate_control)
    # The retired canary is optional; any existing signed B2B card is safe
    # only after the same exact GET/list identity and negative controls.
    assert 'for card in dict.fromkeys((CANARY_ID, *FALLBACK_CANARY_IDS))' in source
    assert '"id": card' in source
    assert 'B2B_POSITIVE_CONTROL_ITEM_UNAVAILABLE' in source
    assert '"Y"' in source and '"N"' in source
    assert 'exact_duplicate(client, base, ABSENT)' in source
    assert '4665948' not in source


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



def test_bitrix_exact_duplicate_list_uses_live_verified_no_select_mode():
    from inspect import getsource
    from leadpilot.b2b_github_to_bitrix import exact_duplicate
    source = getsource(exact_duplicate)
    assert '"filter": {"categoryId": 21, SIGNAL: source_id}' in source
    assert '"select":' not in source
    assert '"crm.item.get"' in source
    assert "CRM_DUPLICATE_EXACT_GET_UNVERIFIED" in source



def test_github_owner_readonly_duplicate_gate_cannot_modify_crm_or_expose_secret():
    wf = Path(".github/workflows/serviceit-b2b-exact-duplicate-readonly.yml").read_text("utf-8")
    probe = Path("leadpilot/serviceit_b2b_exact_duplicate_preflight.py").read_text("utf-8")
    assert "issue_comment:" in wf
    assert "SERVICEIT_B2B_EXACT_DUPLICATE_READONLY_V1" in wf
    assert "github.actor == 'Ramzes2023'" in wf
    assert "github.event.issue.number == 74" in wf
    assert "contents: read" in wf
    assert "issues: read" in wf
    assert "SERVICEIT_BITRIX_WEBHOOK: ${{ secrets.SERVICEIT_BITRIX_WEBHOOK }}" in wf
    assert "serviceit_b2b_exact_duplicate_preflight" in wf
    for forbidden in ("crm.item.add", "crm.item.update", "crm.item.delete",
                      "SERVICEIT_B2B_INGEST_ENABLED", "GIGACHAT_CREDENTIALS",
                      "secret set", "repository_dispatch"):
        assert forbidden not in wf
    assert "B2B_EXACT_DUPLICATE_REASON=" in probe
    assert "NO_NEW_CARDS_OR_AI_ACTIVATED=YES" in probe
    assert "crm.item.add" not in probe and "crm.item.update" not in probe



def test_v9_preserves_preexisting_encrypted_ledger_secret_without_rotation():
    script = Path("scripts/INSTALL_SERVICEIT_FREE_B2B_AUTOMATION_V9.ps1").read_text("utf-8")
    assert "SERVICEIT_GH_LEDGER_KEY=EXISTING_REMOTE_REUSED_NO_ROTATION" in script
    assert "$UploadLedgerKey=$false" in script
    assert "if($UploadLedgerKey){Set-GhSecret 'SERVICEIT_GH_LEDGER_KEY' $Fernet}" in script
    assert "REMOTE_FERNET_KEY_WITHOUT_LOCAL_COPY" not in script
    assert "NEW_LOCAL_KEY_CREATED" in script
    assert script.index("if($names -contains 'SERVICEIT_GH_LEDGER_KEY')") < script.index(
        "if($UploadLedgerKey){Set-GhSecret 'SERVICEIT_GH_LEDGER_KEY' $Fernet}")


def test_v9_error_cleanup_removes_remote_readback_secret_copies():
    script = Path("scripts/INSTALL_SERVICEIT_FREE_B2B_AUTOMATION_V9.ps1").read_text("utf-8")
    assert "foreach($folder in @('stage','readback','backup','extract'))" in script
    assert "Remove-Item -LiteralPath $secretFile -Force" in script
    assert "SERVICEIT_BACKUP_AND_DIAGNOSTICS" in script
