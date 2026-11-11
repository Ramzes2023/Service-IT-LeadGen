"""Offline proof: coordinator-only TinyFish OFF release preserves all old services.

No Docker, VPS, PostgreSQL, TinyFish or Bitrix connections in these tests.
"""
from copy import deepcopy
from pathlib import Path

import pytest

from ops import serviceit_vps_b2b_tinyfish_guarded_install as release


def original():
    return {
        "name": "serviceit-leadgen-vps",
        "services": {
            "coordinator": {
                "image": "sha256:" + "a"*64,
                "environment": {
                    "SERVICEIT_VPS_WRITER_MODE": "LIVE",
                    "SERVICEIT_VPS_POLL_ENABLED": "YES",
                    "SERVICEIT_AI_PROVIDER": "routerai",
                },
                "secrets": [
                    {"source": "serviceit_pg_password",
                     "target": "/run/secrets/serviceit_pg_password"},
                    {"source": "serviceit_bitrix_webhook",
                     "target": "/run/secrets/serviceit_bitrix_webhook"},
                    {"source": "serviceit_routerai_key",
                     "target": "/run/secrets/serviceit_routerai_key"},
                    {"source": "serviceit_brave_search_key",
                     "target": "/run/secrets/serviceit_brave_search_key"},
                ],
                "command": ["python", "-m", "leadpilot.serviceit_vps_worker", "--loop"],
            },
            "observer": {"image": "sha256:" + "b"*64, "environment": {"READONLY": "YES"}},
        },
        "secrets": {
            "serviceit_pg_password": {"file": "/etc/serviceit/secrets/pg_password"},
            "serviceit_bitrix_webhook": {"file": "/etc/serviceit/secrets/bitrix_webhook"},
            "serviceit_routerai_key": {"file": "/etc/serviceit/secrets/routerai_key"},
            "serviceit_brave_search_key": {"file": "/etc/serviceit/secrets/brave_api_key"},
        },
    }


def delta():
    before = original()
    after = deepcopy(before)
    after["services"]["coordinator"]["image"] = "sha256:" + "c"*64
    after["services"]["coordinator"]["environment"].update(release.TINYFISH_FLAGS)
    after["services"]["coordinator"]["secrets"].append({
        "source": "serviceit_tinyfish_key",
        "target": "serviceit_tinyfish_key",
    })
    after["secrets"]["serviceit_tinyfish_key"] = {
        "file": "/etc/serviceit/secrets/tinyfish_api_key"}
    return before, after


def verify(before, after):
    return release.verify_compose_only_changes(
        before, after, "sha256:"+"a"*64, "sha256:"+"c"*64)


def test_four_exact_modules_only_no_restart_or_extra_writers():
    assert release.FILES == (
        "serviceit_b2b_free_public_fallback.py",
        "serviceit_b2b_remote_public_fetch.py",
        "serviceit_b2b_public_fact_extractor.py",
        "serviceit_vps_b2b_deep_research.py",
    )
    assert release.TINYFISH_FLAGS["SERVICEIT_VPS_B2B_FREE_PUBLIC_FETCH_ENABLED"] == "NO"
    assert release.TINYFISH_FLAGS["SERVICEIT_B2B_TENDEM_PUBLIC_EXTRACTION_APPROVED"] == "NO"
    assert release.TINYFISH_FLAGS["SERVICEIT_B2B_CENTER_PUBLIC_EXTRACTION_LICENSED"] == "NO"
    assert release.TINYFISH_SECRET_SOURCE == "/etc/serviceit/secrets/tinyfish_api_key"
    assert release.TINYFISH_SECRET_TARGET == "/run/secrets/serviceit_tinyfish_key"


def test_exact_composition_delta_only():
    before, after = delta()
    release.verify_compose_only_changes(before, after, "sha256:"+"a"*64,
                                        "sha256:"+"c"*64)


@pytest.mark.parametrize("change", [
    lambda x: x["services"]["coordinator"]["environment"].update(
        {"SERVICEIT_VPS_B2B_FREE_PUBLIC_FETCH_ENABLED": "YES"}),
    lambda x: x["services"]["coordinator"]["environment"].update(
        {"SERVICEIT_B2B_TENDEM_PUBLIC_EXTRACTION_APPROVED": "YES"}),
    lambda x: x["services"]["coordinator"]["environment"].update(
        {"SERVICEIT_VPS_POLL_ENABLED": "NO"}),
    lambda x: x["services"]["coordinator"].update({"image": "sha256:"+"d"*64}),
    lambda x: x["services"]["coordinator"]["secrets"].pop(0),
    lambda x: x["services"]["observer"]["environment"].update({"READONLY": "NO"}),
    lambda x: x["services"].update({"extra-writer": {"image": "test"}}),
    lambda x: x["secrets"]["serviceit_tinyfish_key"].update({"file": "/tmp/key"}),
    lambda x: x.update({"name": "other"}),
])
def test_any_unexpected_change_rejected(change):
    before, after = delta()
    change(after)
    with pytest.raises(release.ReleaseBlocked):
        verify(before, after)


def test_live_guarded_release_no_unchecked_secret_or_other_service_restart():
    text = (Path(__file__).parents[1] / "ops" /
            "serviceit_vps_b2b_tinyfish_guarded_install.py").read_text()
    assert "serviceit_vps_b2b_v23_primary_source_receipts" in text
    assert "PENDING_AI_OR_CRM_WRITE_NO_RESTART" in text
    assert "EXISTING_CORE_SOURCE_SHA_UNRECOGNIZED" in text
    assert "TINYFISH_INSTALL_FETCH_ENABLED=NO" in text
    assert "TINYFISH_INSTALL_KEY_MOUNT=YES" in text
    assert "TINYFISH_SECRET_NOT_MOUNTABLE" in text
    assert "COMPOSE_ALREADY_CHANGED_RECHECK_REQUIRED" in text
    assert '"--force-recreate", "coordinator"' in text
    assert '"--network", "none"' in text
