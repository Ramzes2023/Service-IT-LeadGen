"""Fail-closed recovery for completed Codex run with missing local cryptography."""
from pathlib import Path

RESUME = Path("scripts/RESUME_SERVICEIT_BITRIX_AI_CODEX_WORKTREE.ps1").read_text("utf-8")
HEADLESS = Path("scripts/RUN_SERVICEIT_BITRIX_AI_CODEX_HEADLESS.ps1").read_text("utf-8")


def test_resumes_same_completed_worktree_without_reissuing_codex():
    for marker in (
        "20261103-162103",
        "codex-final-report.txt",
        "SERVICEIT_CODEX_RERUN=NO",
        "SERVICEIT_REAL_CRM_WRITES=0",
        "SERVICEIT_AUTOMATIC_DEPLOY=NO",
        "codex/serviceit-bitrix-ai-",
        "SERVICEIT_CODEX_WORKTREE_MISSING",
        "SERVICEIT_WORKTREE_IDENTITY_INVALID",
        "SERVICEIT_SUCCESSFUL_CODEX_REPORT_MISSING",
    ):
        assert marker in RESUME
    assert "codex exec" not in RESUME
    assert "INSTALL_SERVICEIT_BITRIX_EVENT_BRIDGE.ps1" not in RESUME
    assert "crm.item.update" not in RESUME
    assert "SERVICEIT_BITRIX_EVENT_AI_ENABLED" not in RESUME


def test_repairs_only_local_venv_then_verifies_offline_before_pr():
    for marker in (
        "pip', 'install'",
        "-r', (Join-Path $Worktree 'requirements.txt')",
        "import cryptography, pytest, fastapi, httpx, defusedxml",
        "scripts/serviceit_offline_pytest.py",
        "TESTS_FAILED_NO_PR",
        "integrations/bitrix_event_bridge/tests/test_bridge.cjs",
        "ENSURE_SERVICEIT_NODE_LTS.ps1",
        "ParseFile(",
        "diff', '--check'",
        "diff', '--cached', '--check'",
        "SERVICEIT_SECRET_LIKE_FILE_STAGED",
        "SERVICEIT_CREATE_PR_FAILED",
        "SERVICEIT_CODEX_RESUME=PR_CREATED_OR_EXISTS_WAITING_CI_AND_REVIEW",
    ):
        assert marker in RESUME
    assert RESUME.index("SERVICEIT_OFFLINE_FULL_TESTS=PASS") < RESUME.index("git' @('push'")
    assert RESUME.index("SERVICEIT_BITRIX_JS_TESTS=PASS") < RESUME.index("git' @('push'")
    assert RESUME.index("SERVICEIT_STAGED_FILE_LIST_UNVERIFIED") < RESUME.index("git' @('push'")
    assert "pr', 'merge'" not in RESUME


def test_future_headless_runs_bootstrap_python_before_expensive_codex():
    assert "SERVICEIT_PYTHON_DEPENDENCIES=INSTALLING_FROM_REQUIREMENTS" in HEADLESS
    assert "SERVICEIT_PYTHON_DEPENDENCIES=READY" in HEADLESS
    assert "SERVICEIT_PYTHON_REQUIREMENTS_INSTALL_FAILED_NO_CODEX_RUN" in HEADLESS
    assert HEADLESS.index("SERVICEIT_PYTHON_DEPENDENCIES=READY") < HEADLESS.index("SERVICEIT_CODEX_METHOD=HEADLESS_STDIN")
    assert "RUN_SERVICEIT_BITRIX_AI_CODEX_HEADLESS.ps1" not in RESUME
