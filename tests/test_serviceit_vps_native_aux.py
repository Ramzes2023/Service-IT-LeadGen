"""VPS native auxiliary modules must never write unless cutover + per-module opt-in.

All tests synthetic; no external HTTP, credentials, Bitrix or AI.
"""
from unittest.mock import Mock

import pytest
from leadpilot.serviceit_vps_pg_queue import VpsBlocked
from leadpilot import serviceit_vps_native_aux as aux


def test_auxiliary_disabled_by_default_no_pg_or_http():
    result = aux.run_once({}, client=object(), connection=object())
    assert result == {"status": "NATIVE_AUX_DISABLED", "crm_writes": 0,
                      "new_card_candidates": 0, "checks": 0, "review": 0}


def test_no_native_cutover_never_reads_secret_or_makes_request(monkeypatch):
    monkeypatch.setattr(aux, "secret_file",
                        lambda *args, **kwargs: pytest.fail("NO SECRET READ"))
    with pytest.raises(VpsBlocked, match="CUTOVER"):
        aux.run_once({"SERVICEIT_VPS_OWN_STOP_ENABLED": "YES"},
                     client=object(), connection=object())
    with pytest.raises(VpsBlocked, match="FLAG_INVALID"):
        aux.run_once({"SERVICEIT_VPS_OWN_STOP_ENABLED": "MAYBE"},
                     client=object(), connection=object())


def test_task_requires_approved_exact_manager_and_post_ai_receipt():
    import inspect
    from leadpilot import serviceit_stage3_all_new_task_automation as stage3
    from leadpilot import serviceit_vps_native_fence as fence
    assert "NATIVE_TASK_CUTOVER_NOT_APPROVED" in inspect.getsource(stage3.process_card)
    assert 'SERVICEIT_STAGE3_TASK_WRITE_ENABLED' in inspect.getsource(stage3.process_card)
    assert "TASK_V1" in fence.NATIVE_NAMESPACES
    assert "AI_NINE_V1" in fence.NATIVE_NAMESPACES
    assert "HOLD_9_WRITTEN" in inspect.getsource(aux.ai_receipt_allows_task)


def test_each_aux_action_uses_distinct_pg_namespace():
    assert len(set(aux.NAMESPACES.values())) == len(aux.NAMESPACES)
    assert set(aux.FLAGS) == set(aux.NAMESPACES)
    assert aux.enabled({}) == {name: False for name in aux.FLAGS}
    assert "SERVICEIT_GH_LEDGER_KEY" not in __import__("inspect").getsource(aux)
    assert "GITHUB_TOKEN" not in __import__("inspect").getsource(aux)
    assert "serviceit_vps_native_fence" in __import__("inspect").getsource(aux)


def test_vps_compose_defaults_all_aux_writers_to_no():
    from pathlib import Path
    compose = Path("deploy/serviceit-vps.compose.yml").read_text("utf8")
    env = Path("deploy/serviceit-vps.env.example").read_text("utf8")
    for var in aux.FLAGS.values():
        assert var + ":-NO" in compose
        assert var + "=NO" in env
    assert 'leadpilot.serviceit_vps_native_aux", "--loop"' in compose
    assert "serviceit_gh_ledger_key" not in compose
    assert "GIGACHAT_CREDENTIALS" not in compose
