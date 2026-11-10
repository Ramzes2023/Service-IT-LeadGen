from pathlib import Path
def test_fns_stage_probe_contains_only_readonly_calls():
    s=(Path(__file__).resolve().parents[1]/
       ".github/workflows/serviceit-vps-fns-aux-stage-readonly.yml").read_text("utf8")
    for token in ("SERVICEIT_FNS_AUX_STAGES_READONLY_368",
                  "SET TRANSACTION READ ONLY","FNS_STAGE_READONLY=YES",
                  "StrictHostKeyChecking=yes","eligible_cards(",
                  "result_field_verified("):
        assert token in s
    for bad in ("crm.item.update","INSERT INTO","DELETE FROM","docker restart",
                "docker compose up","workflow_dispatch"):
        assert bad not in s
