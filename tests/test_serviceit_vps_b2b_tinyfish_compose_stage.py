"""Pure offline safety tests for TinyFish Compose staged OFF-only overlay.

No actual SSH, Docker, Postgres, Bitrix, API key, or live mutation.
"""
from copy import deepcopy
from pathlib import Path

import pytest

from ops import serviceit_vps_b2b_tinyfish_compose_stage as stage


def baseline():
    return {
        "name": "serviceit-leadgen-vps",
        "services": {
            "coordinator": {
                "image": "sha256:" + "a" * 64,
                "environment": {
                    "SERVICEIT_VPS_WRITER_MODE": "LIVE",
                    "SERVICEIT_VPS_POLL_ENABLED": "YES",
                    "SERVICEIT_AI_PROVIDER": "routerai",
                },
                "secrets": [
                    {"source": "serviceit_pg_password", "target": "/run/secrets/serviceit_pg_password"},
                    {"source": "serviceit_bitrix_webhook", "target": "/run/secrets/serviceit_bitrix_webhook"},
                    {"source": "serviceit_routerai_key", "target": "/run/secrets/serviceit_routerai_key"},
                    {"source": "serviceit_brave_search_key", "target": "/run/secrets/serviceit_brave_search_key"},
                ],
                "command": ["python", "-m", "leadpilot.serviceit_vps_worker", "--loop"],
            },
            "observer": {"image": "sha256:" + "b" * 64, "environment": {"READONLY": "YES"}},
        },
        "secrets": {
            "serviceit_pg_password": {"file": "/etc/serviceit/secrets/pg_password"},
            "serviceit_bitrix_webhook": {"file": "/etc/serviceit/secrets/bitrix_webhook"},
            "serviceit_routerai_key": {"file": "/etc/serviceit/secrets/routerai_key"},
            "serviceit_brave_search_key": {"file": "/etc/serviceit/secrets/brave_api_key"},
        },
    }


def added():
    b = baseline()
    a = deepcopy(b)
    a["services"]["coordinator"]["environment"].update(stage.FLAGS)
    a["services"]["coordinator"]["secrets"].append({
        "source": stage.SECRET, "target": stage.MOUNT})
    a["secrets"][stage.SECRET] = {"file": stage.SOURCE}
    return b, a


def test_exact_overlay_is_only_allowed_off_change():
    old, new = added()
    assert stage.verify_overlay(old, new) is True


@pytest.mark.parametrize("mutate", [
    lambda x: x["services"]["coordinator"]["environment"].update(
        {"SERVICEIT_VPS_B2B_FREE_PUBLIC_FETCH_ENABLED": "YES"}),
    lambda x: x["services"]["coordinator"]["environment"].update(
        {"SERVICEIT_VPS_POLL_ENABLED": "NO"}),
    lambda x: x["services"]["coordinator"].update({"image": "sha256:" + "c"*64}),
    lambda x: x["services"]["coordinator"]["secrets"].pop(0),
    lambda x: x["services"]["observer"]["environment"].update({"READONLY": "NO"}),
    lambda x: x["services"].update({"second-writer": {"image": "x"}}),
    lambda x: x["secrets"][stage.SECRET].update({"file": "/tmp/key"}),
    lambda x: x.update({"name": "another-project"}),
])
def test_any_runtime_or_secret_drift_is_blocked(mutate):
    old, new = added()
    mutate(new)
    with pytest.raises(stage.StageBlocked):
        stage.verify_overlay(old, new)


def test_source_compose_overlay_is_not_a_secret_or_auto_activation():
    text = (Path(__file__).parents[1] /
            "deploy" / "serviceit-vps-tinyfish-free.compose.yml").read_text()
    assert "serviceit_tinyfish_key:" in text
    assert "/tinyfish_api_key" in text
    assert "SERVICEIT_TINYFISH_API_KEY_FILE:" in text
    assert "SERVICEIT_VPS_B2B_FREE_PUBLIC_FETCH_ENABLED: \"NO\"" in text
    assert "SERVICEIT_B2B_TENDEM_PUBLIC_EXTRACTION_APPROVED: \"NO\"" in text
    assert "SERVICEIT_B2B_CENTER_PUBLIC_EXTRACTION_LICENSED: \"NO\"" in text
    assert "SERVICEIT_TINYFISH_API_KEY:" not in text
    assert "serviceit_pg_password" in text
    assert "serviceit_bitrix_webhook" in text
    assert "serviceit_routerai_key" in text
    assert "serviceit_brave_search_key" in text
    assert "serviceit_tinyfish_key" in text


def test_no_restart_or_external_write_command_in_stager():
    text = (Path(__file__).parents[1] /
            "ops" / "serviceit_vps_b2b_tinyfish_compose_stage.py").read_text()
    for dangerous in ('"docker", "compose", "up"', '"docker", "restart"',
                      '"crm.item.update"', "psycopg.connect", '"api.fetch.tinyfish.ai"'):
        assert dangerous not in text
    assert "COMPOSE_GLOBAL_CONFIG_DRIFT" in text
    assert "TINYFISH_STAGE_FETCH_ENABLED=NO" in text
