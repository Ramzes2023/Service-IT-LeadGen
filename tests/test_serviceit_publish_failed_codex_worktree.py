"""Draft publish must never bypass failed tests into main or CRM."""
from pathlib import Path

SCRIPT = Path("scripts/PUBLISH_SERVICEIT_CODEX_WORKTREE_DRAFT.ps1").read_text("utf-8")
WORKFLOW = Path(".github/workflows/tests.yml").read_text("utf-8")


def test_draft_publisher_reuses_existing_worktree_and_stays_fail_closed():
    for key in (
        "20261103-162103",
        "SERVICEIT_CODEX_COMPLETED_REPORT_MISSING",
        "SERVICEIT_CODEX_COMPLETED_REPORT_EMPTY",
        "SERVICEIT_WORKTREE_REMOTE_MISMATCH",
        "SERVICEIT_CODEX_BRANCH_NOT_APPROVED",
        "SERVICEIT_CODEX_RERUN=NO",
        "SERVICEIT_REAL_CRM_WRITES=NO",
        "SERVICEIT_DRAFT_REVIEW_ONLY=YES",
        "SERVICEIT_DRAFT_PR_CREATE_FAILED",
        "SERVICEIT_CODEX_DRAFT_PR=READY_FOR_REVIEW_DO_NOT_MERGE",
        "SERVICEIT_BITRIX_AUTOMATION_LIVE=NO",
        "WIP(serviceit): inspect completed universal Bitrix AI worktree",
    ):
        assert key in SCRIPT
    assert "'--draft'" in SCRIPT
    assert "'--base', 'main'" in SCRIPT
    assert "'--head', $Branch" in SCRIPT
    assert "'pr', 'merge'" not in SCRIPT
    assert "codex exec" not in SCRIPT
    assert "INSTALL_SERVICEIT_BITRIX_EVENT_BRIDGE.ps1 -Apply" not in SCRIPT


def test_git_allowlist_and_secret_scan_happen_before_remote_push():
    for key in (
        "SERVICEIT_OUTSIDE_ALLOWED_PATH_NOT_PUBLISHED",
        "SERVICEIT_SECRET_OR_RUNTIME_FILE_BLOCKED",
        "SERVICEIT_STAGED_UNAPPROVED_PATH",
        "SERVICEIT_STAGED_SECRET_PATH_BLOCKED",
        "SERVICEIT_SUSPECT_CREDENTIAL_BLOCKED",
        "SERVICEIT_STAGED_WHITESPACE_ERROR",
    ):
        assert key in SCRIPT
        assert SCRIPT.index(key) < SCRIPT.index("'push', '-u', 'origin'")
    assert "github_pat_" in SCRIPT and "sk-proj-" in SCRIPT


def test_windows_ci_parses_publish_script_not_runs_it():
    assert "scripts/PUBLISH_SERVICEIT_CODEX_WORKTREE_DRAFT.ps1" in WORKFLOW
    assert "ParseFile(" in WORKFLOW
