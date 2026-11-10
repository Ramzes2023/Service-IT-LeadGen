"""Cutover regression: rollback must not depend on a Docker commit of live containers."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "deploy/serviceit-vps-b2b-guarded-cutover.py"


def load_cutover():
    spec = importlib.util.spec_from_file_location("serviceit_b2b_guarded_cutover", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare(monkeypatch, tmp_path, *, rollback_name_exists=False):
    module = load_cutover()
    module_path = tmp_path / "serviceit/staging/b2b-direct-image-42791235235/source/leadpilot"
    module_path.mkdir(parents=True)
    (module_path / "serviceit_b2b_positive_control.py").write_text("# staged", encoding="utf8")
    monkeypatch.setattr(module.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(module.os, "geteuid", lambda: 10001)
    monkeypatch.setenv("SERVICEIT_B2B_DEPLOY_RUN_ID", "42795485559")
    core = {"Id": "original-core", "State": {"Running": True}}
    receiver = {"Id": "original-receiver", "Image": "sha256:" + "1" * 64,
                "State": {"Running": True}}
    writer = {"Id": "original-writer", "Image": "sha256:" + "2" * 64,
              "State": {"Running": True}}
    objects = {module.CORE: core, module.RECEIVER: receiver, module.WRITER: writer}
    monkeypatch.setattr(module, "inspect", lambda name: objects[name])
    monkeypatch.setattr(module, "validate_existing",
                        lambda name: (objects[name], {}))
    monkeypatch.setattr(module, "source_probe", lambda: None)
    monkeypatch.setattr(module, "check_receiver", lambda: None)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(stdout="sha256:" + "f" * 64, returncode=0)
        if args[:4] == ["docker", "inspect", "--type", "container"]:
            return SimpleNamespace(stdout="", returncode=0 if rollback_name_exists else 1)
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(module, "run", fake_run)
    return module, calls


def test_guarded_promotion_preserves_original_containers_without_docker_commit(
    monkeypatch, tmp_path, capsys
):
    module, calls = prepare(monkeypatch, tmp_path)
    started = []
    monkeypatch.setattr(module, "start_new",
                        lambda name, former, vars: started.append((name, former)))
    assert module.main() == 0
    assert len(started) == 2
    assert started[0][0] == module.RECEIVER
    assert started[1][0] == module.WRITER
    assert all(args[:2] != ["docker", "commit"] for args in calls)
    assert any(args[:2] == ["docker", "rename"] for args in calls)
    assert "B2BLIVE_ORIGINAL_CONTAINERS_READY=YES" in capsys.readouterr().out


def test_preexisting_rollback_name_blocks_before_mutations(monkeypatch, tmp_path):
    module, calls = prepare(monkeypatch, tmp_path, rollback_name_exists=True)
    with pytest.raises(module.Blocked, match="ROLLBACK_NAME_ALREADY_EXISTS"):
        module.main()
    assert not any(args[:2] in (["docker", "rename"], ["docker", "stop"],
                                 ["docker", "commit"]) for args in calls)


def test_failed_promotion_calls_rollback_on_preserved_containers(
    monkeypatch, tmp_path, capsys
):
    module, calls = prepare(monkeypatch, tmp_path)
    restored = []

    def fail_to_start(*args):
        raise module.Blocked("NEW_CONTAINER_NOT_RUNNING")

    monkeypatch.setattr(module, "start_new", fail_to_start)
    monkeypatch.setattr(module, "rollback",
                        lambda backups: restored.append(dict(backups)) or True)
    assert module.main() == 2
    assert restored and module.WRITER in restored[0] and module.RECEIVER in restored[0]
    assert all(args[:2] != ["docker", "commit"] for args in calls)
    assert "B2BLIVE_ROLLBACK=YES" in capsys.readouterr().out
