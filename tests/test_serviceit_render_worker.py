"""Offline-only contract tests for 24/7 ServiceIT Render supervisor."""
from pathlib import Path
from types import SimpleNamespace
import subprocess

from leadpilot.serviceit_render_worker import (
    CYCLE_TIMEOUT_SECONDS, LIVE_FLAG, child_environment, config_state, run_once
)


def settings(**kwargs):
    result = {"SERVICEIT_CLOUD_LIVE_ENABLED": LIVE_FLAG,
              "SERVICEIT_CLOUD_START_AFTER_ID": "7",
              "SERVICEIT_BITRIX_WEBHOOK": "synthetic-NOT-A-REAL-SECRET",
              "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED": "NO"}
    result.update(kwargs)
    return result


def test_disabled_by_default_even_if_secrets_present(tmp_path):
    env = settings(SERVICEIT_CLOUD_LIVE_ENABLED="NO")
    assert config_state(env, root=tmp_path, mounted=True) == "CLOUD_NOT_ENABLED"


def test_persistent_disk_required_for_crash_safe_ledger(tmp_path):
    assert config_state(settings(), root=tmp_path, mounted=False) == (
        "CLOUD_PERSISTENT_DISK_NOT_MOUNTED")


def test_gigachat_requires_scope_credentials_and_independent_privacy(tmp_path):
    env = settings(SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED="YES")
    assert config_state(env, root=tmp_path, mounted=True) == "CLOUD_GIGACHAT_KEY_MISSING"
    env["GIGACHAT_CREDENTIALS"] = "SYNTHETIC-KEY-NEVER-CALL"
    assert config_state(env, root=tmp_path, mounted=True) == "CLOUD_GIGACHAT_SCOPE_INVALID"
    env["GIGACHAT_SCOPE"] = "GIGACHAT_API_PERS"
    assert config_state(env, root=tmp_path, mounted=True) == "CLOUD_CONFIG_READY"


def test_run_once_disabled_never_invokes_subprocess(tmp_path):
    def forbidden(*a, **k):
        raise AssertionError("must not execute in offline mode")
    result = run_once(settings(SERVICEIT_CLOUD_LIVE_ENABLED="NO"),
                      root=tmp_path, mounted=True, executor=forbidden)
    assert result == {"status": "CLOUD_NOT_ENABLED", "crm_writes": 0,
                      "gigachat_calls": 0}


def test_live_worker_preserves_only_expected_scope_and_sanitized_logs(tmp_path):
    seen = []
    env = settings()
    def fake_exec(argv, **kwargs):
        seen.append((argv, kwargs))
        return SimpleNamespace(returncode=0,
                               stdout="sensitive-source-body-do-not-log")
    result = run_once(env, root=tmp_path, mounted=True, executor=fake_exec)
    assert result == {"status": "CLOUD_CYCLE_OK", "exit_code": 0}
    assert len(seen) == 1
    argv, params = seen[0]
    assert argv[-3:] == ["-m", "leadpilot.serviceit_windows_autofill", "--once"]
    assert params["timeout"] == CYCLE_TIMEOUT_SECONDS
    assert params["stderr"] == subprocess.DEVNULL
    assert params["env"]["SERVICEIT_WINDOWS_AUTOFILL_ENABLED"] == "YES_1062_21"
    assert params["env"]["SERVICEIT_AUTOFILL_STATE_DIR"] == str(tmp_path / "state")
    assert (tmp_path / "state").is_dir()
    assert (tmp_path / "approvals").is_dir()
    assert "sensitive-source-body-do-not-log" not in str(result)


def test_model_stays_disabled_until_corporate_permission(tmp_path):
    env = child_environment(settings(), root=tmp_path)
    assert env["AI_ENABLE"] == "0"
    assert env["SERVICEIT_APPROVAL_DIR"] == str(tmp_path / "approvals")


def test_subprocess_timeout_never_retries_ambiguous_crm_operations(tmp_path):
    seen = []
    def timeout(*args, **kwargs):
        seen.append(True)
        raise subprocess.TimeoutExpired("python", 240)
    result = run_once(settings(), root=tmp_path, mounted=True, executor=timeout)
    assert result["status"] == "CLOUD_CYCLE_UNVERIFIED"
    assert len(seen) == 1


def test_render_blueprint_does_not_publish_secrets_or_autostart():
    text = Path("render.yaml").read_text("utf-8")
    assert "type: worker" in text
    assert "autoDeployTrigger: off" in text
    assert "mountPath: /opt/render/project/src/serviceit-data" in text
    assert 'value: "NO"' in text
    assert "sync: false" in text
    assert "GIGACHAT_CREDENTIALS" in text
    assert "1234-secret" not in text


def test_cloud_cutover_required_before_any_real_crm_poll(tmp_path):
    env = settings()
    del env["SERVICEIT_CLOUD_START_AFTER_ID"]
    assert config_state(env, root=tmp_path, mounted=True) == "CLOUD_CUTOVER_CURSOR_REQUIRED"


def test_first_cloud_cycle_persists_baseline_not_reseeded(tmp_path):
    import sqlite3
    from leadpilot.serviceit_render_worker import initialize_cutover
    root = tmp_path
    (root / "state").mkdir()
    assert initialize_cutover(root, 7)
    with sqlite3.connect(root / "state" / "new-card-events.sqlite3") as db:
        assert db.execute("SELECT value FROM worker_meta WHERE key='cursor'").fetchone() == ("7",)
        db.execute("UPDATE worker_meta SET value='101' WHERE key='cursor'")
    assert initialize_cutover(root, 7)
    assert not initialize_cutover(root, 100)
    with sqlite3.connect(root / "state" / "new-card-events.sqlite3") as db:
        assert db.execute("SELECT value FROM worker_meta WHERE key='cursor'").fetchone() == ("101",)
