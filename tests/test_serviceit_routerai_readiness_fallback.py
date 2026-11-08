"""Production new-card GitHub workflows: RouterAI only, no GigaChat fallback.

Read-only synthetic file-policy tests. No CRM, network or model calls.
"""
from pathlib import Path

WORKFLOWS = (
    ".github/workflows/serviceit-bitrix-new-card-ai.yml",
    ".github/workflows/serviceit-bitrix-all-new-batch-ai.yml",
)


def test_cloud_production_uses_routerai_only_with_existing_privacy_gates():
    for path in WORKFLOWS:
        body = Path(path).read_text("utf-8")
        assert "AI_PROVIDER: routerai" in body
        assert "SERVICEIT_ROUTERAI_PRIVACY_APPROVED: ${{ vars.SERVICEIT_ROUTERAI_PRIVACY_APPROVED }}" in body
        assert "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED: ${{ vars.SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED }}" in body
        assert "AI_ENABLE: ${{ vars.SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED == 'YES' && '1' || '0' }}" in body
        assert "SERVICEIT_ROUTERAI_FALLBACK_TO_GIGACHAT: 'NO'" in body
        assert "GIGACHAT_CREDENTIALS:" not in body
        assert "GIGACHAT_SCOPE:" not in body
        assert "GIGACHAT_MODEL:" not in body
        assert "serviceit_gigachat_ca_setup" not in body
        assert "GIGACHAT_CA_BUNDLE_FILE" not in body


def test_only_github_actions_secrets_supply_routerai_key():
    for path in WORKFLOWS:
        body = Path(path).read_text("utf-8")
        assert "ROUTERAI_API_KEY: ${{ secrets.SERVICEIT_ROUTERAI_API_KEY }}" in body
        assert "SERVICEIT_GH_LEDGER_KEY: ${{ secrets.SERVICEIT_GH_LEDGER_KEY }}" in body
        assert "SERVICEIT_BITRIX_EVENT_BRIDGE_KEY: ${{ secrets.SERVICEIT_BITRIX_EVENT_BRIDGE_KEY }}" in body
        assert "vars.SERVICEIT_VPS_CUTOVER_ACTIVE != 'YES'" in body
        assert "concurrency:" in body
        assert "cancel-in-progress: false" in body
