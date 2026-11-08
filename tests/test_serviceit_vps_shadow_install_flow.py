"""Regression for false-success SSH streamed shell scripts (VPS run 42478203798).

No SSH, Docker, Bitrix, PostgreSQL, credentials or network required.
"""
from pathlib import Path


def test_shadow_script_not_consumed_from_remote_ssh_stdin():
    workflow = Path(".github/workflows/serviceit-vps-native-shadow-deploy.yml").read_text("utf8")
    script = Path("deploy/serviceit-vps-native-shadow-install.sh").read_text("utf8")
    assert "bash -s --" not in workflow
    assert 'scp -i "$RUNNER_TEMP/serviceit-shadow-ssh/id"' in workflow
    assert "deploy/serviceit-vps-native-shadow-install.sh" in workflow
    assert "script_digest=" in workflow
    assert "sha256sum" in workflow
    assert "</dev/null" in workflow
    assert "--no-deps coordinator python -c" in script
    assert "--no-deps coordinator python -m leadpilot.serviceit_vps_worker --migrate" in script
    assert script.count("</dev/null") >= 2
    assert script.count("run -T") == 2


def test_success_only_after_real_four_healthchecks_and_pg_migration():
    script = Path("deploy/serviceit-vps-native-shadow-install.sh").read_text("utf8")
    assert "expected_services=(coordinator auxiliaries business observer)" in script
    assert 'docker inspect --format' in script
    assert "'true:healthy'" in script
    assert "SHADOW_EXPECTED_FOUR_HEALTHY_SERVICES_NOT_CONFIRMED" in script
    assert "SHADOW_FOUR_HEALTHY_SERVICES=PASS" in script
    assert "SHADOW_DB_SCHEMA_STEP_FINISHED=YES" in script
    assert script.index("SHADOW_DB_SCHEMA_STEP_FINISHED=YES") < script.index('up \\')
    assert script.index("SHADOW_FOUR_HEALTHY_SERVICES=PASS") < script.index(
        "SERVICEIT_VPS_NATIVE_SHADOW_DEPLOYED_SHA="
    )
    assert "SERVICEIT_VPS_CUTOVER_AUTHORIZED=NO" in script
    assert "SERVICEIT_VPS_WRITER_MODE=READONLY" in script
