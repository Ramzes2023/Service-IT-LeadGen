from pathlib import Path


def test_v8_installer_reads_only_dedicated_dpapi_inputs_and_never_prints_them():
    script = Path("scripts/INSTALL_SERVICEIT_FREE_B2B_AUTOMATION_V8.ps1").read_text(encoding="utf-8")
    for marker in (
        "bitrix-rest.dpapi", "gigachat.dpapi", "gigachat-scope.txt",
        "github-ledger-key.dpapi", "b2b-ingest-hmac.dpapi",
        "Read-Dpapi", "SecureStringToBSTR", "ZeroFreeBSTR",
    ):
        assert marker in script
    assert "Write-Host $Bitrix" not in script
    assert "Write-Host $Giga" not in script
    assert "Write-Host $GitHubToken" not in script
    assert "StandardInput.Write($Value)" in script
    assert "Arguments=" in script
    assert "Arguments=$Value" not in script


def test_v8_installer_requires_separate_owner_privacy_and_admin_approvals():
    script = Path("scripts/INSTALL_SERVICEIT_FREE_B2B_AUTOMATION_V8.ps1").read_text(encoding="utf-8")
    assert "OWNER_INGEST_CONSENT_MISSING" in script
    assert "GigaConsent" in script
    assert "shared manager-form update" in script
    assert "APPS_SCRIPT_OWNER_ONLY_CONFIRMATION_MISSING" in script


def test_ingest_is_enabled_only_after_apps_script_push_and_readback():
    script = Path("scripts/INSTALL_SERVICEIT_FREE_B2B_AUTOMATION_V8.ps1").read_text(encoding="utf-8")
    enable = script.index("Set-GhVar 'SERVICEIT_B2B_INGEST_ENABLED' 'YES'")
    readback = script.index("APPS_SCRIPT_READBACK")
    assert enable > readback
    assert "clasp@latest' push --force" in script
    assert "clasp@latest' pull" in script


def test_installer_does_not_enable_legacy_comment_ingest_or_broken_crm_list_poller():
    script = Path("scripts/INSTALL_SERVICEIT_FREE_B2B_AUTOMATION_V8.ps1").read_text(encoding="utf-8")
    assert "SERVICEIT_B2B_DIRECT_AUTO_ENABLED" not in script
    assert "SERVICEIT_FREE_CLOUD_ENABLED" not in script
    assert "Render" not in script
    assert "SERVICEIT_DIRECT_ID_HANDOFF_ENABLED" in script
    assert "OpenJS.NodeJS.LTS" in script
    assert "NODE_LTS_INSTALL_FAILED" in script


def test_private_google_config_is_temp_injected_and_cleaned():
    script = Path("scripts/INSTALL_SERVICEIT_FREE_B2B_AUTOMATION_V8.ps1").read_text(encoding="utf-8")
    assert "const SERVICEIT_PRIVATE_CONFIG = Object.freeze(" in script
    assert "Remove-Item -LiteralPath (Join-Path $Temp 'stage\\secrets.gs')" in script
    assert "SERVICEIT_BACKUP_AND_DIAGNOSTICS" in script
    assert "NEXT_OWNER_ACTION=Open Apps Script and run installPollingTrigger() once." in script
