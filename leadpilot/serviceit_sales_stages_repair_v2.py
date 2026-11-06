"""One-shot, owner-guarded repair of ONLY the existing LeadGen DEAL funnel stages.

Read-only preflight first. When owner triggers APPLY, preserve every original
stage's code, name, semantics and numeric ID; modify SORT of at most three
existing statuses and add exactly two requested process stages. Never touch
deals, other funnels, contacts, custom fields, or smart process 1062/21.
Uncertain outcome is fenced forever until manual reconciliation.
"""
from __future__ import annotations

from hashlib import sha256
import hmac
import json
import os

import httpx

from . import serviceit_sales_funnel_provision as funnel
from . import serviceit_sales_stages_guarded as stages

READ_MARKER = "SERVICEIT_LEADGEN_SORT_REPAIR_V2_READONLY"
APPLY_MARKER = "SERVICEIT_LEADGEN_SORT_REPAIR_V2_APPLY_ONCE"
FENCE_ID = 79


class RepairBlocked(ValueError):
    pass


class CrmRightsDenied(RepairBlocked):
    pass


class RepairFence(funnel.SalesFunnelFence):
    def url(self, card):
        if card != FENCE_ID:
            raise RepairBlocked("FENCE_ID_INVALID")
        digest = hmac.new(
            self.path_key, b"serviceit-leadgen-sort-stage-repair-v2:deals-only",
            sha256,
        ).hexdigest()
        return ("https://api.github.com/repos/" + funnel.REPOSITORY
                + "/contents/.serviceit/deal-sort-repair-v2/" + digest + ".enc")


def _role(row):
    if row["semantic"] != "process":
        return row["semantic"]
    n = row["name"].casefold()
    if "интерес" in n:
        return "interest"
    if any(t in n for t in ("кп", "расч", "предложен")):
        return "quote"
    return "other"


def build_plan(rows, category):
    """Require the exact observed starting shape; do not guess in a changed CRM."""
    if (type(category) is not int or category <= 0 or len(rows) != 9
            or stages._safe_order_diagnostics(rows)["anonymous_sorted_stage_roles"]
            != "PPPIPSQFF"):
        raise RepairBlocked("PREEXISTING_STAGE_SHAPE_CHANGED")
    if (len({r["code"] for r in rows}) != 9
            or len({r.get("id") for r in rows}) != 9
            or any(type(r.get("id")) is not int or r["id"] <= 0
                   or not r["code"].startswith("C" + str(category) + ":")
                   or r["sort"] < 1 or r["sort"] > 1_000_000
                   for r in rows)):
        raise RepairBlocked("EXISTING_STATUS_IDENTITIES_NOT_VERIFIED")
    interests = [r for r in rows if _role(r) == "interest"]
    quotes = [r for r in rows if _role(r) == "quote"]
    wins = [r for r in rows if r["semantic"] == "success"]
    fails = [r for r in rows if r["semantic"] == "failure"]
    processes = [r for r in rows if r["semantic"] == "process"]
    if (len(interests) != 1 or len(quotes) != 1 or len(wins) != 1
            or len(fails) != 2 or len(processes) != 6
            or any(r["code"].split(":")[-1] in stages.CODES for r in rows)
            or any("переговор" in r["name"].casefold()
                   or "согласован" in r["name"].casefold() for r in rows)):
        # Never create duplicate requested stages even when codes differ.
        raise RepairBlocked("STATUS_ROLES_AMBIGUOUS")
    interest, quote, win = interests[0], quotes[0], wins[0]
    fails.sort(key=lambda r: (r["sort"], r["id"]))
    remaining = [r for r in processes if r["code"] != quote["code"]]
    other_max = max(r["sort"] for r in remaining)
    if (not interest["sort"] < quote["sort"]
            or not other_max <= win["sort"] < quote["sort"]
            or not quote["sort"] < min(r["sort"] for r in fails)):
        raise RepairBlocked("EXISTING_STAGE_RELATIVE_ORDER_CHANGED")
    # Minimal route: place the mis-sorted quotation before success, if three
    # strictly free integers exist; the two new stages follow it immediately.
    if win["sort"] - other_max >= 4:
        base = other_max
        return {
            "strategy": "MOVE_QUOTE_SORT_ONLY",
            "updates": [(quote["code"], base + 1)],
            "new_sorts": (base + 2, base + 3),
            "changed_existing_count": 1,
        }
    # Otherwise move ONLY the current three terminal statuses behind ALL six
    # existing process statuses; no customer/deal/status codes are changed.
    top = max(r["sort"] for r in rows)
    if top > 900_000:
        raise RepairBlocked("SORT_RANGE_EXHAUSTED")
    base = top + 100
    return {
        "strategy": "MOVE_THREE_TERMINAL_SORTS",
        # Reverse failure order first: keep terminal semantics safely trailing.
        "updates": [(fails[1]["code"], base + 400),
                    (fails[0]["code"], base + 300),
                    (win["code"], base + 200)],
        "new_sorts": (base, base + 100),
        "changed_existing_count": 3,
    }


def _same_existing_except_one(before, after, code, sort):
    if len(before) != len(after):
        raise RepairBlocked("SORT_READBACK_CARDINALITY_CHANGED")
    original = {r["code"]: r for r in before}
    observed = {r["code"]: r for r in after}
    if original.keys() != observed.keys():
        raise RepairBlocked("SORT_READBACK_CODE_SET_CHANGED")
    for key, prior in original.items():
        current = observed[key]
        if any(current[field] != prior[field]
               for field in ("id", "name", "semantic")):
            raise RepairBlocked("EXISTING_STATUS_IDENTITY_CHANGED")
        expected = sort if key == code else prior["sort"]
        if current["sort"] != expected:
            raise RepairBlocked("UNEXPECTED_STATUS_SORT_CHANGE")


def _update_sort(client, base, existing, new_sort):
    if (type(existing.get("id")) is not int or type(new_sort) is not int
            or not 0 < new_sort <= 1_000_000):
        raise RepairBlocked("SORT_WRITE_ARGUMENT_INVALID")
    try:
        res = client.post(base + "/crm.status.update.json",
                          json={"id": existing["id"],
                                "fields": {"SORT": new_sort}},
                          timeout=25, follow_redirects=False)
        payload = res.json()
        if (res.status_code in (400, 401, 403)
                and isinstance(payload, dict)
                and ("access denied" in str(payload.get("error_description", "")).lower()
                     or payload.get("error") in ("insufficient_scope", "ACCESS_DENIED"))):
            raise CrmRightsDenied("CRM_SETTINGS_RIGHTS_DENIED")
        if res.status_code != 200 or type(payload) is not dict or payload.get("result") is not True:
            raise RepairBlocked("STATUS_UPDATE_RESULT_UNCERTAIN")
    except CrmRightsDenied:
        raise
    except (httpx.HTTPError, ValueError):
        raise RepairBlocked("STATUS_UPDATE_TRANSPORT_UNCERTAIN") from None


def _verify_final(before, after, plan):
    if len(after) != len(before) + 2:
        raise RepairBlocked("FINAL_STAGE_COUNT_INVALID")
    bycode = {r["code"]: r for r in after}
    if len(bycode) != len(after):
        raise RepairBlocked("DUPLICATE_FINAL_CODES")
    updates = dict(plan["updates"])
    for prior in before:
        got = bycode.get(prior["code"])
        if not got or any(got[x] != prior[x] for x in ("id", "name", "semantic")):
            raise RepairBlocked("EXISTING_STAGE_CHANGED")
        if got["sort"] != updates.get(prior["code"], prior["sort"]):
            raise RepairBlocked("EXISTING_STAGE_SORT_NOT_VERIFIED")
    for name, suffix, sort in zip(stages.NAMES, stages.CODES, plan["new_sorts"]):
        matches = [r for r in after if r["code"].split(":")[-1] == suffix]
        if (len(matches) != 1 or matches[0]["name"] != name
                or matches[0]["semantic"] != "process"
                or matches[0]["sort"] != sort):
            raise RepairBlocked("NEW_STAGE_NOT_VERIFIED")
    interests = [r for r in after if _role(r) == "interest"]
    quotes = [r for r in after if _role(r) == "quote"]
    neg = [r for r in after if r["code"].split(":")[-1] == stages.CODES[0]][0]
    agree = [r for r in after if r["code"].split(":")[-1] == stages.CODES[1]][0]
    win = [r for r in after if r["semantic"] == "success"]
    failed = [r for r in after if r["semantic"] == "failure"]
    processes = [r for r in after if r["semantic"] == "process"]
    if (len(interests) != 1 or len(quotes) != 1 or len(win) != 1
            or len(failed) != 2
            or not interests[0]["sort"] < quotes[0]["sort"] < neg["sort"]
            < agree["sort"] < win[0]["sort"] < min(r["sort"] for r in failed)
            or max(r["sort"] for r in processes) >= win[0]["sort"]):
        raise RepairBlocked("FINAL_STAGE_ORDER_NOT_VERIFIED")


def run(client, env, *, apply=False):
    webhook = (env.get("SERVICEIT_BITRIX_CRM_ADMIN_ONETIME_WEBHOOK")
               or env.get("SERVICEIT_BITRIX_WEBHOOK", ""))
    base = funnel.validate_webhook(webhook)
    if not base.startswith(funnel.PORTAL):
        raise RepairBlocked("WRONG_CORPORATE_PORTAL")
    portal_admin = funnel.rest(client, base, "user.admin", {})
    matches = funnel.candidates(funnel.categories(client, base))
    if len(matches) != 1:
        raise RepairBlocked("FUNNEL_IDENTITY_NOT_UNIQUE")
    category = int(matches[0]["id"])
    funnel.get_category(client, base, category)
    before = stages._read_stages(client, base, category)
    plan = build_plan(before, category)
    summary = {
        "status": "STAGE_SORT_REPAIR_V2_READY_READONLY",
        "crm_writes": 0,
        "portal_admin": portal_admin is True,
        "crm_settings_permission": "UNKNOWN_UNTIL_SCOPED_WRITE",
        "stages_visible": len(before),
        "planned_sort_only_updates": plan["changed_existing_count"],
        "planned_new_stages": 2,
        "strategy": plan["strategy"],
    }
    if not apply:
        return summary
    fence = RepairFence(client, env.get("GITHUB_TOKEN", ""),
                        env.get("SERVICEIT_GH_LEDGER_KEY", ""))
    if fence.reserve(FENCE_ID) != "RESERVED":
        return {"status": "REPAIR_V2_FENCED_NO_RETRY",
                "crm_writes": "UNVERIFIED"}
    prior = before
    changed = 0
    try:
        for code, sort in plan["updates"]:
            current = next(r for r in prior if r["code"] == code)
            _update_sort(client, base, current, sort)
            changed += 1
            after = stages._read_stages(client, base, category)
            _same_existing_except_one(prior, after, code, sort)
            prior = after
        for code, name, sort in zip(stages.CODES, stages.NAMES, plan["new_sorts"]):
            stages._add_stage(client, base, category, code, name, sort)
            changed += 1
            after = stages._read_stages(client, base, category)
            stages._verify_append_only(prior, after, 1)
            prior = after
        _verify_final(before, prior, plan)
    except CrmRightsDenied:
        fence.finalize(FENCE_ID, "NEEDS_REVIEW")
        return {"status": "CRM_SETTINGS_PERMISSION_DENIED",
                "crm_writes": 0 if changed == 0 else "UNVERIFIED"}
    except Exception:
        fence.finalize(FENCE_ID, "UNCERTAIN")
        return {"status": "REPAIR_V2_PARTIAL_OR_UNCERTAIN_NO_RETRY",
                "crm_writes": "UNVERIFIED"}
    finalized = fence.finalize(FENCE_ID, "VERIFIED")
    return {
        "status": "LEADGEN_STAGES_ORDER_AND_BOTH_NAMES_VERIFIED",
        "crm_writes": changed,
        "stage_sort_only_updates": plan["changed_existing_count"],
        "new_stages_added": 2,
        "existing_deal_updates": 0,
        "other_funnel_updates": 0,
        "ledger_finalized": finalized,
    }


def _allow_owner_marker(env, *, apply):
    if (env.get("GITHUB_REPOSITORY") != funnel.REPOSITORY
            or env.get("GITHUB_REF") != "refs/heads/main"
            or env.get("GITHUB_EVENT_NAME") != "issue_comment"
            or env.get("GITHUB_ACTOR") != "Ramzes2023"):
        raise RepairBlocked("GITHUB_OWNER_EVENT_REQUIRED")
    with open(env.get("GITHUB_EVENT_PATH", ""), encoding="utf8") as fh:
        event = json.load(fh)
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
        raise RepairBlocked("OWNER_MARKER_INVALID")


def main():
    env = dict(os.environ)
    apply = env.get("SERVICEIT_STAGE_REPAIR_V2_MODE") == "apply"
    try:
        _allow_owner_marker(env, apply=apply)
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            result = run(client, env, apply=apply)
    except Exception:
        result = {"status": "REPAIR_V2_BLOCKED_NO_WRITE",
                  "crm_writes": 0}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if result["status"] in (
        "STAGE_SORT_REPAIR_V2_READY_READONLY",
        "LEADGEN_STAGES_ORDER_AND_BOTH_NAMES_VERIFIED",
        "CRM_SETTINGS_PERMISSION_DENIED",
    ) else 2


if __name__ == "__main__":
    raise SystemExit(main())
