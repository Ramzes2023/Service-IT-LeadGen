"""Live manager-confirmed post-call -> CRM result and internal next-action task.

Corporate scope: fresh smart-process 1062/category 21 ONLY.
This job never calls customers, sends mail, moves a deal or creates a lead.
Human manager confirms via two EXISTING CRM fields after a completed call:
  UF_CRM_13_SVCIT_REVIEW_DECISION = POSTCALL_APPROVED_YES|NO|UNKNOWN
  UF_CRM_13_SVCIT_MANAGER_NEXT_ACTION = ACTION|RFC3339 (or DO_NOT_CONTACT)
The CRM call activity DESCRIPTION stores manager-entered summary/objections.
Only an update by the card's assigned manager AFTER the recorded completed call
is accepted. The writer creates one linked internal task if appropriate and
replaces the two fields with an auditable confirmed result. Each call receives
an encrypted, once-only GitHub reservation before the first CRM/task write.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import json
import os
import re

import httpx

from .bitrix_metadata_audit import validate_webhook
from .serviceit_bitrix_dispatch_worker import EncryptedEventFence, REPOSITORY, PORTAL
from .serviceit_postcall_confirmed_intent import draft_verified_intent, PostCallBlocked
from .serviceit_b2b_hold_ai_fields import PREFIX
from .serviceit_safe_crm_read_retry import post_read

CUTOVER = datetime(2026, 11, 6, 10, 40, tzinfo=timezone.utc)
ACCEPTED = {
    "POSTCALL_APPROVED_YES": "YES",
    "POSTCALL_APPROVED_NO": "NO",
    "POSTCALL_APPROVED_UNKNOWN": "UNKNOWN",
}
ACTION_BY_INTEREST = {
    "YES": frozenset({"SEND_PROPOSAL", "ASK_DETAILS"}),
    "NO": frozenset({"DO_NOT_CONTACT"}),
    "UNKNOWN": frozenset({"MANAGER_REVIEW"}),
}
REVIEW = PREFIX + "REVIEW_DECISION"
NEXT_ACTION = PREFIX + "MANAGER_NEXT_ACTION"
MAX_CARDS = 150
READ_MARKER = "SERVICEIT_POSTCALL_MANUAL_READONLY_V1"
APPLY_MARKER = "SERVICEIT_POSTCALL_MANAGER_APPROVED_APPLY_NOW_V1"
MODE_WRITE = "SERVICEIT_POSTCALL_MANAGER_APPROVED_AUTO"
BASE_PORTAL = "https://" + PORTAL + "/rest/"


class PostCallLiveBlocked(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise PostCallLiveBlocked(code)


def as_id(v):
    require(type(v) is not bool and re.fullmatch(r"[1-9][0-9]{0,9}", str(v or "")),
            "IDENTIFIER_INVALID")
    return int(v)


def parse_date(v):
    require(type(v) is str and 10 <= len(v) <= 40, "DATE_REQUIRED")
    try:
        value = datetime.fromisoformat(v.replace("Z", "+00:00"))
        require(value.tzinfo is not None, "DATE_TZ_REQUIRED")
        return value.astimezone(timezone.utc)
    except ValueError:
        raise PostCallLiveBlocked("DATE_INVALID") from None


def api(client, base, method, params):
    READS = {
        "crm.item.fields", "crm.item.list", "crm.item.get",
        "crm.activity.list", "crm.activity.get",
        "crm.enum.ownertype", "tasks.task.list", "tasks.task.get",
    }
    WRITES = {"tasks.task.add", "crm.item.update"}
    require(method in READS | WRITES and type(params) is dict,
            "METHOD_NOT_ALLOWED")
    try:
        if method in READS:
            # Only verified read endpoints can recover transient 5xx/timeouts.
            response = post_read(client, base, method, params, timeout=24)
        else:
            # Actual CRM/task writes are NEVER retried on unknown outcomes.
            response = client.post(
                base + "/" + method + ".json", json=params,
                follow_redirects=False, timeout=24,
            )
        require(response.status_code == 200 and
                0 < len(response.content) <= 600_000,
                "BITRIX_HTTP_NOT_VERIFIED")
        result = response.json()
        require(type(result) is dict and "error" not in result
                and "result" in result, "BITRIX_REST_NOT_VERIFIED")
        return result
    except (httpx.HTTPError, ValueError):
        raise PostCallLiveBlocked("BITRIX_TRANSPORT_UNCERTAIN") from None


def field_preflight(client, base):
    payload = api(client, base, "crm.item.fields", {
        "entityTypeId": 1062, "useOriginalUfNames": "Y",
    })["result"]
    fields = payload.get("fields") if type(payload) is dict else None
    require(type(fields) is dict, "CRM_FIELDS_UNAVAILABLE")
    for key in (REVIEW, NEXT_ACTION):
        info = fields.get(key)
        require(type(info) is dict
                and info.get("isReadOnly") is False
                and info.get("isImmutable") in (False, None)
                and info.get("isMultiple") in (False, None),
                "POSTCALL_MANAGER_FIELD_NOT_WRITABLE")
    return True


def _card_ids(client, base, *, native_after=None):
    """Bounded read of new items. No historic cards may be processed."""
    minimum = max(CUTOVER, native_after) if native_after is not None else CUTOVER
    collected = []
    page = 0
    while page < 3:
        args = {
            "entityTypeId": 1062, "useOriginalUfNames": "Y",
            "filter": {"categoryId": 21,
                       ">=createdTime": minimum.isoformat()},
            "select": ["id", "categoryId", "createdTime",
                       "updatedTime", "updatedBy", "assignedById",
                       REVIEW, NEXT_ACTION],
            "order": {"updatedTime": "DESC"}, "start": page * 50,
        }
        response = api(client, base, "crm.item.list", args)
        body = response["result"]
        require(type(body) is dict and type(body.get("items")) is list,
                "ITEM_LIST_SHAPE")
        items = body["items"]
        require(len(items) <= 50, "ITEM_PAGE_OVERSIZED")
        for entry in items:
            require(type(entry) is dict and str(entry.get("categoryId")) == "21"
                    and parse_date(entry.get("createdTime")) >= minimum,
                    "ITEM_LIST_SCOPE")
            collected.append(entry)
        if not items or response.get("next") is None:
            require(response.get("total") in (None, len(collected))
                    or type(response.get("total")) is int
                    and response["total"] <= len(collected),
                    "ITEM_LIST_INCOMPLETE")
            break
        require(response.get("next") == (page + 1) * 50,
                "ITEM_PAGINATION_INVALID")
        page += 1
    else:
        raise PostCallLiveBlocked("ITEM_LIST_CAPPED_NO_WRITES")
    require(len(collected) <= MAX_CARDS, "ITEM_LIST_OVER_LIMIT")
    ids = [as_id(row.get("id")) for row in collected]
    require(len(set(ids)) == len(ids), "ITEM_IDS_DUPLICATE")
    return [as_id(entry["id"]) for entry in collected
            if entry.get(REVIEW) in ACCEPTED]


def _read_card(client, base, cid):
    response = api(client, base, "crm.item.get", {
        "entityTypeId": 1062, "id": cid, "useOriginalUfNames": "Y",
    })["result"]
    card = response.get("item") if type(response) is dict else None
    require(type(card) is dict and as_id(card.get("id")) == cid
            and str(card.get("categoryId")) == "21"
            and parse_date(card.get("createdTime")) >= CUTOVER,
            "CARD_IDENTITY_OR_CUTOVER_UNVERIFIED")
    return card


def _approved_fields(card, *, now):
    interest = ACCEPTED.get(card.get(REVIEW))
    require(interest is not None, "MANAGER_DECISION_NOT_APPROVED")
    responsible = as_id(card.get("assignedById"))
    updated_by = as_id(card.get("updatedBy"))
    require(updated_by == responsible,
            "MANAGER_NOT_AUTHOR_OF_CARD_APPROVAL")
    updated = parse_date(card.get("updatedTime"))
    require(CUTOVER <= updated <= now + timedelta(minutes=5)
            and now - updated <= timedelta(days=7),
            "APPROVAL_TOO_OLD_OR_FUTURE")
    raw = card.get(NEXT_ACTION)
    require(type(raw) is str and len(raw) <= 100,
            "NEXT_ACTION_INPUT_MISSING")
    if interest == "NO":
        require(raw == "DO_NOT_CONTACT", "NO_CONTACT_ACTION_CONFLICT")
        return interest, "DO_NOT_CONTACT", None, responsible, updated
    parts = raw.split("|", 1)
    require(len(parts) == 2 and parts[0] in ACTION_BY_INTEREST[interest],
            "NEXT_ACTION_NOT_MANAGER_APPROVED")
    due = parse_date(parts[1])
    require(now <= due <= now + timedelta(days=90),
            "FOLLOWUP_DEADLINE_NOT_VALID")
    return interest, parts[0], due, responsible, updated


def _completed_call(client, base, cid, manager, approved_at, *, now):
    response = api(client, base, "crm.activity.list", {
        "order": {"ID": "DESC"},
        "filter": {"OWNER_TYPE_ID": 1062, "OWNER_ID": cid,
                   "TYPE_ID": 2, "COMPLETED": "Y"},
        "select": ["ID", "OWNER_ID", "OWNER_TYPE_ID", "TYPE_ID",
                   "COMPLETED", "LAST_UPDATED", "RESPONSIBLE_ID"],
        "start": 0,
    })
    rows = response["result"]
    require(type(rows) is list and len(rows) <= 50
            and response.get("next") is None,
            "CALL_LIST_INCOMPLETE")
    matches = []
    for r in rows:
        require(type(r) is dict, "CALL_ROW_INVALID")
        when = parse_date(r.get("LAST_UPDATED"))
        if (str(r.get("OWNER_TYPE_ID")) == "1062"
                and str(r.get("OWNER_ID")) == str(cid)
                and str(r.get("TYPE_ID")) == "2"
                and str(r.get("COMPLETED")) in ("Y", "1", "True")
                and when <= approved_at
                and approved_at - when <= timedelta(hours=48)
                and now - when <= timedelta(days=7)
                and str(r.get("RESPONSIBLE_ID")) == str(manager)):
            matches.append((when, as_id(r.get("ID"))))
    require(matches, "COMPLETED_MANAGER_CALL_NOT_FOUND")
    matches.sort(reverse=True)
    if len(matches) >= 2:
        require(matches[0][0] > matches[1][0],
                "AMBIGUOUS_LAST_COMPLETED_CALL")
    activity_id = matches[0][1]
    act = api(client, base, "crm.activity.get", {"id": activity_id})["result"]
    require(type(act) is dict and as_id(act.get("ID")) == activity_id
            and str(act.get("OWNER_ID")) == str(cid)
            and str(act.get("OWNER_TYPE_ID")) == "1062"
            and str(act.get("TYPE_ID")) == "2"
            and str(act.get("COMPLETED")) == "Y"
            and str(act.get("RESPONSIBLE_ID")) == str(manager)
            and parse_date(act.get("LAST_UPDATED")) == matches[0][0],
            "COMPLETED_CALL_READBACK_NOT_VERIFIED")
    summary = act.get("DESCRIPTION")
    require(type(summary) is str and len(summary) <= 2000
            and not any(ord(c) < 32 and c not in "\n\t" or ord(c) == 127
                        for c in summary),
            "MANAGER_CALL_SUMMARY_MISSING_OR_INVALID")
    # CRM notes may contain line breaks. Normalize without changing facts;
    # the original manager text stays in the call activity.
    normalized = " ".join(summary.split())
    require(5 <= len(normalized) <= 500,
            "MANAGER_CALL_SUMMARY_MISSING_OR_INVALID")
    return act, normalized


def _binding(client, base):
    r = api(client, base, "crm.enum.ownertype", {})["result"]
    require(type(r) is list, "TASK_BINDING_TYPE_MISSING")
    values = [x.get("SYMBOL_CODE_SHORT") for x in r
              if type(x) is dict and str(x.get("ID")) == "1062"]
    require(len(values) == 1
            and type(values[0]) is str
            and re.fullmatch(r"T[0-9a-f]{1,8}", values[0]),
            "TASK_BINDING_AMBIGUOUS")
    return values[0]


def _task_listing(client, base, bind, unique_title):
    response = api(client, base, "tasks.task.list", {
        "filter": {"UF_CRM_TASK": bind},
        "select": ["ID", "TITLE", "UF_CRM_TASK", "RESPONSIBLE_ID", "DEADLINE"],
        "order": {"ID": "ASC"}, "start": 0,
    })
    result = response["result"]
    rows = result.get("tasks") if type(result) is dict else None
    require(type(rows) is list and len(rows) <= 50
            and response.get("next") is None
            and response.get("total") in (None, len(rows)),
            "POSTCALL_TASK_DEDUPE_VISIBILITY_INCOMPLETE")
    found = []
    for row in rows:
        require(type(row) is dict, "POSTCALL_TASK_ROW_INVALID")
        crm = row.get("ufCrmTask", row.get("UF_CRM_TASK"))
        require(type(crm) is list and bind in crm,
                "TASK_LIST_SCOPE_NOT_PROVEN")
        if row.get("title", row.get("TITLE")) == unique_title:
            found.append(row)
    require(not found, "POSTCALL_TASK_ALREADY_EXISTS")
    return True


def _task_fields(interest, action, due, uid, bind, summary, activity_id):
    require(interest != "NO" and due is not None, "NO_CONTACT_NO_TASK")
    title = f"[Сервис ИТ · звонок {activity_id}] " + {
        "SEND_PROPOSAL": "Подготовить КП (отправка только после проверки)",
        "ASK_DETAILS": "Уточнить потребность клиента",
        "MANAGER_REVIEW": "Проверить итог звонка и дальнейшие действия",
    }[action]
    description = (
        "ВНУТРЕННЯЯ ЗАДАЧА ПО ПОДТВЕРЖДЁННОМУ РЕЗУЛЬТАТУ ЗВОНКА.\n"
        "Итог, введённый менеджером в CRM-активности: " + summary + "\n"
        "Следующее действие: " + action + ".\n"
        "Проверить правовое основание и корпоративный STOP по каналу до "
        "любого исходящего сообщения или звонка. Никаких авторассылок."
    )
    require(len(title) <= 230 and len(description) <= 3000,
            "FOLLOWUP_TASK_TEXT_TOO_LONG")
    return {"TITLE": title, "DESCRIPTION": description,
            "RESPONSIBLE_ID": uid, "DEADLINE": due.isoformat(),
            "UF_CRM_TASK": [bind]}


class CallFence(EncryptedEventFence):
    def __init__(self, client, token, key, card_id):
        super().__init__(client, token, key)
        self.card_id = as_id(card_id)

    def url(self, activity_id):
        aid = as_id(activity_id)
        digest = hmac.new(
            self.path_key,
            f"serviceit-postcall-v1:1062:21:{self.card_id}:{aid}".encode(),
            sha256,
        ).hexdigest()
        return ("https://api.github.com/repos/" + REPOSITORY
                + "/contents/.serviceit/postcall-manager-once/"
                + digest + ".enc")


def _write_one(client, base, env, cid, *, now, native_connection=None):
    before = _read_card(client, base, cid)
    if native_connection is not None:
        from .serviceit_bitrix_poll_fallback import after_activation
        activation = after_activation({
            **env, "SERVICEIT_BITRIX_POLL_START_UTC":
                env["SERVICEIT_VPS_NATIVE_ACTIVATION_UTC"],
        }, now)
        require(activation <= parse_date(before.get("createdTime")) <= now,
                "NATIVE_POSTCALL_HISTORIC_OR_FUTURE_CARD")
    interest, action, due, manager, approval_at = _approved_fields(
        before, now=now,
    )
    act, summary = _completed_call(
        client, base, cid, manager, approval_at, now=now,
    )
    aid = as_id(act["ID"])
    proof = {
        "manager_confirmed": True, "manager_user_id": manager,
        "activity_id": aid, "interest": interest, "next_action": action,
        "next_due_at": due.isoformat() if due else None,
        "summary": summary,
        "confirmation_ref": f"crm:manager:call:{aid}",
    }
    intent = draft_verified_intent(
        {"id": cid, "entityTypeId": 1062, "categoryId": 21,
         "createdTime": before["createdTime"], "assignedById": manager},
        act, proof, now=now,
    )
    require(intent["status"] == "MANAGER_POSTCALL_INTENT_VALIDATED_NOT_WRITTEN"
            and not intent["outreach_authorized"],
            "POSTCALL_INTENT_NOT_SAFE")
    bind = _binding(client, base) + "_" + str(cid)
    fields = intent["next_action_fields"]
    task = None
    if interest != "NO":
        task = _task_fields(
            interest, action, due, manager, bind, summary, aid,
        )
        _task_listing(client, base, bind, task["TITLE"])
    # Exact second readback prevents handling stale manager approval data.
    reread = _read_card(client, base, cid)
    for key in ("updatedTime", "updatedBy", "assignedById", "stageId",
                REVIEW, NEXT_ACTION):
        require(before.get(key) == reread.get(key),
                "MANAGER_APPROVAL_CHANGED_BEFORE_WRITE")
    if native_connection is None:
        fence = CallFence(
            client, env.get("GITHUB_TOKEN", ""),
            env.get("SERVICEIT_GH_LEDGER_KEY", ""), cid,
        )
    else:
        from .serviceit_vps_native_actions import PostgresActionFence
        fence = PostgresActionFence(native_connection, "POSTCALL", cid)
    if fence.reserve(aid) != "RESERVED":
        return "POSTCALL_PRIOR_INTENT_FENCED"
    task_created = False
    try:
        if task is not None:
            response = api(client, base, "tasks.task.add", {"fields": task})
            task_result = response["result"]
            require(type(task_result) is dict
                    and type(task_result.get("task")) is dict,
                    "POSTCALL_TASK_ADD_RESPONSE_INVALID")
            tid = as_id(task_result["task"].get("id"))
            got = api(client, base, "tasks.task.get", {
                "taskId": tid,
                "select": ["ID", "TITLE", "RESPONSIBLE_ID",
                           "UF_CRM_TASK", "DEADLINE"],
            })["result"]
            item = got.get("task") if type(got) is dict else None
            require(type(item) is dict
                    and str(item.get("id", item.get("ID"))) == str(tid)
                    and item.get("title", item.get("TITLE")) == task["TITLE"]
                    and str(item.get("responsibleId", item.get("RESPONSIBLE_ID")))
                    == str(manager)
                    and item.get("ufCrmTask", item.get("UF_CRM_TASK"))
                    == [bind], "POSTCALL_NEW_TASK_READBACK_FAILED")
            # Confirm that the approved follow-up deadline was retained.
            observed_deadline = item.get("deadline", item.get("DEADLINE"))
            require(parse_date(observed_deadline) == due,
                    "POSTCALL_TASK_DEADLINE_NOT_VERIFIED")
            task_created = True
        response = api(client, base, "crm.item.update", {
            "entityTypeId": 1062, "id": cid, "useOriginalUfNames": "Y",
            "fields": fields,
        })["result"]
        require(type(response) is dict and type(response.get("item")) is dict,
                "POSTCALL_ITEM_UPDATE_RESPONSE_INVALID")
        got_card = _read_card(client, base, cid)
        for key, value in fields.items():
            require(got_card.get(key) == value,
                    "POSTCALL_FINAL_FIELDS_NOT_VERIFIED")
        require(str(got_card.get("categoryId")) == "21"
                and str(got_card.get("assignedById")) == str(manager)
                and got_card.get("stageId") == before.get("stageId"),
                "POSTCALL_OUT_OF_SCOPE_CHANGED")
    except Exception:
        fence.finalize(aid, "UNCERTAIN")
        return "POSTCALL_PARTIAL_OR_UNCERTAIN_NO_RETRY"
    fence.finalize(aid, "VERIFIED")
    return ("POSTCALL_MANAGER_RESULT_AND_TASK_VERIFIED"
            if task_created else "POSTCALL_NO_CONTACT_RESULT_VERIFIED")


def run(client, env, *, write=False, now=None, native_connection=None):
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if native_connection is None:
        require(env.get("GITHUB_REPOSITORY") == REPOSITORY
                and env.get("GITHUB_REF") == "refs/heads/main",
                "GITHUB_SCOPE_INVALID")
    else:
        require(
            env.get("SERVICEIT_VPS_WRITER_MODE") == "LIVE"
            and env.get("SERVICEIT_VPS_CUTOVER_AUTHORIZED") == "YES"
            and env.get("SERVICEIT_VPS_OLD_GH_WRITERS_DISABLED_ACK") == "YES"
            and env.get("SERVICEIT_VPS_POLL_ENABLED") == "YES"
            and env.get("SERVICEIT_VPS_POSTCALL_ENABLED") == "YES"
            and env.get("SERVICEIT_VPS_NATIVE_ACTIVATION_UTC"),
            "NATIVE_POSTCALL_CUTOVER_NOT_READY")
    base = validate_webhook(env.get("SERVICEIT_BITRIX_WEBHOOK", ""))
    require(base.startswith(BASE_PORTAL), "CORPORATE_PORTAL_INVALID")
    field_preflight(client, base)
    native_after = None
    if native_connection is not None:
        from .serviceit_bitrix_poll_fallback import after_activation
        native_after = after_activation({
            **env, "SERVICEIT_BITRIX_POLL_START_UTC":
                env["SERVICEIT_VPS_NATIVE_ACTIVATION_UTC"],
        }, now)
    cards = _card_ids(client, base, native_after=native_after)
    counts = {"approved_candidates": len(cards), "inspected": 0,
              "processed": 0, "no_contact": 0, "needs_review": 0,
              "crm_writes": 0, "tasks_created": 0}
    if not write:
        return {"status": "POSTCALL_READONLY_PRECHECK_PASS",
                **counts, "write_enabled": False}
    require(env.get("SERVICEIT_POSTCALL_AUTO_ENABLED") == MODE_WRITE,
            "POSTCALL_AUTO_WRITE_NOT_APPROVED")
    for cid in cards:
        try:
            result = _write_one(client, base, env, cid, now=now,
                                native_connection=native_connection)
        except (PostCallLiveBlocked, PostCallBlocked, ValueError, KeyError):
            counts["needs_review"] += 1
            continue
        counts["inspected"] += 1
        if result == "POSTCALL_MANAGER_RESULT_AND_TASK_VERIFIED":
            counts["processed"] += 1
            counts["tasks_created"] += 1
            counts["crm_writes"] += 1
        elif result == "POSTCALL_NO_CONTACT_RESULT_VERIFIED":
            counts["processed"] += 1
            counts["no_contact"] += 1
            counts["crm_writes"] += 1
        else:
            counts["needs_review"] += 1
    return {"status": "POSTCALL_AUTO_SCAN_COMPLETED",
            **counts, "write_enabled": True}


def main():
    env = dict(os.environ)
    kind = env.get("GITHUB_EVENT_NAME")
    manual_apply = False
    if kind == "issue_comment":
        # Owner may explicitly run NOW; each CRM record still requires
        # independent completed-call and human manager signoff proof.
        try:
            with open(env["GITHUB_EVENT_PATH"], encoding="utf8") as fh:
                event = json.load(fh)
            require(env.get("GITHUB_ACTOR") == "Ramzes2023"
                    and event.get("action") == "created"
                    and (event.get("issue") or {}).get("number") == 148
                    and not (event.get("issue") or {}).get("pull_request")
                    and (event.get("comment") or {}).get("body")
                    in (READ_MARKER, APPLY_MARKER)
                    and ((event.get("comment") or {}).get("user") or {}).get(
                        "login") == "Ramzes2023",
                    "OWNER_MARKER_INVALID")
            manual_apply = event["comment"]["body"] == APPLY_MARKER
        except Exception:
            print(json.dumps({"status": "POSTCALL_OWNER_EVENT_BLOCKED",
                              "crm_writes": 0}), flush=True)
            return 2
    elif kind != "schedule":
        print(json.dumps({"status": "POSTCALL_EVENT_NOT_ALLOWED",
                          "crm_writes": 0}), flush=True)
        return 2
    try:
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            result = run(
                client, env, write=(kind == "schedule" or manual_apply),
            )
    except Exception:
        result = {"status": "POSTCALL_SCAN_BLOCKED",
                  "crm_writes": 0, "tasks_created": 0}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if result.get("status") in (
        "POSTCALL_READONLY_PRECHECK_PASS", "POSTCALL_AUTO_SCAN_COMPLETED",
    ) else 2


if __name__ == "__main__":
    raise SystemExit(main())
