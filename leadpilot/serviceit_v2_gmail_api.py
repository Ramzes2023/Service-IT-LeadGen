"""Gmail readonly OAuth2 transport for Service IT v2.

Credential is an owner-authorized REFRESH TOKEN, not the ChatGPT Gmail
connector or a password. Calls only list/get; never modifies mailbox.
"""
from __future__ import annotations

import base64
import imaplib
import json
import os
import re
from pathlib import Path

import httpx

SENDER = "marketplace@b2b-center.ru"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
MAX_RAW_BYTES = 256_000
MAX_MESSAGES = 100
PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
OAUTH_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


class GmailSourceBlocked(ValueError):
    pass


def _must(ok: bool, reason: str):
    if not ok:
        raise GmailSourceBlocked(reason)


def _safe_json(response: httpx.Response, *, limit=512_000):
    _must(response.status_code == 200 and len(response.content) < limit,
          "REMOTE_RESPONSE_BLOCKED")
    try:
        value = response.json()
    except (ValueError, TypeError):
        raise GmailSourceBlocked("INVALID_RESPONSE_JSON") from None
    _must(isinstance(value, dict), "INVALID_RESPONSE_SHAPE")
    return value


class GmailReadOnly:
    def __init__(self, client: httpx.Client, *, client_id: str, client_secret: str,
                 refresh_token: str, expected_email=None):
        _must(isinstance(client, httpx.Client), "HTTP_CLIENT_REQUIRED")
        for value in (client_id, client_secret, refresh_token):
            _must(isinstance(value, str) and 12 <= len(value) <= 4096
                  and value == value.strip(), "OAUTH_CREDENTIAL_MISSING")
        self.client = client
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        if expected_email is not None:
            _must(isinstance(expected_email, str) and
                  re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}", expected_email),
                  "EXPECTED_MAILBOX_INVALID")
        self.expected_email = expected_email.lower() if expected_email else None

    def _token(self):
        try:
            response = self.client.post(GOOGLE_TOKEN, data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            }, timeout=15, follow_redirects=False)
            payload = _safe_json(response, limit=32_000)
        except httpx.HTTPError:
            raise GmailSourceBlocked("OAUTH_REFRESH_UNAVAILABLE") from None
        token = payload.get("access_token")
        _must(isinstance(token, str) and 12 <= len(token) <= 4096
              and not re.search(r"\s", token), "OAUTH_ACCESS_TOKEN_INVALID")
        return token

    def _verify_account(self, token):
        if not self.expected_email:
            return
        try:
            response = self.client.get(PROFILE_URL,
                headers={"Authorization": "Bearer " + token},
                timeout=15, follow_redirects=False)
            data = _safe_json(response, limit=32_000)
        except httpx.HTTPError:
            raise GmailSourceBlocked("GMAIL_ACCOUNT_CHECK_UNAVAILABLE") from None
        address = data.get("emailAddress")
        _must(isinstance(address, str) and
              address.lower() == self.expected_email,
              "GMAIL_WRONG_ACCOUNT_BLOCKED")

    def recent_messages(self, *, lookback_days=14, max_messages=60, should_fetch=None):
        _must(type(lookback_days) is int and 1 <= lookback_days <= 30,
              "LOOKBACK_INVALID")
        _must(type(max_messages) is int and 1 <= max_messages <= MAX_MESSAGES,
              "MESSAGE_LIMIT_INVALID")
        token = self._token()
        self._verify_account(token)
        headers = {"Authorization": "Bearer " + token}
        # Include archived vendor notices too; API default excludes Spam/Trash.
        query = "from:" + SENDER + " newer_than:" + str(lookback_days) + "d"
        ids: list[str] = []
        cursor = None
        seen_pages = set()
        # Bound all requests; do not read the remaining Gmail mailbox.
        for _ in range(5):
            params = {"q": query, "maxResults": min(100, max_messages - len(ids)),
                      "includeSpamTrash": "false"}
            if cursor:
                params["pageToken"] = cursor
            try:
                response = self.client.get(GMAIL_API, headers=headers,
                                           params=params, timeout=15,
                                           follow_redirects=False)
                data = _safe_json(response)
            except httpx.HTTPError:
                raise GmailSourceBlocked("GMAIL_LIST_UNAVAILABLE") from None
            rows = data.get("messages", [])
            _must(isinstance(rows, list) and len(rows) <= params["maxResults"],
                  "GMAIL_LIST_INVALID")
            for row in rows:
                mid = row.get("id") if isinstance(row, dict) else None
                _must(isinstance(mid, str)
                      and re.fullmatch(r"[a-f0-9]{8,24}", mid) is not None,
                      "GMAIL_MESSAGE_ID_INVALID")
                _must(mid not in ids, "GMAIL_DUPLICATE_MESSAGE_ID")
                ids.append(mid)
            cursor = data.get("nextPageToken")
            if len(ids) >= max_messages or not cursor:
                break
            _must(isinstance(cursor, str) and len(cursor) < 500
                  and cursor not in seen_pages, "GMAIL_PAGE_CURSOR_INVALID")
            seen_pages.add(cursor)
        else:
            raise GmailSourceBlocked("GMAIL_PAGINATION_BUDGET")
        # Fetch chronological-ish order so earlier revisions are inserted first.
        for mid in reversed(ids):
            if should_fetch is not None and not should_fetch(mid):
                continue
            try:
                response = self.client.get(
                    GMAIL_API + "/" + mid, headers=headers,
                    params={"format": "raw"}, timeout=15, follow_redirects=False)
                data = _safe_json(response, limit=512_000)
            except httpx.HTTPError:
                raise GmailSourceBlocked("GMAIL_GET_UNAVAILABLE") from None
            _must(data.get("id") == mid
                  and isinstance(data.get("raw"), str), "GMAIL_RAW_INVALID")
            try:
                raw = base64.urlsafe_b64decode(data["raw"] + "=" *
                                                (-len(data["raw"]) % 4))
            except (ValueError, TypeError, base64.binascii.Error):
                raise GmailSourceBlocked("GMAIL_RAW_DECODE_FAILED") from None
            _must(0 < len(raw) <= MAX_RAW_BYTES, "GMAIL_RAW_OVERSIZE")
            yield mid, raw


def _private_owner_bundle(path_value):
    """One owner-created Gmail readonly bundle; fail closed on wrong file."""
    path = Path(path_value).expanduser()
    _must(path.is_file() and not path.is_symlink() and
          0 < path.stat().st_size <= 16384, "OAUTH_BUNDLE_FILE_INVALID")
    if os.name != "nt":
        _must((path.stat().st_mode & 0o077) == 0 and
              path.stat().st_uid == os.geteuid(),
              "OAUTH_BUNDLE_FILE_NOT_PRIVATE")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError, UnicodeError):
        raise GmailSourceBlocked("OAUTH_BUNDLE_CONTENT_INVALID") from None
    names = {"client_id","client_secret","refresh_token",
             "expected_email","scope"}
    _must(isinstance(data, dict) and set(data) == names
          and data["scope"] == OAUTH_SCOPE
          and isinstance(data["expected_email"],str),
          "OAUTH_BUNDLE_SCOPE_OR_SHAPE_INVALID")
    return data


def from_environment(client: httpx.Client):
    """Use local owner-only file OR explicit private env, never mixed sources."""
    names = ("SERVICEIT_V2_GMAIL_CLIENT_ID", "SERVICEIT_V2_GMAIL_CLIENT_SECRET",
             "SERVICEIT_V2_GMAIL_REFRESH_TOKEN")
    file_path = os.environ.get("SERVICEIT_V2_GMAIL_OAUTH_FILE", "")
    expected = os.environ.get("SERVICEIT_V2_GMAIL_EXPECTED_EMAIL", "")
    if file_path:
        _must(not any(os.environ.get(n) for n in names),
              "MIXED_OAUTH_CREDENTIAL_SOURCES_FORBIDDEN")
        data = _private_owner_bundle(file_path)
        if expected:
            _must(expected.lower() == data["expected_email"].lower(),
                  "EXPECTED_MAILBOX_CONFLICT")
        return GmailReadOnly(
            client, client_id=data["client_id"],
            client_secret=data["client_secret"],
            refresh_token=data["refresh_token"],
            expected_email=data["expected_email"])
    _must(bool(expected), "EXPECTED_GMAIL_MAILBOX_REQUIRED")
    values = tuple(os.environ.get(k, "") for k in names)
    return GmailReadOnly(
        client, client_id=values[0],client_secret=values[1],
        refresh_token=values[2],expected_email=expected)
