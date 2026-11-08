"""Static cutover fence contract for every legacy non-source CRM writer.

The GitHub variable SERVICEIT_VPS_CUTOVER_ACTIVE must be a global gate for
ALL overlapping GitHub cloud writers before any VPS-live cutover. No API,
secrets, CRM interaction or network required for these tests.
"""
from pathlib import Path
import pytest

BASE = Path(".github/workflows")

# Legacy AI event/batch already gated prior to this change.
JOB_CUTOVER_GATED = [
    "serviceit-bitrix-all-new-batch-ai.yml",
    "serviceit-bitrix-new-card-ai.yml",
    "serviceit-free-cloud-ai-cards.yml",
    "serviceit-crm-duplicate-auto.yml",
    "serviceit-finance-hybrid-live.yml",
    "serviceit-fns-msp-crm-result.yml",
    "serviceit-kanban-three-signal-title-auto.yml",
    "serviceit-leadgen-free-msp-internal-score.yml",
    "serviceit-own-signal-stop-auto.yml",
    "serviceit-postcall-manager-approved-auto.yml",
    "serviceit-stage3-all-new-task-automation.yml",
    "serviceit-stage4-all-new-signed-score-writer.yml",
    "serviceit-stage4-prelim-score-live.yml",
]


@pytest.mark.parametrize("name", JOB_CUTOVER_GATED)
def test_every_overlapping_cloud_crm_writer_blocks_after_vps_cutover(name):
    text = (BASE / name).read_text(encoding="utf8")
    assert "    if: >-\n" in text
    anchor = "      github.ref == 'refs/heads/main' &&\n"
    assert anchor in text
    assert anchor + "      vars.SERVICEIT_VPS_CUTOVER_ACTIVE != 'YES' &&\n" in text
    assert text.count("vars.SERVICEIT_VPS_CUTOVER_ACTIVE != 'YES'") == 1


def test_legacy_poll_live_branch_fenced_but_readonly_diagnostics_survive():
    text = (BASE / "serviceit-bitrix-poll-fallback.yml").read_text(encoding="utf8")
    assert (
        "github.event.comment.body == 'SERVICEIT_OWNER_NEW_CARD_LIVE_ONCE_V1' &&\n"
        "          vars.SERVICEIT_VPS_CUTOVER_ACTIVE != 'YES' &&"
    ) in text
    assert (
        "(github.event_name == 'schedule' &&\n"
        "        vars.SERVICEIT_VPS_CUTOVER_ACTIVE != 'YES' &&"
    ) in text
    # Deliberately keep non-mutating manual diagnostic available.
    assert "github.event_name == 'workflow_dispatch'" in text
    assert "run: python -m leadpilot.serviceit_bitrix_poll_fallback --probe" in text


@pytest.mark.parametrize("name", [
    "serviceit-b2b-appsscript-ingest.yml",
    "serviceit-b2b-direct-id-new-notice.yml",
    "b2b-gmail-to-bitrix-hold.yml",
    "serviceit-cloud-reliability-watchdog.yml",
    "serviceit-live-new-card-reconciliation.yml",
    "serviceit-vps-native-shadow-deploy.yml",
])
def test_sources_watchdogs_and_shadow_are_not_blocked(name):
    text = (BASE / name).read_text(encoding="utf8")
    assert "vars.SERVICEIT_VPS_CUTOVER_ACTIVE != 'YES'" not in text
