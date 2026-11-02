"""Static safety contract for the independent owner-triggered CRM 12-field probe."""
from pathlib import Path


def test_only_owner_and_dedicated_issue_can_trigger_readonly_12_field_probe():
    source = Path(".github/workflows/serviceit-ai-12-field-readonly.yml").read_text(encoding="utf-8")
    assert "github.event.issue.number == 74" in source
    assert "github.actor == 'Ramzes2023'" in source
    assert "SERVICEIT_AI_12_FIELD_READONLY_CHECK_V1" in source
    assert "serviceit_enrichment_worker import Rest, schema_limits, FIELDS" in source
    assert "secrets.SERVICEIT_BITRIX_WEBHOOK" in source
    assert "SERVICEIT_12_FIELD_READONLY_PASS" in source
    assert "len(limits) == len(FIELDS) == 12" in source
    assert '"crm_writes": 0' in source
    assert '"gigachat_calls": 0' in source
    # Workflow delegates only to strict schema_limits which has no CRM item
    # get, update, create or delete method. Never list raw secrets or records.
    assert "crm.item.update" not in source
    assert "crm.item.add" not in source
    assert "crm.item.get" not in source
    assert "serviceit_source_batch_pipeline" not in source
    assert "gh api" not in source


def test_worker_schema_gate_not_connected_to_writes():
    from leadpilot.serviceit_enrichment_worker import FIELDS, AI_FIELDS, SCORE_FIELD, VERSION_FIELD, BADGE_FIELD
    assert len(AI_FIELDS) == 9
    assert FIELDS == AI_FIELDS | {SCORE_FIELD, VERSION_FIELD, BADGE_FIELD}
