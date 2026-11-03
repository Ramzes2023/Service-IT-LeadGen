"""Owner-run Desktop OAuth 2.0 PKCE bootstrap for Service IT V2 Gmail readonly.

NO browser automation, no ChatGPT connector access, no passwords. User grants
Gmail readonly through Google in their own browser; credentials are written
only to a NEW private local file after Gmail account identity verification.

For Google OAuth Desktop app clients only. Never prints access/refresh tokens.
"""
from __future__ import annotations

import argparse
import base64
import csv
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import time
from urllib.parse import urlencode, urlsplit, parse_qs
import webbrowser

import httpx

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
MAX_FILE = 16_384


class SetupBlocked(ValueError):
    pass


def must(ok, reason):
    if not ok:
        raise SetupBlocked(reason)


def load_desktop_client(path: Path):
    must(isinstance(path, Path) and path.is_file()
         and not path.is_symlink() and path.stat().st_size <= MAX_FILE,
         "GOOGLE_CLIENT_JSON_NOT_FOUND")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError, OSError):
        raise SetupBlocked("GOOGLE_CLIENT_JSON_INVALID") from None
    must(isinstance(doc, dict) and set(doc) == {"installed"}
         and isinstance(doc["installed"], dict), "GOOGLE_DESKTOP_CLIENT_REQUIRED")
    obj = doc["installed"]
    client_id, secret = obj.get("client_id"), obj.get("client_secret")
    must(isinstance(client_id, str) and
         re.fullmatch(r"[a-zA-Z0-9_-]{6,200}\.apps\.googleusercontent\.com",
                      client_id), "GOOGLE_CLIENT_ID_INVALID")
    must(isinstance(secret, str) and 4 <= len(secret) <= 1000
         and not re.search(r"\s", secret), "GOOGLE_CLIENT_SECRET_INVALID")
    must(obj.get("token_uri", TOKEN_URL) == TOKEN_URL, "TOKEN_ENDPOINT_INVALID")
    must(obj.get("auth_uri", AUTH_URL) in
         {"https://accounts.google.com/o/oauth2/auth", AUTH_URL},
         "AUTH_ENDPOINT_INVALID")
    uris = obj.get("redirect_uris", [])
    must(isinstance(uris, list) and
         any(u in {"http://localhost", "http://127.0.0.1"}
             for u in uris), "GOOGLE_DESKTOP_LOOPBACK_NOT_CONFIGURED")
    return {"client_id": client_id, "client_secret": secret}


def pkce_pair():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    must(43 <= len(verifier) <= 128, "PKCE_VERIFIER_INVALID")
    return verifier, challenge


def authorization_url(client_id, redirect_uri, challenge, state):
    must(re.fullmatch(r"http://127\.0\.0\.1:\d{2,5}/callback", redirect_uri),
         "LOOPBACK_REDIRECT_INVALID")
    must(isinstance(state, str) and len(state) >= 24, "OAUTH_STATE_INVALID")
    must(isinstance(challenge, str) and len(challenge) == 43,
         "PKCE_CHALLENGE_INVALID")
    return AUTH_URL + "?" + urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })


def exchange_code(http, cfg, *, code, verifier, redirect_uri):
    must(isinstance(http, httpx.Client) and
         isinstance(code, str) and 8 <= len(code) <= 2048
         and re.fullmatch(r"[\x21-\x7e]+", code), "AUTH_CODE_INVALID")
    try:
        res = http.post(TOKEN_URL, data={
            "code": code, "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "code_verifier": verifier, "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }, timeout=20, follow_redirects=False)
        must(res.status_code == 200 and len(res.content) < 16_384,
             "OAUTH_TOKEN_EXCHANGE_FAILED")
        token = res.json()
    except (httpx.HTTPError, ValueError, TypeError):
        raise SetupBlocked("OAUTH_TOKEN_EXCHANGE_FAILED") from None
    must(isinstance(token, dict), "OAUTH_TOKEN_INVALID")
    refresh, access = token.get("refresh_token"), token.get("access_token")
    must(all(isinstance(x, str) and 12 <= len(x) <= 4096
             and not re.search(r"\s", x) for x in [refresh, access]),
         "OAUTH_OFFLINE_REFRESH_TOKEN_MISSING")
    scopes = token.get("scope", "")
    must(isinstance(scopes, str) and SCOPE in scopes.split(),
         "GMAIL_READONLY_SCOPE_NOT_GRANTED")
    return refresh, access


def verified_gmail_address(http, access_token, expected_email):
    must(isinstance(expected_email, str)
         and re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
                          expected_email), "EXPECTED_GMAIL_ADDRESS_REQUIRED")
    try:
        res = http.get(PROFILE_URL, headers={
            "Authorization": "Bearer " + access_token,
        }, timeout=15, follow_redirects=False)
        must(res.status_code == 200 and len(res.content) < 16_384,
             "GMAIL_PROFILE_UNAVAILABLE")
        info = res.json()
    except (httpx.HTTPError, ValueError, TypeError):
        raise SetupBlocked("GMAIL_PROFILE_UNAVAILABLE") from None
    address = info.get("emailAddress") if isinstance(info, dict) else None
    must(isinstance(address, str)
         and address.lower() == expected_email.lower(),
         "GMAIL_WRONG_ACCOUNT_REFUSED")
    return address.lower()


def _windows_lock_file(path):
    try:
        who = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"],
                             capture_output=True, text=True, check=True, timeout=10)
        rows = list(csv.reader(who.stdout.splitlines()))
        must(len(rows) == 1 and len(rows[0]) == 2
             and re.fullmatch(r"S-\d+(?:-\d+)+", rows[0][1]),
             "WINDOWS_OWNER_SID_NOT_FOUND")
        sid = rows[0][1]
        for args in (
            ["icacls", str(path), "/inheritance:r"],
            ["icacls", str(path), "/grant:r", "*" + sid + ":(F)"],
        ):
            result = subprocess.run(args,capture_output=True,text=True,timeout=10)
            must(result.returncode == 0, "WINDOWS_PRIVATE_ACL_FAILED")
    except (OSError, subprocess.SubprocessError):
        raise SetupBlocked("WINDOWS_PRIVATE_ACL_FAILED") from None


def save_owner_secret(path: Path, cfg: dict, refresh: str, expected_email: str):
    """Create new output file only. Refuse overwrite; no tokens in stdout."""
    must(isinstance(path, Path) and path.suffix == ".json"
         and path.parent.is_dir() and not path.parent.is_symlink()
         and not path.exists(), "PRIVATE_OUTPUT_NOT_READY_OR_ALREADY_EXISTS")
    if os.name != "nt":
        must((path.parent.stat().st_mode & 0o077) == 0,
             "PRIVATE_PARENT_DIRECTORY_PERMISSIONS_REQUIRED")
    body = json.dumps({
        "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "refresh_token": refresh,
        "expected_email": expected_email,
        "scope": SCOPE,
    }, separators=(",", ":")).encode("utf-8")
    must(len(body) <= MAX_FILE, "OAUTH_SECRET_BUNDLE_TOO_LARGE")
    fd = None
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                     stat.S_IRUSR | stat.S_IWUSR)
        if os.name == "nt":
            _windows_lock_file(path)
        else:
            os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "wb") as f:
            fd = None
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
    except (OSError, SetupBlocked):
        if fd is not None:
            os.close(fd)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise SetupBlocked("PRIVATE_OAUTH_SECRET_SAVE_FAILED") from None
    return {"status": "OWNER_OAUTH_PRIVATE_BUNDLE_SAVED",
            "mailbox_verified": expected_email, "path": str(path),
            "refresh_token_printed": False, "crm_writes": 0}


def owner_loopback_code(*, client_id, timeout_seconds=180, browser_open=webbrowser.open):
    """Wait only for explicit browser approval in loopback. Validate state/Host."""
    must(30 <= timeout_seconds <= 300, "OAUTH_TIMEOUT_INVALID")
    state = secrets.token_urlsafe(32)
    verifier, challenge = pkce_pair()

    class Receiver(BaseHTTPRequestHandler):
        result = None

        def log_message(self, format, *args):
            return   # no codes, querystrings, IPs or tokens in logs

        def do_GET(self):
            parsed = urlsplit(self.path)
            valid_host = self.headers.get("Host", "") == (
                "127.0.0.1:" + str(self.server.server_port))
            args = parse_qs(parsed.query, keep_blank_values=True)
            ok = (valid_host and parsed.path == "/callback"
                  and len(args.get("state", [])) == 1
                  and secrets.compare_digest(args["state"][0], state))
            code = args.get("code", [])
            if ok and len(code) == 1 and code[0] and not args.get("error"):
                self.__class__.result = code[0]
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(
                b"Authorization received. Return to terminal." if ok else b"Invalid callback.")

    server = HTTPServer(("127.0.0.1", 0), Receiver)
    try:
        redirect_uri = f"http://127.0.0.1:{server.server_port}/callback"
        uri = authorization_url(client_id, redirect_uri, challenge, state)
        print("Откройте локальное окно Google и подтвердите доступ только на чтение Gmail.")
        must(browser_open(uri) is not False, "CANNOT_OPEN_BROWSER")
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline and Receiver.result is None:
            server.timeout = min(2.0, max(0.1, deadline-time.monotonic()))
            server.handle_request()
        must(isinstance(Receiver.result, str), "GOOGLE_OAUTH_CALLBACK_TIMEOUT")
        return Receiver.result, verifier, redirect_uri
    finally:
        server.server_close()


def run(argv=None):
    p = argparse.ArgumentParser(description="Service IT Gmail OAuth, desktop only")
    p.add_argument("--client-json",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--expected-email",required=True)
    p.add_argument("--timeout",type=int,default=180)
    a = p.parse_args(argv)
    try:
        cfg = load_desktop_client(Path(a.client_json).expanduser().resolve())
        target = Path(a.output).expanduser().resolve()
        must(not target.exists() and target.parent.is_dir(),
             "PRIVATE_OUTPUT_MUST_BE_NEW")
        code, verifier, redirect_uri = owner_loopback_code(
            client_id=cfg["client_id"],timeout_seconds=a.timeout)
        with httpx.Client(trust_env=False,follow_redirects=False) as http:
            refresh, access = exchange_code(http,cfg,code=code,
                                            verifier=verifier,
                                            redirect_uri=redirect_uri)
            account = verified_gmail_address(http,access,a.expected_email)
        result = save_owner_secret(target,cfg,refresh,account)
        print(json.dumps(result,ensure_ascii=False))
        return 0
    except (SetupBlocked, OSError, httpx.HTTPError):
        print(json.dumps({"status":"SERVICEIT_OAUTH_SETUP_BLOCKED",
                          "tokens_exposed":False,"crm_writes":0}))
        return 2


if __name__ == "__main__":
    raise SystemExit(run())
