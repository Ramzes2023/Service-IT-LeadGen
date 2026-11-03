"""Synthetic evidence: bounded probes only read CRM and expose fixed codes."""
import json
from pathlib import Path

import httpx

from leadpilot.serviceit_b2b_list_shape_probe import (
    KNOWN_SOURCE, LIST_CASES, shape, list_probe, exact_get_probe,
)
URL = "https://serviseit.bitrix24.ru/rest/2/SYNTHETICONLYTOKEN123"


def test_list_shapes_include_nested_empty_without_exposing_rows():
    assert shape({"result": {"items": [[]]}}) == "NESTED_EMPTY"
    assert shape({"result": {"items": []}}) == "FLAT_EMPTY"
    assert shape({"result": {"items": [{"id": 33, "categoryId": 21}]}}) == "FLAT_WITH_CATEGORY"
    assert shape({"result": {"items": [[{"id": 33}]]}}) == "NESTED_OTHER"
    assert shape({"result": {"items": [{"id": None}]}}) == "FLAT_MISSING_ID"
    assert shape({"result": []}) == "INVALID_RESULT"
    assert shape({"error": "ACCESS_DENIED"}) == "API_ERROR"


def test_only_fixed_scoped_list_calls_and_two_exact_gets():
    assert len(LIST_CASES) == 10
    assert {k for k, _ in LIST_CASES} == {
        "KNOWN_Y_SELECTED", "KNOWN_Y_NO_SELECT",
        "CATEGORY_Y_ID_ONLY", "BROAD_Y_ID_ONLY",
        "ID_3_Y_SELECTED", "BROAD_N_ID_ONLY",
        "CATEGORY_N_ID_ONLY", "ID_3_N_SELECTED",
        "CATEGORY_N_GT_3_ORDER", "CATEGORY_N_RECENT"}
    for _, params in LIST_CASES:
        assert params["entityTypeId"] == 1062
        assert params["start"] == 0
        assert "id" in params.get("select", ["id"])
        if "filter" in params and "UF_CRM_13_SVCIT_SIGNAL_ID" in params["filter"]:
            assert params["filter"]["UF_CRM_13_SVCIT_SIGNAL_ID"] == KNOWN_SOURCE


def test_probe_calls_only_list_and_fixed_get_and_returns_no_record_values():
    paths = []
    def handler(request):
        paths.append(request.url.path)
        args = json.loads(request.content)
        if request.url.path.endswith("crm.item.list.json"):
            assert args["entityTypeId"] == 1062
            return httpx.Response(200, json={"result": {"items": [[]]}})
        assert request.url.path.endswith("crm.item.get.json")
        assert args == {"entityTypeId": 1062, "id": 5, "useOriginalUfNames": "Y"}
        return httpx.Response(200, json={"result": {"item": {
            "id": 5, "entityTypeId": 1062, "categoryId": 21,
            "UF_CRM_13_SVCIT_SIGNAL_ID": KNOWN_SOURCE,
            "title": "SECRET_COMPANY_TITLE_NEVER_PRINT",
        }}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert list_probe(client, URL, LIST_CASES[0][1]) == "NESTED_EMPTY"
        assert exact_get_probe(client, URL, 5) == "TARGET_MATCHES_KNOWN_SOURCE"
    assert len(paths) == 2
    assert paths[0].endswith("/crm.item.list.json")
    assert paths[1].endswith("/crm.item.get.json")


def test_workflow_stays_readonly_and_owner_only():
    wf = Path(".github/workflows/serviceit-b2b-list-shape-readonly.yml").read_text("utf-8")
    module = Path("leadpilot/serviceit_b2b_list_shape_probe.py").read_text("utf-8")
    assert "github.actor == 'Ramzes2023'" in wf
    assert "github.event.issue.number == 74" in wf
    assert "SERVICEIT_B2B_LIST_SHAPE_READONLY_V1" in wf
    assert "SERVICEIT_BITRIX_WEBHOOK: ${{ secrets.SERVICEIT_BITRIX_WEBHOOK }}" in wf
    assert "contents: read" in wf and "issues: read" in wf
    for forbidden in ("crm.item.update", "crm.item.add", "crm.item.delete",
                      "GIGACHAT_CREDENTIALS", "workflow_dispatch",
                      "SERVICEIT_B2B_INGEST_ENABLED"):
        assert forbidden not in wf
        assert forbidden not in module
    assert "r.json()" in module
    assert "row.get(SIGNAL) == KNOWN_SOURCE" in module
    assert "CRM_WRITES=0" in module



def test_universal_n_scope_probes_do_not_change_crm_and_are_id_only():
    variants = {name: query for name, query in LIST_CASES}
    for label in ("CATEGORY_N_ID_ONLY", "ID_3_N_SELECTED",
                  "CATEGORY_N_GT_3_ORDER", "CATEGORY_N_RECENT"):
        query = variants[label]
        assert query["entityTypeId"] == 1062
        assert query["useOriginalUfNames"] == "N"
        assert query["start"] == 0
        assert all(v in ("id", "categoryId", "createdTime") for v in query["select"])
    assert variants["CATEGORY_N_GT_3_ORDER"]["filter"][">id"] == 3
    assert variants["CATEGORY_N_GT_3_ORDER"]["order"] == {"id": "ASC"}
    assert variants["CATEGORY_N_RECENT"]["filter"]["categoryId"] == 21
    assert ">=" in "".join(variants["CATEGORY_N_RECENT"]["filter"])
