"""One-shot opt-in HOLD tender card delivery, NEVER a qualified lead.

Only the existing corporate smart-process 1062/21, after read-only schema
verification, exact source-ID check, explicit user approval and durable fence.
Do not schedule this API. No contacts, client database reads or outreach.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from .b2b_bitrix_readiness import ReadOnlyBitrix, PreflightBlocked, preflight

ID_FIELD = "UF_CRM_13_SVCIT_SIGNAL_ID"
KIND_FIELD = "UF_CRM_13_SVCIT_SIGNAL_KIND"
SOURCE_FIELD = "UF_CRM_13_SVCIT_SIGNAL_SOURCE"
URL_FIELD = "UF_CRM_13_SVCIT_SIGNAL_URL"
STOP_FIELD = "UF_CRM_13_SVCIT_STOP_REASON"
KEY_FIELD = "UF_CRM_13_SVCIT_IDEMPOTENCY_KEY"
ALLOW_UF = frozenset({
    ID_FIELD, KIND_FIELD, SOURCE_FIELD, URL_FIELD, STOP_FIELD,
    "UF_CRM_13_SVCIT_SUGGESTED_SERVICE",
    "UF_CRM_13_SVCIT_COMPANY_NAME",
    "UF_CRM_13_SVCIT_EVIDENCE_STATUS",
    "UF_CRM_13_SVCIT_REVIEW_DECISION",
    KEY_FIELD,
})
APPROVAL = "AUTHORIZE ONE B2B TENDER HOLD CARD"
PATTERN = re.compile(r"B2B-CENTER-(\d{5,12})\Z")


class HoldWriteBlocked(ValueError):
    pass


def _must(value, code):
    if not value:
        raise HoldWriteBlocked(code)


def validated_hold(preview, *, tender_id):
    """Strict untrusted observation validation. Does not resolve buyer."""
    _must(isinstance(preview, dict)
          and preview.get("status") == "B2B_EMAIL_TENDER_RESEARCH_PREVIEW_ONLY"
          and preview.get("crm_writes") == 0
          and preview.get("qualified_real_leads") == 0, "NOT_A_HOLD_PREVIEW")
    entries = preview.get("cards_not_sent")
    _must(isinstance(entries, list) and len(entries) <= 80, "CARDS_PREVIEW_INVALID")
    _must(isinstance(tender_id, str) and re.fullmatch(r"\d{5,12}", tender_id),
          "SOURCE_TENDER_ID_INVALID")
    matches = [x for x in entries if isinstance(x, dict)
               and x.get("source_tender_id") == tender_id]
    _must(len(matches) == 1, "SOURCE_NOT_UNIQUE_IN_PREVIEW")
    row = matches[0]
    _must(row.get("action") == "PREVIEW_ONLY_NOT_EXECUTABLE"
          and row.get("entityTypeId") == 1062
          and row.get("data_quality") == "HOLD_ORIGINAL_BUYER_INN_FNS_DATE_STATUS_STOPLIST",
          "RESEARCH_HOLD_NOT_ESTABLISHED")
    fields = row.get("fields_not_sent")
    _must(isinstance(fields, dict), "FIELDS_MISSING")
    expected = {
        "title", "categoryId", "stageId",
        *[x for x in ALLOW_UF if x != KEY_FIELD],
    }
    _must(set(fields) == expected and fields.get("categoryId") == 21
          and fields.get("stageId") == "DT1062_21:NEW",
          "REVIEW_FIELDS_NOT_CANONICAL")
    _must(fields.get(ID_FIELD) == "B2B-CENTER-" + tender_id
          and PATTERN.fullmatch(fields[ID_FIELD]) is not None,
          "SOURCE_ID_MISMATCH")
    _must(fields.get(KIND_FIELD) == "tender-opportunity"
          and fields.get(SOURCE_FIELD) == "B2B-Center email subscription"
          and fields.get("title") == "ВОЗМОЖНОСТЬ · B2B-Center №" + tender_id,
          "TENDER_OPPORTUNITY_TYPE_MISMATCH")
    _must(fields.get(STOP_FIELD) == "Не является квалифицированным B2B-лидом"
          and fields.get("UF_CRM_13_SVCIT_EVIDENCE_STATUS") ==
              "HOLD: дата публикации, статус, ИНН/ФНС и клиент не проверены"
          and fields.get("UF_CRM_13_SVCIT_REVIEW_DECISION") ==
              "Ожидает независимой проверки менеджером",
          "MISSING_HOLD_STOP")
    src = fields.get(URL_FIELD)
    _must(isinstance(src, str) and 20 <= len(src) <= 400
          and "?" not in src and "#" not in src
          and src.startswith("https://www.b2b-center.ru/")
          and src.rstrip("/").endswith("/tender-" + tender_id),
          "UNTRUSTED_SOURCE_URL")
    for name, value in fields.items():
        if name in ("categoryId", "stageId", "title", URL_FIELD):
            continue
        _must(isinstance(value, str) and len(value) <= 400
              and not re.search(r"[\x00-\x1f\x7f]", value),
              "UNSAFE_FIELD_VALUE")
    _must(len(fields["UF_CRM_13_SVCIT_COMPANY_NAME"]) <= 120
          and not re.search(r"@|https?://|tel:|mailto:", fields["UF_CRM_13_SVCIT_COMPANY_NAME"], re.I),
          "BUYER_OR_CONTACT_NOT_VERIFIED")
    _must(isinstance(fields.get("UF_CRM_13_SVCIT_SUGGESTED_SERVICE"), str)
          and fields["UF_CRM_13_SVCIT_SUGGESTED_SERVICE"],
          "NOT_AN_IT_REVIEW_CANDIDATE")
    fingerprint = sha256(("B2B-CENTER\0" + tender_id).encode()).hexdigest()[:32]
    return {
        "entityTypeId": 1062, "useOriginalUfNames": "Y",
        "fields": {**fields, KEY_FIELD: "B2B-HOLD-" + fingerprint},
    }


def _metadata_and_exact_remote(rest: ReadOnlyBitrix, *, signal_id):
    preflight(rest)
    # This module is intentionally NOT an automatic worker. It requires a
    # separate exact-ID list lookup; a partial, inaccessible or paged list
    # prevents any new write.
    try:
        result = rest.client.post(rest.base + "/crm.item.list.json",
            json={"entityTypeId": 1062, "useOriginalUfNames": "Y",
                  "filter": {"categoryId": 21, ID_FIELD: signal_id},
                  "select": ["id", "entityTypeId", "categoryId", ID_FIELD],
                  "start": 0}, follow_redirects=False, timeout=12)
        _must(result.status_code == 200 and len(result.content) <= 256000,
              "EXACT_DUPLICATE_READ_DENIED")
        payload = result.json()
    except Exception:
        raise HoldWriteBlocked("EXACT_DUPLICATE_READ_FAILED") from None
    _must(isinstance(payload, dict) and "error" not in payload
          and "next" not in payload and isinstance(payload.get("result"), dict),
          "EXACT_DUPLICATE_READ_UNTRUSTED")
    val = payload["result"]
    items = val.get("items")
    _must(isinstance(items, list) and len(items) < 50 and "next" not in val,
          "EXACT_DUPLICATE_READ_PARTIAL")
    if "total" in val:
        _must(type(val["total"]) is int and val["total"] == len(items),
              "EXACT_DUPLICATE_COUNT_MISMATCH")
    if "total" in payload:
        _must(type(payload["total"]) is int and payload["total"] == len(items),
              "EXACT_DUPLICATE_COUNT_MISMATCH")
    for i in items:
        _must(isinstance(i, dict) and
              i.get(ID_FIELD) == signal_id and i.get("categoryId") == 21
              and i.get("entityTypeId") == 1062 and
              type(i.get("id")) is int and i["id"] > 0,
              "EXACT_DUPLICATE_ITEM_OUT_OF_SCOPE")
    return items


def deliver_one_hold(preview, *, tender_id, rest: ReadOnlyBitrix,
                     approval, original_verified: bool,
                     fence_dir: Path) -> dict:
    """At most one guarded crm.item.add; no retry after possible POST."""
    params = validated_hold(preview, tender_id=tender_id)
    _must(approval == APPROVAL and original_verified is True,
          "EXPLICIT_HOLD_WRITE_APPROVAL_MISSING")
    _must(isinstance(rest, ReadOnlyBitrix), "READ_ONLY_PREFLIGHT_REQUIRED")
    signal = params["fields"][ID_FIELD]
    existing = _metadata_and_exact_remote(rest, signal_id=signal)
    if existing:
        return {"status": "EXISTING_TENDER_REVIEW_NO_POST",
                "crm_item_add_calls": 0, "qualified_real_leads": 0}
    _must(isinstance(fence_dir, Path), "DURABLE_LOCAL_FENCE_REQUIRED")
    # Must be an operator-chosen isolated local folder, not the repo root.
    _must(fence_dir.is_dir() and fence_dir.resolve() != Path(".").resolve(),
          "EXISTING_OPERATOR_STATE_DIRECTORY_REQUIRED")
    key = params["fields"][KEY_FIELD]
    claim_path = fence_dir / (key + ".json")
    # Durable exclusive file before POST; any interruption is an UNCERTAIN
    # operation requiring manual read-only reconciliation, never replay.
    try:
        with claim_path.open("x", encoding="utf-8") as file:
            json.dump({"operation": key, "state": "POST_UNCERTAIN",
                       "tender_id": tender_id}, file, ensure_ascii=False)
            file.flush()
            import os
            os.fsync(file.fileno())
    except FileExistsError:
        raise HoldWriteBlocked("OPERATION_ALREADY_CLAIMED_DO_NOT_RETRY") from None
    try:
        response = rest.client.post(rest.base + "/crm.item.add.json", json=params,
                                    follow_redirects=False, timeout=12)
        _must(response.status_code == 200 and len(response.content) <= 256000,
              "POST_RESULT_UNCERTAIN_MANUAL_RECONCILIATION")
        body = response.json()
        _must(isinstance(body, dict) and not body.get("error")
              and isinstance(body.get("result"), dict)
              and isinstance(body["result"].get("item"), dict)
              and type(body["result"]["item"].get("id")) is int,
              "POST_RESPONSE_UNCERTAIN_MANUAL_RECONCILIATION")
        candidate = body["result"]["item"]["id"]
        read = rest.client.post(rest.base + "/crm.item.get.json",
                               json={"entityTypeId": 1062, "id": candidate,
                                     "useOriginalUfNames": "Y"},
                               follow_redirects=False, timeout=12)
        _must(read.status_code == 200 and len(read.content) <= 256000,
              "READBACK_UNCERTAIN_DO_NOT_RETRY")
        data = read.json()
        item = data.get("result", {}).get("item") if isinstance(data, dict) else None
        _must(isinstance(item, dict) and item.get("id") == candidate
              and item.get("categoryId") == 21 and
              item.get("entityTypeId") == 1062 and
              item.get("stageId") == "DT1062_21:NEW" and
              all(item.get(k) == v for k, v in params["fields"].items()),
              "READBACK_NOT_EXACT_DO_NOT_RETRY")
        # No mark-verified write is needed in CRM; local file is best-effort
        # and must never be a reason to replay.
        with claim_path.open("w", encoding="utf-8") as file:
            json.dump({"operation": key, "state": "VERIFIED",
                       "tender_id": tender_id, "item_id": candidate}, file)
        return {"status": "ONE_TENDER_HOLD_WRITTEN_AND_READBACK_VERIFIED",
                "item_id": candidate, "crm_item_add_calls": 1,
                "qualified_real_leads": 0, "outreach": "NO"}
    except Exception:
        # The irreversible fence remains in POST_UNCERTAIN on every error.
        raise HoldWriteBlocked("POST_OR_READBACK_UNCERTAIN_DO_NOT_RETRY") from None
