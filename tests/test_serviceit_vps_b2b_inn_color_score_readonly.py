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
