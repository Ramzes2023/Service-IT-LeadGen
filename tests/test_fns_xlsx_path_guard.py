"""Guard missing or oversized official FNS XLSX with actionable error reasons."""
from pathlib import Path

import pytest

from leadpilot.fns_gui_reconcile import MAX_ZIP_BYTES, read_fns_xlsx


def test_missing_fns_xlsx_is_reported_as_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError, match="FNS_XLSX_NOT_FOUND"):
        read_fns_xlsx(tmp_path / "not-downloaded.xlsx")


def test_wrong_extension_is_not_misreported_as_missing(tmp_path):
    p = tmp_path / "fns.xls"
    p.write_text("x")
    with pytest.raises(ValueError, match="FNS_XLSX_INVALID_EXTENSION"):
        read_fns_xlsx(p)


def test_empty_xlsx_has_specific_error(tmp_path):
    p = tmp_path / "empty.xlsx"
    p.touch()
    with pytest.raises(ValueError, match="FNS_XLSX_EMPTY_FILE"):
        read_fns_xlsx(p)


def test_oversized_fns_xlsx_has_specific_error_without_parsing(tmp_path):
    p = tmp_path / "whole-russia.xlsx"
    with p.open("wb") as stream:
        stream.truncate(MAX_ZIP_BYTES + 1)
    with pytest.raises(ValueError, match="FNS_XLSX_TOO_LARGE"):
        read_fns_xlsx(p)


def test_windows_recovery_script_is_ascii_only_and_fails_closed():
    script = Path(__file__).resolve().parents[1] / "scripts" / "RUN_SME_MOSCOW_RECOVERY.ps1"
    raw = script.read_bytes()
    assert raw.isascii()
    body = raw.decode("ascii")
    assert "77_50_" in body
    assert "FNS_XLSX_NOT_DOWNLOADED" in body
    assert "--xlsx $selected.FullName" in body
    assert "SME_FIRST_IMPORT=FAILED" in body
    assert "exit 1" in body
