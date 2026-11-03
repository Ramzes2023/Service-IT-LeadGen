"""Workflow security contract: no autonomous live CRM without repo opt-in."""
from pathlib import Path


def test_b2b_issue_to_bitrix_workflow_requires_explicit_enabled_variable():
    p = Path(".github/workflows/b2b-gmail-to-bitrix-hold.yml")
    yml = p.read_text(encoding="utf-8")
    assert "issue_comment:" in yml and "types: [created]" in yml
    assert "github.event.issue.number == 66" in yml
    assert "github.actor == 'Ramzes2023'" in yml
    assert "startsWith(github.event.comment.body, 'SERVICEIT_B2B_NOTICE_V1')" in yml
    assert "cancel-in-progress: false" in yml
    assert "issues: write" in yml
    assert "vars.SERVICEIT_B2B_CRM_AUTO_ENABLED" in yml
    assert "env.SERVICEIT_B2B_CRM_AUTO_ENABLED == 'YES'" in yml
    assert "secrets.SERVICEIT_BITRIX_WEBHOOK" in yml
    assert "secrets.GITHUB_TOKEN" in yml
    assert "github.event.comment.body }}" not in yml.split("run:")[-1]
    assert "crm.item.add" not in yml
    assert "crm.item.update" not in yml
    assert "SERVICEIT_B2B_GMAIL_APP_PASSWORD" not in yml



def test_all_b2b_source_writers_share_one_non_cancelling_concurrency_group():
    from pathlib import Path
    for workflow in (
        "b2b-gmail-to-bitrix-hold.yml",
        "serviceit-b2b-single-hold-pilot.yml",
        "serviceit-b2b-direct-id-new-notice.yml",
        "serviceit-b2b-appsscript-ingest.yml",
    ):
        text = (Path(".github/workflows") / workflow).read_text("utf-8")
        assert "concurrency:" in text, workflow
        assert "group: serviceit-b2b-bitrix-issue-66" in text, workflow
        assert "cancel-in-progress: false" in text, workflow
    # A serial queue prevents concurrent "read marker then reserve" races.
