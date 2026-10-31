"""Strict offline tests for the real Krasoft MCP transport (network mocked)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
import pytest

from leadpilot.krasoft_public_mcp import (
    MAX_RESPONSE, SourceBlocked, _decode_response, _results_shape,
    probe, validate_queries, ENDPOINT,
)


class FakeReply:
    def __init__(self, payload, status=200, content_type="application/json"):
        self.payload = payload
        self.status = status
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, count):
        return self.payload[:count]


class FakeOpener:
    def __init__(self):
        self.calls = []

    def open(self, request, timeout):
        assert request.full_url == ENDPOINT
        assert request.get_method() == "POST"
        assert timeout <= 12
        request_body = json.loads(request.data)
        self.calls.append(request_body)
        method = request_body["method"]
        if method == "initialize":
            data = {"jsonrpc": "2.0", "id": 1, "result": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "serverInfo": {"name": "fake-test-server", "version": "1"}}}
            return FakeReply(json.dumps(data).encode())
        if method == "notifications/initialized":
            return FakeReply(b"", status=202)
        assert method == "tools/call"
        data = {"jsonrpc": "2.0", "id": request_body["id"],
                "result": {"content": [{"type": "text", "text": json.dumps({
                    "tenders": [{"title": "Сопровождение 1С",
                                 "ntfUniqID": 12345, "buyerInn": "0000000000"}]})}]}}
        return FakeReply(json.dumps(data).encode())


def test_real_documented_mcp_endpoint():
    assert ENDPOINT == "https://zakupki.krasoft.ru/api/mcp"


def test_queries_must_be_bounded():
    with pytest.raises(SourceBlocked):
        validate_queries(["1С"] * 4, "77", 5)
    with pytest.raises(SourceBlocked):
        validate_queries(["1С"], "06", 5)
    with pytest.raises(SourceBlocked):
        validate_queries(["1С"], "77", 11)
    with pytest.raises(SourceBlocked):
        validate_queries(["\r"], "77", 5)


def test_correct_read_only_mcp_handshake():
    fake = FakeOpener()
    report = probe(["сопровождение 1С"], opener=fake,
                   observed_at=datetime(2026, 10, 31, 12, tzinfo=timezone.utc))
    assert [x["method"] for x in fake.calls] == [
        "initialize", "notifications/initialized", "tools/call"]
    args = fake.calls[-1]["params"]
    assert args["name"] == "search_tenders"
    assert args["arguments"]["regionCodes"] == ["77"]
    assert args["arguments"]["pageSize"] == 5
    assert report["queries"][0]["raw_record_count"] == 1
    assert report["queries"][0]["kind"] == "STRUCTURED"
    assert report["qualified_real_leads"] == 0
    assert report["crm_writes"] == "NO"


def test_moscow_oblast():
    fake = FakeOpener()
    probe(["кассовое оборудование"], region="50", opener=fake)
    assert fake.calls[-1]["params"]["arguments"]["regionCodes"] == ["50"]


def test_second_query_has_fresh_request_id():
    fake = FakeOpener()
    result = probe(["1С", "Битрикс24"], opener=fake)
    assert result["searches_executed"] == 2
    assert [x["id"] for x in fake.calls if x["method"] == "tools/call"] == [2, 3]


def test_oversized_response_rejected():
    with pytest.raises(SourceBlocked, match="SOURCE_RESPONSE_TOO_LARGE"):
        _decode_response(b"a" * (MAX_RESPONSE + 1), "application/json")


def test_login_html_rejected():
    with pytest.raises(SourceBlocked, match="UNEXPECTED_CONTENT_TYPE"):
        _decode_response(b"<html>login</html>", "text/html")


def test_sse_supported():
    result = _decode_response(
        b'event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{"ok":true}}\n\n',
        "text/event-stream")
    assert result["result"]["ok"] is True


def test_mcp_protocol_error_redacted():
    with pytest.raises(SourceBlocked, match="MCP_ERROR"):
        _decode_response(
            b'{"jsonrpc":"2.0","id":1,"error":{"code":-32000,"message":"secret"}}',
            "application/json")


def test_unknown_schema_is_not_proof():
    assert _results_shape({"result": {"structuredContent": {"unexpected": 2}}})[
        "kind"] == "SCHEMA_PENDING"


def test_unstructured_message_never_counts_as_records():
    result = _results_shape({"result": {"content": [
        {"type": "text", "text": "About 10,000 tenders found"}
    ]}})
    assert result["kind"] == "UNSTRUCTURED_TEXT"
    assert result["raw_record_count"] is None


def test_tool_level_error_not_qualified():
    with pytest.raises(SourceBlocked, match="MCP_TOOL_ERROR"):
        _results_shape({"result": {"isError": True}})
