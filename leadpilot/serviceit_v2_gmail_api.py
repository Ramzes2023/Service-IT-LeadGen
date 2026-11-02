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

import httpx

SENDER = "marketplace@b2b-center.ru"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
MAX_RAW_BYTES = 256_000
MAX_MESSAGES = 100


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
                 refresh_token: str):
        _must(isinstance(client, httpx.Client), "HTTP_CLIENT_REQUIRED")
        for value in (client_id, client_secret, refresh_token):
            _must(isinstance(value, str) and 12 <= len(value) <= 4096
                  and value == value.strip(), "OAUTH_CREDENTIAL_MISSING")
        self.client = client
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token

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

    def recent_messages(self, *, lookback_days=14, max_messages=60, should_fetch=None):
        _must(type(lookback_days) is int and 1 <= lookback_days <= 30,
              "LOOKBACK_INVALID")
        _must(type(max_messages) is int and 1 <= max_messages <= MAX_MESSAGES,
              "MESSAGE_LIMIT_INVALID")
        token = self._token()
        headers = {"Authorization": "Bearer " + token}
        query = "from:" + SENDER + " newer_than:" + str(lookback_days) + "d in:inbox"
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


def from_environment(client: httpx.Client):
    # Never disclose secret values in errors or logs.
    keys = ("SERVICEIT_V2_GMAIL_CLIENT_ID", "SERVICEIT_V2_GMAIL_CLIENT_SECRET",
            "SERVICEIT_V2_GMAIL_REFRESH_TOKEN")
    values = tuple(os.environ.get(key, "") for key in keys)
    return GmailReadOnly(client, client_id=values[0], client_secret=values[1],
                         refresh_token=values[2])
