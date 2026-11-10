from pathlib import Path
def test_fns_runtime_sha_readonly_audit():
    s=(Path(__file__).resolve().parents[1] /
       ".github/workflows/serviceit-vps-fns-runtime-image-sha-readonly.yml").read_text("utf8")
    for marker in ("SERVICEIT_FNS_AUX_IMAGE_SHA_READONLY_368","github.actor == 'Ramzes2023'",
                   "StrictHostKeyChecking=yes","docker','cp'","say(\'ALL_READONLY\',\'YES\')",
                   "sha256sum leadpilot/"):
        assert marker in s
    for bad in ("crm.item.update","INSERT INTO","DELETE FROM","docker restart","docker compose up","workflow_dispatch"):
        assert bad not in s
