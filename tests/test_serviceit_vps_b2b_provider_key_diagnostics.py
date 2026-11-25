"""Offline checks for B2B readiness diagnostics; no network or credentials."""
from unittest.mock import patch

from leadpilot.serviceit_vps_worker import SearchBudget, exact_environment
from ops.serviceit_vps_b2b_future_card_readiness import provider_key_diagnostics


def test_owner_disabled_brave_does_not_imply_missing_other_keys():
    def reader(env, name):
        return "offline-placeholder"

    with patch("leadpilot.serviceit_vps_worker.secret_file", reader):
        flags, legacy = provider_key_diagnostics(
            {"SERVICEIT_AI_PROVIDER": "routerai"}, reader, exact_environment, SearchBudget)
    assert flags == {
        "BITRIX_MOUNTED_KEY_PRESENT": "YES",
        "ROUTERAI_MOUNTED_KEY_PRESENT": "YES",
        "BRAVE_EXACT_ENV_DISABLED": "YES",
        "BRAVE_BLOCKED_BY_OWNER_POLICY": "YES",
    }
    assert legacy is False


def test_missing_routerai_key_reported_separately():
    def reader(env, name):
        if name == "SERVICEIT_ROUTERAI_API_KEY":
            raise ValueError("offline missing")
        return "offline-placeholder"

    with patch("leadpilot.serviceit_vps_worker.secret_file", reader):
        flags, legacy = provider_key_diagnostics(
            {"SERVICEIT_AI_PROVIDER": "routerai"}, reader, exact_environment, SearchBudget)
    assert flags["BITRIX_MOUNTED_KEY_PRESENT"] == "YES"
    assert flags["ROUTERAI_MOUNTED_KEY_PRESENT"] == "NO"
    assert flags["BRAVE_EXACT_ENV_DISABLED"] == "UNKNOWN"
    assert flags["BRAVE_BLOCKED_BY_OWNER_POLICY"] == "YES"
    assert legacy is False
