"""Offline tests: the B2B task rollout must never restart the CRM/source stack."""
from __future__ import annotations
import inspect

import pytest

from ops import serviceit_vps_b2b_aux_task_activate as deploy


def test_aux_pin_changes_only_auxiliary_image():
    digest="sha256:"+"f"*64
    result=deploy.pin_value(digest)
    assert result=="services:\n  auxiliaries:\n    image: \""+digest+"\"\n    pull_policy: never\n"
    for unwanted in ("coordinator:","receiver:","writer:","business:","postgres:"):
        assert unwanted not in result


def test_missing_or_invalid_args_never_call_docker(monkeypatch):
    monkeypatch.setattr(deploy.os,"geteuid",lambda:10001)
    monkeypatch.setattr(deploy.sys,"argv",["activate.py","BAD"])
    monkeypatch.setattr(deploy,"call",lambda *a,**k: pytest.fail("No Docker on invalid args"))
    with pytest.raises(deploy.Blocked,match="REQUIRED_ARGS_MISSING"):
        deploy.main()


def test_scope_requires_verified_old_source_and_approved_manager():
    script=inspect.getsource(deploy)
    assert "LIVE_AUX_NOT_REVIEWED_BASELINE" in script
    assert "REQUIRED_APPROVED_TASK_FLAGS_MISSING" in script
    assert "NEW_AUX_MODULES_NOT_APPLIED" in script
    assert '"--no-deps"' in script
    assert '"--force-recreate","auxiliaries"' in script
    assert "OTHER_CONTAINERS_CHANGED" in script
    for forbidden in ("crm.item.add","tasks.task.add","crm.item.update"):
        assert forbidden not in script
