"""Trace bounded public source discovery for three verified inbox records."""
import os
import re
from collections import Counter

import httpx

from leadpilot.serviceit_b2b_v19_backfill import Rest, normalized, validate_identity
from leadpilot.serviceit_public_evidence_research import MAX_HTML, VisibleText
from leadpilot.serviceit_vps_b2b_deep_research import safe_notice, source_findings
from leadpilot.serviceit_vps_pg_queue import db
from leadpilot.serviceit_vps_worker import exact_environment

TENDERS = (("4676478", 19), ("4676643", 21), ("4677404", 23))


def out(key, value):
    print(f"SOURCE_AUDIT_{key}={value}", flush=True)


def provider(request):
    host = request.url.host.removeprefix("www.")
    path = request.url.path
    if host == "127.0.0.1" and request.url.port == 18785 and path == "/render":
        return "BROWSER"
    if host == "api.search.brave.com":
        return "BRAVE_CONTEXT" if "context" in path else "BRAVE_WEB"
    if host == "api.search.tinyfish.ai":
        return "TINYFISH_SEARCH"
    if host == "api.fetch.tinyfish.ai":
        return "TINYFISH_FETCH"
    if host == "b2b-center.ru":
        if path == "/robots.txt":
            return "B2B_ROBOTS"
        return "B2B_DETAIL" if "tender-" in path else "B2B_OTHER"
    if host == "tendem.ru":
        return "TENDEM"
    if host == "zakupki.kontur.ru":
        return "KONTUR"
    return "OTHER"


def trace(tid, item, notice, env):
    calls, responses, metrics = Counter(), Counter(), Counter()

    def requested(request):
        calls[provider(request)] += 1

    def received(response):
        name = provider(response.request)
        responses[name, response.status_code] += 1
        if name == "B2B_DETAIL" and response.status_code == 200:
            response.read()
            if len(response.content) <= MAX_HTML:
                try:
                    parser = VisibleText()
                    parser.feed(response.text)
                    visible = " ".join(parser.parts)
                    exact = re.compile(r"(?<!\d)" + tid + r"(?!\d)")
                    metrics["DETAIL_VISIBLE_CHARS"] += len(visible)
                    metrics["DETAIL_VISIBLE_EXACT_ID"] += bool(exact.search(visible))
                    metrics["DETAIL_RAW_EXACT_ID"] += bool(exact.search(response.text))
                    metrics["DETAIL_VISIBLE_EXCERPT_EXACT_ID"] += bool(exact.search(visible[:4000]))
                except ValueError:
                    metrics["DETAIL_HTML_PARSE_FAILED"] += 1
        if name == "BROWSER":
            try:
                request = response.request
                exact_url = notice.get("source_url", "").encode()
                if exact_url and exact_url in request.content:
                    metrics[f"OFFICIAL_BROWSER_HTTP_{response.status_code}"] += 1
            except httpx.RequestNotRead:
                pass
        if response.status_code != 200 or name not in {
            "TINYFISH_SEARCH", "TINYFISH_FETCH", "BRAVE_WEB",
        }:
            return
        try:
            response.read()
            if len(response.content) > 350000:
                return
            payload = response.json()
            rows = (payload.get("web", {}).get("results") if name == "BRAVE_WEB"
                    else payload.get("results"))
            if not isinstance(rows, list):
                metrics[name + "_UNEXPECTED_SCHEMA"] += 1
                return
            metrics.setdefault(name + "_ROWS", 0)
            metrics.setdefault(name + "_EXACT_ID", 0)
            metrics[name + "_ROWS"] += len(rows)
            for row in rows:
                if not isinstance(row, dict):
                    continue
                searchable = ("url", "title", "snippet", "description")
                if name == "TINYFISH_FETCH":
                    searchable = ("text",)
                    metrics[name + "_TEXT_ROWS"] += bool(row.get("text"))
                    metrics[name + "_FINAL_URL_CHANGED"] += bool(
                        row.get("url") and row.get("final_url") != row.get("url"))
                metrics[name + "_EXACT_ID"] += any(
                    tid in str(row.get(field, "")) for field in searchable)
        except (ValueError, TypeError, AttributeError, httpx.HTTPError):
            metrics[name + "_UNREADABLE_PAYLOAD"] += 1

    with httpx.Client(trust_env=False, follow_redirects=False, timeout=18,
                      event_hooks={"request": [requested], "response": [received]}) as client:
        result = source_findings(item, notice, env, client, enable_v18=True)
    for name, count in sorted(calls.items()):
        out(f"{tid}_{name}_REQUESTS", count)
    for (name, status), count in sorted(responses.items()):
        out(f"{tid}_{name}_HTTP_{status}", count)
    for name, count in sorted(metrics.items()):
        out(f"{tid}_{name}", count)
    out(f"{tid}_SEARCH", result.get("status", "UNKNOWN"))
    out(f"{tid}_BRAVE_INDEX", result.get("v18_search_status", "UNKNOWN"))
    out(f"{tid}_BRAVE_CANDIDATES", len(result.get("indexed_candidates") or []))
    out(f"{tid}_TINYFISH", result.get("tinyfish_independent_stage", "UNKNOWN"))
    out(f"{tid}_VERIFIED_PAGES", len(result.get("pages") or []))


def main():
    env = exact_environment(dict(os.environ))
    for label, key in (
        ("TENDEM_AUTHORIZED", "SERVICEIT_B2B_TENDEM_PUBLIC_EXTRACTION_APPROVED"),
        ("B2B_CLOUD_AUTHORIZED", "SERVICEIT_B2B_CENTER_PUBLIC_EXTRACTION_LICENSED"),
        ("TINYFISH_RETRY_ENABLED", "SERVICEIT_VPS_B2B_TINYFISH_RETRY_ENABLED"),
        ("BROWSER_ENABLED", "SERVICEIT_VPS_BROWSER_RESEARCH_ENABLED"),
    ):
        out(label, "YES" if env.get(key) == "YES" else "NO")
    cards = []
    with db(env) as conn, conn.transaction(), httpx.Client(
            trust_env=False, follow_redirects=False, timeout=18) as client:
        conn.execute("SET TRANSACTION READ ONLY")
        has_retries = conn.execute(
            "SELECT to_regclass('serviceit_vps_b2b_tinyfish_retry_attempts') IS NOT NULL"
        ).fetchone()[0]
        rest = Rest(env["SERVICEIT_BITRIX_WEBHOOK"], client)
        for tid, card in TENDERS:
            source = f"B2B-CENTER-{tid}"
            row = conn.execute(
                "SELECT notice_json FROM serviceit_vps_b2b_inbox "
                "WHERE source_id=%s AND card_id=%s AND state='VERIFIED'",
                (source, card)).fetchone()
            if row is None:
                out(f"{tid}_IDENTITY", "NO_VERIFIED_INBOX")
                continue
            item = normalized(rest("crm.item.get", {
                "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y",
            })["item"])
            validate_identity(item, card)
            safe_notice(row[0], card, source, item)
            out(f"{tid}_IDENTITY", "VERIFIED")
            retry = conn.execute(
                "SELECT attempt_no,status FROM serviceit_vps_b2b_tinyfish_retry_attempts "
                "WHERE card_id=%s ORDER BY attempt_no DESC LIMIT 1", (card,)
            ).fetchone() if has_retries else None
            out(f"{tid}_RETRY", retry[1] if retry else "NO_RECEIPT")
            out(f"{tid}_RETRY_COUNT", retry[0] if retry else 0)
            cards.append((tid, item, row[0]))
    for tid, item, notice in cards:
        try:
            trace(tid, item, notice, env)
        except Exception as error:
            out(f"{tid}_FAILURE_CLASS", type(error).__name__.upper())
    out("CRM_WRITES", 0)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        out("FAILURE_CLASS", type(error).__name__.upper())
        raise SystemExit(2) from None
