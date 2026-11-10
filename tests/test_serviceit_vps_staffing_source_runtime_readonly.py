"""Source runtime audit is owner-only and read only."""
from pathlib import Path
def test_staffing_audit_contract():
    code=(Path(__file__).resolve().parents[1] /
        ".github/workflows/serviceit-vps-b2b-4673595-source-readonly.yml").read_text("utf8")
    for marker in ("SERVICEIT_B2B_4673595_SOURCE_RUNTIME_READONLY_314",
                   "github.actor == 'Ramzes2023'","StrictHostKeyChecking=yes",
                   "SET TRANSACTION READ ONLY","STAFFING_ALL_READONLY=YES",
                   "STAFFING_\'+label+\'_CLASSIFIER","STAFFING_PG_SOURCE_PRESENT"):
        assert marker in code
    for bad in ("crm.item.add","crm.item.update","INSERT INTO",
                "DELETE FROM","docker compose up","docker restart",
                "workflow_dispatch"):
        assert bad not in code
