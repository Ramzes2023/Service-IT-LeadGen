"""Prevent Windows PowerShell 5.1 UTF-8-no-BOM mojibake in recovery scripts.

PowerShell 5.1 may decode a UTF-8 .ps1 without BOM in the active Windows code page.
Keep this PS1 strictly ASCII, constructing Cyrillic by Unicode scalar instead.
"""
from pathlib import Path
import re

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "RUN_1C_PAGE1_RECOVERY.ps1"


def test_recovery_script_is_ascii_safe_for_ps51():
    raw = SCRIPT.read_bytes()
    assert raw.isascii(), "PowerShell 5.1 requires ASCII-only PS1 or an explicit UTF-8 BOM"
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert raw.decode("cp1252") == raw.decode("utf-8")


def test_cyrillic_1c_query_is_built_at_runtime_and_page_index_is_one():
    script = SCRIPT.read_text(encoding="ascii")
    assert re.search(r"\$term\s*=\s*'1'\s*\+\s*\[char\]0x0421\b", script)
    assert re.search(r"leadpilot\.cli\b[^\n]*--term\s+\$term\b", script)
    assert re.search(r"leadpilot\.cli\b[^\n]*--offset\s+1\b", script)
    assert "SOURCE_SCAN_FAILED_REGION_" in script
    assert "MULTIPAGE_FNS_BATCH=FAILED" in script
    assert "exit 1" in script
