"""No unverified RouterAI API may halt live ServiceIT new-card automation."""
from pathlib import Path

WORKFLOWS = (
    ".github/workflows/serviceit-bitrix-new-card-ai.yml",
    ".github/workflows/serviceit-bitrix-all-new-batch-ai.yml",
)

def test_routerai_requires_successful_live_api_attestation_to_replace_gigachat():
    for path in WORKFLOWS:
        content=Path(path).read_text("utf-8")
        provider=next(row for row in content.splitlines()
                      if row.strip().startswith("AI_PROVIDER:"))
        assert "vars.SERVICEIT_AI_PROVIDER == 'routerai'" in provider
        assert "vars.SERVICEIT_ROUTERAI_LIVE_READY == 'YES_API_CHECK_PASSED'" in provider
        assert "vars.SERVICEIT_ROUTERAI_PRIVACY_APPROVED == 'YES_1062_21'" in provider
        assert "|| 'gigachat'" in provider


def test_gigachat_tls_trust_is_not_skipped_during_fallback():
    for path in WORKFLOWS:
        content=Path(path).read_text("utf-8")
        ifline=next(row for row in content.splitlines()
                    if row.strip().startswith("if: vars.SERVICEIT_AI_PROVIDER !="))
        assert "vars.SERVICEIT_ROUTERAI_LIVE_READY != 'YES_API_CHECK_PASSED'" in ifline
        assert "vars.SERVICEIT_ROUTERAI_PRIVACY_APPROVED != 'YES_1062_21'" in ifline
        assert "serviceit_gigachat_ca_setup" in content


def test_routerai_key_remains_a_github_secret():
    for path in WORKFLOWS:
        text=Path(path).read_text("utf-8")
        assert "secrets.SERVICEIT_ROUTERAI_API_KEY" in text
        assert "ROUTERAI_API_KEY: ${{ secrets.SERVICEIT_ROUTERAI_API_KEY }}" in text
