from pathlib import Path
def test_fns_auxiliary_root_cause_probe_is_readonly():
    w=(Path(__file__).resolve().parents[1] /
       ".github/workflows/serviceit-vps-fns-aux-only-readonly.yml").read_text('utf8')
    for s in ("SERVICEIT_FNS_AUX_FORENSICS_READONLY_368",
              "github.actor == 'Ramzes2023'","StrictHostKeyChecking=yes",
              "FNS_AUX_","ALL_READONLY","OOMKilled","ExitCode"):
        assert s in w
    for bad in ("docker start","docker restart","docker compose up",
                "crm.item.update","INSERT INTO","workflow_dispatch"):
        assert bad not in w


def test_aux_config_probe_redacts_env_values():
    w=(Path(__file__).resolve().parents[1] /
       ".github/workflows/serviceit-vps-fns-aux-only-readonly.yml").read_text('utf8')
    assert "DOCKER_CONFIG_ENV" in w
    assert "GATE_"+'SERVICEIT_VPS_FNS_CRM_RESULT_ENABLED' in w or "('SERVICEIT_VPS_FNS_CRM_RESULT_ENABLED','NO')" in w
    assert "json .Config.Env" in w
    assert "print(allowed)" not in w
    assert "print(rows)" not in w
