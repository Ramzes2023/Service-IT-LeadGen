"""Static audit: keep source-to-CRM card creation while avoiding GigaChat
double-processing after native RouterAI worker takes over.

A source-generated HOLD card remains eligible for VPS future-only polling.
This test does not call any CRM/provider or perform an actual cutover.
"""
from pathlib import Path

SOURCES = {
    "serviceit-b2b-appsscript-ingest.yml": "repository_dispatch:",
    "serviceit-b2b-direct-id-new-notice.yml": "issue_comment:",
    "b2b-gmail-to-bitrix-hold.yml": "issue_comment:",
}


def test_each_source_retains_crm_intake_but_disables_old_model_at_cutover():
    for name, trigger in SOURCES.items():
        text = Path(".github/workflows", name).read_text("utf8")
        assert trigger in text
        assert "gh workflow disable" not in text
        assert "vars.SERVICEIT_VPS_CUTOVER_ACTIVE == 'YES' && 'NO'" in text
        assert "vars.SERVICEIT_VPS_CUTOVER_ACTIVE != 'YES' && vars.SERVICEIT_B2B_GIGACHAT_AUTO_ENABLED == 'YES'" in text
        assert "AI_ENABLE:" in text
        assert "SERVICEIT_B2B_GIGACHAT_AUTO_ENABLED:" in text
    gmail = Path(".github/workflows/b2b-gmail-to-bitrix-hold.yml").read_text("utf8")
    assert 'env.SERVICEIT_B2B_CRM_AUTO_ENABLED == \'YES\'' in gmail
    assert "--live" in gmail
    appscript = Path(".github/workflows/serviceit-b2b-appsscript-ingest.yml").read_text("utf8")
    assert "vars.SERVICEIT_B2B_INGEST_ENABLED == 'YES'" in appscript
    direct = Path(".github/workflows/serviceit-b2b-direct-id-new-notice.yml").read_text("utf8")
    assert "vars.SERVICEIT_B2B_DIRECT_AUTO_ENABLED == 'YES'" in direct
