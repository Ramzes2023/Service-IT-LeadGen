"""Guard the Google Apps Script handoff from accidental runtime writes or Node dependency."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "OPEN_SERVICEIT_APPS_SCRIPT_NO_NODE.ps1"
DOC = ROOT / "docs" / "SERVICEIT_APPSSCRIPT_NO_NODE_HANDOFF_20261102.md"


def test_no_node_handoff_is_manual_only():
    body = SCRIPT.read_text(encoding="utf-8")
    assert "Set-Clipboard" in body
    assert "git.exe" in body
    assert "bdbf13f2144498641eed36479fb2b76e5eb1d1e0" in body
    assert "90f4b1d5afb9738caa836bdcb7879674d275a779" in body
    assert "UPSTREAM_FILES_CHANGED_REVIEW_REQUIRED" in body
    assert "appsscript.json" in body
    assert "NoBrowser" in body
    assert "Start-Process 'https://script.google.com/home/projects'" in body
    for forbidden in (
        "& npx", "& npm", "& node", "clasp push",
        "crm.item.add", "crm.item.update",
        "SERVICEIT_SEND_ENABLED=YES", "installPollingTrigger()",
        "SERVICEIT_HMAC_SECRET')",
    ):
        assert forbidden not in body


def test_runbook_requires_owner_gates():
    content = DOC.read_text(encoding="utf-8")
    assert "checkB2BCenter" in content
    assert "serviceITStatus" in content
    assert "READ_ONLY" in content
    assert "installPollingTrigger" in content
    assert "HTTPS" in content
    assert "PostgreSQL" in content
    assert "не" in content.lower()
