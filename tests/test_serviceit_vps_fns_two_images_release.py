"""Two-service FNS native release constrained to exact approved code and images."""
from pathlib import Path
import runpy

ROOT=Path(__file__).resolve().parents[1]
def test_two_image_rollback_activation_security():
    p=ROOT/"ops/serviceit_vps_fns_calibration_two_images_activate.py"
    s=p.read_text("utf8")
    cfg=runpy.run_path(str(p))
    assert set(cfg["SERVICES"])=={"auxiliaries","fns-msp"}
    assert cfg["SERVICES"]["auxiliaries"][1]=="serviceit_vps_native_aux.py"
    assert cfg["SERVICES"]["fns-msp"][1]=="serviceit_vps_native_msp.py"
    for token in ('["docker","cp"', "--network","none","--read-only","--no-deps",
                  "COMPOSE_NON_IMAGE_SETTINGS_CHANGED","LIVE_",
                  "FNS_ONLY_ROLLBACK","FNS_ONLY_OTHER_IMAGES_UNCHANGED",
                  "FNS_ONLY_CRM_WRITES_BY_INSTALLER=NO"):
        assert token in s
    for bad in ("crm.item.update","crm.item.add","tasks.task.add",
                "DELETE FROM","DROP TABLE","TRUNCATE TABLE","docker compose down"):
        assert bad not in s

def test_two_image_workflow_exact_owner_pin_and_bounded_modules():
    w=(ROOT/".github/workflows/serviceit-vps-fns-two-images-release.yml").read_text("utf8")
    for expected in ("SERVICEIT_FNS_TWO_IMAGES_RELEASE_368",
                     "github.actor == 'Ramzes2023'",
                     "StrictHostKeyChecking=yes",
                     "127befa6e86916982325ddf39a6acb5424f6641e",
                     "e081445429856bfd988a6fc9ad3c94a9592476c0",
                     "sha256sum --check --status",
                     "FNS_ONLY_AUXILIARIES_PATCH=YES",
                     "FNS_ONLY_FNS_MSP_PATCH=YES"):
        assert expected in w
    assert "workflow_dispatch" not in w


def test_failure_emits_only_safe_codes_before_propagating_exit():
    w=(ROOT/".github/workflows/serviceit-vps-fns-two-images-release.yml").read_text("utf8")
    assert "remote_rc=$?" in w
    assert '[[ "$remote_rc" -eq 0 ]] || exit "$remote_rc"' in w
    assert "^FNS_ONLY_[A-Z0-9_]+=(YES|NO|NOT_NEEDED|FAILED)$" in w
