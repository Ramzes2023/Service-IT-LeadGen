"""CI guards: browser activation is explicit, owner-only and rollback-capable."""
from pathlib import Path

WORKFLOW = Path(".github/workflows/serviceit-vps-browser-live-activate.yml").read_text("utf8")
SCRIPT = Path("deploy/serviceit-vps-browser-live-activate.py").read_text("utf8")
COMPOSE = Path("deploy/serviceit-vps-browser.compose.yml").read_text("utf8")


def test_owner_only_manual_browser_rollout():
    assert "workflow_dispatch:" in WORKFLOW
    assert "github.actor == 'Ramzes2023'" in WORKFLOW
    assert "github.ref == 'refs/heads/main'" in WORKFLOW
    assert "ACTIVATE_ISOLATED_CHROMIUM_PUBLIC_RESEARCH_1062_21" in WORKFLOW
    assert "git archive --format=tar.gz" in WORKFLOW
    assert "sha256sum --check --status" in WORKFLOW
    assert "persist-credentials: false" in WORKFLOW
    assert "environment: serviceit-vps-readonly" in WORKFLOW
    assert "serviceit-vps-native-shadow-readonly" in WORKFLOW
    assert "serviceit-vps-browser-live-activate.py" in WORKFLOW
    assert "docker compose up" not in WORKFLOW


def test_live_updater_only_replaces_coordinator_after_browser_smoke():
    assert "smoke_browser()" in SCRIPT
    assert "BROWSER_REAL_PUBLIC_RENDER_UNVERIFIED" in SCRIPT
    assert "BROWSER_REAL_PUBLIC_RENDER" not in SCRIPT or "BROWSER_REAL_PUBLIC_RENDER_UNVERIFIED" in SCRIPT
    assert 'compose_core(core_yaml, config, "build", "coordinator"' in SCRIPT
    assert '"--force-recreate", "coordinator"' in SCRIPT
    assert "write_private(config, original)" in SCRIPT
    assert "serviceit-leadgen-vps-coordinator:latest" in SCRIPT
    assert '"SERVICEIT_VPS_BROWSER_RESEARCH_ENABLED": "YES"' in SCRIPT
    assert '"SERVICEIT_VPS_BROWSER_PROXY_ISOLATION_VERIFIED": "YES"' in SCRIPT
    assert "CORE_FOUR_HEALTHY=PASS" in SCRIPT
    # Failure must identify an opaque stage, not leak stderr, URL or secrets.
    assert "BLOCKED_PHASE=" in SCRIPT
    assert "BLOCKED_COMMAND_EXIT=" in SCRIPT
    assert 'browser_started = True  # Roll back even if Compose starts just one container.' in SCRIPT
    assert 'compose_browser(browser_yaml, browser_env, "down"' in SCRIPT
    for forbidden in ("tasks.task.add", "crm.item.update", '"--once"',
                      "SERVICEIT_VPS_CUTOVER_AUTHORIZED=NO"):
        assert forbidden not in SCRIPT


def test_bridge_network_sandbox_shape():
    assert "internal: true" in COMPOSE
    assert "serviceit_vps_public_browser_proxy" in COMPOSE
    assert '127.0.0.1:18785:8766' in COMPOSE\n    assert "ports:" not in COMPOSE.split("\\n  egress:", 1)[0]
    assert "user: pwuser" in COMPOSE
    assert "cap_drop: [ALL]" in COMPOSE
    assert "read_only: true" in COMPOSE
    assert "SERVICEIT_VPS_BROWSER_PROXY_URL" in COMPOSE
    assert "network_mode: host" not in COMPOSE
    assert "docker.sock" not in COMPOSE
