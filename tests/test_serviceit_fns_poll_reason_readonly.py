from pathlib import Path
def test_fns_poll_reason_audit_readonly():
    content=(Path(__file__).resolve().parents[1] /
      ".github/workflows/serviceit-vps-fns-poll-reason-readonly.yml").read_text("utf8")
    for item in ("SERVICEIT_FNS_BITRIX_POLL_REASON_READONLY_368",
                 "FNS_POLL_ALL_READONLY=YES","StrictHostKeyChecking=yes",
                 "calibrate_known_card(reader)","discover(reader,lower,now)",
                 "cur.execute('SELECT 1')"):
        assert item in content
    for bad in ("crm.item.update","INSERT INTO","DELETE FROM",
                "docker restart","docker compose up","workflow_dispatch"):
        assert bad not in content
