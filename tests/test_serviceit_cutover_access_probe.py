"""Offline safety contract tests: no real Bitrix24 access or CRM writes."""
from pathlib import Path

import httpx
import pytest

from leadpilot.serviceit_cutover_access_probe import (
    _list_kind, main, probe,
)


def test_list_structures_are_closed_enum_only():
    assert _list_kind({"items": []}) == "FLAT_EMPTY"
    assert _list_kind({"items": [[]]}) == "NESTED_EMPTY"
    assert _list_kind({"items": [[{"id": 10}]]}) == "NESTED_OBJECTS"
    assert _list_kind({"items": [{"id": 10}]}) == "FLAT_OBJECTS"
    assert _list_kind({"items": [[{"id": 10}], []]}) == "NESTED_MALFORMED"
    assert _list_kind({"items": ["CONFIDENTIAL_SECRET"]}) == "MIXED_OR_INVALID"
    assert _list_kind({"items": 44}) == "INVALID_ITEMS"
    assert _list_kind({"items": [{"id": 1}] * 51}) == "INVALID_ITEMS"


def test_probe_safely_differentiates_list_empty_and_known_card_reads():
    methods = []
    def rest(method, args):
        methods.append((method, args))
        assert method in {
            "crm.type.getByEntityTypeId", "crm.item.list", "crm.item.get",
        }
        if method == "crm.type.getByEntityTypeId":
            assert args == {"entityTypeId": 1062}
            return {"type": {"id": 13, "entityTypeId": 1062}}
        if method == "crm.item.list":
            assert args["entityTypeId"] == 1062
            assert args["select"] == ["id", "categoryId"]
            assert "order" not in args
            return {"items": [[]]} if "filter" in args else {"items": [{"id": 7}]}
        assert method == "crm.item.get"
        assert args["entityTypeId"] == 1062
        return {"item": {
            "id": args["id"], "entityTypeId": 1062, "categoryId": 21,
            "title": "NEVER_EXPOSE_PRIVATE_CUSTOMER_INFO",
        }}
    assert probe(rest) == {
        "TYPE": "VERIFIED_1062",
        "LIST_MINIMAL": "FLAT_OBJECTS",
        "LIST_CATEGORY_ONLY": "NESTED_EMPTY",
        "GET_3": "VISIBLE_1062_21",
        "GET_CURRENT": "NOT_SPECIFIED",
    }
    assert [m for m, _ in methods] == [
        "crm.type.getByEntityTypeId", "crm.item.list", "crm.item.list",
        "crm.item.get",
    ]


def test_refused_read_and_arbitrary_api_exception_never_leak_to_status():
    def rest(method, args):
        if method == "crm.type.getByEntityTypeId":
            raise RuntimeError("WEBHOOK_CREDENTIALS_DO_NOT_PRINT")
        if method == "crm.item.get":
            raise ValueError("LEAKED_CUSTOMER_NAME")
        return {"items": [["NEVER_LOG_SOURCE_BODY"]]}
    result = probe(rest)
    assert result["TYPE"] == "API_UNAVAILABLE"
    assert result["GET_CURRENT"] == "NOT_SPECIFIED"
    assert result["GET_3"] == "API_UNAVAILABLE"
    assert result["LIST_MINIMAL"] == "NESTED_MALFORMED"
    assert "CUSTOMER" not in str(result)
    assert "CREDENTIAL" not in str(result)


def test_main_refuses_empty_key_without_network(monkeypatch, capsys):
    monkeypatch.delenv("SERVICEIT_BITRIX_WEBHOOK", raising=False)
    assert main() == 2
    assert capsys.readouterr().out.strip() == "SERVICEIT_CUTOVER_PROBE_STATUS=NO_WEBHOOK"


def test_main_with_mock_network_only_prints_status_codes(monkeypatch, capsys):
    private = "VERY_PRIVATE_CLIENT_DATA"
    hook = "https://serviseit.bitrix24.ru/rest/1/syntheticSECRETabc123"
    def route(request):
        assert request.url.path.endswith((
            "crm.item.list.json", "crm.item.get.json",
            "crm.type.getByEntityTypeId.json",
        ))
        if request.url.path.endswith("crm.type.getByEntityTypeId.json"):
            return httpx.Response(200, json={"result": {
                "type": {"id": 13, "entityTypeId": 1062}}})
        if request.url.path.endswith("crm.item.list.json"):
            return httpx.Response(200, json={"result": {"items": [[]]}})
        return httpx.Response(200, json={"result": {
            "item": {"id": 7, "entityTypeId": 1062, "categoryId": 21,
                     "title": private},
        }})
    transport = httpx.MockTransport(route)
    original = httpx.Client
    monkeypatch.setattr("leadpilot.serviceit_cutover_access_probe.httpx.Client",
                        lambda **kwargs: original(transport=transport, **kwargs))
    monkeypatch.setenv("SERVICEIT_BITRIX_WEBHOOK", hook)
    assert main() == 0
    output = capsys.readouterr().out
    assert "SERVICEIT_CUTOVER_PROBE_TYPE=VERIFIED_1062" in output
    assert "SERVICEIT_CUTOVER_PROBE_LIST_MINIMAL=NESTED_EMPTY" in output
    assert "SERVICEIT_CUTOVER_PROBE_GET_CURRENT=NOT_SPECIFIED" in output
    assert "SERVICEIT_CUTOVER_PROBE_CRM_WRITES=0" in output
    assert private not in output
    assert "syntheticSECRET" not in output


def test_powershell_diagnostic_runs_probe_only_in_failed_readonly_branch():
    source = Path("scripts/SETUP_SERVICEIT_FREE_GITHUB_ACTIONS.ps1").read_text("utf-8")
    diagnosis = source[source.index("if ($DiagnoseCutover) {"):
                       source.index("function Set-SecretFromStdin")]
    assert diagnosis.index("if ($DiagnosticExit -ne 0) {") < diagnosis.index(
        "serviceit_cutover_access_probe")
    assert "Disable-ScheduledTask" not in diagnosis
    assert "Set-Variable" not in diagnosis
    assert "secret set" not in diagnosis
    assert "crm.item.update" not in diagnosis
    assert "GIGACHAT_CREDENTIALS" not in diagnosis
    assert "SERVICEIT_CUTOVER_PROBE_" in diagnosis


def test_secondary_probe_executes_with_repo_working_directory_even_from_home():
    ps = Path("scripts/SETUP_SERVICEIT_FREE_GITHUB_ACTIONS.ps1").read_text("utf-8")
    branch = ps[ps.index("if ($DiagnosticExit -ne 0) {"):ps.index("function Set-SecretFromStdin")]
    assert branch.index("Push-Location $Repo") < branch.index(
        "$Python -m leadpilot.serviceit_cutover_access_probe")
    assert branch.index("$Python -m leadpilot.serviceit_cutover_access_probe") < branch.index(
        "Pop-Location")
    assert "CUTOVER_ACCESS_PROBE=UNAVAILABLE_SAFE" in branch
    assert "Write-Host $_" not in branch
    assert "Disable-ScheduledTask" not in branch
    assert "Set-SecretFromStdin" not in branch
    assert "Set-Variable 'SERVICEIT_FREE_CLOUD_ENABLED'" not in branch


def test_probe_of_known_current_item_only_with_owner_supplied_id():
    calls = []
    def rest(method, args):
        calls.append((method, args))
        if method == "crm.type.getByEntityTypeId":
            return {"type": {"entityTypeId": 1062}}
        if method == "crm.item.list":
            return {"items": [[]]}
        return {"item": {
            "id": args["id"], "entityTypeId": 1062, "categoryId": 21,
            "title": "CLIENT_DETAILS_NOT_FOR_LOGS",
        }}
    result = probe(rest, current_id=81)
    assert result["GET_CURRENT"] == "VISIBLE_1062_21"
    assert [args["id"] for method, args in calls if method == "crm.item.get"] == [3, 81]
    assert "CLIENT_DETAILS_NOT_FOR_LOGS" not in str(result)


@pytest.mark.parametrize("bad", [0, 7, -2, True, 2_000_000_001, "81"])
def test_probe_refuses_invalid_current_id(bad):
    with pytest.raises(ValueError, match="CURRENT_CARD_ID_INVALID"):
        probe(lambda *_: {}, current_id=bad)


def test_powershell_probe_current_id_only_in_readonly_diagnostic():
    source = Path("scripts/SETUP_SERVICEIT_FREE_GITHUB_ACTIONS.ps1").read_text("utf-8")
    assert "param([switch]$Apply, [switch]$DiagnoseCutover, [int]$CurrentCardId = 0)" in source
    read_only = source[source.index("if ($DiagnoseCutover) {"):
                       source.index("function Set-SecretFromStdin")]
    assert "SERVICEIT_CUTOVER_CURRENT_CARD_ID" in read_only
    assert "Remove-Item Env:\\SERVICEIT_CUTOVER_CURRENT_CARD_ID" in read_only
    assert "Disable-ScheduledTask" not in read_only
    assert "GET_CURRENT" in read_only
    assert "GET_7" not in read_only
