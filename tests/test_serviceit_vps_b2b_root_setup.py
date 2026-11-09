"""Offline-only Caddy route validation and HMAC bootstrap allowlist."""
from pathlib import Path
import runpy

import pytest

BASE = Path(__file__).parents[1]
INSTALLER = runpy.run_path(
    str(BASE / "deploy/serviceit-vps-b2b-caddy-root-enable.py")
)
SECRETS = runpy.run_path(
    str(BASE / "deploy/serviceit-vps-interactive-secret-install.py")
)
render = INSTALLER["render"]
ActivationBlocked = INSTALLER["ActivationBlocked"]


def test_b2b_hmac_supported_without_overwrite_of_old_secrets():
    assert "b2b_ingest_hmac" in SECRETS["AUTHORIZED"]
    assert "pg_password" not in SECRETS["AUTHORIZED"]
    assert "bitrix_webhook" not in SECRETS["AUTHORIZED"]


def test_caddy_injection_only_one_new_route_before_existing_catchall():
    old = (BASE / "deploy/serviceit-vps.caddy.example").read_text("utf-8")
    after = render(old)
    assert after.count("reverse_proxy 127.0.0.1:18781") == 1
    assert after.count("reverse_proxy 127.0.0.1:18780") == 2
    assert after.count("leadgen.serviseit.ru {") == 1
    assert after.index("handle @serviceit_b2b_direct") < after.index(
        '    handle {\n        respond "Not Found" 404'
    )
    assert render(after) == after
    assert "max_size 6KB" in after


def test_caddy_valid_production_no_catchall_is_supported():
    """Observed live VPS has no exact '    handle {' site catchall."""
    old = (BASE / "deploy/serviceit-vps.caddy.example").read_text("utf-8")
    old = old.replace('    handle {\n        respond "Not Found" 404\n    }\n', '')
    after = render(old)
    assert after != old
    assert after.count("reverse_proxy 127.0.0.1:18781") == 1
    assert after.count("reverse_proxy 127.0.0.1:18780") == 2
    assert after.index("handle @serviceit_b2b_direct") < after.rindex("\n}")
    assert render(after) == after


def test_caddy_valid_tabs_or_two_space_catchall_is_supported():
    old = (BASE / "deploy/serviceit-vps.caddy.example").read_text("utf-8")
    old = old.replace('    handle {\n        respond "Not Found" 404\n    }',
                      '\thandle {\n\t\trespond "Not Found" 404\n\t}')
    after = render(old)
    assert after.index("handle @serviceit_b2b_direct") < after.index(
        '\thandle {\n\t\trespond "Not Found" 404'
    )


def test_caddy_fails_closed_on_unknown_site_or_missing_routes():
    old = (BASE / "deploy/serviceit-vps.caddy.example").read_text("utf-8")
    for bad in (
        old.replace("leadgen.serviseit.ru", "unknown.example"),
        old.replace("@bitrix_event", "@another_event").replace(
            "/integrations/serviceit/bitrix24/new-card", "/other-path"),
        old + old,
    ):
        with pytest.raises(ActivationBlocked):
            render(bad)


def test_caddy_rejects_route_collision():
    old = (BASE / "deploy/serviceit-vps.caddy.example").read_text("utf-8")
    with pytest.raises(ActivationBlocked):
        render(old.replace(
            'respond "Not Found" 404',
            'respond "/integrations/serviceit/b2b-center/notice" 404'
        ))
