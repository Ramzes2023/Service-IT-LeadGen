"""V7 deploy: only reviewed five modules and guarded one-flag-only activation."""
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "serviceit_vps_worker.py",
    "serviceit_vps_native_fence.py",
    "serviceit_public_evidence_research.py",
    "serviceit_b2b_public_supplement.py",
    "serviceit_vps_b2b_public_inn.py",
)


def test_v7_deployment_contract():
    path = ROOT / "ops/serviceit_vps_b2b_inn_v7_activate.py"
    source = path.read_text("utf8")
    cfg = runpy.run_path(str(path))
    assert cfg["FILES"] == FILES
    assert cfg["FLAG"] == "SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED"
    assert cfg["EXTRA_FLAG"] == "SERVICEIT_PUBLIC_COMPANY_REQUISITES_EXTRA_SEARCH_ENABLED"
    assert cfg["INN_FLAG"] == "SERVICEIT_VPS_B2B_PUBLIC_INN_ENABLED"
    assert cfg["make_pin"]("sha256:" + "a" * 64, active=True, extra=True) == (
        'services:\n  coordinator:\n    image: "sha256:' + "a" * 64 +
        '"\n    pull_policy: never\n    environment:\n'
        '      SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED: "YES"\n'
        '      SERVICEIT_PUBLIC_COMPANY_REQUISITES_EXTRA_SEARCH_ENABLED: "YES"\n'
    )
    assert cfg["INN_FLAG"] in cfg["make_pin"](
        "sha256:" + "a" * 64, active=True, extra=True, inn=True)
    for marker in (
        "LIVE_INN_V7_SHA_DRIFT",
        "CURRENT_V6_ACTIVE_INN_WRITER_NOT_ALREADY_ENABLED",
        "ONLY_ONE_NEW_INN_FLAG_REQUIRED",
        "V4_OR_AI_INFLIGHT_NOT_SAFE_TO_RESTART",
        "COORDINATOR_PYTHON_SHA_MISMATCH",
        "B2B_INN_V7_ROLLBACK=YES",
        '"--network", "none"',
    ):
        assert marker in source
    assert "crm.item.update" not in source
    assert "tasks.task.add" not in source
    assert "docker compose down" not in source


def test_v7_owner_scoped_workflow_pins_actual_live_sha_manifest():
    path = ROOT / ".github/workflows/serviceit-vps-b2b-inn-v7-release.yml"
    workflow = path.read_text("utf8")
    for marker in (
        "github.actor == 'Ramzes2023'",
        "github.event.issue.number == 368",
        "SERVICEIT_B2B_INN_V7_RELEASE_368",
        "StrictHostKeyChecking=yes",
        "ce706d0777295db4b03fae714c12a83d47ffa2c9",
        "d640f68e000aef521eff440967618b34f6feb233b65a8a379ab66fc8f6fe5f9d",
        "ed581c4b27fd5d2a948a51ef8f23b0d32e956a161c019eec3e25559fe5e0207a",
        "5bcef8732b8d11c3af744b6eef3b570859a2f0424596f36d4d59ca89347e34e9",
        "'MISSING'"
    ):
        assert marker in workflow
    assert "remote_rc=$?" in workflow
    assert "B2B_INN_V7_VPS_LIVE_ENABLED=YES" in workflow
    assert "ops/serviceit_vps_b2b_inn_v7_activate.py" in workflow
    assert "ops/serviceit_vps_b2b_contacts_v5_deploy.py" not in workflow
