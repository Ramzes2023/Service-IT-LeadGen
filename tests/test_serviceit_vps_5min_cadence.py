"""Native VPS 5-minute scheduling regression safety, no CRM or network calls."""

import inspect
from leadpilot import serviceit_vps_worker
from leadpilot import serviceit_vps_native_aux
from leadpilot import serviceit_vps_native_business
from leadpilot import serviceit_vps_native_observer
from leadpilot import serviceit_vps_native_msp


def test_three_live_bitrix_cycles_share_five_minute_interval():
    for module in (
        serviceit_vps_worker,
        serviceit_vps_native_aux,
        serviceit_vps_native_business,
    ):
        assert module.INTERVAL_SECONDS == 300
        source = inspect.getsource(module.main)
        assert "cycle_started = time.monotonic()" in source
        assert (
            "time.sleep(max(0.0, INTERVAL_SECONDS - "
            "(time.monotonic() - cycle_started)))"
        ) in source


def test_natural_long_cadence_preserved_for_readonly_observer_and_official_msp():
    # Do not download 2–3GB official FNS zip every 5 minutes.
    assert serviceit_vps_native_observer.INTERVAL_SECONDS == 3600
    assert serviceit_vps_native_msp.INTERVAL_SECONDS == 86400


def test_exactly_one_loop_process_per_service_remains_a_compose_contract():
    from pathlib import Path
    config = (
        Path(__file__).resolve().parents[1]
        / "deploy" / "serviceit-vps.compose.yml"
    ).read_text(encoding="utf-8")
    for name in (
        "serviceit_vps_worker",
        "serviceit_vps_native_aux",
        "serviceit_vps_native_business",
    ):
        assert config.count(f'leadpilot.{name}", "--loop"') == 1
    assert 'SERVICEIT_VPS_INTAKE_ENABLED: "NO"' in config
