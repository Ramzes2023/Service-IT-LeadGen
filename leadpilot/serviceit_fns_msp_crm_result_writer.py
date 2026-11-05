"""Automatic, bounded and fail-closed FNS-MSP receipt -> Bitrix card result.

Runs after the official daily snapshot collector. A monthly MSP membership
receipt proves only inclusion in that dated snapshot, NOT active EGRUL status,
permission to contact, or formal Lead Score. Existing AI fields and scores
are never changed. A dedicated approved single-line CRM field must exist.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re

import httpx

from .bitrix_1062_mapping import _is_valid_inn
from .bitrix_metadata_audit import validate_webhook
from .serviceit_bitrix_batch_worker import list_only
from .serviceit_bitrix_poll_fallback import after_activation, calibrate_known_card, discover
from .serviceit_bitrix_dispatch_worker import EncryptedEventFence, REPOSITORY, PORTAL
from .serviceit_enrichment_worker import PREFIX, fingerprint, normalized, validate_identity
from .serviceit_stage4_fns_dedupe_watch import read_item
from .serviceit_fns_msp_official_live import (
    OfficialMspFence, discover_official_url,
)

FIELD = "UF_CRM_13_SVCIT_FNS_MSP_RESULT"
ENABLED = "YES_1062_21_FNS_MSP_RESULT_WRITE"
MAX_PER_RUN = 20


class MspWriteBlocked(ValueError):
    pass


def need(ok, reason):
    if not ok:
        raise MspWriteBlocked(reason)


def receipt_label(receipt):
    """Human-readable membership only, capped to a single-line Bitrix field."""
    need(type(receipt) is dict
         and receipt.get("msp_category") in ("MICRO", "SMALL", "MEDIUM", None)
         and type(receipt.get("published")) is str
         and re.fullmatch(r"20\d{2}-\d{2}-\d{2}", receipt["published"])
         and receipt.get("fns_egrul_verified") is False
         and receipt.get("formal_score_authorized") is False,
         "MSP_RECEIPT_NOT_INDEPENDENT_CLEARANCE")
    category = {
        "MICRO": "микропредприятие", "SMALL": "малое предприятие",
        "MEDIUM": "среднее предприятие",
        None: "не найдено в данном срезе реестра",
    }[receipt["msp_category"]]
    return ("ФНС-МСП: " + category + "; срез " + receipt["published"]
            + "; только реестр МСП; ЕГРЮЛ/ЕГРИП и право контакта НЕ проверены")


class ResultFence(EncryptedEventFence):
    def url(self, card):
        import hmac
        from hashlib import sha256
        need(type(card) is int and 8 <= card <= 2_000_000_000
             and card not in (3, 7), "MSP_WRITER_ID_OUTSIDE_NEW_SCOPE")
        digest = hmac.new(
            self.path_key, f"official-msp-crm-result-v1:1062:21:{card}".encode(),
            sha256,
        ).hexdigest()
        return (f"https://api.github.com/repos/{REPOSITORY}/contents/"
                f".serviceit/official-msp-crm-result-once/{digest}.enc")


def rest(client, base, method, params):
    need(method in ("crm.item.fields", "crm.item.get", "crm.item.update"),
         "MSP_REST_METHOD_NOT_ALLOWED")
    need(type(params) is dict and params.get("entityTypeId") == 1062,
         "MSP_REST_SCOPE_INVALID")
    if method == "crm.item.fields":
        need(params == {"entityTypeId": 1062, "useOriginalUfNames": "Y"},
             "MSP_SCHEMA_PARAMS_INVALID")
    else:
        need(type(params.get("id")) is int
             and 8 <= params["id"] <= 2_000_000_000
             and params.get("useOriginalUfNames") == "Y",
             "MSP_ITEM_PARAMS_INVALID")
        if method == "crm.item.update":
            fields = params.get("fields")
            need(type(fields) is dict and set(fields) == {FIELD}
                 and type(fields[FIELD]) is str
                 and 1 <= len(fields[FIELD]) <= 240,
                 "MSP_CRM_WRITE_ALLOWLIST")
        else:
            need(set(params) == {"entityTypeId", "id", "useOriginalUfNames"},
                 "MSP_READ_SCOPE_INVALID")
    try:
        response = client.post(base + "/" + method + ".json",
                               json=params, timeout=25, follow_redirects=False)
        need(response.status_code == 200 and 0 < len(response.content) <= 400000,
             "MSP_HTTP_UNVERIFIED")
        body = response.json()
        need(type(body) is dict and "error" not in body
             and type(body.get("result")) is dict, "MSP_REST_BODY_INVALID")
        return body["result"]
    except (httpx.HTTPError, ValueError, TypeError) as error:
        raise MspWriteBlocked("MSP_REST_UNCERTAIN") from error


def result_field_verified(client, base):
    fields = rest(client, base, "crm.item.fields", {
        "entityTypeId": 1062, "useOriginalUfNames": "Y",
    }).get("fields")
    need(type(fields) is dict, "MSP_FIELDS_NOT_READABLE")
    field = fields.get(FIELD)
    need(type(field) is dict and field.get("type") == "string"
         and field.get("isReadOnly") is False
         and field.get("isMultiple") in (False, None),
         "MSP_APPROVED_CRM_FIELD_NOT_AVAILABLE")
    return True


def write_one(client, env, base, fence, official_fence, card):
    before = read_item(client, base, card)
    inn = before.get(PREFIX + "COMPANY_INN")
    if type(inn) is not str or not _is_valid_inn(inn):
        return "HOLD_NO_VALID_INN"
    receipt = official_fence.read_checked_receipt(card, inn)
    if receipt is None:
        return "HOLD_AWAITING_OFFICIAL_SNAPSHOT"
    value = receipt_label(receipt)
    need(FIELD in before, "MSP_FIELD_NOT_IN_CRM_SNAPSHOT")
    if before[FIELD] == value:
        return "ALREADY_EXACT_RESULT"
    if before[FIELD] not in (None, ""):
        return "EXISTING_MSP_FIELD_PROTECTED"
    result_field_verified(client, base)
    latest = read_item(client, base, card)
    need(fingerprint(latest) == fingerprint(before),
         "MSP_CARD_CHANGED_BEFORE_WRITE")
    reservation = fence.reserve(card)
    if reservation != "RESERVED":
        return "ALREADY_RESERVED_NO_RETRY"
    # Durable receipt BEFORE sending REST mutation; ambiguous responses never retry.
    fence.receipt = {"expected": {"field": FIELD, "value": value}}
    need(fence.finalize(card, "UNCERTAIN"),
         "MSP_PREWRITE_CHECKPOINT_UNVERIFIED")
    try:
        rest(client, base, "crm.item.update", {
            "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y",
            "fields": {FIELD: value},
        })
        response = rest(client, base, "crm.item.get", {
            "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y",
        })
        need(type(response.get("item")) is dict, "MSP_READBACK_MISSING")
        after = normalized(response["item"])
        validate_identity(after, card)
        need(after.get(FIELD) == value, "MSP_READBACK_FIELD_MISMATCH")
        ignored = {FIELD, "updatedTime", "updatedBy",
                   "lastActivityTime", "lastActivityBy"}
        need(all(after.get(k) == v for k, v in latest.items()
                 if k not in ignored), "MSP_OTHER_CARD_DATA_CHANGED")
    except Exception:
        return "UNCERTAIN_DO_NOT_RETRY"
    # Ledger was finalized UNCERTAIN before mutation; do not overwrite it
    # through a stale file SHA. CRM readback is the success evidence.
    return "MSP_CRM_RESULT_EXACT_READBACK"


def run(client, env, *, now=None):
    now = now or datetime.now(timezone.utc)
    need(env.get("GITHUB_REPOSITORY") == REPOSITORY
         and env.get("GITHUB_REF") == "refs/heads/main"
         and env.get("GITHUB_EVENT_NAME") == "schedule"
         and env.get("SERVICEIT_FNS_MSP_CRM_RESULT_ENABLED") == ENABLED,
         "MSP_WRITE_OWNER_GATE_MISSING")
    base = validate_webhook(env.get("SERVICEIT_BITRIX_WEBHOOK", ""))
    need(base.startswith(f"https://{PORTAL}/rest/"),
         "MSP_WRONG_PORTAL")
    source = lambda method, args: list_only(client, base, method, args)
    calibrate_known_card(source)
    cards = discover(source, after_activation(env, now), now)
    need(len(cards) <= 200, "MSP_DISCOVERY_OVER_BUDGET")
    if not cards:
        return {"status": "NO_NEW_CARDS", "new_cards": 0, "verified_writes": 0}
    result_field_verified(client, base)
    _, publication = discover_official_url(client, today=now.date())
    official_fence = OfficialMspFence(client, env.get("GITHUB_TOKEN", ""),
                                     env.get("SERVICEIT_GH_LEDGER_KEY", ""),
                                     publication)
    fence = ResultFence(client, env.get("GITHUB_TOKEN", ""),
                        env.get("SERVICEIT_GH_LEDGER_KEY", ""))
    report = {"status": "MSP_CRM_RESULT_RUN", "new_cards": len(cards),
              "verified_writes": 0, "hold": 0, "uncertain": 0}
    for card in cards[:MAX_PER_RUN]:
        try:
            outcome = write_one(client, env, base, fence, official_fence, card)
        except Exception:
            outcome = "HOLD_ERROR_OR_INCOMPLETE_EVIDENCE"
        report["verified_writes"] += outcome == "MSP_CRM_RESULT_EXACT_READBACK"
        report["uncertain"] += outcome == "UNCERTAIN_DO_NOT_RETRY"
        report["hold"] += outcome not in ("MSP_CRM_RESULT_EXACT_READBACK",
                                          "ALREADY_EXACT_RESULT")
        if report["uncertain"]:
            break
    return report


def main():
    try:
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            output = run(client, dict(os.environ))
        print(json.dumps(output, ensure_ascii=False, sort_keys=True))
        return 0 if not output.get("uncertain") else 2
    except Exception:
        print(json.dumps({"status": "FNS_MSP_CRM_WRITE_HOLD",
                          "verified_writes": 0,
                          "reason": "MISSING_APPROVAL_FIELD_OR_EVIDENCE"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
