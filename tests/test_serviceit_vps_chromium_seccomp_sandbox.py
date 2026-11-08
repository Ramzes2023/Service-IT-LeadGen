"""Offline regression: Chromium sandbox must not be disabled to ease Docker setup."""
import json
from pathlib import Path


def test_browser_gets_vendor_playwright_userns_seccomp_not_unconfined():
    data = json.loads(Path("deploy/playwright-chromium-sandbox-seccomp.json").read_text("utf8"))
    assert data["defaultAction"] == "SCMP_ACT_ERRNO"
    assert any(
        rule.get("action") == "SCMP_ACT_ALLOW"
        and set(("clone", "setns", "unshare")).issubset(rule.get("names", []))
        for rule in data["syscalls"]
    )
    browser = Path("deploy/serviceit-vps-browser.compose.yml").read_text("utf8")
    main = Path("deploy/serviceit-vps.compose.yml").read_text("utf8")
    assert "seccomp:${SERVICEIT_VPS_BROWSER_SECCOMP_PROFILE:" in browser
    assert "seccomp:${SERVICEIT_VPS_BROWSER_SECCOMP_PROFILE:" not in main
    assert browser.count("seccomp:${SERVICEIT_VPS_BROWSER_SECCOMP_PROFILE:") == 1
    assert "no-new-privileges:true" in browser
    assert "cap_drop: [ALL]" in browser
    assert "internal: true" in browser
    assert "seccomp=unconfined" not in browser
    assert "apparmor=unconfined" not in browser
    assert "--no-sandbox" not in browser


def test_owner_only_offline_sandbox_probe_cannot_touch_live_crm():
    text = Path(".github/workflows/serviceit-vps-browser-offline-sandbox-probe.yml").read_text("utf8")
    assert "workflow_dispatch:" in text
    assert "github.actor == 'Ramzes2023'" in text
    assert "PROBE_CHROMIUM_SANDBOX_OFFLINE_NO_CRM" in text
    assert "github.ref == 'refs/heads/main'" in text
    assert "--network none" in text
    assert "--read-only" in text
    assert "chromium_sandbox=True" in text
    assert "seccomp=$seccomp" in text
    assert "sha256sum --check --status" in text
    assert "no-new-privileges:true" in text
    assert "--cap-drop ALL" in text
    assert "APPARMOR_UNPRIVILEGED_USERNS_RESTRICT" in text
    for forbidden in ("crm.item.update", "tasks.task.add", "docker compose up",
                      "kernel.apparmor_restrict_unprivileged_userns=0", "--no-sandbox",
                      "docker run --privileged"):
        assert forbidden not in text


def test_live_activate_wires_only_browser_seccomp_after_preflight():
    script = Path("deploy/serviceit-vps-browser-live-activate.py").read_text("utf8")
    assert "OFFICIAL_CHROMIUM_SECCOMP_PROFILE_MISSING" in script
    assert 'browser_env["SERVICEIT_VPS_BROWSER_SECCOMP_PROFILE"] = str(seccomp)' in script
    assert "chromium_sandbox=True" in script
    assert "BLOCKED_PHASE=" in script
    assert "core_restarted" in script
