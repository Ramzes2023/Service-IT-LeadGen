"""Offline guards for dedicated, host-enforced Chromium AppArmor profile."""
from pathlib import Path


def test_browser_container_alone_gets_enforcing_userns_profile():
    policy = Path("deploy/serviceit-chromium.apparmor").read_text("utf8")
    browser = Path("deploy/serviceit-vps-browser.compose.yml").read_text("utf8")
    core = Path("deploy/serviceit-vps.compose.yml").read_text("utf8")
    proxy = browser.split("\n  egress:", 1)[1]
    scoped_browser = browser.split("\n  egress:", 1)[0]
    assert "profile serviceit-chromium flags=(attach_disconnected,mediate_deleted)" in policy
    assert "  userns," in policy
    assert "  deny mount," in policy
    assert "  deny @{PROC}/* w," in policy
    assert "  deny /sys/kernel/security/** rwklx," in policy
    assert "flags=(unconfined)" not in policy
    assert 'apparmor:serviceit-chromium' in scoped_browser
    assert 'apparmor:serviceit-chromium' not in proxy
    assert 'apparmor:serviceit-chromium' not in core
    assert 'no-new-privileges:true' in scoped_browser
    assert 'seccomp:${SERVICEIT_VPS_BROWSER_SECCOMP_PROFILE:' in scoped_browser
    assert 'cap_drop: [ALL]' in scoped_browser
    assert 'internal: true' in browser
    assert 'apparmor=unconfined' not in browser
    assert '--no-sandbox' not in browser


def test_offline_probe_must_select_custom_profile_and_no_network():
    probe = Path(".github/workflows/serviceit-vps-browser-offline-sandbox-probe.yml").read_text("utf8")
    assert '--security-opt "apparmor=serviceit-chromium"' in probe
    assert '--network none' in probe
    assert 'chromium_sandbox=True' in probe
    assert "APPARMOR_UNPRIVILEGED_USERNS_RESTRICT=ON" in probe
    assert "CHROMIUM_SANDBOX_WITH_SCOPED_SECCOMP_APPARMOR=PASS" in probe
    for forbidden in ("--privileged", "apparmor=unconfined",
                      "kernel.apparmor_restrict_unprivileged_userns=0",
                      "--no-sandbox", "crm.item.update"):
        assert forbidden not in probe
