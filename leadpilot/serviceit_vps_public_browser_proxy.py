"""Pinned-public-IP HTTPS CONNECT egress for the isolated public browser.

The browser itself runs on an INTERNAL-only Docker bridge. This separate
secretless proxy has the only public egress. It permits HTTPS CONNECT to
owner-approved hostnames whose DNS results are entirely global; the socket is
connected to the *validated IP*, never re-resolved after inspection.
No HTTP forward proxy, private networks, metadata, authentication or CRM calls.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
import select
import socket
import threading
import time

from .serviceit_vps_public_browser import approved_hosts
from .serviceit_public_evidence_research import SAFE_HOST

CONNECT_LIMIT = threading.BoundedSemaphore(8)
MAX_TUNNEL_SECONDS = 28
MAX_TUNNEL_BYTES = 16 * 1024 * 1024


def resolve_public_socket(host: str, port: int, allowed: frozenset[str],
                          *, resolver=socket.getaddrinfo, connector=None):
    """Returns a connected socket to an ALREADY validated public IP."""
    if (type(host) is not str or not SAFE_HOST.fullmatch(host)
            or not isinstance(allowed, frozenset)
            or not any(host == item or host.endswith("." + item) for item in allowed)
            or port != 443):
        raise ValueError("PROXY_PUBLIC_HOST_NOT_APPROVED")
    addresses = resolver(host, port, type=socket.SOCK_STREAM)
    if not addresses or len(addresses) > 30:
        raise ValueError("PROXY_DNS_NOT_VERIFIED")
    # Fail the entire request if ANY answer resolves to a non-global address.
    # No DNS rebinding window: TCP connects to the vetted sockaddr below.
    if any(not ipaddress.ip_address(info[4][0]).is_global for info in addresses):
        raise ValueError("PROXY_DNS_NOT_PUBLIC")
    for family, socktype, proto, _name, sockaddr in addresses:
        if socktype != socket.SOCK_STREAM:
            continue
        upstream = socket.socket(family, socktype, proto)
        upstream.settimeout(6)
        try:
            upstream.connect(sockaddr)
            upstream.settimeout(2)
            return upstream
        except (OSError, TimeoutError):
            upstream.close()
    raise ValueError("PROXY_PUBLIC_CONNECT_FAILED")


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    def log_message(self, fmt, *args):
        # Never log browsing targets, company content or customer information.
        pass

    def do_CONNECT(self):
        target = self.path.split(":")
        if (len(target) != 2 or not target[1].isdigit()
                or len(self.path) > 250):
            self.send_error(403)
            return
        if not CONNECT_LIMIT.acquire(blocking=False):
            self.send_error(429)
            return
        upstream = None
        try:
            upstream = resolve_public_socket(target[0].lower(),
                                             int(target[1]), self.server.hosts)
            self.send_response(200, "Connection Established")
            self.end_headers()
            started = time.monotonic()
            transferred = 0
            self.connection.settimeout(2)
            while (time.monotonic() - started < MAX_TUNNEL_SECONDS
                   and transferred < MAX_TUNNEL_BYTES):
                ready, _, _ = select.select([self.connection, upstream], [], [], 2)
                if not ready:
                    continue
                for source in ready:
                    data = source.recv(min(65536, MAX_TUNNEL_BYTES - transferred))
                    if not data:
                        return
                    dest = upstream if source is self.connection else self.connection
                    dest.sendall(data)
                    transferred += len(data)
        except (ValueError, OSError, TimeoutError):
            # No URL or IP details in console logs or response.
            if upstream is None:
                try:
                    self.send_error(403)
                except OSError:
                    pass
        finally:
            if upstream is not None:
                upstream.close()
            CONNECT_LIMIT.release()

    def do_GET(self):
        self.send_error(403)

    def do_POST(self):
        self.send_error(403)


class ProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    def __init__(self, address, hosts: frozenset[str]):
        self.hosts = hosts
        super().__init__(address, ProxyHandler)


def main():
    hosts = approved_hosts(os.environ.get("SERVICEIT_VPS_BROWSER_APPROVED_HOSTS", ""))
    with ProxyServer(("0.0.0.0", 3128), hosts) as server:
        print(json.dumps({"status": "PUBLIC_HTTPS_CONNECT_PROXY_READY",
                          "secrets": 0, "crm_writes": 0}), flush=True)
        server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
