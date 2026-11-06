"""Isolated, one-use repair of two *missing* stages in the named DEAL funnel.

No changes to existing stages, deals, smart process 1062/21, or support funnels.
Only an owner issue-comment event with a confirmed CRM-admin credential may add.
Unknown response means UNCERTAIN, never a blind retry.
"""
from __future__ import annotations

from hashlib import sha256
import hmac
import json
import os

import httpx

from . import serviceit_sales_funnel_provision as funnel

READ_MARKER = "SERVICEIT_LEADGEN_STAGES_READONLY_ONCE_V1"
APPLY_MARKER = "SERVICEIT_LEADGEN_STAGES_ADD_MISSING_ONCE_V1"
NAMES = ("Переговоры", "Согласование")
CODES = ("SVCIT_NEGOTIATION", "SVCIT_AGREEMENT")
FENCE_ID = 78


class StageGateBlocked(ValueError):
    pass


class StageFence(funnel.SalesFunnelFence):
    def url(self, card):
        if card != FENCE_ID:
            raise StageGateBlocked("STAGE_FENCE_INVALID_ID")
        digest = hmac.new(
            self.path_key, b"serviceit-leadgen-sales-stages-v1:entity2:two-missing",
            sha256,
        ).hexdigest()
        return (
            "https://api.github.com/repos/" + funnel.REPOSITORY
            + "/contents/.serviceit/deal-stages-once/" + digest + ".enc"
        )


def _read_stages(client, base, category_id):
    entity = "DEAL_STAGE_" + str(category_id)
    rows = funnel.rest(client, base, "crm.status.list",
                       {"filter": {"ENTITY_ID": entity}})
    if type(rows) is not list or not 3 <= len(rows) <= 50:
        raise StageGateBlocked("STAGES_UNAVAILABLE")
    ids = set()
    normal = []
    for r in rows:
        if (type(r) is not dict or r.get("ENTITY_ID") != entity
                or type(r.get("STATUS_ID")) is not str
                or not r["STATUS_ID"] or r["STATUS_ID"] in ids
                or type(r.get("NAME")) is not str or not r["NAME"].strip()
                or not str(r.get("SORT", "")).isdigit()
                or type(r.get("EXTRA") or {}) is not dict):
            raise StageGateBlocked("STAGE_SCOPE_OR_SHAPE_INVALID")
        ids.add(r["STATUS_ID"])
        meaning = (r.get("EXTRA") or {}).get("SEMANTICS", r.get("SEMANTICS"))
        meaning = str(meaning or "process").strip().casefold()
        if meaning in ("s", "success"):
            sem = "success"
        elif meaning in ("f", "failure"):
            sem = "failure"
        elif meaning in ("process", "none", ""):
            sem = "process"
        else:
            raise StageGateBlocked("UNKNOWN_STAGE_SEMANTICS")
        normal.append({
            "code": r["STATUS_ID"],
            "name": r["NAME"].strip(),
            "sort": int(r["SORT"]),
            "semantic": sem,
        })
    if sum(r["semantic"] == "success" for r in normal) != 1:
        raise StageGateBlocked("SUCCESS_STAGE_AMBIGUOUS")
    if not any(r["semantic"] == "failure" for r in normal):
        raise StageGateBlocked("MISSING_FAILURE_STAGE")
    return normal


def _plan(rows):
    names = [r["name"].casefold() for r in rows]
    negotiation = [i for i, n in enumerate(names) if "переговор" in n]
    agreement = [i for i, n in enumerate(names) if "согласован" in n]
    if len(negotiation) > 1 or len(agreement) > 1:
        raise StageGateBlocked("AMBIGUOUS_STAGE_NAMES")
    present = (bool(negotiation), bool(agreement))
    if any(x != y for x, y in zip(present, (False, False))):
        if all(present):
            return {"status": "LEADGEN_SALES_STAGES_READY", "ready": True,
                    "can_add": False, "missing": 0}
        # One already exists: never add blindly in the wrong relative order.
        return {"status": "PARTIAL_STAGES_REQUIRE_MANAGER_REVIEW",
                "ready": False, "can_add": False, "missing": 1}
    processes = [r for r in rows if r["semantic"] == "process"]
    successes = [r for r in rows if r["semantic"] == "success"]
    failures = [r for r in rows if r["semantic"] == "failure"]
    if not processes or not successes or not failures:
        raise StageGateBlocked("STAGE_SEMANTICS_NOT_READY")
    max_process = max(r["sort"] for r in processes)
    min_success = min(r["sort"] for r in successes)
    if (max_process >= min_success
            or any(r["sort"] <= min_success for r in failures)):
        return {"status": "STAGE_SORT_REVIEW_REQUIRED", "ready": False,
                "can_add": False, "missing": 2}
    if not any("интерес" in n for n in names) or not any(
        ("кп" in n or "расч" in n or "предложен" in n) for n in names
    ):
        return {"status": "INTEREST_OR_QUOTATION_STAGE_MISSING",
                "ready": False, "can_add": False, "missing": 2}
    if any(r["code"].split(":")[-1] in CODES for r in rows):
        raise StageGateBlocked("CODE_ALREADY_USED_BY_DIFFERENT_STAGE")
    gap = min_success - max_process
    if gap < 3:
        return {"status": "NO_SAFE_STAGE_SORT_GAP", "ready": False,
                "can_add": False, "missing": 2}
    return {"status": "TWO_MISSING_STAGES_CAN_BE_ADDED",
            "ready": False, "can_add": True, "missing": 2,
            "sorts": (max_process + gap // 3, max_process + (2 * gap) // 3)}


def _add_stage(client, base, cat, code, name, sort):
    if (cat <= 0 or code not in CODES or name != NAMES[CODES.index(code)]
            or type(sort) is not int or sort <= 0):
        raise StageGateBlocked("STAGE_ADD_PARAMS_INVALID")
    params = {"fields": {"ENTITY_ID": "DEAL_STAGE_" + str(cat),
                         "STATUS_ID": code, "NAME": name, "SORT": sort,
                         "SEMANTICS": ""}}
    response = client.post(base + "/crm.status.add.json", json=params,
                           timeout=25, follow_redirects=False)
    if response.status_code != 200 or not 0 < len(response.content) < 500000:
        raise StageGateBlocked("STAGE_ADD_RESULT_UNCERTAIN")
    data = response.json()
    if type(data) is not dict or "error" in data:
        raise StageGateBlocked("STAGE_ADD_API_ERROR_UNCERTAIN")
    if not str(data.get("result", "")).isdigit():
        raise StageGateBlocked("STAGE_ADD_RESULT_SHAPE_UNCERTAIN")


def _verify_append_only(before, after, added):
    prior = {(x["code"], x["name"], x["sort"], x["semantic"]) for x in before}
    current = {(x["code"], x["name"], x["sort"], x["semantic"]) for x in after}
    if len(after) != len(before) + added or not prior.issubset(current):
        raise StageGateBlocked("EXISTING_STAGE_MUTATED_OR_READBACK_UNCERTAIN")
    for code, name in zip(CODES[:added], NAMES[:added]):
        matches = [r for r in after if r["code"].split(":")[-1] == code
                   and r["name"] == name and r["semantic"] == "process"]
        if len(matches) != 1:
            raise StageGateBlocked("NEW_STAGE_READBACK_UNCERTAIN")


def run(client, env, *, apply=False):
    funnel.event_allowed_marker = None  # no effect on existing function
    webhook = (env.get("SERVICEIT_BITRIX_CRM_ADMIN_ONETIME_WEBHOOK")
               or env.get("SERVICEIT_BITRIX_WEBHOOK", ""))
    base = funnel.validate_webhook(webhook)
    if not base.startswith(funnel.PORTAL):
        raise StageGateBlocked("WRONG_PORTAL")
    is_admin = funnel.rest(client, base, "user.admin", {})
    matches = funnel.candidates(funnel.categories(client, base))
    if len(matches) != 1:
        raise StageGateBlocked("LEADGEN_FUNNEL_NOT_UNIQUE")
    cat = int(matches[0]["id"])
    funnel.get_category(client, base, cat)
    before = _read_stages(client, base, cat)
    plan = _plan(before)
    summary = {"status": plan["status"], "crm_writes": 0,
               "stages_visible": len(before), "admin_ready": is_admin is True,
               "missing_required_stages": plan["missing"]}
    if not apply or not plan["can_add"]:
        return summary
    if is_admin is not True:
        summary["status"] = "CRM_ADMIN_REQUIRED_NO_WRITE"
        return summary
    fence = StageFence(client, env.get("GITHUB_TOKEN", ""),
                       env.get("SERVICEIT_GH_LEDGER_KEY", ""))
    if fence.reserve(FENCE_ID) != "RESERVED":
        return {"status": "STAGE_WRITE_PREVIOUSLY_RESERVED_NO_RETRY",
                "crm_writes": "UNVERIFIED"}
    try:
        prior = before
        for i, (code, name, sort) in enumerate(zip(CODES, NAMES, plan["sorts"])):
            _add_stage(client, base, cat, code, name, sort)
            after = _read_stages(client, base, cat)
            _verify_append_only(prior, after, 1)
            prior = after
        _verify_append_only(before, prior, 2)
        verified = _plan(prior)
        if not verified["ready"]:
            raise StageGateBlocked("REPAIRED_STAGES_NOT_READY")
    except Exception:
        fence.finalize(FENCE_ID, "UNCERTAIN")
        return {"status": "STAGE_WRITE_UNCERTAIN_NO_RETRY",
                "crm_writes": "UNVERIFIED"}
    fence.finalize(FENCE_ID, "VERIFIED")
    return {"status": "LEADGEN_TWO_SALES_STAGES_ADDED_AND_VERIFIED",
            "crm_writes": 2, "support_funnel_writes": 0,
            "existing_stage_updates": 0, "existing_deal_updates": 0}


def _allowed_event(env, apply):
    if (env.get("GITHUB_REPOSITORY") != funnel.REPOSITORY
            or env.get("GITHUB_REF") != "refs/heads/main"
            or env.get("GITHUB_EVENT_NAME") != "issue_comment"
            or env.get("GITHUB_ACTOR") != "Ramzes2023"):
        raise StageGateBlocked("UNAUTHORIZED_GITHUB_EVENT")
    with open(env.get("GITHUB_EVENT_PATH", ""), encoding="utf8") as handle:
        event = json.load(handle)
    expected = APPLY_MARKER if apply else READ_MARKER
    if not (
        event.get("action") == "created"
        and (event.get("repository") or {}).get("full_name") == funnel.REPOSITORY
        and (event.get("issue") or {}).get("number") == 148
        and not (event.get("issue") or {}).get("pull_request")
        and (event.get("sender") or {}).get("login") == "Ramzes2023"
        and (event.get("comment") or {}).get("body") == expected
        and ((event.get("comment") or {}).get("user") or {}).get("login")
        == "Ramzes2023"
    ):
        raise StageGateBlocked("OWNER_MARKER_NOT_AUTHORIZED")


def main():
    env = dict(os.environ)
    apply = env.get("SERVICEIT_STAGES_MODE") == "apply"
    try:
        _allowed_event(env, apply)
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            out = run(client, env, apply=apply)
    except Exception:
        out = {"status": "LEADGEN_STAGE_CHECK_BLOCKED",
               "crm_writes": 0}
    print(json.dumps(out, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if out["status"] in {
        "LEADGEN_SALES_STAGES_READY", "TWO_MISSING_STAGES_CAN_BE_ADDED",
        "CRM_ADMIN_REQUIRED_NO_WRITE",
        "LEADGEN_TWO_SALES_STAGES_ADDED_AND_VERIFIED",
        "PARTIAL_STAGES_REQUIRE_MANAGER_REVIEW",
        "STAGE_SORT_REVIEW_REQUIRED", "INTEREST_OR_QUOTATION_STAGE_MISSING",
        "NO_SAFE_STAGE_SORT_GAP",
    } else 2


if __name__ == "__main__":
    raise SystemExit(main())
