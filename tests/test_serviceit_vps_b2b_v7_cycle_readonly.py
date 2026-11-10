"""V7 actual worker log diagnostic is owner-only and cannot write."""
from pathlib import Path

def test_readonly_v7_cycle_audit_contract():
    text=(Path(__file__).resolve().parents[1] /
        ".github/workflows/serviceit-vps-b2b-v7-cycle-readonly.yml").read_text("utf8")
    for marker in ("SERVICEIT_B2B_V7_INN_CYCLES_READONLY_368",
                   "github.actor == 'Ramzes2023'",
                   "github.event.issue.number == 368",
                   "V7_CORE_CYCLES=", "V7_RECEIPT_READONLY",
                   "SET TRANSACTION READ ONLY",
                   "StrictHostKeyChecking=yes", "V7_ALL_READONLY=YES"):
        assert marker in text
    for bad in ("crm.item.update", "INSERT INTO", "DELETE FROM", "docker restart",
                "docker compose up", "workflow_dispatch"):
        assert bad not in text
