"""Write ONLY a formally verified 0-100 score for any FUTURE ServiceIT card.

The external corporate verifier must supply independently signed nine-gate
evidence bound to the current exact 1062/21 CRM before-image (FNS, MСП,
source, customer/dedup, STOP, legal, contact, manager criteria). Missing
evidence always means HOLD, never an invented numeric score or CRM write.

Uses a separate encrypted one-use GitHub CAS ledger before any update.
Ambiguous REST writes never replay. No other CRM fields, stages, tasks, user
records, business pipelines, source adapters or customer contact touched.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re

import httpx

from .serviceit_stage3_all_new_task_automation import (
    CUTOVER, TaskAutomationBlocked, rest, card_read, scan_cards,
)
from .serviceit_bitrix_dispatch_worker import (
    verify_event, EncryptedEventFence, REPOSITORY, PORTAL, BRANCH,
)
from .serviceit_bitrix_poll_fallback import after_activation, discover, date_value
from .bitrix_metadata_audit import validate_webhook
from .serviceit_enrichment_worker import fingerprint, normalized, validate_identity
from .serviceit_kanban_presentation import (
    SCORE_FIELD, VERSION_FIELD, BADGE_FIELD, score_label,
)
from .serviceit_stage4_live_proof_readonly import review_signed_card_readonly

GATE = "YES_1062_21_SIGNED_ONLY"
FOLDER = ".serviceit/verified-score-once"
FIELDS = frozenset((SCORE_FIELD, VERSION_FIELD, BADGE_FIELD))

class ScoreBlocked(ValueError):
    pass

def require(ok, code):
    if not ok:
        raise ScoreBlocked(code)

class ScoreFence(EncryptedEventFence):
    def url(self, card):
        require(type(card) is int and 8<=card<=2_000_000_000
                and card not in (3,7),"SCORE_CARD_ID_INVALID")
        digest=hmac.new(self.path_key,
                       f"stage4-formal-score-v1:1062:21:{card}".encode(),
                       sha256).hexdigest()
        return (f"https://api.github.com/repos/{REPOSITORY}/contents/"
                f"{FOLDER}/{digest}.enc")

def proof_for_card(env, card):
    raw=env.get("SERVICEIT_STAGE4_CORPORATE_RECEIPTS_JSON","")
    key=env.get("SERVICEIT_STAGE4_VERIFIER_KEY","")
    if not raw or not key:
        return None, None
    require(len(raw)<=256_000 and len(key)>=32,
            "CORPORATE_VERIFIER_SECRET_INVALID")
    try:
        proofs=json.loads(raw)
    except (ValueError,TypeError):
        raise ScoreBlocked("CORPORATE_RECEIPTS_JSON_INVALID") from None
    require(type(proofs) is dict and len(proofs)<=250
            and all(type(x) is str
                    and re.fullmatch(r"[1-9][0-9]{0,9}",x)
                    for x in proofs),"CORPORATE_RECEIPTS_NOT_INDEXED_BY_CARD")
    return proofs.get(str(card)),key

def score_schema_ok(client, base):
    data=rest(client,base,"crm.item.fields",{
        "entityTypeId":1062,"useOriginalUfNames":"Y",
    })
    require(type(data) is dict and type(data.get("fields")) is dict,
            "SCORE_FIELDS_SCHEMA_MISSING")
    fields=data["fields"]
    for code in FIELDS:
        row=fields.get(code)
        require(type(row) is dict and row.get("isReadOnly") is False
                and row.get("isMultiple") in (None,False)
                and row.get("type")==("integer" if code==SCORE_FIELD else "string"),
                "SCORE_FIELD_TYPE_OR_PERMISSION")
    return True

def evaluate_one(client, env, card, *, now=None):
    now=now or datetime.now(timezone.utc)
    require(type(card) is int and 8<=card<=2_000_000_000
            and card not in (3,7),"SCORE_CARD_OUTSIDE_SCOPE")
    require(env.get("SERVICEIT_STAGE4_SIGNED_SCORE_WRITE_ENABLED")==GATE
            and env.get("GITHUB_REPOSITORY")==REPOSITORY
            and env.get("GITHUB_REF")=="refs/heads/main",
            "SCORE_OWNER_OR_REPO_GATE_MISSING")
    receipt,key=proof_for_card(env,card)
    if receipt is None:
        return "HOLD_NO_SIGNED_PROOF"
    base=validate_webhook(env.get("SERVICEIT_BITRIX_WEBHOOK",""))
    require(base.startswith(f"https://{PORTAL}/rest/"),"SCORE_PORTAL_INVALID")
    before=card_read(client,base,card,now=now)
    require(all(before.get(code) in (None,"") for code in FIELDS),
            "SCORE_ALREADY_WRITTEN_OR_UNREADABLE")
    decision=review_signed_card_readonly(
        client,env["SERVICEIT_BITRIX_WEBHOOK"],card,receipt,key,now=now)
    score=decision.get("formal_lead_score")
    if (decision.get("status") not in
            ("MANAGER_REVIEW_REQUIRED","VACANCY_ONLY_MANAGER_REVIEW")
            or type(score) is not int or not 0<=score<=100
            or decision.get("crm_writes") != 0):
        return "HOLD_PROOF_NOT_VERIFIED"
    # A vacancy must never be converted to a qualified sales lead.
    if decision["status"]=="VACANCY_ONLY_MANAGER_REVIEW":
        return "VACANCY_REVIEW_ONLY_NO_FORMAL_SALES_SCORE"
    version=decision.get("score_version")
    require(type(version) is str and 1<=len(version)<=60,
            "SCORE_VERSION_INVALID")
    score_schema_ok(client,base)
    latest=card_read(client,base,card,now=now)
    require(fingerprint(latest)==fingerprint(before),
            "CARD_CHANGED_DURING_SCORE_REVIEW")
    output={SCORE_FIELD:score,VERSION_FIELD:version,
            BADGE_FIELD:score_label(score)}
    require(set(output)==FIELDS,"SCORE_FIELDS_NOT_EXACT")
    fence=ScoreFence(client,env.get("GITHUB_TOKEN",""),
                     env.get("SERVICEIT_GH_LEDGER_KEY",""))
    reserved=fence.reserve(card)
    if reserved!="RESERVED":
        return "ALREADY_FENCED_SCORE_NO_RETRY"
    try:
        rest(client,base,"crm.item.update",{
            "entityTypeId":1062,"id":card,
            "useOriginalUfNames":"Y","fields":output,
        })
        result=rest(client,base,"crm.item.get",{
            "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y",
        })
        require(type(result) is dict and type(result.get("item")) is dict,
                "SCORE_READBACK_INVALID")
        after=normalized(result["item"])
        validate_identity(after,card)
        require(all(after.get(code)==val for code,val in output.items()),
                "SCORE_EXACT_READBACK_FAILED")
        ignored=set(FIELDS)|{"updatedTime","updatedBy","lastActivityTime","lastActivityBy"}
        require(all(after.get(k)==v for k,v in latest.items() if k not in ignored),
                "SCORE_NON_TARGET_CHANGED")
    except Exception:
        fence.finalize(card,"UNCERTAIN")
        return "SCORE_WRITE_UNCERTAIN_NO_RETRY"
    fence.finalize(card,"VERIFIED")
    return "FORMAL_SCORE_EXACT_READBACK_VERIFIED"

def scan(client,env,*,now=None):
    now=now or datetime.now(timezone.utc)
    require(env.get("SERVICEIT_STAGE4_SIGNED_SCORE_WRITE_ENABLED")==GATE,
            "SCORE_OWNER_GATE_MISSING")
    raw=env.get("SERVICEIT_STAGE4_CORPORATE_RECEIPTS_JSON","")
    if not raw or not env.get("SERVICEIT_STAGE4_VERIFIER_KEY"):
        return {"status":"HOLD_NO_CORPORATE_VERIFIER","candidates":0,
                "score_writes_verified":0,"crm_writes":0}
    base=validate_webhook(env.get("SERVICEIT_BITRIX_WEBHOOK",""))
    require(base.startswith(f"https://{PORTAL}/rest/"),"SCORE_PORTAL_INVALID")
    start=max(CUTOVER,after_activation(env,now))
    # Reuse the already tested Bitrix list implementation, including TOP-
    # LEVEL next/total preservation. It runs the same read-only scan.
    def list_only(method,args):
        require(method=="crm.item.list" and type(args) is dict
                and args.get("entityTypeId")==1062
                and args.get("useOriginalUfNames")=="N"
                and args.get("filter",{}).get("categoryId")==21,
                "SCORE_SCAN_SCOPE_INVALID")
        response=client.post(base+"/crm.item.list.json",json=args,
                             timeout=25,follow_redirects=False)
        require(response.status_code==200 and len(response.content)<=512000,
                "SCORE_SCAN_HTTP_INVALID")
        body=response.json()
        require(type(body) is dict and "error" not in body
                and type(body.get("result")) is dict,
                "SCORE_SCAN_RESPONSE_INVALID")
        result=body["result"]
        for name in ("next","total"):
            if name in body:
                require(name not in result or result[name]==body[name],
                        "SCORE_SCAN_PAGINATION_CONFLICT")
                result[name]=body[name]
        return result
    cards=discover(list_only,start,now)
    result={"status":"STAGE4_SIGNED_CATCHUP","candidates":len(cards),
            "score_writes_verified":0,"hold":0,"uncertain":0}
    for card in cards:
        try:
            status=evaluate_one(client,env,card,now=now)
        except Exception:
            status="HOLD_OR_READBACK_BLOCKED"
        if status=="FORMAL_SCORE_EXACT_READBACK_VERIFIED":
            result["score_writes_verified"]+=1
        elif status=="SCORE_WRITE_UNCERTAIN_NO_RETRY":
            result["uncertain"]+=1
        else:
            result["hold"]+=1
    return result

def main():
    env=dict(os.environ)
    try:
        require(env.get("GITHUB_EVENT_NAME") in ("repository_dispatch","schedule")
                and env.get("GITHUB_REPOSITORY")==REPOSITORY
                and env.get("GITHUB_REF")=="refs/heads/main",
                "SCORE_EVENT_OR_REPO_INVALID")
        with httpx.Client(trust_env=False,follow_redirects=False) as client:
            if env["GITHUB_EVENT_NAME"]=="repository_dispatch":
                path=Path(env.get("GITHUB_EVENT_PATH",""))
                require(path.is_file() and 0<path.stat().st_size<=256000,
                        "SIGNED_EVENT_FILE_INVALID")
                event=json.loads(path.read_text("utf8"))
                card=verify_event(event,env.get("SERVICEIT_BITRIX_EVENT_BRIDGE_KEY",""))
                status=evaluate_one(client,env,card)
                result={"status":status,
                        "score_write_verified":status=="FORMAL_SCORE_EXACT_READBACK_VERIFIED",
                        "task_created":False,"customer_contacted":False}
            else:
                result={**scan(client,env),"customer_contacted":False}
        print(json.dumps(result,sort_keys=True))
        return 0
    except Exception:
        print(json.dumps({"status":"STAGE4_HOLD_NO_UNVERIFIED_SCORE",
                          "score_write_verified":False,
                          "customer_contacted":False}))
        return 2

if __name__=="__main__":
    raise SystemExit(main())
