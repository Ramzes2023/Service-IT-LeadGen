"""Owner-only read-only live INN and colored score audit."""
from pathlib import Path
def test_four_card_score_audit_readonly():
    y=(Path(__file__).resolve().parents[1]/".github/workflows/serviceit-vps-b2b-inn-color-score-readonly.yml").read_text("utf8")
    for s in ("SERVICEIT_INN_COLOR_SCORE_READONLY_314","github.actor == 'Ramzes2023'",
              "SET TRANSACTION READ ONLY","INN_SCORE_ALL_READONLY=YES",
              "INN_SCORE_CARD_","StrictHostKeyChecking=yes"):
        assert s in y
    for bad in ("crm.item.update","INSERT INTO","DELETE FROM","docker restart","docker compose up","workflow_dispatch"):
        assert bad not in y


def test_score_cycle_diagnostics_do_not_mutate():
    s=(Path(__file__).resolve().parents[1]/".github/workflows/serviceit-vps-b2b-inn-color-score-readonly.yml").read_text("utf8")
    assert "INN_SCORE_AUX_LOG_" in s
    assert "AI_READY=" in s and "TYPE_READY=" in s
    assert "NATIVE_AUX_CYCLE_BLOCKED" in s
    assert "classify_preliminary(i,datetime.now(timezone.utc))" in s
    assert "crm.item.update" not in s


def test_ai_status_diagnostic_bounded_to_public_fixed_machine_code():
    s=(Path(__file__).resolve().parents[1]/".github/workflows/serviceit-vps-b2b-inn-color-score-readonly.yml").read_text("utf8")
    assert "INN_SCORE_CARD_" in s and "_AI_CODE=" in s
    assert "re.fullmatch(r'[A-Z0-9_]{4,85}',raw_status)" in s
    assert "crm.item.update" not in s
