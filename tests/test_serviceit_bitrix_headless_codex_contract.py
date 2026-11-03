from pathlib import Path


def test_headless_codex_exec_uses_stdin_and_isolated_worktree():
    script = Path("scripts/RUN_SERVICEIT_BITRIX_AI_CODEX_HEADLESS.ps1").read_text("utf-8")
    assert "SERVICEIT_CODEX_METHOD=HEADLESS_STDIN" in script
    assert "$Instructions | & $Codex @Args" in script
    assert "'exec', '-C', $Worktree, '--sandbox', 'workspace-write'" in script
    assert "git -C $Repo worktree add -b $Branch $Worktree origin/main" in script
    assert "CODEX_REAL_CRM_WRITES=FORBIDDEN" in script
    assert "SERVICEIT_BITRIX_AI_CODEX_PROMPT.txt" in script
    assert "SERVICEIT_BITRIX_EVENT_AI_ENABLED" not in script
    assert "SERVICEIT_BITRIX_WEBHOOK" not in script
    assert "crm.item.update" not in script


def test_code_prompt_only_bitrix_and_protects_existing_clients():
    prompt = Path("scripts/SERVICEIT_BITRIX_AI_CODEX_PROMPT.txt").read_text("utf-8")
    assert "EVERY new card" in prompt
    assert "entityTypeId=1062" in prompt
    assert "categoryId=21" in prompt
    assert "No sources/Gmail." in prompt
    assert "GigaChat" in prompt
    assert "corporate privacy" in prompt
    assert "Do NOT use crm.item.list" in prompt
    assert "do not call the automation LIVE" in prompt
    assert "Never paste secret tokens" in prompt


def test_installer_defaults_to_preview_and_uses_dpapi_not_chat_secrets():
    script = Path("scripts/INSTALL_SERVICEIT_BITRIX_EVENT_BRIDGE.ps1").read_text("utf-8")
    assert "param([switch]$Apply)" in script
    assert "if(-not $Apply)" in script
    assert "bitrix-rest.dpapi" in script
    assert "bridge-hmac.dpapi" in script
    assert "SecureStringToBSTR" in script
    assert "ZeroFreeBSTR" in script
    assert "Read-Host 'Paste Bitrix-generated OUTGOING application_token (hidden)' -AsSecureString" in script
    assert "SERVICEIT_BITRIX_EVENT_AI_ENABLED' 'NO'" in script
    assert "SERVICEIT_BITRIX_EVENT_AI_ENABLED' 'YES_1062_21_CLOUD'" in script
    assert script.index("SERVICEIT_BITRIX_EVENT_AI_ENABLED' 'NO'") < script.index("SERVICEIT_BITRIX_EVENT_AI_ENABLED' 'YES_1062_21_CLOUD'")
    assert "LOCAL_DPAPI" not in script or "bitrix-rest.dpapi" in script


def test_installer_separates_gigachat_privacy_and_manager_authorization():
    script = Path("scripts/INSTALL_SERVICEIT_BITRIX_EVENT_BRIDGE.ps1").read_text("utf-8")
    assert "CORPORATE_CRM_AI_WRITE_APPROVAL_REQUIRED" in script
    assert "GOOGLE_SCRIPT_OWNER_ONLY_REQUIRED" in script
    assert "GIGACHAT_LOCAL_DPAPI_OR_SCOPE_MISSING" in script
    assert "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED" in script
    assert "SERVICEIT_PRELIMINARY_PUBLIC_TITLE_AI_ENABLED" in script
    assert "GIGACHAT_CREDENTIALS" in script
    assert "Set-GhSecret 'SERVICEIT_BITRIX_WEBHOOK' $Webhook" in script
    assert "StandardInput.Write($Value)" in script
    assert "BITRIX_SETUP=Developer resources > Other > Outgoing webhook" in script
    assert "GOOGLE_ANONYMOUS_WEBAPP_NOT_ACCESSIBLE" in script


def test_no_existing_gmail_script_project_is_overwritten():
    script = Path("scripts/INSTALL_SERVICEIT_BITRIX_EVENT_BRIDGE.ps1").read_text("utf-8")
    assert "ServiceIT\\\\Bitrix-Event-Bridge" in script or "ServiceIT\\Bitrix-Event-Bridge" in script
    assert "ServiceIT Bitrix24 AI New Card Bridge" in script
    assert "Service IT Gmail" not in script
    assert "create --type webapp" in script
    assert "APPROVAL" in script or "approval" in script
    assert "LIVE_CANARY_NINE_FIELDS_READBACK_PASS" in script
    assert "CANARY_NINE_AI_FIELDS_NOT_CONFIRMED_CLOUD_DISABLED_NO_REPLAY" in script
