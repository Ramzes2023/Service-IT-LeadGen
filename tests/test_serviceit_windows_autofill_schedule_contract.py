"""Offline static contract of the owner-operated local scheduled task."""
from pathlib import Path


def test_install_requires_explicit_corporate_write_permission_and_dpapi():
    source = Path("scripts/INSTALL_SERVICEIT_ALL_CARD_AUTOFILL.ps1").read_text("utf-8")
    assert "OWNER_AUTOFILL_CONSENT_MISSING" in source
    assert "ConvertFrom-SecureString" in source
    assert "New-ScheduledTaskTrigger" in source
    assert "New-TimeSpan -Minutes 5" in source
    assert "Register-ScheduledTask" in source
    assert "LogonType Interactive" in source
    assert "YES" in source
    assert "GIGACHAT_CREDENTIALS" in source
    assert "SOURCE" not in source.split("Register-ScheduledTask")[-1]


def test_runner_never_stores_cleartext_credentials():
    source = Path("scripts/RUN_SERVICEIT_ALL_CARD_AUTOFILL.ps1").read_text("utf-8")
    assert "ConvertTo-SecureString" in source
    assert "SecureStringToBSTR" in source
    assert "ZeroFreeBSTR" in source
    assert "SERVICEIT_WINDOWS_AUTOFILL_ENABLED" in source
    assert "SERVICEIT_AUTOFILL_STATE_DIR" in source
    assert "SERVICEIT_APPROVAL_DIR" in source
    assert "serviceit_windows_autofill --once" in source
    assert "Write-Host $env:GIGACHAT_CREDENTIALS" not in source
    assert "Write-Host $env:SERVICEIT_BITRIX_WEBHOOK" not in source


def test_scheduler_never_writes_historical_item_or_source():
    source = Path("leadpilot/serviceit_windows_autofill.py").read_text("utf-8")
    assert "FIRST_ID = 8" in source
    assert "preliminary_fields(item)" in source
    assert '"crm.item.update"' in source
    assert 'store.reserve(item_id, owner, "write"' in source
    assert "Worker(store, rest" in source
    assert "SOURCE_CONTRACT_REVIEW" not in source
    assert "crm.item.add" not in source
    assert "crm.item.delete" not in source
    assert "crm.item.details.configuration.set" not in source
