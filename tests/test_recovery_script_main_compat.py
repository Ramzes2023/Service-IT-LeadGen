"""Static regression guard: historic FNS recovery entrypoints remain runnable after merge.

No network, subprocess, company data, CRM access or PowerShell execution.
"""
from pathlib import Path
import pytest


ROOT = Path(__file__).resolve().parents[1]
CASES = (
    ("RUN_1C_PAGE1_RECOVERY.ps1", "$branch", "$current"),
    ("RUN_SME_MOSCOW_RECOVERY.ps1", "$expectedBranch", "$currentBranch"),
    ("RUN_SME_VACANCY_AUDIT.ps1", "$requiredBranch", "$current"),
)


@pytest.mark.parametrize("filename,legacy_branch,current", CASES)
def test_historic_recovery_scripts_support_main_without_removing_old_branch_guard(
    filename, legacy_branch, current
):
    script = ROOT / "scripts" / filename
    raw = script.read_bytes()
    assert raw.isascii(), "PowerShell 5.1 compatibility: script must remain ASCII"
    body = raw.decode("ascii")
    assert f"@('main', {legacy_branch}) -notcontains {current}" in body
    assert "branch --show-current" in body
    assert "if ($LASTEXITCODE -ne 0 -or" in body
    assert "exit 1" in body
