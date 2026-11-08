"""Offline regression for an owner-only, READONLY RouterAI privacy-flag sync.

Only local temporary files. Never connects to Bitrix, VPS, PostgreSQL,
RouterAI, GitHub Actions, or any secrets.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import stat

import pytest

SCRIPT = Path("deploy/serviceit-vps-readonly-routerai-prepare.py")
spec = importlib.util.spec_from_file_location("vps_routerai_prepare", SCRIPT)
assert spec and spec.loader
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


BASE = (
    "SERVICEIT_VPS_WRITER_MODE=READONLY\n"
    "SERVICEIT_VPS_CUTOVER_AUTHORIZED=NO\n"
    "SERVICEIT_VPS_POLL_ENABLED=NO\n"
    "SERVICEIT_VPS_INTAKE_ENABLED=NO\n"
    "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED=NO\n"
    "SERVICEIT_ROUTERAI_PRIVACY_APPROVED=NO\n"
    "EXISTING_PRIVATE_VALUE=NEVER_LOG_OR_CHANGE\n"
)


def make_config(tmp_path, text=BASE):
    config = tmp_path / "vps.env"
    config.write_text(text, encoding="utf-8")
    config.chmod(0o600)
    return config


def test_update_only_two_flags_and_preserve_private_other_values(tmp_path):
    config = make_config(tmp_path)
    assert prepare.apply_readonly_privacy(config) == "PREPARED"
    actual = config.read_text(encoding="utf-8")
    assert actual == BASE.replace(
        "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED=NO",
        "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED=YES",
    ).replace(
        "SERVICEIT_ROUTERAI_PRIVACY_APPROVED=NO",
        "SERVICEIT_ROUTERAI_PRIVACY_APPROVED=YES_1062_21",
    )
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    backups = list(tmp_path.glob("vps.env.bak-routerai-readonly-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == BASE
    assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600
    assert prepare.apply_readonly_privacy(config) == "ALREADY_PREPARED"
    assert len(list(tmp_path.glob("vps.env.bak-routerai-readonly-*"))) == 1


def test_missing_approval_keys_are_inserted_not_other_keys(tmp_path):
    source = BASE.replace("SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED=NO\n", "").replace(
        "SERVICEIT_ROUTERAI_PRIVACY_APPROVED=NO\n", "")
    config = make_config(tmp_path, source)
    assert prepare.apply_readonly_privacy(config) == "PREPARED"
    actual = config.read_text(encoding="utf-8")
    assert actual.startswith(source)
    assert actual.count("SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED=YES\n") == 1
    assert actual.count("SERVICEIT_ROUTERAI_PRIVACY_APPROVED=YES_1062_21\n") == 1


@pytest.mark.parametrize("replacement", [
    "SERVICEIT_VPS_WRITER_MODE=LIVE",
    "SERVICEIT_VPS_CUTOVER_AUTHORIZED=YES",
    "SERVICEIT_VPS_POLL_ENABLED=YES",
    "SERVICEIT_VPS_INTAKE_ENABLED=YES",
])
def test_never_changes_live_or_unauthorized_config(tmp_path, replacement):
    key = replacement.split("=", 1)[0]
    original = BASE.replace(key + "=" + {
        "SERVICEIT_VPS_WRITER_MODE": "READONLY",
        "SERVICEIT_VPS_CUTOVER_AUTHORIZED": "NO",
        "SERVICEIT_VPS_POLL_ENABLED": "NO",
        "SERVICEIT_VPS_INTAKE_ENABLED": "NO",
    }[key], replacement)
    config = make_config(tmp_path, original)
    with pytest.raises(prepare.Blocked):
        prepare.apply_readonly_privacy(config)
    assert config.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob("vps.env.bak-routerai-readonly-*"))


@pytest.mark.parametrize("extra", [
    "SERVICEIT_VPS_OLD_GH_WRITERS_DISABLED_ACK=YES\n",
    "SERVICEIT_VPS_NATIVE_ACTIVATION_UTC=2026-11-08T12:00:00Z\n",
    "SERVICEIT_VPS_MANAGER_TASK_ENABLED=YES\n",
    "SERVICEIT_VPS_FINANCE_ENABLED=YES\n",
    "SERVICEIT_VPS_CRM_DUPLICATE_ENABLED=YES\n",
    "SERVICEIT_ROUTERAI_PRIVACY_APPROVED=YES_OTHER_SCOPE\n",
])
def test_rejects_inconsistent_or_activated_gates(tmp_path, extra):
    text = BASE
    if extra.startswith("SERVICEIT_ROUTERAI_PRIVACY_APPROVED"):
        text = text.replace("SERVICEIT_ROUTERAI_PRIVACY_APPROVED=NO\n", "")
    config = make_config(tmp_path, text + extra)
    with pytest.raises(prepare.Blocked):
        prepare.apply_readonly_privacy(config)


def test_rejects_duplicate_and_insecure_config(tmp_path):
    config = make_config(tmp_path, BASE + "SERVICEIT_VPS_POLL_ENABLED=NO\n")
    with pytest.raises(prepare.Blocked):
        prepare.apply_readonly_privacy(config)
    config.write_text(BASE, encoding="utf-8")
    config.chmod(0o644)
    with pytest.raises(prepare.Blocked):
        prepare.apply_readonly_privacy(config)


def test_workflow_requires_owner_explicit_approval_and_does_not_cut_over():
    yaml = Path(".github/workflows/serviceit-vps-routerai-readonly-prepare.yml").read_text(encoding="utf-8")
    assert "github.actor == 'Ramzes2023'" in yaml
    assert "PREPARE_ROUTERAI_1062_21_READONLY" in yaml
    assert "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED" in yaml
    assert "SERVICEIT_ROUTERAI_PRIVACY_APPROVED" in yaml
    assert "VPS_LIVE_CUTOVER=NOT_PERFORMED" in yaml
    assert "OLD_GITHUB_WRITERS=UNCHANGED" in yaml
    assert "sha256sum" in yaml
    assert "serviceit-vps-native-shadow-readonly" in yaml
    assert "gh workflow disable" not in yaml
    assert "docker compose up" not in yaml
