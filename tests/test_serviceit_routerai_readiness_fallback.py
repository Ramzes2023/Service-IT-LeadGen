"""RouterAI became primary after real API attestation 42034007218.

GigaChat remains pre-provisioned only as standby, never unpinned TLS.
Both workflows keep explicit provider and privacy gates and fail closed.
"""
from pathlib import Path

WORKFLOWS = (
    ".github/workflows/serviceit-bitrix-new-card-ai.yml",
    ".github/workflows/serviceit-bitrix-all-new-batch-ai.yml",
)

def test_routerai_is_primary_only_under_user_approved_provider_and_privacy():
    for path in WORKFLOWS:
        content=Path(path).read_text("utf-8")
        provider=next(row for row in content.splitlines()
                      if row.strip().startswith("AI_PROVIDER:"))
        assert "vars.SERVICEIT_AI_PROVIDER == 'routerai'" in provider
        assert "vars.SERVICEIT_ROUTERAI_PRIVACY_APPROVED == 'YES_1062_21'" in provider
        assert "secrets.SERVICEIT_ROUTERAI_API_KEY != ''" in provider
        assert "&& 'routerai' || 'gigachat'" in provider
        # API completed live synthetic validation under new secret #42034007218.
        assert "SERVICEIT_ROUTERAI_LIVE_READY == 'YES_API_CHECK_PASSED'" not in provider


def test_gigachat_tls_is_pinned_for_runtime_routerai_failure():
    for path in WORKFLOWS:
        content=Path(path).read_text("utf-8")
        assert "SERVICEIT_ROUTERAI_FALLBACK_TO_GIGACHAT: 'YES'" in content
        assert "serviceit_gigachat_ca_setup" in content
        assert "GIGACHAT_CA_BUNDLE_FILE" in content
        # Never conditionally skip TLS setup when RouterAI is selected.
        for line in content.splitlines():
            assert "if: vars.SERVICEIT_AI_PROVIDER !=" not in line
        assert "secrets.GIGACHAT_CREDENTIALS" in content


def test_both_provider_tokens_remain_github_secrets():
    for path in WORKFLOWS:
        content=Path(path).read_text("utf-8")
        assert "secrets.SERVICEIT_ROUTERAI_API_KEY" in content
        assert "ROUTERAI_API_KEY: ${{ secrets.SERVICEIT_ROUTERAI_API_KEY }}" in content
        assert "GIGACHAT_CREDENTIALS: ${{ secrets.GIGACHAT_CREDENTIALS }}" in content
