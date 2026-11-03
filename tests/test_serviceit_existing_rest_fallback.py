"""Existing Bitrix REST-only fallback must remain read-only and fail closed."""
from pathlib import Path

SCRIPT = Path("scripts/PROBE_SERVICEIT_EXISTING_REST_FALLBACK.ps1").read_text("utf-8")


def test_fallback_uses_only_existing_user_dpapi_and_readonly_probes():
    for marker in (
        "ServiceIT\\AI-Autofill\\bitrix-rest.dpapi",
        "SERVICEIT_REST_SCOPE_UNVERIFIED",
        "leadpilot.serviceit_cutover_access_probe",
        "leadpilot.serviceit_cloud_cutover_readonly",
        "SERVICEIT_CRM_WRITES=0",
        "SERVICEIT_GIGACHAT_CALLS=0",
        "SERVICEIT_EVENT_GATE_CHANGE=NO",
        "SERVICEIT_EXISTING_GOOGLE_BRIDGE_CHANGE=NO",
        "SERVICEIT_NO_OUTGOING_FALLBACK=NOT_VERIFIED_NO_AUTOMATION",
        "SERVICEIT_NO_OUTGOING_FALLBACK=READONLY_DISCOVERY_POSSIBLE_NOT_LIVE",
    ):
        assert marker in SCRIPT
    assert "Marshal]::ZeroFreeBSTR($Ptr)" in SCRIPT
    assert "Remove-Item Env:\\SERVICEIT_BITRIX_WEBHOOK" in SCRIPT
    assert "Write-Host $Webhook" not in SCRIPT
    assert "crm.item.update" not in SCRIPT
    assert "gh variable set" not in SCRIPT
    assert "gh secret set" not in SCRIPT
    assert "GIGACHAT_CREDENTIALS" not in SCRIPT
    assert "INSTALL_SERVICEIT_BITRIX_EVENT_BRIDGE.ps1" not in SCRIPT
    assert "-Apply" not in SCRIPT


def test_fallback_does_not_assert_live_on_readonly_status():
    assert "SERVICEIT_NO_OUTGOING_FALLBACK=READONLY_DISCOVERY_POSSIBLE_NOT_LIVE" in SCRIPT
    assert "SERVICEIT_NO_OUTGOING_FALLBACK_NEXT=BOUND_NEW_CARD_ONLY_CANARY_REQUIRED" in SCRIPT
    assert "if ($ProbeStatus -ne 0 -or $CutoverStatus -ne 0)" in SCRIPT
    assert SCRIPT.index("SERVICEIT_NO_OUTGOING_FALLBACK=NOT_VERIFIED_NO_AUTOMATION") < SCRIPT.index(
        "SERVICEIT_NO_OUTGOING_FALLBACK=READONLY_DISCOVERY_POSSIBLE_NOT_LIVE")
