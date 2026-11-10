"""Source identifiers never exposed by issue-gated tender correlation."""
from pathlib import Path

def test_public_tender_correlation_readonly():
    s=(Path(__file__).resolve().parents[1] / ".github/workflows/serviceit-vps-b2b-focus-4673595-readonly.yml").read_text("utf8")
    for req in ("SERVICEIT_B2B_FOUR_4673595_CORRELATE_READONLY_368",
                "SET TRANSACTION READ ONLY","StrictHostKeyChecking=yes",
                "FOCUS_READONLY=YES","FOCUS_CARD_",
                "source==target"):
        assert req in s
    for bad in ("crm.item.update","INSERT INTO","DELETE FROM",
                "docker restart","docker compose up","workflow_dispatch"):
        assert bad not in s
