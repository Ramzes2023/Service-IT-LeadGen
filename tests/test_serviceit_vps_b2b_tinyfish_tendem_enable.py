"""Offline tests: only public Tendem flag can turn on, never B2B Center.

No Docker, Bitrix, external API or PostgreSQL calls.
"""
from copy import deepcopy
from pathlib import Path

import pytest

from ops import serviceit_vps_b2b_tinyfish_tendem_enable as mod


def data():
    root = {
        "name": "serviceit-leadgen-vps",
        "services": {
            "coordinator": {
                "image": "sha256:" + "a"*64,
                "command": ["python", "-m", "leadpilot.serviceit_vps_worker", "--loop"],
                "environment": {
                    "SERVICEIT_VPS_WRITER_MODE": "LIVE",
                    "SERVICEIT_VPS_POLL_ENABLED": "YES",
                    "SERVICEIT_AI_PROVIDER": "routerai",
                    **mod.FLAGS_OFF,
                },
                "secrets": [
                    {"source": "serviceit_pg_password"},
                    {"source": "serviceit_bitrix_webhook"},
                    {"source": "serviceit_routerai_key"},
                    {"source": "serviceit_brave_search_key"},
                    {"source": "serviceit_tinyfish_key"},
                ],
            },
            "observer": {"image": "sha256:" + "b"*64},
        },
        "secrets": {"serviceit_tinyfish_key":
                    {"file": "/etc/serviceit/secrets/tinyfish_api_key"}},
    }
    new = deepcopy(root)
    new["services"]["coordinator"]["environment"].update(mod.FLAGS_ON)
    return root, new


def test_exact_two_flags_are_only_permitted_delta():
    before, after = data()
    mod.diff_only_two_flags(before, after)
    assert mod.FLAGS_ON[mod.FEATURE] == "YES"
    assert mod.FLAGS_ON[mod.TENDEM] == "YES"
    assert mod.FLAGS_ON[mod.B2B] == "NO"
    assert mod.FLAGS_ON[mod.KEY] == "/run/secrets/serviceit_tinyfish_key"


@pytest.mark.parametrize("change", [
    lambda d: d["services"]["coordinator"]["environment"].update(
        {"SERVICEIT_B2B_CENTER_PUBLIC_EXTRACTION_LICENSED": "YES"}),
    lambda d: d["services"]["coordinator"]["environment"].update(
        {"SERVICEIT_AI_PROVIDER": "another"}),
    lambda d: d["services"]["coordinator"]["environment"].update(
        {"SERVICEIT_VPS_POLL_ENABLED": "NO"}),
    lambda d: d["services"]["coordinator"].update({"image": "other"}),
    lambda d: d["services"]["coordinator"]["secrets"].pop(),
    lambda d: d["services"]["observer"].update({"image": "other"}),
    lambda d: d.update({"name": "another-project"}),
    lambda d: d["secrets"]["serviceit_tinyfish_key"].update(
        {"file": "/tmp/plaintext"}),
])
def test_drift_blocks_source_enable(change):
    b, a = data()
    change(a)
    with pytest.raises(mod.ActivationBlocked):
        mod.diff_only_two_flags(b, a)


def test_activate_script_is_owner_and_receipt_fenced():
    text = (Path(__file__).parents[1] / "ops" /
            "serviceit_vps_b2b_tinyfish_tendem_enable.py").read_text()
    for item in (
        "pg_try_advisory_lock", # not present; check global install lock instead
    ):
        assert item not in text
    for item in (
        ".serviceit-five-minute-activate.lock",
        "PENDING_AI_OR_CRM_WRITE_NO_RESTART",
        "serviceit_vps_b2b_v23_primary_source_receipts",
        "serviceit_vps_b2b_tender_doc_receipts",
        'SERVICEIT_B2B_CENTER_PUBLIC_EXTRACTION_LICENSED',
        'FLAGS_ON',
        'FLAGS_OFF',
        '"--force-recreate", "coordinator"',
        "_ROLLBACK=YES",
    ):
        assert item in text
    assert "TINYFISH_TENDEM_ENABLE" in text
