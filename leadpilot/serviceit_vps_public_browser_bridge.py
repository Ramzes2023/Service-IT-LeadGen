"""Secretless localhost-only bridge for a separate, sandboxed public Chromium runner.

Only the Docker HOST is allowed to access this service via an explicitly bound
127.0.0.1 port. It accepts one approved public HTTPS URL and returns bounded,
unverified page text. It never sees Bitrix/AI/PG tokens or customer records.
An actual egress firewall is required *before* the container can be enabled.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading

from .serviceit_vps_public_browser import (
    BrowserBlocked, approved_hosts, render_public,
)

MAX_REQUEST_BYTES = 1200
MAX_TEXT_RESPONSE = 6000
RENDER_LIMIT = threading.BoundedSemaphore(value=1)


def render_request(request: object, hosts: frozenset[str]) -> dict:
    """Pure boundary: prohibit extra fields/CRM contents or arbitrary actions."""
    if (type(request) is not dict or set(request) != {"url"}
            or type(request["url"]) is not str):
        raise BrowserBlocked("BROWSER_RENDER_REQUEST_INVALID")
    result = render_public(request["url"], hosts)
    if (not isinstance(result, dict)
            or result.get("status") != "BROWSER_RENDERED_PUBLIC_PAGE_UNVERIFIED"
            or type(result.get("text")) is not str
            or type(result.get("public_page_host")) is not str):
        raise BrowserBlocked("BROWSER_RENDER_RESULT_INVALID")
    return {
        "status": "BROWSER_RENDERED_PUBLIC_PAGE_UNVERIFIED",
        "public_page_host": result["public_page_host"],
        "text": result["text"][:MAX_TEXT_RESPONSE],
        "requests_checked": result.get("requests_checked", 0),
        "verified_company": False,
        "crm_writes": 0,
        "model_calls": 0,
    }


class BrowserServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, hosts: frozenset[str]):
        self.hosts = hosts
        super().__init__(address, BrowserHandler)


class BrowserHandler(BaseHTTPRequestHandler):
    server: BrowserServer
    protocol_version = "HTTP/1.0"

    def log_message(self, format, *args):
        # URL bodies and page contents must never enter stdout/GitHub logs.
        pass

    def respond(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != "/health":
            return self.respond(404, {"status": "BROWSER_ENDPOINT_NOT_FOUND"})
        return self.respond(200, {
            "status": "BROWSER_BRIDGE_READY_NOT_EGRESS_CERTIFIED",
            "crm_writes": 0,
        })

    def do_POST(self):
        if self.path != "/render":
            return self.respond(404, {"status": "BROWSER_ENDPOINT_NOT_FOUND"})
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return self.respond(415, {"status": "BROWSER_JSON_ONLY"})
        size = self.headers.get("Content-Length", "")
        if not size.isdecimal() or not 0 < int(size) <= MAX_REQUEST_BYTES:
            return self.respond(413, {"status": "BROWSER_REQUEST_TOO_LARGE"})
        if not RENDER_LIMIT.acquire(blocking=False):
            return self.respond(429, {"status": "BROWSER_BUSY_NO_RETRY"})
        try:
            try:
                request = json.loads(self.rfile.read(int(size)))
                result = render_request(request, self.server.hosts)
                return self.respond(200, result)
            except (ValueError, UnicodeError, TypeError):
                return self.respond(422, {"status": "BROWSER_BLOCKED_OR_UNVERIFIED"})
            except Exception:
                return self.respond(503, {"status": "BROWSER_RENDER_FAILED"})
        finally:
            RENDER_LIMIT.release()


def main() -> None:
    hosts = approved_hosts(os.environ.get("SERVICEIT_VPS_BROWSER_APPROVED_HOSTS", ""))
    # Bind to the container interface; Compose must publish *HOST* loopback
    # only. Container bridge network must have a reviewed egress firewall.
    with BrowserServer(("0.0.0.0", 8765), hosts) as server:
        print(json.dumps({"status": "BROWSER_BRIDGE_LISTENING", "crm_writes": 0}),
              flush=True)
        server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
