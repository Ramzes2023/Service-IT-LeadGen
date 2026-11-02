"""Read-only trusted cutover inventory for free GitHub Actions ServiceIT worker.

The live REST webhook is read only from an environment variable and never printed.
No CRM writes, model calls, or source changes. Does not create a test item.
"""
from __future__ import annotations

import os

import httpx

from .serviceit_enrichment_worker import Rest
from .serviceit_bitrix_list_shape import strict_bitrix_items, ItemListShapeError


class CutoverItemShapeError(ValueError):
    """Only a bounded index + a type from a fixed enum. Never carries item data."""
    def __init__(self, index: int, item_kind: str):
        self.index = index
        self.kind = item_kind
        super().__init__("CUTOVER_ITEM_INVALID")


def _valid_id(item):
    raw_id = item.get("id")
    if not (type(raw_id) is int or
            (type(raw_id) is str and raw_id.isascii() and raw_id.isdecimal())):
        raise ValueError("CUTOVER_CARD_ID_INVALID")
    value = int(raw_id)
    if not 0 < value <= 2_000_000_000:
        raise ValueError("CUTOVER_CARD_ID_OUT_OF_BOUNDS")
    return value


def _get_items(rest, *, filtered):
    params = {
        "entityTypeId": 1062, "useOriginalUfNames": "Y",
        "order": {"id": "DESC"},
        "select": ["id", "categoryId"], "start": 0,
    }
    if filtered:
        params["filter"] = {"categoryId": 21}
    response = rest("crm.item.list", params)
    try:
        return strict_bitrix_items(response)
    except ItemListShapeError as exc:
        if exc.kind == "EMPTY" and filtered:
            # A nonstandard nested empty group from the category-filtered
            # endpoint is not evidence of no historical cards. Retry with an
            # independent all-category view of the *same* type 1062.
            return None
        raise CutoverItemShapeError(exc.index, exc.kind) from None
    except ValueError:
        raise ValueError("CUTOVER_INVENTORY_INVALID") from None


def latest_existing_card_id(rest) -> int:
    """Highest visible ID across ALL categories, a conservative safe cutover.

    The category-filtered list is preferred. When it returns the known
    nonstandard [[]] shape, a fresh unfiltered list is mandatory. If there are
    other-category newer items, use their larger IDs instead of replaying them.
    The highest item is verified with crm.item.get, never written.
    """
    filtered = _get_items(rest, filtered=True)
    used_broad = filtered is None
    items = _get_items(rest, filtered=False) if used_broad else filtered
    if used_broad and not items:
        raise ValueError("CUTOVER_BROAD_EMPTY_UNVERIFIED")
    seen = set()
    previous = None
    for item in items:
        card_id = _valid_id(item)
        if ("entityTypeId" in item and str(item["entityTypeId"]) != "1062"):
            raise ValueError("CUTOVER_CATEGORY_MISMATCH")
        if not used_broad and str(item.get("categoryId")) != "21":
            raise ValueError("CUTOVER_CATEGORY_MISMATCH")
        if used_broad and "categoryId" not in item:
            raise ValueError("CUTOVER_CATEGORY_MISMATCH")
        if card_id in seen or (previous is not None and card_id >= previous):
            raise ValueError("CUTOVER_RESULT_NOT_DESCENDING")
        seen.add(card_id)
        previous = card_id
    if not items:
        return 7
    first = _valid_id(items[0])
    full = rest("crm.item.get", {
        "entityTypeId": 1062,
        "id": first,
        "useOriginalUfNames": "Y",
    }).get("item")
    if (not isinstance(full, dict) or str(full.get("id")) != str(first)
            or str(full.get("entityTypeId")) != "1062"
            or str(full.get("categoryId")) != str(items[0].get("categoryId"))):
        raise ValueError("CUTOVER_TOP_ITEM_IDENTITY_UNVERIFIED")
    return max(7, first)

# Diagnostic labels are closed enums. Never print exceptions, API messages,
# webhook URLs, raw JSON, client identity or arbitrary server-controlled strings.
_SAFE_REASONS = frozenset({
    "REST_SCOPE_BLOCKED", "REST_HTTP_UNVERIFIED", "REST_RESPONSE_UNVERIFIED",
    "CUTOVER_INVENTORY_INVALID", "CUTOVER_ITEM_INVALID",
    "CUTOVER_CARD_ID_INVALID", "CUTOVER_CARD_ID_OUT_OF_BOUNDS",
    "CUTOVER_CATEGORY_MISMATCH", "CUTOVER_RESULT_NOT_DESCENDING",
    "CUTOVER_TOP_ITEM_IDENTITY_UNVERIFIED", "CUTOVER_BROAD_EMPTY_UNVERIFIED",
    "BITRIX24_WEBHOOK_MISSING_OR_BAD_FORMAT",
    "BITRIX24_WEBHOOK_MUST_BE_OFFICIAL_PORTAL",
})
_SAFE_API_ERRORS = frozenset({
    "ACCESS_DENIED", "INVALID_CREDENTIALS", "INVALID_TOKEN",
    "ERROR_METHOD_NOT_FOUND", "ERROR_CORE", "INVALID_REQUEST",
    "ERROR_ARGUMENT", "NO_AUTH_FOUND", "EXPIRED_TOKEN",
    "INSUFFICIENT_SCOPE", "ENTITY_TYPE_NOT_SUPPORTED",
    "ERROR_NOT_FOUND", "ERROR_PERMISSION_DENIED",
})


def _safe_reason(exc) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "HTTP_TIMEOUT"
    if isinstance(exc, httpx.TransportError):
        return "HTTP_TRANSPORT_FAILED"
    if isinstance(exc, ValueError) and len(exc.args) == 1:
        if type(exc.args[0]) is str and exc.args[0] in _SAFE_REASONS:
            return exc.args[0]
    return "UNCLASSIFIED_READ_ONLY_FAILURE"


def _observe_http(state, response):
    """Read response only for exact safe HTTP/API codes, no content in logs."""
    state["http"] = str(response.status_code) if 100 <= response.status_code <= 599 else "UNKNOWN"
    state["api"] = "NONE"
    if response.status_code != 200:
        return
    try:
        length = response.headers.get("content-length")
        if length and int(length) > 512000:
            return
        response.read()
        if len(response.content) > 512000:
            return
        data = response.json()
        if isinstance(data, dict) and "error" in data:
            code = data.get("error")
            state["api"] = code if type(code) is str and code in _SAFE_API_ERRORS else "OTHER_REDACTED"
    except (ValueError, TypeError, httpx.HTTPError):
        state["api"] = "UNREADABLE"


def main() -> int:
    webhook = os.environ.pop("SERVICEIT_BITRIX_WEBHOOK", "")
    if not webhook:
        print("SERVICEIT_CLOUD_CUTOVER=BLOCKED_MISSING_WEBHOOK")
        return 2
    stage = "WEBHOOK_VALIDATE"
    observed = {"http": "NOT_SENT", "api": "NONE"}
    try:
        with httpx.Client(
            trust_env=False, follow_redirects=False, timeout=20,
            event_hooks={"response": [lambda response: _observe_http(observed, response)]},
        ) as client:
            rest = Rest(webhook, client)
            def traced(method, params):
                nonlocal stage
                stage = ("LIST_BROAD" if "filter" not in params else "LIST") if method == "crm.item.list" else "GET_TOP"
                return rest(method, params)
            latest = latest_existing_card_id(traced)
        print(f"SERVICEIT_CLOUD_CUTOVER_MAX_ID={latest}")
        print("SERVICEIT_CLOUD_CUTOVER_CRM_WRITES=0")
        return 0
    except Exception as exc:
        print("SERVICEIT_CLOUD_CUTOVER=UNVERIFIED_READ_ONLY")
        shape = (":INDEX_" + str(exc.index) + ":KIND_" + exc.kind
                 if type(exc) is CutoverItemShapeError else "")
        print("SERVICEIT_CLOUD_CUTOVER_DIAGNOSTIC="
              + stage + ":" + _safe_reason(exc)
              + ":HTTP_" + observed["http"]
              + ":API_" + observed["api"] + shape)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
