"""Offline synthetic bounded Bitrix list/get/fields source visibility probes."""
import json

import httpx
from leadpilot import serviceit_b2b_duplicate_forensics_readonly as audit


def test_exact_probe_uses_only_sanitized_get_fields_list_and_no_crm_writes(monkeypatch):
    monkeypatch.setattr(audit, "validate_webhook", lambda _: "https://example.invalid/rest")
    methods = []
    def transport(request):
        method = request.url.path.rsplit("/", 1)[-1].removesuffix(".json")
        assert request.method == "POST"
        assert method in {"crm.item.list", "crm.item.fields", "crm.item.get"}
        methods.append(method)
        params = json.loads(request.content)
        mode = params["useOriginalUfNames"]
        field = audit.ORIGINAL if mode == "Y" else audit.CAMEL
        if method == "crm.item.get":
            assert params["id"] in (3, 7)
            return httpx.Response(200, json={"result": {"item": {
                "id": params["id"], "entityTypeId": audit.ENTITY,
                "categoryId": audit.CATEGORY,
                field: "SVCIT-TEST-SOURCE" if params["id"] == 3 else audit.KNOWN7}}})
        if method == "crm.item.fields":
            return httpx.Response(200, json={"result": {"fields": {
                field: {"type": "string"}}}})
        target = params["filter"][field]
        rows = ([{"id": 7, "categoryId": 21, field: audit.KNOWN7}]
                if target == audit.KNOWN7 else
                [{"id": 3, "categoryId": 21, field: "SVCIT-TEST-SOURCE"}]
                if target == "SVCIT-TEST-SOURCE" else [])
        return httpx.Response(200, json={"result": {"items": rows}})
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        result = audit.run("SYNTHETIC-WEBHOOK-NOT-REAL", client)
    assert result["GET_7_Y"] == result["GET_7_N"] == "KNOWN_4661617"
    assert result["LIST_KNOWN_4661617_N"] == "SOURCE_MATCH"
    assert result["LIST_OLD_4665948_Y"] == "EMPTY"
    assert result["LIST_ABSENT_N"] == "EMPTY"
    assert methods.count("crm.item.get") == 3
    assert methods.count("crm.item.fields") == 2
    assert methods.count("crm.item.list") == 7
    assert result["POSITIVE_3_Y"] == result["POSITIVE_3_N"] == "VERIFIED"
    assert result["crm_writes"] == result["outreach"] == 0
    assert audit.ORIGINAL not in str(result)
    assert audit.CAMEL not in str(result)
    assert "SVCIT-TEST-SOURCE" not in str(result)
    assert "SYNTHETIC-WEBHOOK" not in str(result)


def test_nested_or_paged_response_never_means_negative_source_match():
    assert audit._shape({"result": {"items": [[]]}}, audit.KNOWN7, audit.CAMEL) == "UNTRUSTED_ROW"
    assert audit._shape({"result": {"items": [], "next": 50}},
                        audit.KNOWN7, audit.CAMEL) == "PAGED"
    assert audit._shape({"result": {"items": [{"id": 7, "categoryId": 21}]}},
                        audit.KNOWN7, audit.CAMEL) == "FIELD_ABSENT"
