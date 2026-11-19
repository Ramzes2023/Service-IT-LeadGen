"""Offline regression: B2B future-card credential gate never requires paid Brave."""
from pathlib import Path

from ops.serviceit_vps_b2b_future_card_readiness import credential_policy


def test_live_required_keys_with_intentionally_disabled_brave_pass():
    effective = {
        "SERVICEIT_BITRIX_WEBHOOK": "mock-bitrix-only",
        "ROUTERAI_API_KEY": "mock-router-only",
        "SERVICEIT_BRAVE_SEARCH_API_KEY": "",
        "SERVICEIT_BRAVE_SEARCH_API_KEY_FILE": "",
    }
    assert credential_policy(effective) == (True, True)


def test_missing_bitrix_or_routerai_still_blocks():
    for missing in ("SERVICEIT_BITRIX_WEBHOOK", "ROUTERAI_API_KEY"):
        effective = {
            "SERVICEIT_BITRIX_WEBHOOK": "mock-bitrix-only",
            "ROUTERAI_API_KEY": "mock-router-only",
            "SERVICEIT_BRAVE_SEARCH_API_KEY": "",
            "SERVICEIT_BRAVE_SEARCH_API_KEY_FILE": "",
        }
        effective[missing] = ""
        assert credential_policy(effective) == (False, True)


def test_nonempty_brave_key_or_file_fails_closed():
    for enabled in ("SERVICEIT_BRAVE_SEARCH_API_KEY",
                    "SERVICEIT_BRAVE_SEARCH_API_KEY_FILE"):
        effective = {
            "SERVICEIT_BITRIX_WEBHOOK": "mock-bitrix-only",
            "ROUTERAI_API_KEY": "mock-router-only",
            "SERVICEIT_BRAVE_SEARCH_API_KEY": "",
            "SERVICEIT_BRAVE_SEARCH_API_KEY_FILE": "",
        }
        effective[enabled] = "mock-value"
        assert credential_policy(effective) == (True, False)


def test_live_exact_environment_explicitly_disables_brave():
    src = Path("leadpilot/serviceit_vps_worker.py").read_text("utf-8")
    assert 'result["SERVICEIT_BRAVE_SEARCH_API_KEY"] = ""' in src
    assert 'result["SERVICEIT_BRAVE_SEARCH_API_KEY_FILE"] = ""' in src
    # Never weaken unrelated source-identity, review, receipt or CRM gates.
    audit = Path("ops/serviceit_vps_b2b_future_card_readiness.py").read_text("utf-8")
    for guard in ("v11_failures == 0", "queue_failures == 0", "crm_verified",
                  "POLL_FRESH_UNDER_12MIN", "NEW_CARD_QUEUE_REVIEW_UNCERTAIN"):
        assert guard in audit
