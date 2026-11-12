"""V46: asynchronous Tavily Research PRO for new signed B2B tender dossiers.

The Research API is not Search: POST /research with model='pro' returns a
request_id; GET /research/{uuid} later returns citations and text.

All new tasks are source-fenced, once-ever and spend at most remaining included
plan credits (reserve worst-case 250 Pro credits until task completes).
Tavily Research has dynamic 15..250 credits/task. Never use pay-as-you-go
balance to authorize a task. Fail-closed on unverified key/plan/usage.

A research report is NOT independent proof of buyer/INN/deadlines. Only actual
public pages separately fetched under the existing HTTPS/robots/tender-ID
identity policy can become normal dossier source evidence.

No CRM writes, Bitrix stage changes, contacts, tasks, or raw secret logging.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from urllib.parse import urlsplit

import httpx

from .serviceit_vps_pg_queue import secret_file
from .serviceit_vps_worker import SEARCH_POLICY_START

MODEL = "pro"
MAX_PRO_CREDITS = 250
PER_MONTH_MAX_TASKS = 1000  # account's actual remaining included credits are authoritative
MAX_SOURCES = 24
MAX_REPORT_CHARS = 120000
API = "https://api.tavily.com"
TABLE = "serviceit_vps_b2b_tavily_research_pro_v46"
LOCK_ID = 693714610
SCOPED = re.compile(r"B2B-CENTER-([0-9]{5,12})\Z")
UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")
DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  source_hash CHAR(64) PRIMARY KEY,
  source_id VARCHAR(40) NOT NULL,
  card_id BIGINT NOT NULL,
  tender_id VARCHAR(12) NOT NULL,
  status VARCHAR(20) NOT NULL CHECK (
    status IN ('RESERVED','SUBMITTED','IN_PROGRESS','COMPLETED',
               'FAILED','UNCERTAIN')),
  request_id VARCHAR(40) UNIQUE,
  credits_reserved SMALLINT NOT NULL DEFAULT 250,
  credits_used SMALLINT,
  report TEXT,
  sources JSONB NOT NULL DEFAULT '[]'::jsonb,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (jsonb_typeof(sources)='array')
);
CREATE INDEX IF NOT EXISTS serviceit_vps_b2b_tavily_pro_started_idx
 ON {TABLE}(started_at);
"""


def approved(env):
    return (env.get("SERVICEIT_VPS_B2B_TAVILY_RESEARCH_PRO_ENABLED")=="YES"
            and env.get("SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED")=="YES"
            and env.get("SERVICEIT_VPS_WRITER_MODE")=="LIVE"
            and env.get("SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED")=="YES"
            and env.get("SERVICEIT_ROUTERAI_PRIVACY_APPROVED")=="YES_1062_21")


def _key(env):
    key=secret_file(env,"SERVICEIT_TAVILY_SEARCH_API_KEY",optional=True)
    if not isinstance(key,str) or not re.fullmatch(r"tvly-[!-~]{15,215}",key):
        return ""
    return key


def _auth(key):
    return {"Authorization":"Bearer "+key,"Accept":"application/json"}


def _json_response(response,expected_codes):
    if (response.status_code not in expected_codes or
        "application/json" not in response.headers.get("content-type","") or
        len(response.content)>350000):
        return None
    try:
        payload=response.json()
    except (ValueError,TypeError):
        return None
    return payload if isinstance(payload,dict) else None


def _usage(conn,client,key):
    # No monetary exposure on /usage. If provider access is 403, fail closed.
    payload=_json_response(client.get(API+"/usage",headers=_auth(key),timeout=15),
                           (200,))
    if payload is None:
        return None
    account=payload.get("account")
    if not isinstance(account,dict):
        return None
    limit,used=account.get("plan_limit"),account.get("plan_usage")
    if (type(limit) is not int or type(used) is not int
            or not 0<=used<=limit<=10000000):
        return None
    # Unfinished tasks might already consume plan credits asynchronously.
    # Reserve the WHOLE maximum cost per submitted/in-progress PRO request.
    reserved=conn.execute(f"""
        SELECT coalesce(sum(credits_reserved),0) FROM {TABLE}
        WHERE status IN ('RESERVED','SUBMITTED','IN_PROGRESS')
          AND started_at >= date_trunc('month',now())
    """).fetchone()[0]
    if not isinstance(reserved,int) or reserved<0:
        return None
    return max(0,limit-used-reserved)


def _notice(tender):
    match=SCOPED.fullmatch(tender)
    return match.group(1) if match else None


def _candidate(conn):
    rows=conn.execute(f"""
        SELECT b.card_id,b.source_id,b.notice_json,b.received_at
        FROM serviceit_vps_b2b_inbox b
        LEFT JOIN {TABLE} r ON r.source_id=b.source_id
        WHERE b.state='VERIFIED' AND b.received_at >= %s
          AND r.source_id IS NULL
          AND b.source_id ~ '^B2B-CENTER-[0-9]{{5,12}}
    """,(SEARCH_POLICY_START,)).fetchall()
    for card,source,notice,received in rows:
        tid=_notice(source)
        if (tid is None or not isinstance(notice,dict)
                or notice.get("source")!="B2B-Center"
                or notice.get("tender_id")!=tid):
            continue
        source_hash=hashlib.sha256(source.encode("ascii")).hexdigest()
        state=conn.execute(
            f"SELECT 1 FROM {TABLE} WHERE source_hash=%s",(source_hash,)).fetchone()
        if not state:
            return int(card),source,tid,source_hash
    return None


def _prompt(tid):
    # Do not send signed vendor emails, hidden CRM data or personal records.
    return (
        f"Глубоко исследуй публичную закупку B2B-Center №{tid}. "
        f"Найди точную карточку именно №{tid}, заказчика и организатора "
        "раздельно, полное юридическое наименование и ИНН, сроки, "
        "бюджет, регион и место поставки, предмет/ТЗ, объёмы, "
        "условия участия, оплаты, критерии оценки, гарантии, "
        "контакты только если они общедоступны, документы и "
        "основные риски для ИТ-поставщика. "
        "Для КАЖДОГО факта дай конкретный URL источника. "
        "Отличай организатора от заказчика. Не путай с другим "
        "тендером или номером лота. Если информация закрыта или "
        "не раскрыта публично — напиши «не подтверждено». "
        "Запрещено придумывать ИНН, суммы, даты и условия."
    )


def _submit(client,key,tid):
    payload=_json_response(client.post(
        API+"/research",headers={**_auth(key),"Content-Type":"application/json"},
        json={"input":_prompt(tid),"model":MODEL,"stream":False,
              "citation_format":"numbered","output_length":"long"},
        follow_redirects=False,timeout=25), (201,))
    request_id=payload.get("request_id") if payload else None
    return request_id if isinstance(request_id,str) and UUID.fullmatch(request_id) else None


def _sources(value):
    result=[]
    if not isinstance(value,list):
        return result
    for entry in value[:80]:
        if not isinstance(entry,dict):
            continue
        u=entry.get("url")
        if not isinstance(u,str) or len(u)>1000:
            continue
        try:
            p=urlsplit(u)
            if (p.scheme!="https" or not p.hostname or
                    p.username or p.password or p.fragment or
                    p.port not in (None,443)):
                continue
        except ValueError:
            continue
        if u not in result:
            result.append(u)
        if len(result)>=MAX_SOURCES:
            break
    return result


def _poll(conn,client,key,row):
    source_hash,task_id,state=row
    if not isinstance(task_id,str) or not UUID.fullmatch(task_id):
        return {"status":"UNRECOVERABLE_TASK_ID","writes":0}
    response=_json_response(client.get(
        API+"/research/"+task_id,headers=_auth(key),
        params={"include_usage":"true"},timeout=20), (200,202))
    if response is None or response.get("request_id")!=task_id:
        return {"status":"PROVIDER_STATUS_UNAVAILABLE","writes":0}
    current=response.get("status")
    if current in ("pending","in_progress"):
        with conn.transaction():
            conn.execute(f"UPDATE {TABLE} SET status='IN_PROGRESS',"
                         "updated_at=now() WHERE source_hash=%s "
                         "AND status IN ('SUBMITTED','IN_PROGRESS')",(source_hash,))
        return {"status":"RESEARCH_PRO_IN_PROGRESS","writes":0}
    if current=="failed":
        with conn.transaction():
            conn.execute(f"UPDATE {TABLE} SET status='FAILED',updated_at=now() "
                         "WHERE source_hash=%s AND status IN ('SUBMITTED','IN_PROGRESS')",
                         (source_hash,))
        return {"status":"RESEARCH_PRO_FAILED","writes":0}
    if current!="completed":
        return {"status":"PROVIDER_STATUS_UNKNOWN","writes":0}
    content=response.get("content")
    sources=_sources(response.get("sources"))
    credits=(response.get("usage") or {}).get("credits") if isinstance(response.get("usage"),dict) else None
    if (not isinstance(content,str) or len(content)>MAX_REPORT_CHARS
            or not 10<=len(content) or len(sources)==0
            or type(credits) is not int or not 0<=credits<=MAX_PRO_CREDITS):
        return {"status":"RESEARCH_PRO_OUTPUT_UNVERIFIED","writes":0}
    with conn.transaction():
        conn.execute(
            f"UPDATE {TABLE} SET status='COMPLETED',report=%s,"
            "sources=%s::jsonb,credits_used=%s,updated_at=now() "
            "WHERE source_hash=%s AND status IN ('SUBMITTED','IN_PROGRESS')",
            (content,json.dumps(sources),credits,source_hash))
    return {"status":"RESEARCH_PRO_STORED_UNVERIFIED","writes":0}


def run_once(env,*,preview=False):
    """One bounded step/5-minute cycle: collect existing task or create one.

    HTTP resources use a dedicated client to avoid hijacking other providers'
    per-card request hooks; paid submits have their OWN durable PG+usage fence.
    """
    if not approved(env):
        return {"status":"RESEARCH_PRO_DISABLED","writes":0}
    key=_key(env)
    if not key:
        return {"status":"RESEARCH_PRO_KEY_NOT_MOUNTED","writes":0}
    from .serviceit_vps_pg_queue import db
    with db(env) as conn, httpx.Client(
            trust_env=False,follow_redirects=False,timeout=20) as client:
        with conn.transaction():
            conn.execute(DDL)
        with conn.transaction():
            got=conn.execute(
                "SELECT pg_try_advisory_xact_lock(%s)",(LOCK_ID,)).fetchone()
            if not got or not got[0]:
                return {"status":"RESEARCH_PRO_BUSY","writes":0}
        # Use persistent advisory session lock for all HTTP+PG work.
        if not conn.execute(
                "SELECT pg_try_advisory_lock(%s)",(LOCK_ID,)).fetchone()[0]:
            return {"status":"RESEARCH_PRO_BUSY","writes":0}
        try:
            row=conn.execute(f"""
                SELECT source_hash,request_id,status FROM {TABLE}
                WHERE status IN ('SUBMITTED','IN_PROGRESS')
                ORDER BY started_at ASC LIMIT 1
            """).fetchone()
            if row is not None:
                if preview:
                    return {"status":"RESEARCH_PRO_PENDING_PREVIEW","writes":0}
                return _poll(conn,client,key,row)
            row=_candidate(conn)
            if not row:
                return {"status":"RESEARCH_PRO_IDLE","writes":0}
            card,source,tid,source_hash=row
            if preview:
                return {"status":"RESEARCH_PRO_ELIGIBLE_PREVIEW","writes":0}
            count=conn.execute(f"""
                SELECT count(*) FROM {TABLE}
                WHERE started_at >= date_trunc('month',now())
            """).fetchone()[0]
            if count>=PER_MONTH_MAX_TASKS:
                return {"status":"RESEARCH_PRO_MONTHLY_CAP","writes":0}
            try:
                available=_usage(conn,client,key)
            except httpx.HTTPError:
                available=None
            if available is None:
                return {"status":"RESEARCH_PRO_USAGE_UNREACHABLE","writes":0}
            if available<MAX_PRO_CREDITS:
                return {"status":"RESEARCH_PRO_INCLUDED_CREDITS_INSUFFICIENT","writes":0}
            with conn.transaction():
                reserved=conn.execute(f"""
                  INSERT INTO {TABLE}
                   (source_hash,source_id,card_id,tender_id,status)
                  VALUES (%s,%s,%s,%s,'RESERVED')
                  ON CONFLICT(source_hash) DO NOTHING RETURNING source_hash
                """,(source_hash,source,card,tid)).fetchone()
                if not reserved:
                    return {"status":"RESEARCH_PRO_ALREADY_RESERVED","writes":0}
            # After this line there is NO blind retry for network ambiguity.
            task_id=None
            try:
                task_id=_submit(client,key,tid)
            except httpx.HTTPError:
                pass
            with conn.transaction():
                conn.execute(
                    f"UPDATE {TABLE} SET status=%s,request_id=%s,"
                    "updated_at=now() WHERE source_hash=%s AND status='RESERVED'",
                    ("SUBMITTED" if task_id else "UNCERTAIN",task_id,source_hash))
            return {"status":("RESEARCH_PRO_SUBMITTED" if task_id else
                              "RESEARCH_PRO_PROVIDER_FAILED_NO_RETRY"),"writes":0}
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)",(LOCK_ID,))


def independently_verified_pages(conn,client,env,notice,browser=None):
    """Expose ONLY independently fetched public tender pages, not AI report."""
    if not approved(env) or not isinstance(notice,dict):
        return []
    tid=notice.get("tender_id")
    if not isinstance(tid,str) or not re.fullmatch(r"\d{5,12}",tid):
        return []
    source_hash=hashlib.sha256(("B2B-CENTER-"+tid).encode("ascii")).hexdigest()
    row=conn.execute(f"SELECT sources FROM {TABLE} "
                     "WHERE source_hash=%s AND status='COMPLETED'",
                     (source_hash,)).fetchone()
    if not row or not isinstance(row[0],list):
        return []
    from .serviceit_b2b_public_mirrors import (
        MIRRORS,_from_page_or_public_browser,_detail_score)
    subject=notice.get("subject")
    if not isinstance(subject,str) or not 10<=len(subject)<=240:
        return []
    result=[]
    for url in row[0][:MAX_SOURCES]:
        try:
            host=urlsplit(url).hostname
            if host not in MIRRORS:
                continue
            page=_from_page_or_public_browser(client,url,tid,subject,host,browser)
            if page and isinstance(page.get("excerpt"),str) and tid in page["excerpt"]:
                result.append(page)
        except (ValueError,httpx.HTTPError):
            continue
        if len(result)>=3:
            break
    return sorted(result,key=lambda z:-_detail_score(z.get("excerpt","")))
