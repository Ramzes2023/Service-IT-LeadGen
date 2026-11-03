"""Encrypted, exactly-once-ish post-create handoff of the CRM ID.

Only invoked AFTER crm.item.add + exact crm.item.get readback by the existing
owner-approved B2B source writer. No crm.item.list, CRM update, GigaChat,
contacts, customer text, source URLs or Lead Score. The public GitHub branch
stores only Fernet-encrypted data and an opaque SHA256 path, using a PUT
precondition (no overwrite and no blind retries). This is an ID delivery
receipt, not evidence that qualification/AI is complete.
"""
from __future__ import annotations

import base64
from hashlib import sha256
import json
import re

from cryptography.fernet import Fernet
import httpx

REPO = "Ramzes2023/Service-IT-LeadGen"
BRANCH = "serviceit-ai-encrypted-ledger"
PREFIX = ".serviceit/handoffs"
EXPECTED_STATES = frozenset((
    "NOT_RUN_SOURCE_UNVERIFIED", "HYPOTHESIS_UNVERIFIED_SOURCE_UNVERIFIED",
))


class DirectHandoffBlocked(ValueError):
    pass


def receipt(card_id, source_id, *, ai_status):
    if (type(card_id) is not int or card_id in (3, 7)
            or not 8 <= card_id <= 2_000_000_000):
        raise DirectHandoffBlocked("HANDOFF_CARD_ID_INVALID")
    if (not isinstance(source_id, str)
            or not re.fullmatch(r"B2B-CENTER-[0-9]{5,12}", source_id)):
        raise DirectHandoffBlocked("HANDOFF_SOURCE_ID_INVALID")
    if ai_status not in EXPECTED_STATES:
        raise DirectHandoffBlocked("HANDOFF_AI_STATUS_UNVERIFIED")
    return {
        "schema": "serviceit-direct-card-id-v1",
        "entity_type_id": 1062,
        "category_id": 21,
        "card_id": card_id,
        "source_id": source_id,
        "ai_status": ai_status,
        "qualified_lead": False,
        "score_verified": False,
        "creation_readback": "EXACT",
    }


def _url(source_id):
    # Never use the source ID, card ID, text or buyer name in a public
    # filename/commit message. This path is unique for each source key.
    fingerprint = sha256(source_id.encode("ascii")).hexdigest()
    return ("https://api.github.com/repos/" + REPO
            + "/contents/" + PREFIX + "/" + fingerprint + ".enc")


def post_encrypted_handoff(client, token, secret_key, event):
    if (not isinstance(client, httpx.Client)
            or not isinstance(token, str) or len(token) < 12):
        raise DirectHandoffBlocked("HANDOFF_AUTH_MISSING")
    valid = receipt(event.get("card_id"), event.get("source_id"),
                    ai_status=event.get("ai_status")) if isinstance(event, dict) else None
    if valid != event:
        raise DirectHandoffBlocked("HANDOFF_UNEXPECTED_DATA")
    try:
        cipher = Fernet(secret_key.encode("ascii"))
    except (ValueError, TypeError, UnicodeError) as exc:
        raise DirectHandoffBlocked("HANDOFF_FERNET_KEY_INVALID") from None
    path = _url(valid["source_id"])
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    # GET 200 means *a handoff already exists*. Never overwrite/replay even
    # if we cannot currently decrypt that previous record.
    try:
        previous = client.get(path, params={"ref": BRANCH}, headers=headers,
                              timeout=20, follow_redirects=False)
        if previous.status_code == 200:
            return "ALREADY_RECORDED_NO_RETRY"
        if previous.status_code != 404:
            raise DirectHandoffBlocked("HANDOFF_REMOTE_STATE_UNVERIFIED")
        data = json.dumps(valid, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ciphertext = cipher.encrypt(data)
        result = client.put(path, headers=headers, timeout=25,
                            follow_redirects=False, json={
                                "branch": BRANCH,
                                "message": "checkpoint(serviceit): encrypted direct card handoff",
                                "content": base64.b64encode(ciphertext).decode("ascii"),
                            })
        if result.status_code != 201:
            raise DirectHandoffBlocked("HANDOFF_PERSISTENCE_UNCONFIRMED")
        response = result.json()
        if (not isinstance(response, dict)
                or not isinstance(response.get("content"), dict)
                or not isinstance(response["content"].get("sha"), str)):
            raise DirectHandoffBlocked("HANDOFF_ACK_UNVERIFIED")
        return "ENCRYPTED_ID_DELIVERED"
    except httpx.HTTPError:
        raise DirectHandoffBlocked("HANDOFF_TRANSPORT_UNCERTAIN") from None


def receive_encrypted_handoff(client, token, secret_key, source_id):
    """Read a known source's receipt. Never list/search through Bitrix."""
    # Validate source ID independent of card ID.
    if not isinstance(source_id, str) or not re.fullmatch(
            r"B2B-CENTER-[0-9]{5,12}", source_id):
        raise DirectHandoffBlocked("HANDOFF_SOURCE_ID_INVALID")
    if not isinstance(token, str) or len(token) < 12:
        raise DirectHandoffBlocked("HANDOFF_AUTH_MISSING")
    try:
        cipher = Fernet(secret_key.encode("ascii"))
        r = client.get(_url(source_id),
                       headers={"Authorization": "Bearer " + token,
                                "Accept": "application/vnd.github+json",
                                "X-GitHub-Api-Version": "2022-11-28"},
                       params={"ref": BRANCH}, timeout=20, follow_redirects=False)
        if r.status_code != 200 or len(r.content) > 12000:
            raise DirectHandoffBlocked("HANDOFF_NOT_VERIFIED")
        data = r.json()
        if (not isinstance(data, dict) or data.get("type") != "file"
                or data.get("encoding") != "base64"
                or not isinstance(data.get("content"), str)):
            raise DirectHandoffBlocked("HANDOFF_NOT_VERIFIED")
        raw = base64.b64decode(data["content"], validate=False)
        if not raw or len(raw) > 4096:
            raise DirectHandoffBlocked("HANDOFF_NOT_VERIFIED")
        event = json.loads(cipher.decrypt(raw))
        if event != receipt(event.get("card_id"), event.get("source_id"),
                            ai_status=event.get("ai_status")) or event["source_id"] != source_id:
            raise DirectHandoffBlocked("HANDOFF_RECORD_IDENTITY_MISMATCH")
        return event
    except DirectHandoffBlocked:
        raise
    except Exception:
        raise DirectHandoffBlocked("HANDOFF_DECRYPT_OR_SHAPE_UNVERIFIED") from None


def verify_direct_target(rest, event):
    """One exact CRM get by producer ID. No lists, no model, no CRM write.

    This validates receipt-to-card identity only. Corporate company/source/
    contact checks are STILL required separately for any Lead Score.
    """
    if not isinstance(event, dict):
        raise DirectHandoffBlocked("HANDOFF_UNEXPECTED_DATA")
    known = receipt(event.get("card_id"), event.get("source_id"),
                    ai_status=event.get("ai_status"))
    if known != event:
        raise DirectHandoffBlocked("HANDOFF_UNEXPECTED_DATA")
    try:
        response = rest("crm.item.get", {
            "entityTypeId": 1062, "id": known["card_id"],
            "useOriginalUfNames": "Y",
        })
        item = response["item"]
    except Exception:
        raise DirectHandoffBlocked("HANDOFF_TARGET_READ_UNVERIFIED") from None
    if not isinstance(item, dict):
        raise DirectHandoffBlocked("HANDOFF_TARGET_READ_UNVERIFIED")
    if (str(item.get("id")) != str(known["card_id"])
            or str(item.get("entityTypeId")) != "1062"
            or str(item.get("categoryId")) != "21"):
        raise DirectHandoffBlocked("HANDOFF_TARGET_SCOPE_CONFLICT")
    from .serviceit_enrichment_worker import normalized
    try:
        fields = normalized(item)
    except Exception:
        raise DirectHandoffBlocked("HANDOFF_TARGET_FIELDS_UNVERIFIED") from None
    if (fields.get("UF_CRM_13_SVCIT_SIGNAL_ID") != known["source_id"]
            or fields.get("UF_CRM_13_SVCIT_AI_STATUS") != known["ai_status"]):
        raise DirectHandoffBlocked("HANDOFF_TARGET_SOURCE_OR_STATUS_MISMATCH")
    return {
        "status": "DIRECT_ID_PRELIMINARY_READBACK_ONLY",
        "crm_reads": 1, "crm_writes": 0,
        "gigachat_calls": 0, "lead_score_calculated": False,
    }
