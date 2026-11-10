"""Static release contract: production VPS research patch must be minimal and fenced."""
from pathlib import Path


def test_v8_release_is_two_file_only_and_attested():
    deploy = Path("ops/serviceit_vps_b2b_evidence_v8_activate.py").read_text("utf-8")
    workflow = Path(".github/workflows/serviceit-vps-b2b-evidence-v8-release.yml").read_text("utf-8")
    marker = deploy.split("FILES = (", 1)[1].split(")", 1)[0]
    assert "serviceit_vps_b2b_deep_research.py" in marker
    assert "serviceit_b2b_routerai_deep.py" in marker
    assert "serviceit_vps_worker.py" not in marker
    assert "serviceit_vps_b2b_public_inn.py" not in marker
    assert 'len(sys.argv) == 2 + 2 * len(FILES)' in deploy
    assert 'runtime == dict(zip(FILES, old_shas))' in deploy
    assert 'V4_OR_AI_INFLIGHT_NOT_SAFE_TO_RESTART' in deploy
    assert "'B2B_PUBLIC_INN_V1'" in deploy
    assert 'flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)' in deploy
    assert 'docker' in deploy and '"--network", "none"' in deploy
    assert "ROLLBACK=YES" in deploy
    assert 'current_paths()' in deploy
    assert 'make_pin(old_image, active=True, extra=True, inn=True)' in deploy
    assert 'make_pin(new_image, active=True, extra=True, inn=True)' in deploy
    assert "CURRENT_V7_INN_WRITER_NOT_ACTIVE" in deploy
    assert 'SERVICEIT_B2B_EVIDENCE_V8_RELEASE_368' in workflow
    assert 'e8afe5439609a342c9e43a01cc389acd8857bebce4fe074318d7ec32d0a07b81' in workflow
    assert '54690407408513d3430ac9ea57b90eb4bb7cd9a6eac390e8d869fed68a7f07c3' in workflow
    assert 'git diff --quiet 9997c78bad8594d82def78ea688e7930e0fc5e72 HEAD' in workflow
    assert 'github.actor == \'Ramzes2023\'' in workflow
    assert 'if: always()' in workflow
    assert 'persist-credentials: false' in workflow
