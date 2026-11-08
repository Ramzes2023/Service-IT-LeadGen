"""Localhost-only browser ingress relay running in the secretless egress sidecar.

The Chromium container remains on exactly one Docker internal bridge and has
no public route. Docker publishes the ingress on the egress sidecar's normal
bridge to 127.0.0.1 on the VPS host. Relay cannot target arbitrary addresses,
ports, paths, containers, or Internet hosts.
"""
from __future__ import annotations

import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from urllib.parse import urlsplit

from .serviceit_vps_public_browser import approved_hosts

MAX_REQUEST_BYTES = 1200
MAX_REPLY_BYTES = 10000
RENDER_LIMIT = threading.BoundedSemaphore(1)
UPSTREAM = "browser"
UPSTREAM_PORT = 8765


def approved_url_only(url: object, hosts: frozenset[str]) -> bool:
    if type(url) is not str or len(url) > 1000 or any(ord(c) < 32 for c in url):
        return False
    try:
        p = urlsplit(url)
        host = (p.hostname or "").lower()
        port = p.port
    except ValueError:
        return False
    return (p.scheme == "https" and bool(host) and
            port in (None, 443) and not p.username and not p.password and
            not p.fragment and
            any(host == h or host.endswith("." + h) for h in hosts))


def browser_request(path: str, *, payload: bytes | None = None) -> tuple[int, dict]:
    """Forward to one hardcoded internal browser service; never use env proxies."""
    connection = http.client.HTTPConnection(UPSTREAM, UPSTREAM_PORT, timeout=45)
    try:
        if payload is None:
            connection.request("GET", "/health", headers={"Connection": "close"})
        else:
            connection.request("POST", "/render", body=payload,
                               headers={"Content-Type": "application/json",
                                        "Connection": "close"})
        response = connection.getresponse()
        if response.status not in (200, 422, 429, 503):
            raise ValueError("UNEXPECTED_UPSTREAM_STATUS")
        body = response.read(MAX_REPLY_BYTES + 1)
        if len(body) > MAX_REPLY_BYTES:
            raise ValueError("UPSTREAM_RESPONSE_TOO_LARGE")
        if "application/json" not in response.getheader("Content-Type", ""):
            raise ValueError("UPSTREAM_RESPONSE_NOT_JSON")
        data = json.loads(body)
        if type(data) is not dict or type(data.get("status")) is not str:
            raise ValueError("UPSTREAM_RESPONSE_INVALID")
        if response.status == 200:
            if path == "/health" and data.get("status") != "BROWSER_BRIDGE_READY_NOT_EGRESS_CERTIFIED":
                raise ValueError("UPSTREAM_HEALTH_INVALID")
            if path == "/render" and (
                data.get("status") != "BROWSER_RENDERED_PUBLIC_PAGE_UNVERIFIED"
                or type(data.get("text")) is not str
                or len(data["text"]) > 6000
                or data.get("crm_writes") != 0
                or data.get("model_calls") != 0
                or not isinstance(data.get("public_page_host"), str)
            ):
                raise ValueError("UPSTREAM_RENDER_INVALID")
        return response.status, data
    finally:
        connection.close()


class IngressHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *_args):
        # Hostnames, text, and request bodies must never enter CI logs.
        pass

    def send_json(self, status: int, data: dict):
        body = json.dumps(data, separators=(",", ":")).encode("utf8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != "/health":
            return self.send_json(404, {"status": "INGRESS_PATH_DENIED"})
        try:
            status, payload = browser_request("/health")
            return self.send_json(status, payload)
        except (OSError, ValueError, TimeoutError, http.client.HTTPException):
            return self.send_json(503, {"status": "BROWSER_HEALTH_UNREACHABLE"})

    def do_POST(self):
        if self.path != "/render":
            return self.send_json(404, {"status": "INGRESS_PATH_DENIED"})
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
            return self.send_json(415, {"status": "INGRESS_JSON_REQUIRED"})
        length = self.headers.get("Content-Length", "")
        if not length.isdecimal() or not 0 < int(length) <= MAX_REQUEST_BYTES:
            return self.send_json(413, {"status": "INGRESS_REQUEST_TOO_LARGE"})
        if not RENDER_LIMIT.acquire(blocking=False):
            return self.send_json(429, {"status": "INGRESS_BUSY_NO_RETRY"})
        try:
            try:
                self.connection.settimeout(5)
                raw = self.rfile.read(int(length))
                request = json.loads(raw)
                if (type(request) is not dict or set(request) != {"url"}
                        or not approved_url_only(request["url"], self.server.hosts)):
                    return self.send_json(422, {"status": "INGRESS_PUBLIC_URL_NOT_APPROVED"})
                safe = json.dumps({"url": request["url"]}).encode("utf8")
                self.connection.settimeout(48)
                status, result = browser_request("/render", payload=safe)
                return self.send_json(status, result)
            except (OSError, ValueError, UnicodeError, TimeoutError,
                    http.client.HTTPException):
                return self.send_json(503, {"status": "BROWSER_INGRESS_UNAVAILABLE"})
        finally:
            RENDER_LIMIT.release()


class IngressServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple, hosts: frozenset[str]):
        self.hosts = hosts
        super().__init__(address, IngressHandler)
