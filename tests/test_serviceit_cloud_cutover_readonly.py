"""Offline-only tests for cutover inventory, no real Bitrix/credential calls."""
from pathlib import Path
import pytest

from leadpilot.serviceit_cloud_cutover_readonly import latest_existing_card_id


def test_empty_category_starts_after_7():
    def rest(method, args):
        assert method == "crm.item.list"
        assert args["filter"] == {"categoryId": 21}
        assert args["order"] == {"id": "DESC"}
        return {"items": []}
    assert latest_existing_card_id(rest) == 7


def test_last_existing_card_is_high_water_mark():
    calls = []
    def rest(method, args):
        calls.append((method, args))
        if method == "crm.item.list":
            # Real Bitrix24 response drops entityTypeId, even if selected.
            assert args["select"] == ["id", "categoryId"]
            return {"items": [
                {"id": 25, "categoryId": 21},
                {"id": 22, "categoryId": 21},
            ]}
        assert method == "crm.item.get" and args["id"] == 25
        return {"item": {"id": 25, "entityTypeId": 1062, "categoryId": 21}}
    assert latest_existing_card_id(rest) == 25
    assert [name for name, _ in calls] == ["crm.item.list", "crm.item.get"]


def test_historical_card_7_not_written_or_enqueued():
    def rest(method, args):
        if method == "crm.item.list":
            return {"items": [{"id": 7, "categoryId": 21}]}
        assert method == "crm.item.get"
        assert args["id"] == 7
        return {"item": {"id": 7, "entityTypeId": 1062, "categoryId": 21}}
    assert latest_existing_card_id(rest) == 7


def test_cutover_never_trusts_type_missing_from_list():
    def rest(method, args):
        if method == "crm.item.list":
            return {"items": [{"id": 58, "categoryId": 21}]}
        return {"item": {"id": 58, "entityTypeId": 999, "categoryId": 21}}
    with pytest.raises(ValueError, match="CUTOVER_TOP_ITEM_IDENTITY_UNVERIFIED"):
        latest_existing_card_id(rest)


@pytest.mark.parametrize("items", [
    [{"id": 12, "entityTypeId": 1062, "categoryId": 99}],
    [{"id": 12, "entityTypeId": 1062, "categoryId": 21},
     {"id": 13, "entityTypeId": 1062, "categoryId": 21}],
    [{"id": 12, "entityTypeId": 1062, "categoryId": 21},
     {"id": 12, "entityTypeId": 1062, "categoryId": 21}],
    [{"id": -1, "entityTypeId": 1062, "categoryId": 21}],
])
def test_fails_closed_on_bad_inventory(items):
    with pytest.raises(ValueError):
        latest_existing_card_id(lambda *_: {"items": items})


def test_cannot_guess_missing_inventory():
    with pytest.raises(ValueError, match="CUTOVER_INVENTORY_INVALID"):
        latest_existing_card_id(lambda *_: {"error": "not authorized"})


def test_installer_uses_owner_dpapi_and_repo_verified_only():
    source = Path("scripts/SETUP_SERVICEIT_FREE_GITHUB_ACTIONS.ps1").read_text("utf-8")
    assert "-AsSecureString" not in source  # Existing DPAPI, never prompts for keys.
    assert "ConvertTo-SecureString" in source
    assert "SecureStringToBSTR" in source
    assert "ZeroFreeBSTR" in source
    assert "github-ledger-key.dpapi" in source
    assert "gh.exe" in source
    assert "gh auth login" not in source  # Uses correctly quoted native CLI call.
    assert "& $Gh auth login" in source
    assert "Disable-ScheduledTask" in source
    assert "LOCAL_TASK_DISABLE_UNVERIFIED" in source
    assert "SERVICEIT_GH_START_AFTER_ID" in source
    assert "SERVICEIT_FREE_CLOUD_ENABLED" in source
    assert "secret set" in source
    assert "RedirectStandardInput" in source
    assert "Set-Variable 'SERVICEIT_FREE_CLOUD_ENABLED' 'YES'" in source
    assert "TenderLens" not in source and "VALYQON" not in source


def test_script_activation_not_default():
    source = Path("scripts/SETUP_SERVICEIT_FREE_GITHUB_ACTIONS.ps1").read_text("utf-8")
    assert "param([switch]$Apply, [switch]$DiagnoseCutover)" in source
    assert "if (-not $Apply)" in source
    assert "OWNER_CLOUD_CONSENT_MISSING" in source
    assert "CLOUD_ALREADY_ENABLED_REFUSE_SECRET_OR_CURSOR_OVERWRITE" in source
