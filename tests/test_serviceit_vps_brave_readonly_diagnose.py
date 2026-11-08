"""Check read-only Brave diagnosis never mutates native LIVE or discloses tokens."""
from pathlib import Path

WORKFLOW = Path(".github/workflows/serviceit-vps-brave-readonly-diagnose.yml").read_text("utf8")


def test_owner_only_manual_diagnostic_gate():
    assert "workflow_dispatch:" in WORKFLOW
    assert "github.actor == 'Ramzes2023'" in WORKFLOW
    assert "github.ref == 'refs/heads/main'" in WORKFLOW
    assert "DIAGNOSE_BRAVE_WEB_CONTEXT_2_PUBLIC_API_CALLS" in WORKFLOW
    assert "serviceit-vps-native-shadow-readonly" in WORKFLOW
    assert "persist-credentials" not in WORKFLOW
    assert "environment: serviceit-vps-readonly" in WORKFLOW


def test_no_crm_or_server_mutations():
    for forbidden in (
        "docker compose up", '"up",', '"build",', "docker image rm",
        "crm.item.update", "tasks.task.add",
        "SERVICEIT_BRAVE_SEARCH_API_KEY: ${{",
        'secrets.SERVICEIT_BRAVE_SEARCH_API_KEY',
        "docker restart", "docker stop",
        "cat /etc/serviceit/secrets",
    ):
        assert forbidden not in WORKFLOW
    assert '"run","--rm","--no-deps","--no-build","-T"' in WORKFLOW
    assert "CHROMIUM_HEALTHY=" in WORKFLOW
    assert "NATIVE_FOUR_HEALTHY=" in WORKFLOW
    assert "BRAVE_SECRET_MOUNT_IN_LIVE=" in WORKFLOW
    assert "EXISTING_CONTAINERS_RESTARTED=0" in WORKFLOW


def test_probe_status_is_safe_allowlist_not_raw_provider_response():
    assert 'json.loads(raw)' in WORKFLOW
    assert 'len(raw)>1600' in WORKFLOW
    assert 're.fullmatch(r"[A-Z][A-Z0-9_]{1,65}",v)' in WORKFLOW
    assert "BRAVE_WEB_STATUS=" in WORKFLOW
    assert "BRAVE_LLM_CONTEXT_STATUS=" in WORKFLOW
    assert "BRAVE_INVALID_KEY_401" in WORKFLOW
    assert "BRAVE_RATE_LIMIT_429" in WORKFLOW
    assert "BRAVE_CONTEXT_SCHEMA_UNVERIFIED" in WORKFLOW
    assert "print(r.stderr)" not in WORKFLOW
    assert "print(raw)" not in WORKFLOW
    assert "print(d)" not in WORKFLOW
    assert "BRAVE_DISPOSABLE_PROBE_EXIT=" in WORKFLOW
    assert "REAL_NEW_CARD_E2E=NOT_YET_VERIFIED" in WORKFLOW
