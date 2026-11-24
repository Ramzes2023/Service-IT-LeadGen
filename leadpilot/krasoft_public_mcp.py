"""Read-only public Krasoft procurement MCP probe (no account or API key).

Official endpoint: https://zakupki.krasoft.ru/api/mcp
This is an AGGREGATOR of 44-FZ/223-FZ notices and possibly commercial sources:
it is NOT a first-party B2B-Center, SUPPMAN or ONLINECONTRACT connector.
No bids, CRM writes, outreach, buyer-size inference or lead qualification.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

ENDPOINT = "https://zakupki.krasoft.ru/api/mcp"
MCP_VERSION = "2025-06-18"
MAX_RESPONSE = 1024 * 1024
TIMEOUT = 12
MAX_QUERIES_PER_RUN = 3
REGIONS = {"77", "50"}
SEARCH_TERMS = ("сопровождение 1С", "IT-аутсорсинг", "Битрикс24", "кассовое оборудование",
                "торговое оборудование", "ЭДО", "администрирование серверов",
                "интеграция систем", "разработка чат-бота")
REQUEST_TAG = "KrasoftPublicProcurement/0.1"


class SourceBlocked(RuntimeError):
    """Redacted source error; never includes response bodies or external instructions."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_queries(queries: list[str], region: str, page_size: int) -> None:
    if not 1 <= len(queries) <= MAX_QUERIES_PER_RUN:
        raise SourceBlocked("QUERY_BUDGET_EXCEEDED")
    if region not in REGIONS:
        raise SourceBlocked("ONLY_MOSCOW_OR_OBLAST")
    if type(page_size) is not int or not 1 <= page_size <= 10:
        raise SourceBlocked("PAGE_SIZE_LIMIT_EXCEEDED")
    for item in queries:
        if not isinstance(item, str) or not item.strip() or len(item) > 100 or re.search(r"[\x00-\x1f]", item):
            raise SourceBlocked("INVALID_QUERY")


def _decode_response(body: bytes, content_type: str) -> dict:
    if len(body) > MAX_RESPONSE:
        raise SourceBlocked("SOURCE_RESPONSE_TOO_LARGE")
    if "application/json" in content_type:
        try:
            result = json.loads(body)
        except (UnicodeError, ValueError):
            raise SourceBlocked("BAD_MCP_JSON") from None
    elif "text/event-stream" in content_type:
        events = []
        for event in body.decode("utf-8", errors="replace").splitlines():
            if event.startswith("data:"):
                try:
                    obj = json.loads(event[5:].strip())
                except ValueError:
                    raise SourceBlocked("BAD_MCP_EVENT") from None
                if isinstance(obj, dict):
                    events.append(obj)
        if not events:
            raise SourceBlocked("MCP_EVENT_EMPTY")
        result = events[-1]
    else:
        raise SourceBlocked("MCP_UNEXPECTED_CONTENT_TYPE")
    if not isinstance(result, dict) or result.get("jsonrpc") != "2.0":
        raise SourceBlocked("MCP_INVALID_RESPONSE_SHAPE")
    if "error" in result:
        raise SourceBlocked("MCP_ERROR")
    return result


def _post(method: str, params: dict, *, request_id: int | None,
          opener, session: str | None) -> tuple[dict, str | None]:
    envelope = {"jsonrpc": "2.0", "method": method, "params": params}
    if request_id is not None:
        envelope["id"] = request_id
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream",
               "User-Agent": REQUEST_TAG,
               "MCP-Protocol-Version": MCP_VERSION}
    if session:
        headers["Mcp-Session-Id"] = session
    req = Request(ENDPOINT, method="POST", headers=headers,
                  data=json.dumps(envelope, ensure_ascii=False).encode("utf-8"))
    try:
        with opener.open(req, timeout=TIMEOUT) as resp:
            if resp.status == 202 and request_id is None:
                return {}, session
            if resp.status != 200:
                raise SourceBlocked("MCP_NON_200")
            raw = resp.read(MAX_RESPONSE + 1)
            sess = resp.headers.get("Mcp-Session-Id", session)
            result = _decode_response(raw, resp.headers.get("Content-Type", ""))
            return result, sess
    except HTTPError as exc:
        if exc.code in (301, 302, 303, 307, 308, 401, 403, 407, 429):
            raise SourceBlocked("MCP_ACCESS_OR_RATE_LIMITED") from None
        raise SourceBlocked("MCP_HTTP_ERROR") from None
    except (URLError, TimeoutError, OSError):
        raise SourceBlocked("MCP_NETWORK_ERROR") from None


def _results_shape(payload: dict) -> dict:
    """Report public result metadata without claiming any verified purchasing intent."""
    result = payload.get("result")
    if not isinstance(result, dict):
        raise SourceBlocked("MCP_TOOL_RESULT_INVALID")
    if result.get("isError") is True:
        raise SourceBlocked("MCP_TOOL_ERROR")
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        shape = structured
    else:
        snippets = [part.get("text") for part in result.get("content", [])
                    if isinstance(part, dict) and part.get("type") == "text"
                    and isinstance(part.get("text"), str)]
        if not snippets:
            raise SourceBlocked("MCP_TOOL_CONTENT_MISSING")
        try:
            shape = json.loads(snippets[0])
        except (UnicodeError, ValueError):
            # Human-readable summaries are not machine-verifiable procurement records.
            return {"kind": "UNSTRUCTURED_TEXT", "raw_record_count": None}
    if not isinstance(shape, (dict, list)):
        raise SourceBlocked("MCP_UNEXPECTED_DATA")
    if isinstance(shape, list):
        rows = shape
    else:
        rows = next((shape[k] for k in ("items", "data", "tenders", "results", "notices")
                     if isinstance(shape.get(k), list)), None)
    return {
        "kind": "STRUCTURED" if isinstance(rows, list) else "SCHEMA_PENDING",
        "raw_record_count": len(rows) if isinstance(rows, list) else None,
        "top_level_fields": sorted(shape)[:40] if isinstance(shape, dict) else [],
    }


def probe(queries: list[str], *, region: str = "77", page_size: int = 5, opener=None,
          observed_at: datetime | None = None) -> dict:
    validate_queries(queries, region, page_size)
    http = opener if opener is not None else build_opener(NoRedirect())
    init, session = _post(
        "initialize",
        {"protocolVersion": MCP_VERSION, "capabilities": {},
         "clientInfo": {"name": "serviceit-leadgen", "version": "0.1"}},
        request_id=1, opener=http, session=None)
    if not isinstance(init.get("result"), dict):
        raise SourceBlocked("MCP_INIT_FAILED")
    negotiated = init["result"].get("protocolVersion", MCP_VERSION)
    if negotiated != MCP_VERSION:
        raise SourceBlocked("MCP_VERSION_NOT_NEGOTIATED")
    _post("notifications/initialized", {}, request_id=None, opener=http, session=session)
    observed = observed_at or datetime.now(timezone.utc)
    output = []
    for idx, phrase in enumerate(queries):
        response, session = _post(
            "tools/call",
            {"name": "search_tenders", "arguments": {
                "query": phrase, "regionCodes": [region],
                "page": 0, "pageSize": page_size, "period": "2y"}},
            request_id=idx + 2, opener=http, session=session)
        output.append({"query": phrase, "region": region, **_results_shape(response)})
    return {
        "source": "KRASOFT_OFFICIAL_PUBLIC_MCP",
        "channel_type": "AGGREGATED_PROCUREMENT_NOT_FIRST_PARTY",
        "observed_at": observed.astimezone(timezone.utc).isoformat(),
        "live_http_probe": "MCP_RESPONSES_ACCEPTED",
        "searches_executed": len(output),
        "queries": output,
        "qualified_real_leads": 0,
        "buyer_inn_verified": 0,
        "fns_micro_small_77_50": 0,
        "crm_writes": "NO",
        "outreach": "NO",
        "warning": "No original notice, buyer, FNS, deadline, dedup or suppression checks yet.",
    }


def main(argv=None):
    cli = argparse.ArgumentParser(description="Authorized free anonymous Krasoft MCP research probe")
    cli.add_argument("--query", action="append", dest="queries")
    cli.add_argument("--region", default="77", choices=sorted(REGIONS))
    cli.add_argument("--page-size", type=int, default=5)
    cli.add_argument("--output", required=True)
    args = cli.parse_args(argv)
    out = Path(args.output).resolve()
    if out.suffix != ".json" or not out.is_relative_to(Path("exports").resolve()) or out.exists():
        raise SourceBlocked("OUTPUT_MUST_BE_NEW_JSON_UNDER_EXPORTS")
    try:
        report = probe(args.queries or ["сопровождение 1С"], region=args.region, page_size=args.page_size)
    except SourceBlocked as exc:
        print(json.dumps({"live_probe": "BLOCKED", "reason": str(exc),
                          "qualified_real_leads": 0, "crm_writes": "NO"}))
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"live_probe": report["live_http_probe"],
                      "searches_executed": len(report["queries"]),
                      "qualified_real_leads": 0, "crm_writes": "NO"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
