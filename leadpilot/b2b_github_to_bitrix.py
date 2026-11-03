"""GitHub issue #66 B2B-Center notice -> guarded Bitrix24 1062/21 HOLD.
No qualified leads are promoted from unverified vendor email. All live writes
require a repository opt-in, a separate Bitrix webhook secret and a bot ledger.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import httpx

from .b2b_email_opportunity_bridge import classify_services, clean_tender_url
from .b2b_bitrix_readiness import ReadOnlyBitrix, preflight
from .bitrix_metadata_audit import validate_webhook
from .serviceit_b2b_hold_ai_fields import AI_FIELDS, hold_analysis_fields
from .serviceit_manager_readiness import MANAGER_NEXT, preliminary_manager_action

REPO = "Ramzes2023/Service-IT-LeadGen"
OWNER = "Ramzes2023"
ISSUE = 66
BEGIN = "SERVICEIT_B2B_NOTICE_V1\n"
LOCK_PREFIX = "SERVICEIT_B2B_CRM_LOCK_V1 "
SCHEMA = "serviceit-b2b-notice-v1"
HOLD_STAGE = "DT1062_21:NEW"
SIGNAL = "UF_CRM_13_SVCIT_SIGNAL_ID"
KEY = "UF_CRM_13_SVCIT_IDEMPOTENCY_KEY"
FIELDS = frozenset({
    "UF_CRM_13_SVCIT_SIGNAL_KIND", SIGNAL,
    "UF_CRM_13_SVCIT_SIGNAL_SOURCE", "UF_CRM_13_SVCIT_SIGNAL_URL",
    "UF_CRM_13_SVCIT_SUGGESTED_SERVICE", "UF_CRM_13_SVCIT_COMPANY_NAME",
    "UF_CRM_13_SVCIT_EVIDENCE_STATUS", "UF_CRM_13_SVCIT_REVIEW_DECISION",
    "UF_CRM_13_SVCIT_STOP_REASON", KEY, MANAGER_NEXT, *AI_FIELDS,
})


class BridgeBlocked(ValueError):
    pass


def must(ok, code):
    if not ok:
        raise BridgeBlocked(code)


def safe_text(value, maximum):
    return (isinstance(value, str) and 1 <= len(value) <= maximum
            and not re.search(r"[\x00-\x1f\x7f]|https?://|mailto:|@", value, re.I))


def notice_from_event(event):
    must(isinstance(event, dict), "INVALID_EVENT")
    repo = event.get("repository") or {}
    issue = event.get("issue") or {}
    sender = event.get("sender") or {}
    comment = event.get("comment") or {}
    must(isinstance(repo, dict) and isinstance(issue, dict)
         and isinstance(sender, dict) and isinstance(comment, dict)
         and isinstance(comment.get("user"), dict)
         and event.get("action") == "created"
         and repo.get("full_name") == REPO
         and issue.get("number") == ISSUE and not issue.get("pull_request")
         and sender.get("login") == OWNER
         and comment["user"].get("login") == OWNER, "UNTRUSTED_GITHUB_EVENT")
    body = comment.get("body")
    must(isinstance(body, str) and body.startswith(BEGIN) and len(body) <= 2500,
         "UNTRUSTED_NOTICE_COMMENT")
    try:
        notice = json.loads(body[len(BEGIN):])
    except (ValueError, TypeError):
        raise BridgeBlocked("INVALID_NOTICE_JSON") from None
    names = {"schema", "source", "email_message_id", "tender_id", "source_url",
             "subject", "organizer", "buyer_inn", "fns_msp", "region",
             "original_publication_verified", "contact_approved", "qualified_lead"}
    must(isinstance(notice, dict) and set(notice) == names
         and notice["schema"] == SCHEMA and notice["source"] == "B2B-Center",
         "NOTICE_SCHEMA_INVALID")
    tid = notice["tender_id"]
    must(isinstance(tid, str) and re.fullmatch(r"\d{5,12}", tid),
         "TENDER_NUMBER_INVALID")
    ref = notice.get("email_message_id")
    must(isinstance(ref, str) and re.fullmatch(r"[0-9a-f]{16,24}", ref),
         "ORIGIN_EMAIL_REF_INVALID")
    must(clean_tender_url(notice.get("source_url")) ==
         (notice["source_url"], tid), "OFFICIAL_CANONICAL_URL_REQUIRED")
    must(safe_text(notice.get("subject"), 240)
         and safe_text(notice.get("organizer"), 120),
         "UNSAFE_TITLE_OR_ORGANIZER")
    kinds = classify_services(notice["subject"])
    must(bool(kinds), "NOT_A_SERVICEIT_PROCUREMENT")
    must(notice["buyer_inn"] is None and notice["fns_msp"] is None
         and notice["region"] is None
         and notice["original_publication_verified"] is False
         and notice["contact_approved"] is False
         and notice["qualified_lead"] is False,
         "UNVERIFIED_VENDOR_EMAIL_CANNOT_BE_LEAD")
    return notice, kinds


def hold_payload(notice, groups, *, ai=None):
    number = notice["tender_id"]
    source_id = "B2B-CENTER-" + number
    fingerprint = sha256(source_id.encode()).hexdigest()[:32]
    fields = {
        "title": "ТЕНДЕР / проверка · B2B-Center №" + number,
        "categoryId": 21, "stageId": HOLD_STAGE,
        "UF_CRM_13_SVCIT_SIGNAL_KIND": "tender-opportunity",
        SIGNAL: source_id,
        "UF_CRM_13_SVCIT_SIGNAL_SOURCE": "B2B-Center email subscription",
        "UF_CRM_13_SVCIT_SIGNAL_URL": notice["source_url"],
        "UF_CRM_13_SVCIT_SUGGESTED_SERVICE": ", ".join(groups),
        "UF_CRM_13_SVCIT_COMPANY_NAME": notice["organizer"],
        "UF_CRM_13_SVCIT_EVIDENCE_STATUS":
            "HOLD: письмо; оригинал, статус, ИНН, ФНС и регион не проверены",
        "UF_CRM_13_SVCIT_REVIEW_DECISION":
            "Проверить закупку и покупателя; не контактировать",
        "UF_CRM_13_SVCIT_STOP_REASON": "Не квалифицированный B2B-лид",
        MANAGER_NEXT: preliminary_manager_action(notice, groups),
        KEY: "B2B-HOLD-" + fingerprint,
        **hold_analysis_fields(notice, groups, ai=ai),
    }
    must(set(fields) == FIELDS | {"title", "categoryId", "stageId"},
         "UNEXPECTED_CRM_FIELDS")
    return {"entityTypeId": 1062, "useOriginalUfNames": "Y", "fields": fields}


class GitHubLedger:
    """Persist immutable GitHub Actions bot reservation before external POST."""
    def __init__(self, client, token):
        must(isinstance(token, str) and len(token) >= 12, "GITHUB_TOKEN_REQUIRED")
        self.client = client
        self.headers = {"Authorization": "Bearer " + token,
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28"}
        self.root = "https://api.github.com/repos/" + REPO + "/issues/66"

    def markers(self):
        found = []
        for page in range(1, 21):
            r = self.client.get(self.root + "/comments", headers=self.headers,
                                params={"per_page": 100, "page": page}, timeout=15)
            must(r.status_code == 200 and len(r.content) <= 1500000,
                 "GITHUB_LEDGER_READ_FAILED")
            data = r.json()
            must(isinstance(data, list), "GITHUB_LEDGER_SHAPE_BAD")
            for comment in data:
                must(isinstance(comment, dict), "GITHUB_LEDGER_ITEM_BAD")
                user = comment.get("user") or {}
                if isinstance(user, dict) and user.get("login") == "github-actions[bot]":
                    body = comment.get("body")
                    if isinstance(body, str) and body.startswith(LOCK_PREFIX):
                        found.append(body[len(LOCK_PREFIX):].strip())
            if len(data) < 100:
                return found
        raise BridgeBlocked("GITHUB_LEDGER_REVIEW_REQUIRED")

    def reserve(self, source_id):
        must(re.fullmatch(r"B2B-CENTER-\d{5,12}", source_id),
             "INVALID_LEDGER_KEY")
        entry = LOCK_PREFIX + source_id
        r = self.client.post(self.root + "/comments", headers=self.headers,
                             json={"body": entry}, timeout=15)
        must(r.status_code == 201 and len(r.content) < 10000,
             "GITHUB_RESERVATION_UNCERTAIN")
        result = r.json()
        must(isinstance(result, dict) and result.get("body") == entry
             and (result.get("user") or {}).get("login") == "github-actions[bot]"
             and type(result.get("id")) is int, "GITHUB_RESERVATION_UNCERTAIN")


def bitrix_call(client, base, method, params):
    must(method in {"crm.item.list", "crm.item.add", "crm.item.get"},
         "CRM_METHOD_NOT_ALLOWED")
    r = client.post(base + "/" + method + ".json", json=params,
                    timeout=15, follow_redirects=False)
    must(r.status_code == 200 and len(r.content) <= 256000,
         "CRM_TRANSPORT_UNCERTAIN")
    content = r.json()
    must(isinstance(content, dict) and "error" not in content
         and isinstance(content.get("result"), dict),
         "CRM_RESULT_UNCERTAIN")
    return content


def exact_duplicate(client, base, source_id):
    response = bitrix_call(client, base, "crm.item.list", {
        "entityTypeId": 1062, "useOriginalUfNames": "Y",
        "filter": {"categoryId": 21, SIGNAL: source_id},
        "select": ["id", "entityTypeId", "categoryId", SIGNAL], "start": 0})
    body = response["result"]
    must("next" not in body and "next" not in response,
         "CRM_PAGED_UNCERTAIN")
    items = body.get("items")
    must(isinstance(items, list) and len(items) < 50,
         "CRM_LIST_INCOMPLETE")
    for item in items:
        must(isinstance(item, dict)
             and str(item.get("entityTypeId")) == "1062"
             and str(item.get("categoryId")) == "21"
             and item.get(SIGNAL) == source_id
             and type(item.get("id")) is int and item["id"] > 0,
             "CRM_ITEM_OUT_OF_SCOPE")
    if "total" in body:
        must(type(body["total"]) is int and body["total"] == len(items),
             "CRM_TOTAL_MISMATCH")
    return items


def run_once(event, *, client=None, github_token="", webhook="", live=False):
    notice, services = notice_from_event(event)
    payload = hold_payload(notice, services)
    if not live:
        return {"status": "B2B_HOLD_VALIDATED_DRY_RUN",
                "tender_id": notice["tender_id"], "services": services,
                "qualified_leads": 0, "crm_writes": 0}
    must(isinstance(client, httpx.Client), "APPROVED_HTTP_CLIENT_MISSING")
    ledger = GitHubLedger(client, github_token)
    base = validate_webhook(webhook)
    source_id = payload["fields"][SIGNAL]
    if source_id in ledger.markers():
        return {"status": "ALREADY_RESERVED_DO_NOT_RETRY",
                "tender_id": notice["tender_id"], "qualified_leads": 0, "crm_writes": 0}
    # Explicit live metadata read only. Fail if existing 1062/21 is altered.
    preflight(ReadOnlyBitrix(base, client=client))
    if exact_duplicate(client, base, source_id):
        return {"status": "ALREADY_EXISTS_IN_CRM",
                "tender_id": notice["tender_id"], "qualified_leads": 0, "crm_writes": 0}
    # Validate privacy and model authorization BEFORE any GitHub/CRM write.
    # Reserve the source AFTER approval but BEFORE the optional model API call
    # so failed/ambiguous model requests cannot be repeatedly billed.
    use_gigachat = os.environ.get("SERVICEIT_B2B_GIGACHAT_AUTO_ENABLED") == "YES"
    if use_gigachat:
        must(os.environ.get("SERVICEIT_B2B_GIGACHAT_PRIVACY_APPROVED") == "YES"
             and os.environ.get("AI_ENABLE") == "1"
             and os.environ.get("AI_PROVIDER") == "gigachat"
             and bool(os.environ.get("GIGACHAT_CREDENTIALS")),
             "B2B_GIGACHAT_PRIVACY_OR_CREDENTIALS_NOT_APPROVED")
    ledger.reserve(source_id)
    # Optional one model call on supplier email SUBJECT ONLY. No names,
    # company INN, source URL, message body, contacts or full CRM records.
    # This remains a hypothesis, NOT a qualified lead or Lead Score.
    gigachat_attempted = False
    if use_gigachat:
        gigachat_attempted = True
        try:
            from .serviceit_ai_report import plain
            subject=plain(notice["subject"],240)
            from .ai import classify_cockpit_signal
            model_result=classify_cockpit_signal(subject)
            if (isinstance(model_result,dict)
                    and model_result.get("provider")=="gigachat"
                    and model_result.get("quality_status")=="complete"):
                payload=hold_payload(notice,services,ai=model_result)
        except (RuntimeError,ValueError,TypeError,KeyError):
            # Provider unavailable or untrusted text: still create an honest
            # HOLD with AI_STATUS=NOT_RUN_SOURCE_UNVERIFIED, never fake success.
            pass
    # One permitted CRM write after the durable source reservation.
    added = bitrix_call(client, base, "crm.item.add", payload)
    created = added["result"].get("item")
    must(isinstance(created, dict) and type(created.get("id")) is int
         and created["id"] > 0, "CRM_POST_UNCERTAIN_NO_RETRY")
    card_id = created["id"]
    read = bitrix_call(client, base, "crm.item.get", {
        "entityTypeId": 1062, "id": card_id, "useOriginalUfNames": "Y"})
    actual = read["result"].get("item")
    must(isinstance(actual, dict)
         and str(actual.get("entityTypeId")) == "1062"
         and str(actual.get("categoryId")) == "21"
         and all(actual.get(k) == v for k, v in payload["fields"].items()),
         "CRM_READBACK_UNCERTAIN_NO_RETRY")
    # Producer-owned direct handoff: this exact ID comes from crm.item.add +
    # exact crm.item.get readback, NEVER from crm.item.list. The sidecar is
    # optional and off by default; if its remote checkpoint is ambiguous,
    # keep the proven CRM write and NEVER attempt to create the card again.
    handoff_state = "OFF"
    if os.environ.get("SERVICEIT_DIRECT_ID_HANDOFF_ENABLED") == "YES":
        try:
            from .serviceit_direct_id_handoff import (
                receipt, post_encrypted_handoff, DirectHandoffBlocked,
            )
            key = os.environ.get("SERVICEIT_GH_LEDGER_KEY", "")
            event = receipt(card_id, source_id,
                            ai_status=payload["fields"][
                                "UF_CRM_13_SVCIT_AI_STATUS"])
            handoff_state = post_encrypted_handoff(
                client, github_token, key, event)
        except Exception:
            # A failure AFTER confirmed crm.item.add is not a reason to retry
            # the source: the GitHub bot source reservation remains immutable.
            handoff_state = "UNCONFIRMED_DO_NOT_RETRY"
    return {"status": "ONE_TENDER_HOLD_VERIFIED", "tender_id": notice["tender_id"],
            "card_id": card_id, "qualified_leads": 0, "crm_writes": 1,
            "handoff_state": handoff_state, "ai_status": payload["fields"][
                "UF_CRM_13_SVCIT_AI_STATUS"], "gigachat_attempted": gigachat_attempted}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Gated GitHub -> Bitrix tender HOLD")
    parser.add_argument("--event", required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    try:
        p = Path(args.event)
        must(p.is_file() and p.stat().st_size <= 100000, "EVENT_FILE_INVALID")
        event = json.loads(p.read_text(encoding="utf-8"))
        if args.live:
            hook = os.environ.pop("SERVICEIT_BITRIX_WEBHOOK", "")
            gh_token = os.environ.pop("GITHUB_TOKEN", "")
            with httpx.Client(trust_env=False, follow_redirects=False) as client:
                result = run_once(event, client=client, github_token=gh_token,
                                  webhook=hook, live=True)
        else:
            result = run_once(event)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (BridgeBlocked, ValueError, TypeError, KeyError, AttributeError,
            OSError, httpx.HTTPError, json.JSONDecodeError):
        # Never log raw webhook, email or remote HTTP error body.
        print(json.dumps({"status": "B2B_PIPELINE_BLOCKED_OR_UNCERTAIN",
                          "never_blind_retry": True, "secrets_exposed": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
