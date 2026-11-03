"""Codex CLI 0.163 stderr model-catalog timeouts must not abort PS5.1."""
from pathlib import Path

SCRIPT = Path("scripts/RUN_SERVICEIT_BITRIX_AI_CODEX_HEADLESS.ps1").read_text("utf-8")
CI = Path(".github/workflows/tests.yml").read_text("utf-8")
SMOKE = Path("scripts/TEST_SERVICEIT_CODEX_NATIVE_STDERR.ps1").read_text("utf-8")


def test_powershell_stderr_cannot_preempt_codex_exit_code():
    assert "$PreviousErrorActionPreference = $ErrorActionPreference" in SCRIPT
    assert "$ErrorActionPreference = 'Continue'" in SCRIPT
    assert "$ErrorActionPreference = $PreviousErrorActionPreference" in SCRIPT
    assert "$Instructions | & $Codex @Args 2>&1" in SCRIPT
    assert "$CodexExit = $LASTEXITCODE" in SCRIPT
    assert SCRIPT.index("$CodexExit = $LASTEXITCODE") < SCRIPT.index("if ($CodexExit -eq 0) { break }")
    assert "Tee-Object -FilePath $CodexLog -Append" in SCRIPT


def test_retry_only_once_when_clean_and_timed_out():
    assert "for ($Attempt = 1; $Attempt -le 2; $Attempt++)" in SCRIPT
    assert "git -C $Worktree status --porcelain" in SCRIPT
    assert "Select-String -LiteralPath $CodexLog -SimpleMatch" in SCRIPT
    assert "failed to refresh available models: request timed out" in SCRIPT
    assert "$Attempt -ge 2 -or $ModifiedBeforeRetry.Count -ne 0 -or -not $TimedOut" in SCRIPT
    assert "CODEX_HEADLESS_NONZERO_REVIEW_REQUIRED" in SCRIPT
    assert "Start-Sleep -Seconds 5" in SCRIPT
    assert "CODEX_FINAL_REPORT_MISSING_DEPLOY_BLOCKED" in SCRIPT
    assert SCRIPT.index("CODEX_FINAL_REPORT_MISSING_DEPLOY_BLOCKED") < SCRIPT.index("INSTALLER_STATUS=STARTING_OWNER_APPROVAL")


def test_real_nonzero_exit_is_not_masked_and_synthetic_test_is_in_ci():
    assert "Invoke-SyntheticNative 0" in SMOKE
    assert "Invoke-SyntheticNative 23" in SMOKE
    assert "STDERR_WARNING_MASKED_REAL_NONZERO_EXIT" in SMOKE
    assert "SERVICEIT_CODEX_NATIVE_STDERR_PS51=PASS" in SMOKE
    assert "scripts/TEST_SERVICEIT_CODEX_NATIVE_STDERR.ps1" in CI
    for blocked in ("crm.item.update", "gh secret", "SERVICEIT_BITRIX_WEBHOOK", "bitrix-rest.dpapi"):
        assert blocked not in SMOKE
