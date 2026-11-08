"""Chromium Crashpad must use a writable in-memory XDG path, not a host mount."""
from pathlib import Path


def test_readonly_browser_chromium_uses_tmpfs_for_xdg():
    dockerfile = Path("Dockerfile.serviceit-vps-browser").read_text("utf8")
    compose = Path("deploy/serviceit-vps-browser.compose.yml").read_text("utf8")
    browser = compose.split("\n  egress:", 1)[0]
    egress = compose.split("\n  egress:", 1)[1]
    assert "XDG_CONFIG_HOME=/tmp XDG_CACHE_HOME=/tmp" in dockerfile
    assert 'XDG_CONFIG_HOME: "/tmp"' in browser
    assert 'XDG_CACHE_HOME: "/tmp"' in browser
    assert 'XDG_CONFIG_HOME: "/tmp"' not in egress
    assert "read_only: true" in browser
    assert "- /tmp:size=256m,mode=1777" in browser
    assert "apparmor:serviceit-chromium" in browser
    assert "seccomp:${SERVICEIT_VPS_BROWSER_SECCOMP_PROFILE:" in browser
    assert "cap_drop: [ALL]" in browser
    assert "internal: true" in compose
    assert "seccomp=unconfined" not in compose
    assert "apparmor=unconfined" not in compose
    assert "--no-sandbox" not in dockerfile + compose


def test_owner_only_offline_probe_uses_same_writable_xdg_without_leaks():
    workflow = Path(".github/workflows/serviceit-vps-browser-offline-sandbox-probe.yml").read_text("utf8")
    assert "-e XDG_CONFIG_HOME=/tmp -e XDG_CACHE_HOME=/tmp" in workflow
    assert "--network none" in workflow
    assert "--read-only" in workflow
    assert "--tmpfs /tmp:size=256m,mode=1777" in workflow
    assert "chromium_sandbox=True" in workflow
    assert 'apparmor=serviceit-chromium' in workflow
    assert "CHROMIUM_SANDBOX_WITH_SCOPED_SECCOMP_APPARMOR=PASS" in workflow
    for forbidden in ("--privileged", "--no-sandbox",
                      "apparmor=unconfined", "seccomp=unconfined",
                      "kernel.apparmor_restrict_unprivileged_userns=0",
                      "crm.item.update"):
        assert forbidden not in workflow
