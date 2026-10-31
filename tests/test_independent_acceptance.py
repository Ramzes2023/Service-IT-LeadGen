"""Independent offline acceptance of integration boundary failures."""
import io
import json

import httpx
import pytest

from leadpilot.bitrix_sandbox import run
from leadpilot.bitrix_readonly import readonly_preflight


def test_blueprint_on_windows_cp1252(monkeypatch):
    output = io.BytesIO()
    console = io.TextIOWrapper(output, encoding="cp1252")
    monkeypatch.setattr("sys.stdout", console)
    run(["--blueprint"])
    console.flush()
    report = json.loads(output.getvalue().decode("cp1252").split("=", 1)[1])
    assert report["crm_writes"] == "NO"
    assert report["pre_interest_isolation"].startswith("LOCAL_UNTIL")


def test_invalid_category_never_echoes_configuration(monkeypatch, capsys):
    marker = "PRIVATE_CONFIGURATION_VALUE"
    monkeypatch.setenv("BITRIX24_SANDBOX_CATEGORY_ID", marker)
    with pytest.raises(SystemExit) as result:
        run(["--preflight"])
    assert result.value.code == 2
    output = capsys.readouterr().out
    assert marker not in output
    assert "CONFIG_ERROR" in output


@pytest.mark.parametrize("payload", [None, 3, [], "unexpected"])
def test_legacy_readonly_handles_non_object_json(payload):
    with httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=json.dumps(payload))
    )) as client:
        result = readonly_preflight(
            "https://synthetic.bitrix24.ru/rest/42/fake_token", client=client)
    assert result["crm_writes"] is False
    assert set(result["methods"].values()) == {"API_ERROR_OR_PERMISSION_MISSING"}


def test_legacy_manager_cli_preserves_database_and_permissions(tmp_path, monkeypatch, capsys):
    from leadpilot.cli import run as cli
    from test_manager_briefs import db_with_lead, NOW

    db, _ = db_with_lead(tmp_path)
    before = db.read_bytes()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["leadpilot", "--db", str(db),
        "manager-briefs", "--today", NOW.isoformat()])
    cli()
    assert "MANAGER_BRIEFS=" in capsys.readouterr().out
    report = json.loads((tmp_path / "exports/manager_briefs.json").read_text(encoding="utf8"))
    assert report["total_briefs"] == 1
    assert report["briefs"][0]["outbound_authorized"] is False
    assert db.read_bytes() == before


def test_synthetic_end_to_end_has_no_network(monkeypatch):
    import socket
    from leadpilot.integration_demo import demonstrate

    def forbidden(*args, **kwargs):
        pytest.fail("Offline acceptance attempted network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    report = demonstrate()
    assert report["simulated_leads"] == report["simulated_tasks"] == 1
    assert report["outbound_messages"] == report["crm_writes"] == "NO"
