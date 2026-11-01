"""Strict Bitrix24 read-only transport for the isolated ServiceIT 1062 demo.

This is NOT a live writer, CRM sync worker, source connector, or authorization
to ingest a real customer. It can only read one synthetic key from 1062/21 via
crm.item.list / crm.item.get. No secret is logged, persisted or serialized.

The caller must provide a validated, rotated webhook IN MEMORY and an httpx
Client. Running unit tests never calls the company portal.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

import httpx

from .bitrix_metadata_audit import validate_webhook

ENTITY_TYPE_ID = 1062
CATEGORY_ID = 21
KEY_FIELD = "UF_CRM_13_SVCIT_IDEMPOTENCY_KEY"
KEY_PATTERN = re.compile(r"DEMO-IDEMP-[a-f0-9]{32}\Z")
LIST_SELECT = ("id", "entityTypeId", "categoryId", "stageId", "title", KEY_FIELD)
ALLOWLIST = frozenset(("crm.item.list", "crm.item.get"))
MAX_BYTES = 512_000


class Bitrix1062ReadError(ValueError):
    """Sanitized failure; never include full REST URL, token or response body."""


def _deny(code: str) -> None:
    raise Bitrix1062ReadError(code)


def validate_read_request(method: str, params: Mapping[str, Any]) -> None:
    """Reject broad CRM scans, other processes and any mutation method."""
    if method not in ALLOWLIST or not isinstance(params, dict):
        _deny("BITRIX1062_READ_METHOD_FORBIDDEN")
    if params.get("entityTypeId") != ENTITY_TYPE_ID or type(params.get("entityTypeId")) is not int:
        _deny("BITRIX1062_OUTSIDE_ENTITY")
    if params.get("useOriginalUfNames") != "Y":
        _deny("BITRIX1062_ORIGINAL_UF_NAMES_REQUIRED")
    if method == "crm.item.list":
        if set(params) != {"entityTypeId", "filter", "select", "useOriginalUfNames", "start"}:
            _deny("BITRIX1062_LIST_SHAPE_FORBIDDEN")
        filt = params["filter"]
        if (not isinstance(filt, dict) or set(filt) != {"categoryId", KEY_FIELD}
                or type(filt["categoryId"]) is not int or filt["categoryId"] != CATEGORY_ID
                or not isinstance(filt[KEY_FIELD], str) or not KEY_PATTERN.fullmatch(filt[KEY_FIELD])):
            _deny("BITRIX1062_LIST_NOT_EXACT_SYNTHETIC_KEY")
        if params["select"] != list(LIST_SELECT) or type(params["start"]) is not int or params["start"] != 0:
            _deny("BITRIX1062_LIST_SELECT_OR_PAGINATION_INVALID")
    else:
        if set(params) != {"entityTypeId", "id", "useOriginalUfNames"}:
            _deny("BITRIX1062_GET_SHAPE_FORBIDDEN")
        if type(params["id"]) is not int or params["id"] <= 0:
            _deny("BITRIX1062_GET_ID_INVALID")


class Bitrix1062ReadOnly:
    """Callable injected into inspect_remote_synthetic_key/reconcile_uncertain_key.

    No POST to CRM mutation methods is possible through this object. An
    injected client enables isolated tests using httpx.MockTransport.
    """

    def __init__(self, base_webhook: str, *, client: httpx.Client) -> None:
        self._base = validate_webhook(base_webhook)
        if not isinstance(client, httpx.Client):
            raise TypeError("BITRIX1062_HTTP_CLIENT_REQUIRED")
        self._client = client
        # GET is permitted only after an exact synthetic-key LIST in this session.
        self._allowed_ids: dict[int, str] = {}

    def __repr__(self) -> str:
        return "Bitrix1062ReadOnly(portal=serviseit.bitrix24.ru, methods=READ_ONLY)"

    def __call__(self, method: str, params: dict) -> dict:
        validate_read_request(method, params)
        if method == "crm.item.list":
            self._allowed_ids.clear()
        elif params["id"] not in self._allowed_ids:
            _deny("BITRIX1062_GET_REQUIRES_EXACT_LIST_BINDING")
        try:
            response = self._client.post(
                self._base + "/" + method + ".json",
                json=params,
                follow_redirects=False,
                timeout=12.0,
            )
        except (httpx.HTTPError, ValueError, TypeError):
            _deny("BITRIX1062_HTTP_OR_PARSE_FAILURE")
        if response.status_code != 200 or len(response.content) > MAX_BYTES:
            _deny("BITRIX1062_HTTP_DENIED_OR_UNAVAILABLE")
        try:
            body = response.json()
        except (ValueError, TypeError):
            _deny("BITRIX1062_HTTP_OR_PARSE_FAILURE")
        if not isinstance(body, dict) or "error" in body or not isinstance(body.get("result"), dict):
            _deny("BITRIX1062_REST_DENIED_OR_MALFORMED")
        result = body["result"]
        if method == "crm.item.list":
            items = result.get("items")
            if not isinstance(items, list):
                _deny("BITRIX1062_ITEMS_MALFORMED")
            # A full page or REST paging metadata can hide more duplicates.
            # Do not accept a partial view as proof of uniqueness.
            if (len(items) >= 50 or body.get("next") is not None
                    or (type(body.get("total")) is int and body["total"] > len(items))):
                _deny("BITRIX1062_RESULT_PAGINATION_AMBIGUOUS")
            key = params["filter"][KEY_FIELD]
            for item in items:
                if (not isinstance(item, dict) or type(item.get("id")) is not int
                        or item["id"] <= 0 or item.get("entityTypeId") != ENTITY_TYPE_ID
                        or item.get("categoryId") != CATEGORY_ID
                        or item.get(KEY_FIELD) != key
                        or not isinstance(item.get("title"), str)
                        or not item["title"].startswith("DEMO ServiceIT AI — DEMO-")):
                    _deny("BITRIX1062_LIST_ITEM_OUT_OF_SCOPE")
            if len(items) == 1:
                self._allowed_ids[items[0]["id"]] = key
        else:
            item = result.get("item")
            if (not isinstance(item, dict) or item.get("id") != params["id"]
                    or item.get("entityTypeId") != ENTITY_TYPE_ID
                    or item.get("categoryId") != CATEGORY_ID
                    or item.get(KEY_FIELD) != self._allowed_ids[params["id"]]
                    or not isinstance(item.get("title"), str)
                    or not item["title"].startswith("DEMO ServiceIT AI — DEMO-")):
                _deny("BITRIX1062_GET_ITEM_OUT_OF_SCOPE")
        return result
