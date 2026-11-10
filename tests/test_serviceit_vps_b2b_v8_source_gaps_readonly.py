"""Live source-only B2B diagnostic must never modify production."""
from pathlib import Path

def test_b2b_four_source_gap_workflow_is_readonly():
    text=(Path(__file__).resolve().parents[1] /
          ".github/workflows/serviceit-vps-b2b-v8-source-gaps-readonly.yml").read_text("utf8")
    for required in (
        "SERVICEIT_B2B_V8_SOURCE_GAPS_READONLY_368",
        "github.actor == 'Ramzes2023'",
        "StrictHostKeyChecking=yes",
        "SET TRANSACTION READ ONLY",
        "source_findings(item,notice,env,client)",
        "public_buyer_candidate(findings)",
        "source_backed_company_inn_update(item,findings)",
        "V8_GAPS_READONLY=YES",
        "assert len(rows)==4",
    ):
        assert required in text
    for forbidden in (
        "crm.item.update","crm.item.add","tasks.task.add","INSERT INTO",
        "DELETE FROM","docker restart","docker compose up",
        "workflow_dispatch","ROUTERAI_API_KEY"
    ):
        assert forbidden not in text
