"""Safely inspect V8 live INN retrieval without CRM/PG mutation."""
from pathlib import Path

def test_v8_source_retry_audit_no_mutations():
    text=(Path(__file__).resolve().parents[1] /
        ".github/workflows/serviceit-vps-b2b-inn-v8-cycle-readonly.yml").read_text("utf8")
    for marker in (
        "SERVICEIT_B2B_V8_INN_CYCLES_READONLY_368",
        "github.actor == 'Ramzes2023'",
        "github.event.issue.number == 368",
        "V8_CORE_CYCLES=",
        "B2B_PUBLIC_INN_V2",
        "V8_RECEIPT_READONLY=YES",
        "V8_RETRY_READONLY=YES",
        "SET TRANSACTION READ ONLY",
        "StrictHostKeyChecking=yes",
        "V8_ALL_READONLY=YES",
    ):
        assert marker in text
    for bad in ("crm.item.update","INSERT INTO","DELETE FROM","docker restart",
                "docker compose up","DROP TABLE","workflow_dispatch"):
        assert bad not in text
