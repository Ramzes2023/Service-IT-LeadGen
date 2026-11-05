"""Official FNS-MSP receipt delivery to a NEW Bitrix smart-process timeline.

Fallback when no approved dedicated CRM field exists; no CRM schema changes.
All mutations are exact allowlisted timeline comments with a one-use encrypted
fence and conservative readback. Never qualifies a lead or authorizes contact.
"""
from __future__ import annotations

import hmac
from hashlib import sha256
import httpx

from .serviceit_bitrix_dispatch_worker import EncryptedEventFence, REPOSITORY
from .serviceit_fns_msp_crm_result_writer import receipt_label

ENTITY_TYPE = "DYNAMIC_1062"
PREFIX = "[SERVICEIT_FNS_MSP_OFFICIAL_V1]"
MAX_COMMENT_PAGES = 6


class TimelineBlocked(ValueError):
    pass


def require(ok, why):
    if not ok:
        raise TimelineBlocked(why)


class TimelineFence(EncryptedEventFence):
    def url(self, card):
        require(type(card) is int and 8 <= card <= 2_000_000_000
                and card not in (3, 7), "TIMELINE_ID_OUTSIDE_NEW_SCOPE")
        digest = hmac.new(self.path_key,
                          f"official-fns-msp-timeline-v1:1062:21:{card}".encode(),
                          sha256).hexdigest()
        return (f"https://api.github.com/repos/{REPOSITORY}/contents/"
                f".serviceit/official-msp-timeline-once/{digest}.enc")


def timeline_rest(client, base, method, params):
    require(method in ("crm.timeline.comment.list", "crm.timeline.comment.add"),
            "TIMELINE_METHOD_DENIED")
    require(type(params) is dict, "TIMELINE_PARAMS_INVALID")
    if method.endswith(".list"):
        require(set(params) == {"filter", "order", "start"}
                and type(params["filter"]) is dict
                and set(params["filter"]) == {"ENTITY_ID", "ENTITY_TYPE"}
                and params["order"] == {"ID": "ASC"}
                and type(params["start"]) is int
                and 0 <= params["start"] <= 50 * MAX_COMMENT_PAGES,
                "TIMELINE_LIST_ARGS_INVALID")
        row = params["filter"]
    else:
        require(set(params) == {"fields"}
                and type(params["fields"]) is dict
                and set(params["fields"]) == {"ENTITY_ID", "ENTITY_TYPE", "COMMENT"}
                and type(params["fields"]["COMMENT"]) is str
                and 20 <= len(params["fields"]["COMMENT"]) <= 512,
                "TIMELINE_ADD_ARGS_INVALID")
        row = params["fields"]
    require(type(row.get("ENTITY_ID")) is int
            and 8 <= row["ENTITY_ID"] <= 2_000_000_000
            and row["ENTITY_ID"] not in (3, 7)
            and row.get("ENTITY_TYPE") == ENTITY_TYPE,
            "TIMELINE_WRONG_SCOPE")
    if method.endswith(".add"):
        require(row["COMMENT"].startswith(PREFIX)
                and "ЕГРЮЛ/ЕГРИП" in row["COMMENT"]
                and "НЕ проверены" in row["COMMENT"],
                "TIMELINE_UNTRUSTED_MESSAGE")
    try:
        response = client.post(base + "/" + method + ".json", json=params,
                               timeout=25, follow_redirects=False)
        require(response.status_code == 200
                and 0 < len(response.content) <= 400_000,
                "TIMELINE_HTTP_UNVERIFIED")
        body = response.json()
        require(type(body) is dict and "error" not in body,
                "TIMELINE_RESPONSE_ERROR")
        if method.endswith(".list"):
            require(type(body.get("result")) is list
                    and len(body["result"]) <= 50, "TIMELINE_LIST_INVALID")
            return body
        require(str(body.get("result", "")).isdigit()
                and int(body["result"]) > 0, "TIMELINE_ADD_NOT_ACKNOWLEDGED")
        return body
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as error:
        raise TimelineBlocked("TIMELINE_NETWORK_OR_FORMAT_UNVERIFIED") from error


def read_comments(client, base, card):
    """All visible comments up to a strict finite limit; no inferred global access."""
    items = []
    seen = set()
    for page in range(MAX_COMMENT_PAGES):
        body = timeline_rest(client, base, "crm.timeline.comment.list", {
            "filter": {"ENTITY_ID": card, "ENTITY_TYPE": ENTITY_TYPE},
            "order": {"ID": "ASC"}, "start": page * 50,
        })
        rows = body["result"]
        for row in rows:
            require(type(row) is dict and str(row.get("ID", "")).isdigit()
                    and str(row.get("ENTITY_ID")) == str(card)
                    and str(row.get("ENTITY_TYPE", "")).lower() == ENTITY_TYPE.lower()
                    and type(row.get("COMMENT")) is str,
                    "TIMELINE_COMMENT_ROW_IDENTITY_INVALID")
            key = int(row["ID"])
            require(key not in seen, "TIMELINE_DUPLICATED_PAGE")
            seen.add(key)
            items.append(row)
        next_page = body.get("next")
        if next_page is None:
            total = body.get("total")
            require(total is None or
                    (type(total) is int and total == len(items)),
                    "TIMELINE_TOTAL_NOT_EXHAUSTED")
            return items
        require(type(next_page) is int and next_page == (page + 1) * 50
                and len(rows) == 50, "TIMELINE_PAGINATION_UNVERIFIED")
    raise TimelineBlocked("TIMELINE_OVER_BUDGET")


def add_once(client, base, fence, card, receipt):
    comment = PREFIX + " " + receipt_label(receipt)
    before = read_comments(client, base, card)
    existing = [x for x in before if x["COMMENT"].startswith(PREFIX)]
    if existing:
        return ("TIMELINE_ALREADY_EXACT" if len(existing) == 1
                and existing[0]["COMMENT"] == comment
                else "TIMELINE_EXISTING_RESULT_REVIEW")
    if fence.reserve(card) != "RESERVED":
        return "TIMELINE_ALREADY_RESERVED_NO_RETRY"
    fence.receipt = {"expected": {"comment_sha256": sha256(comment.encode()).hexdigest()}}
    require(fence.finalize(card, "UNCERTAIN"),
            "TIMELINE_PREWRITE_CHECKPOINT_FAILED")
    try:
        ack = timeline_rest(client, base, "crm.timeline.comment.add", {
            "fields": {"ENTITY_ID": card, "ENTITY_TYPE": ENTITY_TYPE,
                       "COMMENT": comment},
        })
        created_id = int(ack["result"])
        after = read_comments(client, base, card)
        exact = [x for x in after
                 if x["COMMENT"] == comment and int(x["ID"]) == created_id]
        require(len(exact) == 1
                and len([x for x in after if x["COMMENT"].startswith(PREFIX)]) == 1,
                "TIMELINE_READBACK_NOT_EXACT")
    except Exception:
        return "TIMELINE_WRITE_UNCERTAIN_NO_RETRY"
    return ("TIMELINE_RESULT_EXACT_READBACK"
            if fence.finalize(card, "VERIFIED")
            else "TIMELINE_READBACK_OK_LEDGER_UNCERTAIN")
