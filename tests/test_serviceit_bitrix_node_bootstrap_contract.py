"""Offline invariants for owner-local Node bootstrap; no network or CRM calls."""
from pathlib import Path

HELPER = Path("scripts/ENSURE_SERVICEIT_NODE_LTS.ps1").read_text("utf-8")
HEADLESS = Path("scripts/RUN_SERVICEIT_BITRIX_AI_CODEX_HEADLESS.ps1").read_text("utf-8")
INSTALLER = Path("scripts/INSTALL_SERVICEIT_BITRIX_EVENT_BRIDGE.ps1").read_text("utf-8")
WORKFLOW = Path(".github/workflows/tests.yml").read_text("utf-8")


def test_headless_and_owner_installer_both_bootstrap_node_before_use():
    for script in (HEADLESS, INSTALLER):
        assert ". (Join-Path $PSScriptRoot 'ENSURE_SERVICEIT_NODE_LTS.ps1')" in script
        assert "$Node = Ensure-ServiceITNode" in script or "$node=Ensure-ServiceITNode" in script
    assert HEADLESS.index("Ensure-ServiceITNode") < HEADLESS.index("SERVICEIT_CODEX_METHOD=HEADLESS_STDIN")
    assert INSTALLER.index("Ensure-ServiceITNode") < INSTALLER.index("GITHUB_AUTH")
    assert "NODE_NOT_FOUND_FOR_JS_BRIDGE_TESTS" not in HEADLESS
    assert "NODE_LTS_AND_NPX_REQUIRED" not in INSTALLER


def test_bootstrap_official_lts_sha256_and_winget_fallback_not_untrusted_mirrors():
    for marker in (
        "https://nodejs.org/dist/index.json",
        "https://nodejs.org/dist/",
        "SHASUMS256.txt",
        "Get-FileHash",
        "SHA256",
        "Expand-Archive",
        "win-x64.zip",
        "npm.cmd",
        "npx.cmd",
        "OFFICIAL_PORTABLE_LTS_SHA256_OK",
        "OpenJS.NodeJS.LTS",
        "SERVICEIT_NODE_BOOTSTRAP_FAILED_OFFICIAL_AND_WINGET",
    ):
        assert marker in HELPER
    assert "Invoke-Expression" not in HELPER
    assert "iwr " not in HELPER
    assert "iex " not in HELPER
    assert "http://" not in HELPER
    assert "github.com" not in HELPER


def test_node_bootstrap_does_not_bypass_production_consent():
    assert "SERVICEIT_NODE" in HELPER
    for forbidden in ("crm.item.update", "SERVICEIT_BITRIX_WEBHOOK", "bitrix-rest.dpapi",
                      "SERVICEIT_BITRIX_EVENT_AI_ENABLED", "gh secret"):
        assert forbidden not in HELPER
    assert "CORPORATE_CRM_AI_WRITE_APPROVAL_REQUIRED" in INSTALLER
    assert "OWNER_LIVE_ACTIVATION_MISSING" in INSTALLER
    assert "CANARY_NINE_AI_FIELDS_NOT_CONFIRMED_CLOUD_DISABLED_NO_REPLAY" in INSTALLER
    assert "'scripts/ENSURE_SERVICEIT_NODE_LTS.ps1'" in WORKFLOW
