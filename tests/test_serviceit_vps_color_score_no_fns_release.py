"""Test exact FNS-optional VPS AUX colored score guarded deploy."""
from pathlib import Path
import runpy
ROOT=Path(__file__).resolve().parents[1]
def test_color_score_patch_guard():
    s=(ROOT/"ops/serviceit_vps_score_no_fns_color_activate.py").read_text("utf8")
    v=runpy.run_path(str(ROOT/"ops/serviceit_vps_score_no_fns_color_activate.py"))
    assert v["MODULE"]=="serviceit_preliminary_score_markers.py"
    assert v["NEW_FLAGS"]=={
        "SERVICEIT_VPS_FNS_CRM_RESULT_ENABLED":"NO",
        "SERVICEIT_VPS_FREE_MSP_SCORE_ENABLED":"NO",
        "SERVICEIT_VPS_PROVISIONAL_SCORE_ENABLED":"YES",
    }
    for marker in ("LIVE_COLOR_SCORE_SHA_DRIFT","--network","none",
                   "NON_FLAG_AUX_COMPOSE_CHANGED","UNRELATED_SERVICE_IMAGE_CHANGED",
                   "SCORE_NO_FNS_ROLLBACK","SCORE_NO_FNS_INN_WORKER_UNCHANGED=YES",
                   '"--no-deps"', '"--read-only"'):
        assert marker in s
    for forbidden in ("crm.item.add","crm.item.update","tasks.task.add",
                      "DELETE FROM","DROP TABLE","TRUNCATE TABLE"):
        assert forbidden not in s

def test_color_score_workflow_owner_and_pinned_sources():
    y=(ROOT/".github/workflows/serviceit-vps-color-score-no-fns-release.yml").read_text("utf8")
    for marker in ("SERVICEIT_VPS_SCORE_COLOR_NO_FNS_DEPLOY_314",
                   "github.actor == 'Ramzes2023'",
                   "StrictHostKeyChecking=yes",
                   "5aae5b2288f072b94fb1edc08ce1dd6077ab4309",
                   "80ec482521ef3001508c45424c78706edbf8d536",
                   "sha256sum --check --status"):
        assert marker in y
    assert "workflow_dispatch" not in y
