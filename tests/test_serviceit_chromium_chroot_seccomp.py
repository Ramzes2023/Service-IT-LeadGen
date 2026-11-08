"""Chromium zygote sys_chroot in the NEW user namespace needs seccomp permission."""
from pathlib import Path
import json


def test_only_browser_scoped_policy_allows_chroot_without_host_caps():
    stock = json.loads(Path("deploy/playwright-chromium-sandbox-seccomp.json").read_text("utf8"))
    custom = json.loads(Path("deploy/serviceit-chromium-sandbox-seccomp.json").read_text("utf8"))
    assert stock["defaultAction"] == "SCMP_ACT_ERRNO"
    assert custom["defaultAction"] == "SCMP_ACT_ERRNO"
    assert {k: v for k, v in custom.items() if k != "syscalls"} == {
        k: v for k, v in stock.items() if k != "syscalls"
    }
    custom_rules = custom["syscalls"]
    added = [s for s in custom_rules if s.get("names") == ["chroot"] and
             s.get("action") == "SCMP_ACT_ALLOW" and not s.get("includes")]
    assert len(added) == 1
    stock_rules = stock["syscalls"]
    assert len(custom_rules) == len(stock_rules) + 1
    stripped = [s for s in custom_rules if s is not added[0]]
    assert stripped == stock_rules
    # Adding an allowed syscall is NOT the same as giving root-like caps.
    assert not any(rule.get("names") == ["mount"] and not rule.get("includes")
                   for rule in custom_rules)


def test_browser_remains_unprivileged_no_network_and_only_custom_seccomp():
    compose = Path("deploy/serviceit-vps-browser.compose.yml").read_text("utf8")
    browser = compose.split("\n  egress:", 1)[0]
    assert "cap_drop: [ALL]" in browser
    assert "cap_add:" not in browser
    assert "read_only: true" in browser
    assert 'apparmor:serviceit-chromium' in browser
    assert "seccomp:${SERVICEIT_VPS_BROWSER_SECCOMP_PROFILE:" in browser
    assert "browser_internal" in browser
    assert "internal: true" in compose
    assert "--no-sandbox" not in compose


def test_probe_and_live_activation_both_load_chroot_seccomp():
    probe = Path(".github/workflows/serviceit-vps-browser-offline-sandbox-probe.yml").read_text("utf8")
    live = Path("deploy/serviceit-vps-browser-live-activate.py").read_text("utf8")
    path = "deploy/serviceit-chromium-sandbox-seccomp.json"
    assert path in probe and path in live
    assert 'chromium_sandbox=True' in probe and 'chromium_sandbox=True' in live
    assert "--network none" in probe
    assert "no-new-privileges:true" in probe
    assert "--cap-drop ALL" in probe
    assert 'apparmor=serviceit-chromium' in probe
    for value in ("--no-sandbox", "--privileged", "seccomp=unconfined",
                  "apparmor=unconfined", "--cap-add SYS_CHROOT",
                  "kernel.apparmor_restrict_unprivileged_userns=0"):
        assert value not in probe + live
