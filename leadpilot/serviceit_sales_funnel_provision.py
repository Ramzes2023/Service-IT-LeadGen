"""Owner-authorized, exact one-time independent DEAL funnel provisioning.

CRM entityTypeId=2 ONLY. Never modify tech-support, smart process 1062/21,
existing funnels, existing deals, or cards. A non-admin webhook cannot prove
absence (Bitrix list is permission-filtered): no write without user.admin true.

crm.category.add is NOT idempotent in Bitrix. Reserve an encrypted one-use
GitHub CAS receipt BEFORE the call; after ambiguous outcomes, observe only,
NEVER retry blindly.
"""
from __future__ import annotations

from hashlib import sha256
import hmac
import json
import os

import httpx

from .bitrix_metadata_audit import validate_webhook
from .serviceit_bitrix_dispatch_worker import (
    EncryptedEventFence, EventCloudBlocked, REPOSITORY, BRANCH,
)

PORTAL="https://serviseit.bitrix24.ru/rest/"
DEAL_TYPE=2
FUNNEL_NAME="Лидогенерация"
OWNER_MARKER="SERVICEIT_SALES_FUNNEL_CREATE_ONCE_V1"
READONLY_MARKER="SERVICEIT_SALES_FUNNEL_READONLY_ONCE_V1"
ISSUE=148
FENCE_ID=77
READS=frozenset({"user.admin","crm.category.list","crm.category.get","crm.status.list"})
WRITES=frozenset({"crm.category.add"})


class SalesFunnelBlocked(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise SalesFunnelBlocked(code)


class SalesFunnelFence(EncryptedEventFence):
    def url(self, card):
        need(card==FENCE_ID, "FUNNEL_FENCE_ID_INVALID")
        path=hmac.new(self.path_key,
                      b"serviceit-deals-funnel-create-v1:entity2:leadgeneration",
                      sha256).hexdigest()
        return ("https://api.github.com/repos/" + REPOSITORY
                + "/contents/.serviceit/deal-funnel-once/" + path + ".enc")


def rest(client, base, method, args):
    need(method in READS|WRITES, "FORBIDDEN_REST_METHOD")
    need(type(args) is dict, "FUNNEL_PARAMS_NOT_OBJECT")
    if method in ("crm.category.list","crm.category.get","crm.category.add"):
        need(args.get("entityTypeId")==DEAL_TYPE, "ONLY_DEAL_ENTITY_ALLOWED")
    if method=="crm.category.add":
        need(args=={"entityTypeId":2, "fields":{"name":FUNNEL_NAME,
                                               "sort":550}},
             "CATEGORY_ADD_PARAMS_NOT_EXACT")
    if method=="crm.status.list":
        need(type(args.get("filter")) is dict and len(args["filter"])==1
             and type(args["filter"].get("ENTITY_ID")) is str
             and args["filter"]["ENTITY_ID"].startswith("DEAL_STAGE_"),
             "FORBIDDEN_STAGE_LIST")
    try:
        resp=client.post(base+"/"+method+".json",json=args,
                         timeout=25,follow_redirects=False)
        need(resp.status_code==200 and 0<len(resp.content)<500000,
             "FUNNEL_API_HTTP_OR_SCOPE_DENIED")
        payload=resp.json()
        need(type(payload) is dict and "error" not in payload
             and "result" in payload, "FUNNEL_REST_RESULT_UNVERIFIED")
        return payload["result"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        if type(exc) is SalesFunnelBlocked:
            raise
        raise SalesFunnelBlocked("FUNNEL_REST_TRANSPORT_OR_SHAPE_UNVERIFIED") from None


def categories(client,base):
    result=rest(client,base,"crm.category.list",{"entityTypeId":DEAL_TYPE})
    need(type(result) is dict and type(result.get("categories")) is list,
         "FUNNEL_LIST_NOT_COMPLETE")
    rows=result["categories"]
    need(1<=len(rows)<=500,"FUNNEL_LIST_EMPTY_OR_EXCESSIVE")
    ids=set()
    for r in rows:
        need(type(r) is dict and str(r.get("entityTypeId"))=="2"
             and type(r.get("name")) is str
             and str(r.get("id","")).isdigit()
             and int(r["id"]) not in ids, "FUNNEL_LIST_IDENTITY_UNVERIFIED")
        ids.add(int(r["id"]))
    need(any(str(r.get("isDefault")) in ("Y","True")
             for r in rows),"DEFAULT_DEAL_FUNNEL_NOT_VISIBLE")
    return rows


def candidates(rows):
    return [r for r in rows if r["name"].strip().casefold()
            ==FUNNEL_NAME.casefold()]


def get_category(client,base,id):
    need(type(id) is int and id>0, "FUNNEL_ID_INVALID")
    obj=rest(client,base,"crm.category.get",{"entityTypeId":2,"id":id})
    need(type(obj) is dict and type(obj.get("category")) is dict,
         "CATEGORY_READBACK_SHAPE_INVALID")
    c=obj["category"]
    need(str(c.get("id"))==str(id)
         and c.get("name")==FUNNEL_NAME
         and str(c.get("entityTypeId"))=="2"
         and str(c.get("isDefault")) in ("N","False"),
         "CATEGORY_READBACK_IDENTITY_NOT_VERIFIED")
    return c


def stage_readback(client,base,id):
    entity="DEAL_STAGE_"+str(id)
    rows=rest(client,base,"crm.status.list",{"filter":{"ENTITY_ID":entity}})
    need(type(rows) is list and 1<=len(rows)<=50,
         "NEW_FUNNEL_STAGES_UNVERIFIED")
    need(all(type(x) is dict and x.get("ENTITY_ID")==entity
             and type(x.get("NAME")) is str
             and type(x.get("STATUS_ID")) is str for x in rows),
         "NEW_FUNNEL_STAGE_CROSS_CATEGORY")
    sem=[(x.get("EXTRA") or {}).get("SEMANTICS",x.get("SEMANTICS"))
         for x in rows]
    need(any(s in ("success","S") for s in sem)
         and any(s in ("failure","F") for s in sem),
         "SUCCESS_FAILURE_STAGES_NOT_VERIFIED")
    return len(rows)


def event_allowed(env,*,apply):
    need(env.get("GITHUB_REPOSITORY")==REPOSITORY
         and env.get("GITHUB_REF")=="refs/heads/main"
         and env.get("GITHUB_EVENT_NAME")=="issue_comment"
         and env.get("GITHUB_ACTOR")=="Ramzes2023",
         "FUNNEL_EVENT_CONTEXT_NOT_AUTHORIZED")
    with open(env.get("GITHUB_EVENT_PATH",""),encoding="utf8") as f:
        event=json.load(f)
    marker=OWNER_MARKER if apply else READONLY_MARKER
    need(type(event) is dict and event.get("action")=="created"
         and (event.get("repository") or {}).get("full_name")==REPOSITORY
         and (event.get("issue") or {}).get("number")==ISSUE
         and not (event.get("issue") or {}).get("pull_request")
         and (event.get("sender") or {}).get("login")=="Ramzes2023"
         and (event.get("comment") or {}).get("body")==marker
         and ((event.get("comment") or {}).get("user") or {}).get("login")=="Ramzes2023",
         "FUNNEL_OWNER_MARKER_INVALID")


def ensure_funnel(client,env,*,apply=False):
    """Never add unless portal-wide admin verified; only exact sales funnel."""
    webhook=env.get("SERVICEIT_BITRIX_WEBHOOK","")
    base=validate_webhook(webhook)
    need(base.startswith(PORTAL), "WRONG_CORPORATE_PORTAL")
    is_admin=rest(client,base,"user.admin",{})
    # Even a successful nonadmin list is visibility-limited.
    if is_admin is not True:
        return {"status":"CRM_ADMIN_REQUIRED_NO_WRITE",
                "deal_funnel_created":False,"crm_writes":0,
                "access":"LIMITED_VISIBILITY"}
    before=categories(client,base)
    matching=candidates(before)
    need(len(matching)<=1, "DUPLICATE_NAMED_FUNNEL_NEEDS_ADMIN_REVIEW")
    if matching:
        id=int(matching[0]["id"])
        get_category(client,base,id)
        count=stage_readback(client,base,id)
        return {"status":"DEAL_FUNNEL_ALREADY_PRESENT_READBACK_VERIFIED",
                "deal_funnel_created":False,"funnel_id":id,
                "stages_visible":count,"crm_writes":0}
    if not apply:
        return {"status":"FUNNEL_ABSENT_ADMIN_SCOPED_READONLY",
                "deal_funnel_created":False,"crm_writes":0}
    fence=SalesFunnelFence(client,env.get("GITHUB_TOKEN",""),
                           env.get("SERVICEIT_GH_LEDGER_KEY",""))
    token=fence.reserve(FENCE_ID)
    if token!="RESERVED":
        # Never blindly retry even if a prior CRM POST timed out.
        return {"status":"PRIOR_FUNNEL_ATTEMPT_FENCED_REVIEW_REQUIRED",
                "deal_funnel_created":False,"crm_writes":"UNVERIFIED"}
    try:
        added=rest(client,base,"crm.category.add",
                   {"entityTypeId":2,
                    "fields":{"name":FUNNEL_NAME,"sort":550}})
        need(type(added) is dict and type(added.get("category")) is dict,
             "FUNNEL_ADD_RESPONSE_UNVERIFIED")
        id=int(added["category"].get("id"))
        get_category(client,base,id)
        after=categories(client,base)
        need(len(candidates(after))==1
             and len(after)==len(before)+1,
             "FUNNEL_LIST_READBACK_UNVERIFIED")
        stage_count=stage_readback(client,base,id)
    except Exception:
        fence.finalize(FENCE_ID,"UNCERTAIN")
        return {"status":"FUNNEL_WRITE_UNCERTAIN_NO_RETRY",
                "deal_funnel_created":"UNVERIFIED","crm_writes":"UNVERIFIED"}
    fence.finalize(FENCE_ID,"VERIFIED")
    return {"status":"NEW_SALES_DEAL_FUNNEL_CREATED_VERIFIED",
            "funnel_id":id,"stages_visible":stage_count,
            "crm_writes":1,"deal_funnel_created":True,
            "support_funnel_changes":0,"existing_deals_changed":0}


def main():
    env=dict(os.environ)
    apply=(env.get("FUNNEL_CREATE_MODE")=="apply")
    try:
        event_allowed(env,apply=apply)
        with httpx.Client(trust_env=False,follow_redirects=False) as client:
            out=ensure_funnel(client,env,apply=apply)
    except Exception:
        out={"status":"FUNNEL_CHECK_BLOCKED_REVIEW_REQUIRED",
             "crm_writes":0,"deal_funnel_created":False}
    print(json.dumps(out,ensure_ascii=False,sort_keys=True),flush=True)
    return 0 if out["status"] in (
        "CRM_ADMIN_REQUIRED_NO_WRITE",
        "DEAL_FUNNEL_ALREADY_PRESENT_READBACK_VERIFIED",
        "FUNNEL_ABSENT_ADMIN_SCOPED_READONLY",
        "NEW_SALES_DEAL_FUNNEL_CREATED_VERIFIED") else 2


if __name__=="__main__":
    raise SystemExit(main())
