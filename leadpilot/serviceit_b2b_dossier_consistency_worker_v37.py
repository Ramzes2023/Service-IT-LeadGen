"""Versioned, source-attested V37 passport/CRM consistency worker.

No new lead, no outreach, no task, stage, score or HOLD change. One signed
card per 300-second coordinator cycle. Uses dedicated PostgreSQL receipts;
external Bitrix write is durable-intent fenced and independently read back.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
import secrets

import httpx

from .bitrix_metadata_audit import validate_webhook
from .serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX
from .serviceit_b2b_dossier_v11 import LABELS, original_nine
from .serviceit_b2b_dossier_consistency_v37 import (
    prepare, reviewed_or_verified, manager_fields, PROVEN_QUOTE, REPORT, ReconcileBlocked,
    RELATED_PUBLIC, DECISION, MANAGER_PLACEHOLDER, MANAGER_HEADER, BRIEF, INN, manager_score_badge,
)
from .bitrix_1062_mapping import _is_valid_inn
from .serviceit_bitrix_list_shape import strict_bitrix_items
from .serviceit_kanban_presentation import SCORE_FIELD, VERSION_FIELD, BADGE_FIELD
from .serviceit_corporate_stop_inn import precheck as corporate_stop_precheck
from .serviceit_own_signal_stop import check as own_stop_check, read_rules
from .serviceit_qualification_proof_v2 import crm_inn_visibility_readonly
from .serviceit_vps_native_msp_result import verified_receipt
from .b2b_github_to_bitrix import exact_duplicate
from .serviceit_b2b_v19_backfill import active_card_write
from .serviceit_enrichment_worker import Rest, normalized, validate_identity, fingerprint
from .serviceit_vps_b2b_deep_research import safe_notice
from .serviceit_vps_b2b_dossier_v11_worker import LOCK_ID, compute_digest
from .serviceit_vps_pg_queue import secret_file
from .serviceit_vps_worker import live_permitted, exact_environment

ENABLED = "SERVICEIT_VPS_B2B_DOSSIER_CONSISTENCY_ENABLED"
TABLE = "serviceit_vps_b2b_dossier_consistency_v37_receipts"
DDL = """
CREATE TABLE IF NOT EXISTS serviceit_vps_b2b_dossier_consistency_v37_receipts (
 card_id BIGINT NOT NULL CHECK(card_id BETWEEN 8 AND 2000000000),
 attempt_no INTEGER NOT NULL CHECK(attempt_no BETWEEN 1 AND 1000000),
 source_hash CHAR(64) NOT NULL,
 status TEXT NOT NULL CHECK(status IN (
   'RESERVED','WRITE_INTENT','VERIFIED','NO_ATTESTED_NEW_FACTS',
   'INSUFFICIENT_CAPACITY','NONSTANDARD_REPORT','CONFLICT','UNCERTAIN')),
 before_fields JSONB NOT NULL,
 after_fields JSONB,
 salt BYTEA,
 digest BYTEA,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(card_id,attempt_no),
 CHECK ((salt IS NULL AND digest IS NULL) OR
        (octet_length(salt)=16 AND octet_length(digest)=32))
);
"""
RESULT = frozenset(("VERIFIED","NO_ATTESTED_NEW_FACTS","INSUFFICIENT_CAPACITY",
                    "NONSTANDARD_REPORT","CONFLICT","UNCERTAIN"))
# This is a standalone, explicitly audited subset update contract. The
# universal Rest deliberately permits only complete nine/twelve-field
# bundles, and must NOT be relaxed to support V41.
ALLOWED_PATCH = AI_FIELDS | {
    PREFIX+"COMPANY_INN", PREFIX+"COMPANY_NAME", PREFIX+"REGION",
    SCORE_FIELD, VERSION_FIELD, BADGE_FIELD, DECISION,
}
MAX_CRM_REPORT=12000
MAX_CRM_OTHER=4096

class V37Blocked(ValueError):
    pass


def need(value, name):
    if not value:
        raise V37Blocked(name)


def _patch_value(key, value):
    if key == SCORE_FIELD:
        return type(value) is int and 0 <= value <= 100
    if key == DECISION:
        return value == ""
    return isinstance(value, str) and bool(value.strip()) and len(value) <= (
        MAX_CRM_REPORT if key == REPORT else MAX_CRM_OTHER
    )


def verify_patch_schema(rest,patch):
    """Fail-closed against the real live Bitrix 1062 field definitions."""
    need(isinstance(patch,dict) and 0<len(patch)<=len(ALLOWED_PATCH)
         and set(patch)<=ALLOWED_PATCH
         and all(_patch_value(key, value) for key,value in patch.items()),
         "V41_SCOPED_UPDATE_FIELDS_INVALID")
    definitions=rest("crm.item.fields",{
        "entityTypeId":1062,"useOriginalUfNames":"Y"})["fields"]
    need(isinstance(definitions,dict),"V41_SCHEMA_READ_FAILED")
    for key in patch:
        detail=definitions.get(key)
        need(isinstance(detail,dict)
             and detail.get("type")==("integer" if key==SCORE_FIELD else "string")
             and detail.get("isReadOnly") is False
             and detail.get("isImmutable") in (None,False)
             and detail.get("isMultiple") in (None,False),
             "V41_SCHEMA_FIELD_NOT_WRITABLE")


def scoped_subset_update(rest,card,patch):
    """Only verified 1062/21 subset; do NOT weaken general Rest safety.

    Called only after verification of signed notice, snapshot, live field
    schema and durable PostgreSQL WRITE_INTENT. Zero retries on uncertainty.
    """
    need(isinstance(rest,Rest) and type(card) is int and 8<=card<=2_000_000_000
         and card not in (3,7),"V41_REST_ITEM_ID_INVALID")
    need(isinstance(patch,dict) and 0<len(patch)<=len(ALLOWED_PATCH)
         and set(patch)<=ALLOWED_PATCH
         and all(_patch_value(key, value) for key,value in patch.items()),
         "V41_SCOPED_UPDATE_FIELDS_INVALID")
    request={
        "entityTypeId":1062,"id":card,
        "useOriginalUfNames":"Y","fields":patch,
    }
    response=rest.client.post(rest.base+"/crm.item.update.json",json=request,
                              follow_redirects=False,timeout=20)
    need(response.status_code==200 and len(response.content)<=512000,
         "V41_BITRIX_UPDATE_HTTP_UNVERIFIED")
    try:
        obj=response.json()
    except ValueError:
        raise V37Blocked("V41_BITRIX_UPDATE_JSON_UNVERIFIED") from None
    need(isinstance(obj,dict) and "error" not in obj
         and isinstance(obj.get("result"),dict),
         "V41_BITRIX_UPDATE_REJECTED")
    return obj["result"]


def uncertain_no_effect_verified(conn,card,hash_,item):
    """Allow a fresh attempt only if a prior UNCERTAIN write was proven to
    have affected absolutely ZERO targeted fields. Do not alter old receipt.
    """
    row=conn.execute(f"""
       SELECT before_fields,after_fields,salt,digest
       FROM {TABLE}
       WHERE card_id=%s AND status='UNCERTAIN' AND source_hash=%s
       ORDER BY attempt_no DESC LIMIT 1
    """,(card,hash_)).fetchone()
    if row is None:
        return False
    old,desired,salt,proof=row
    if (not isinstance(old,dict) or not isinstance(desired,dict)
            or not old or set(old)!=set(desired)
            or not set(old)<=ALLOWED_PATCH
            or not isinstance(salt,(bytes,memoryview))
            or not isinstance(proof,(bytes,memoryview))):
        return False
    if (not secrets.compare_digest(_digest(desired,bytes(salt)),bytes(proof))
            or not all(item.get(key)==value for key,value in old.items())
            or not all(item.get(key)!=value for key,value in desired.items())):
        return False
    return True


def partial_badge_repair(conn, card, source_hash, item):
    row = conn.execute(f"""
        SELECT before_fields,after_fields,salt,digest FROM {TABLE}
        WHERE card_id=%s AND source_hash=%s AND status='UNCERTAIN'
        ORDER BY attempt_no DESC LIMIT 1
    """, (card,source_hash)).fetchone()
    if not row:
        return None
    before, desired, salt, digest = row
    if not (isinstance(before,dict) and isinstance(desired,dict)
            and set(before)==set(desired) and set(desired)<=ALLOWED_PATCH
            and isinstance(salt,(bytes,memoryview)) and isinstance(digest,(bytes,memoryview))
            and secrets.compare_digest(_digest(desired,bytes(salt)),bytes(digest))):
        return None
    legacy = desired.get(BADGE_FIELD)
    score = item.get(SCORE_FIELD)
    if not (isinstance(legacy,str) and legacy.startswith(("🟢", "🟡", "🔴"))
            and type(score) is int and 0 <= score <= 100
            and item.get(BADGE_FIELD) in ("",None)
            and item.get(BADGE_FIELD) == before.get(BADGE_FIELD)
            and isinstance(desired.get(BRIEF),str) and desired[BRIEF].startswith(MANAGER_HEADER)):
        return None
    if any(item.get(k) != v and not (k==DECISION and v=="" and item.get(k) is None)
           for k,v in desired.items() if k!=BADGE_FIELD):
        return None
    return {BADGE_FIELD:manager_score_badge(score)}


def _sha(source):
    need(type(source) is str and re.fullmatch(r"B2B-CENTER-\d{5,12}",source),
         "SIGNED_SOURCE_REQUIRED")
    return hashlib.sha256(source.encode("ascii")).hexdigest()


def eligible(conn, target_ids=None, *, refresh=False):
    """One due card, even if V19/V20 already VERIFIED: no stale-source replay."""
    targets = sorted(set(target_ids)) if target_ids is not None else []
    if target_ids is not None:
        need(targets and len(targets)<=4 and
             all(type(x) is int and 8<=x<=2000000000 for x in targets),
             "TARGET_SCOPE_INVALID")
    refresh_clause = ("OR last.status IN ('VERIFIED','NO_ATTESTED_NEW_FACTS',"
                      "'INSUFFICIENT_CAPACITY','NONSTANDARD_REPORT','UNCERTAIN')"
                      if refresh and targets else "")
    refresh_sources = (
        "serviceit_vps_b2b_tender_doc_receipts",
        "serviceit_vps_b2b_v19_research_receipts",
        "serviceit_vps_b2b_v20_revision_receipts",
    )
    evidence_refresh = ""
    for table in refresh_sources:
        table_row = conn.execute("SELECT to_regclass(%s)", ("public." + table,)).fetchone()
        if isinstance(table_row, tuple) and table_row[0]:
            evidence_refresh += f"""
                OR (last.status IN ('VERIFIED','NO_ATTESTED_NEW_FACTS','INSUFFICIENT_CAPACITY')
                    AND EXISTS (
                        SELECT 1 FROM {table} evidence
                        WHERE evidence.card_id=b.card_id AND evidence.status='VERIFIED'
                          AND evidence.source_hash=last.source_hash
                          AND evidence.updated_at>last.updated_at))
            """
    with conn.cursor() as cur:
        cur.execute(f"""
           SELECT b.card_id,b.source_id,b.notice_json,last.status,last.source_hash
           FROM serviceit_vps_b2b_inbox b
           JOIN serviceit_vps_b2b_dossier_v11_receipts v
             ON v.card_id=b.card_id AND v.status='VERIFIED'
           LEFT JOIN LATERAL (
             SELECT r.status,r.source_hash,r.updated_at
             FROM {TABLE} r WHERE r.card_id=b.card_id
             ORDER BY attempt_no DESC LIMIT 1
           ) last ON true
           WHERE b.state='VERIFIED' AND b.card_id IS NOT NULL
             AND b.received_at < now() - interval '20 minutes'
             AND (%s::BIGINT[] IS NULL OR b.card_id=ANY(%s::BIGINT[]))
             AND (last.status IS NULL
               {refresh_clause}
               {evidence_refresh}
               OR (last.status IN ('VERIFIED','NO_ATTESTED_NEW_FACTS')
                   AND EXISTS (
                       SELECT 1 FROM {TABLE} known
                       JOIN {TABLE} peer
                         ON peer.after_fields->>'{INN}'=known.after_fields->>'{INN}'
                       WHERE known.card_id=b.card_id AND known.status='VERIFIED'
                         AND peer.card_id<>b.card_id AND peer.status='VERIFIED'
                         AND peer.updated_at>last.updated_at))
               OR (last.status='VERIFIED'
                   AND last.updated_at < now() - interval '72 hours')
               OR (last.status IN (
                    'NO_ATTESTED_NEW_FACTS','INSUFFICIENT_CAPACITY',
                    'NONSTANDARD_REPORT')
                   AND last.updated_at < now() - interval '24 hours')
               OR (last.status='UNCERTAIN'
                   AND last.updated_at < now() - interval '15 minutes')
               -- A prior company conflict is never auto-replayed by the
               -- five-minute coordinator. Only an owner-scoped explicit
               -- signed-card apply may re-evaluate after code correction.
               OR (last.status='CONFLICT'
                   AND %s::BIGINT[] IS NOT NULL
                   AND b.card_id=ANY(%s::BIGINT[])))
             AND NOT EXISTS(
               SELECT 1 FROM serviceit_vps_native_receipts n
               WHERE n.card_id=b.card_id AND n.status IN ('RESERVED','WRITE_INTENT'))
           ORDER BY COALESCE(last.updated_at,b.received_at),
                    b.received_at,b.source_id LIMIT 1
        """,(targets if target_ids is not None else None,
              targets if target_ids is not None else None,
              targets if target_ids is not None else None,
              targets if target_ids is not None else None))
        return cur.fetchone()


def _v11_report(conn, card, source_hash):
    row = conn.execute("""
        SELECT after_fields,salt,digest FROM serviceit_vps_b2b_dossier_v11_receipts
        WHERE card_id=%s AND source_hash=%s AND status='VERIFIED'
    """, (card,source_hash)).fetchone()
    if not row or not isinstance(row[0],dict) or not row[1] or not row[2]:
        return ""
    fields, salt, digest = row
    if not secrets.compare_digest(compute_digest(fields,bytes(salt)),bytes(digest)):
        return ""
    return fields.get(REPORT,"")


def _v11_quotes(original, current_report):
    refs = dict(re.findall(r"(?m)^\[(?:ОРИГИНАЛ = )?(\d+)\] (https://[^\s]+)$",original))
    categories = {label:category for category,label in LABELS.items()}
    result = []
    for label, ref, quote in re.findall(r"(?m)^\d+\. (.+) \[(\d+)\]\n    «(.+)»$",original):
        if label in categories and ref in refs and quote in current_report:
            line = f"ПРОЧИТАННЫЙ ИСТОЧНИК / {categories[label]}: {quote} [{refs[ref]}]"
            if PROVEN_QUOTE.fullmatch(line):
                result.append(line)
    return result


def _v11_identity(report):
    """Use only explicit buyer/INN pairs written in the attested V11 dossier."""
    passport = report.split("01  ПАСПОРТ ЗАКУПКИ\n",1)
    if len(passport) != 2:
        return None
    passport = passport[1].split("\n02  ",1)[0]
    buyer = re.search(r"(?m)^Заказчик: ((?:АО|ООО|ПАО|ОАО) [^\n]{3,145}?) \(из публичного текста, не проверено ФНС\)$", passport)
    inn = re.search(r"(?m)^ИНН: (\d{10}|\d{12}) \(публичный кандидат; НЕ ФНС\)$", passport)
    if buyer and inn and _is_valid_inn(inn[1]):
        return {"buyer":buyer[1], "inn":inn[1]}
    registry = re.search(
        r"(?m)^ЗАКАЗЧИК ПО ИЗВЕЩЕНИЮ ЕИС\n((?:АО|ООО|ПАО|ОАО) [^\n]{3,145}?), ИНН (\d{10}|\d{12})\.\n"
        r"Реквизиты: (https://sprosradar\.ru/zakupki/223/(\d{11})/)\n"
        r"Сверка наименования: https://tendik\.ru/tender/eis223/\4(?:\n|$)",
        report,
    )
    if registry and _is_valid_inn(registry[2]):
        return {"buyer":registry[1], "inn":registry[2], "legal_url":registry[3]}
    return None


def verified_lines(conn,card,source_hash,report,original=None):
    """Only source quotes in signed VERIFIED receipts and the current report."""
    lines = _v11_quotes(_v11_report(conn,card,source_hash) if original is None else original,report)
    with conn.cursor() as cur:
        for name in ("serviceit_vps_b2b_v19_research_receipts",
                     "serviceit_vps_b2b_v20_revision_receipts"):
            cur.execute("SELECT to_regclass(%s)", ("public."+name,))
            if cur.fetchone()[0] is None:
                continue
            cur.execute(f"""
                SELECT after_fields FROM {name}
                WHERE card_id=%s AND status='VERIFIED' AND source_hash=%s
                ORDER BY updated_at DESC LIMIT 8
            """,(card,source_hash))
            for (fields,) in cur.fetchall():
                if not isinstance(fields,dict):
                    continue
                prior=fields.get(REPORT)
                if not isinstance(prior,str):
                    continue
                for line in prior.splitlines():
                    if (PROVEN_QUOTE.fullmatch(line.strip())
                            and line in report and line not in lines):
                        lines.append(line)
                    if len(lines)>=32:
                        return lines
    return lines


def _digest(patch,salt):
    need(type(patch) is dict and len(patch)>0 and
         type(salt) is bytes and len(salt)==16,
         "V37_DIGEST_INVALID")
    raw=json.dumps(patch,ensure_ascii=False,sort_keys=True,
                   separators=(",",":")).encode("utf-8")
    return hashlib.sha256(salt+raw).digest()


def reserve(conn,card,hash_,before):
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(f"""
                INSERT INTO {TABLE}
                (card_id,attempt_no,source_hash,status,before_fields)
                SELECT %s,COALESCE(MAX(attempt_no),0)+1,%s,'RESERVED',%s::jsonb
                FROM {TABLE} WHERE card_id=%s RETURNING attempt_no
            """,(card,hash_,json.dumps(before,ensure_ascii=False),card))
            row=cur.fetchone()
            need(row is not None and 1<=int(row[0])<=1000000,
                 "V37_RESERVE_FAILED")
            return int(row[0])


def mark(conn,card,attempt,status,patch=None):
    need(status in RESULT | {"WRITE_INTENT"}, "V37_STATUS_INVALID")
    with conn.transaction():
        with conn.cursor() as cur:
            if status == "WRITE_INTENT":
                need(type(patch) is dict and len(patch)>0,
                     "V37_PATCH_REQUIRED")
                salt=secrets.token_bytes(16)
                proof=_digest(patch,salt)
                cur.execute(f"""
                    UPDATE {TABLE}
                    SET status='WRITE_INTENT',after_fields=%s::jsonb,
                        salt=%s,digest=%s,updated_at=now()
                    WHERE card_id=%s AND attempt_no=%s AND status='RESERVED'
                    RETURNING attempt_no
                """,(json.dumps(patch,ensure_ascii=False),
                      salt,proof,card,attempt))
                need(cur.fetchone() is not None,"V37_WRITE_INTENT_NOT_DURABLE")
                return salt,proof
            previous = "WRITE_INTENT" if status in ("VERIFIED","UNCERTAIN") else "RESERVED"
            cur.execute(f"""
               UPDATE {TABLE} SET status=%s,updated_at=now()
               WHERE card_id=%s AND attempt_no=%s AND status=%s
               RETURNING attempt_no
            """,(status,card,attempt,previous))
            need(cur.fetchone() is not None,"V37_RECEIPT_STATE_CONFLICT")


def _get(rest,card):
    obj=normalized(rest("crm.item.get",{
        "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y"})["item"])
    validate_identity(obj,card)
    return obj


def _non_target_unchanged(before,after,changed):
    ignored=set(changed) | {
        "updatedTime","updatedBy","lastActivityTime","lastActivityBy"}
    return all(after.get(key)==value for key,value in before.items()
               if key not in ignored)


def _manager_checks(conn, client, env, webhook, base, card, source, inn):
    checks = {}
    try:
        matches = exact_duplicate(client, base, source)
        ids = {row["id"] for row in matches}
        if card in ids:
            checks["duplicate_hit"] = len(ids) > 1
            checks["duplicate"] = "да" if checks["duplicate_hit"] else "нет, по номеру этой закупки"
    except (ValueError, TypeError, KeyError, httpx.HTTPError):
        pass
    if conn.execute("SELECT to_regclass('public.serviceit_vps_b2b_tender_doc_receipts')").fetchone()[0]:
        row = conn.execute("""
            SELECT status,scanned_count FROM serviceit_vps_b2b_tender_doc_receipts
            WHERE card_id=%s AND source_hash=%s
        """,(card,_sha(source))).fetchone()
        if row and row[0] == "VERIFIED":
            checks["documents"] = f"прочитано файлов: {row[1]}; разбор в таймлайне"
        elif row and row[0] == "SCANNED_NO_DOCS":
            checks["documents"] = "доступные читаемые вложения пока не найдены"
        elif row and row[0] in {"WRITE_INTENT","UNCERTAIN","CONFLICT"}:
            checks["documents"] = "публикация анализа требует проверки"
    if not _is_valid_inn(inn):
        return checks
    try:
        result = crm_inn_visibility_readonly(client, webhook, inn)
        if result.get("visible_pages_complete") is True:
            present = result.get("visible_matches", 0) > 0
            checks["customer"] = ("есть реквизиты в видимой CRM; активность не подтверждена"
                                  if present else "в видимой CRM совпадений нет; полный список не подтверждён")
    except (ValueError, TypeError, KeyError, httpx.HTTPError):
        pass
    try:
        own, _ = own_stop_check(inn, read_rules(), card_id=card)
        corporate = corporate_stop_precheck(inn, env)
        hit = own == "HOLD_OWN_SIGNAL_STOP" or corporate == "HIT"
        checks["stop_hit"] = hit
        if hit:
            checks["stop"] = "да"
        elif corporate == "NO_MATCH_IN_INN_SCOPE_ONLY":
            checks["stop"] = "нет совпадения в проверенном перечне ИНН"
        elif own == "NOT_IN_OWN_SIGNAL_STOP":
            checks["stop"] = "нет в локальном списке; корпоративный список не подтверждён"
    except (ValueError, TypeError, OSError):
        pass
    if conn.execute("SELECT to_regclass('public.serviceit_vps_msp_snapshot')").fetchone()[0]:
        try:
            official = verified_receipt(conn, card, inn, datetime.now(timezone.utc))
            if official:
                checks["registry"] = "реестр МСП проверен; выписка ЕГРЮЛ не подтверждена"
        except (ValueError, TypeError):
            pass
    return checks


def _related_signals(rest, conn, card, notice, inn):
    if not _is_valid_inn(inn):
        return []
    tid = notice["tender_id"]
    signals = {other: f"№{other} · {name} · {url}"
               for other,name,url in RELATED_PUBLIC.get(inn, ()) if other != tid}
    # Exact CRM matches nominate candidates. Signed intake and a fresh item
    # read still have to establish the same buyer and immutable source.
    peers = set()
    try:
        result = rest("crm.item.list", {
            "entityTypeId": 1062, "useOriginalUfNames": "Y",
            "filter": {"categoryId": 21, INN: inn},
            "select": ["id", "categoryId", INN], "start": 0,
        })
        for row in strict_bitrix_items(result):
            raw_id = str(row.get("id"))
            if (raw_id.isascii() and raw_id.isdecimal() and 8 <= int(raw_id) <= 2_000_000_000
                    and int(raw_id) != card and row.get(INN) == inn
                    and str(row.get("categoryId")) == "21"):
                peers.add(int(raw_id))
    except (ValueError, TypeError, KeyError, httpx.HTTPError):
        pass
    rows = conn.execute(f"""
        SELECT b.card_id,b.source_id,b.notice_json
        FROM serviceit_vps_b2b_inbox b
        WHERE b.state='VERIFIED' AND b.card_id<>%s
          AND (b.card_id=ANY(%s::BIGINT[]) OR b.source_id=ANY(%s::TEXT[])
               OR EXISTS (
                   SELECT 1 FROM {TABLE} receipt
                   WHERE receipt.card_id=b.card_id AND receipt.status='VERIFIED'
                     AND receipt.after_fields->>(%s::text)=%s))
        ORDER BY b.received_at DESC LIMIT 50
    """, (card, sorted(peers), ["B2B-CENTER-" + tender for tender in signals],
           INN, inn)).fetchall()
    for peer, source, original in rows:
        if not isinstance(original,dict):
            continue
        other = original.get("tender_id")
        if not isinstance(other, str) or other == tid:
            continue
        try:
            item = _get(rest, peer)
            if item.get(INN) != inn:
                continue
            url, title = safe_notice(original, peer, source, item)
            signal = signals.get(other, f"№{other} · {title[:180]} · {url}")
            if not signal.startswith("Карточка №"):
                signals[other] = f"Карточка №{peer} · {signal}"
        except (ValueError, TypeError, KeyError, httpx.HTTPError):
            continue
    return list(signals.values())[:5]


def fresh_primary_evidence(notice, item, client, env):
    """Fill missing passport data from the newly read exact primary source.

    No model or CRM writes. The caller's V37 intent/readback receipt records
    all resulting fields; manager decisions remain out of scope.
    """
    from .serviceit_vps_b2b_deep_research import (
        verified_original_b2b_details, public_buyer_candidate,
        followup_public_buyer_research, published_buyer_inn_evidence, _self_buyer,
    )
    from .serviceit_b2b_routerai_deep import source_only_investigation
    from .serviceit_b2b_dossier_consistency_v37 import _record_from_verified

    found = verified_original_b2b_details(
        {"status": "PUBLIC_SOURCE_NOT_READABLE", "pages": []}, notice, client)
    if not found.get("original_b2b_detail_independently_read"):
        return {}, []
    key = secret_file(env, "SERVICEIT_BRAVE_SEARCH_API_KEY", optional=True) or ""
    try:
        found = followup_public_buyer_research(found, env, client, key)
    except (ValueError, httpx.HTTPError):
        pass
    buyer = public_buyer_candidate(found)
    identity = {"buyer": buyer, "tender_url": notice["source_url"]} if buyer else {}
    if any(_self_buyer(row) == buyer for row in found.get("pages", []) if buyer):
        identity["organizer"] = buyer + " (закупка для собственных нужд)"
    inn = published_buyer_inn_evidence(found)
    if inn:
        identity.update(inn=inn["inn"], legal_url=inn["public_source"])
    facts = source_only_investigation(found)["facts"]
    lines = [f'ПРОЧИТАННЫЙ ИСТОЧНИК / {f["category"]}: {f["quote"]} [{f["url"]}]'
             for f in facts]
    record = _record_from_verified(notice, item, lines, identity) or {}
    record = {k: v for k, v in record.items() if v and k != "reviewed"}
    if found.get("document_access") == "AUTH_REQUIRED":
        record["document_access"] = "AUTH_REQUIRED"
        record["documents"] = "Оригиналы на B2B доступны после входа; полное ТЗ не прочитано"
    record["_fresh_source_lines"] = lines[:12]
    return record, lines


def run_once(env,client,conn,*,target_ids=None,preview=False,refresh=False,search_scope=None):
    """Never mutate on preview; one card per normal cycle; versioned receipts."""
    if env.get(ENABLED)!="YES":
        return {"status":"V37_DISABLED","crm_writes":0}
    need(live_permitted(env)
         and env.get("SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED")=="YES"
         and env.get("SERVICEIT_ROUTERAI_PRIVACY_APPROVED")=="YES_1062_21",
         "V37_PRIVACY_GATES_MISSING")
    # Do not permit a pending V36 code-only deployment to change
    # reviewed source evidence/authorizations inside the same call.
    exact=exact_environment(env)
    if preview:
        # A read-only preview must not CREATE TABLE or mutate any receipt.
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s)", ("public." + TABLE,))
            if cur.fetchone()[0] is None:
                return {"status": "V37_PREVIEW_LEDGER_NOT_READY",
                        "crm_writes": 0}
    else:
        conn.execute(DDL)
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)",(LOCK_ID,))
        locked=bool(cur.fetchone()[0])
    if not locked:
        return {"status":"V37_OTHER_DOSSIER_WORKER_ACTIVE","crm_writes":0}
    try:
        row=eligible(conn,target_ids=target_ids,refresh=True) if refresh else eligible(conn,target_ids=target_ids)
        if not row:
            return {"status":"V37_NO_DUE_SIGNED_CARD","crm_writes":0}
        card,source,notice,last_status,last_hash=row
        source_hash=_sha(source)
        if last_hash and last_hash!=source_hash:
            return {"status":"V37_SOURCE_CHANGED_NO_REPLAY","crm_writes":0}
        if (not isinstance(notice,dict)
                or notice.get("tender_id")!=source.split("-")[-1]
                or notice.get("source")!="B2B-Center"):
            return {"status":"V37_ORIGINAL_IDENTITY_CONFLICT","crm_writes":0}
        webhook=secret_file(env,"SERVICEIT_BITRIX_WEBHOOK")
        validate_webhook(webhook)
        rest=Rest(webhook,client)
        first=_get(rest,card)
        src_url,title=safe_notice(notice,card,source,first)
        need(src_url==notice["source_url"] and bool(title),
             "V37_SIGNED_IDENTITY_CONFLICT")
        badge_repair = partial_badge_repair(conn,card,source_hash,first) if last_status=="UNCERTAIN" else None
        if last_status=="UNCERTAIN" and not badge_repair and not uncertain_no_effect_verified(
                conn,card,source_hash,first):
            return {"status":"V41_UNCERTAIN_RECONCILIATION_BLOCKED",
                    "crm_writes":0}
        before=original_nine(first)
        original_v11 = _v11_report(conn,card,source_hash)
        lines=verified_lines(conn,card,source_hash,before[REPORT],original_v11)
        try:
            if badge_repair:
                patch,meta=badge_repair,{"status":"DRAFT"}
            else:
                identity = _v11_identity(original_v11)
                prior = reviewed_or_verified(notice,first,lines,identity) or {}
                if (exact.get("SERVICEIT_VPS_B2B_PRIMARY_DETAIL_ENABLED") == "YES"
                        and not all(prior.get(k) for k in ("buyer","inn","deadline","delivery"))):
                    from contextlib import nullcontext
                    # The coordinator's client otherwise rejects every
                    # search API call outside V11. Only the first new-card
                    # pass may open a bounded scope; later refreshes use
                    # direct public sources without recurring search costs.
                    scope = (search_scope(card,source) if callable(search_scope)
                             and last_status is None and not preview
                             else nullcontext())
                    with scope:
                        fresh_identity, fresh_lines = fresh_primary_evidence(notice,first,client,exact)
                    if fresh_identity:
                        identity = {**(identity or {}), **fresh_identity}
                        lines = fresh_lines + lines
                patch,meta=prepare(notice,first,lines,identity)
                record=reviewed_or_verified(notice,first,lines,identity)
                inn=(record or {}).get("inn") or first.get(INN)
                checks=_manager_checks(conn,client,exact,webhook,rest.base,card,source,inn)
                if (record or {}).get("document_access") == "AUTH_REQUIRED":
                    checks["documents"] = "B2B требует входа для оригиналов; " + checks.get("documents", "")
                related=_related_signals(rest,conn,card,notice,inn)
                base_patch=patch or {}
                revised=manager_fields(notice,{**first,**base_patch},record,checks,related)
                patch={key:value for key,value in {**base_patch,**revised}.items() if first.get(key)!=value}
                if not patch:
                    patch=None
        except ReconcileBlocked:
            if preview:
                return {"status":"V37_PREVIEW_CONFLICT_NO_WRITE",
                        "crm_writes":0, "card":card, "draft_ready":False}
            # A persistent legal/company mismatch must NOT starve every
            # subsequent signed tender for repeated five-minute cycles.
            # Record only a dedicated PG conflict receipt; never call CRM.
            if active_card_write(conn,card):
                return {"status":"V37_NATIVE_WRITE_IN_FLIGHT","crm_writes":0}
            attempt=reserve(conn,card,source_hash,{})
            mark(conn,card,attempt,"CONFLICT")
            return {"status":"V37_PREPARE_CONFLICT_NO_WRITE",
                    "crm_writes":0}
        if preview:
            return {"status":"V37_READONLY_PREVIEW","crm_writes":0,
                    "card":card,"draft_ready":bool(patch),
                    "verified_lines":len(lines),
                    "structured_candidates":meta.get("structured_candidates",0),
                    "preview_result":meta.get("status","UNKNOWN")}
        if active_card_write(conn,card):
            return {"status":"V37_NATIVE_WRITE_IN_FLIGHT","crm_writes":0}
        if patch is None:
            reason=meta.get("status")
            result=("INSUFFICIENT_CAPACITY" if reason=="CRM_FIELD_CAPACITY_INSUFFICIENT"
                    else "NONSTANDARD_REPORT" if reason=="NONSTANDARD_REPORT_MANUAL_REVIEW"
                    else "NO_ATTESTED_NEW_FACTS")
            attempt=reserve(conn,card,source_hash,{})
            mark(conn,card,attempt,result)
            return {"status":"V37_"+result,"crm_writes":0}
        try:
            verify_patch_schema(rest,patch)
        except (V37Blocked, KeyError, TypeError):
            # Treat missing or non-writable CRM fields as a conflict; do not
            # persist a WRITE_INTENT or endlessly select this first card.
            if active_card_write(conn,card):
                return {"status":"V37_NATIVE_WRITE_IN_FLIGHT","crm_writes":0}
            attempt=reserve(conn,card,source_hash,{})
            mark(conn,card,attempt,"CONFLICT")
            return {"status":"V41_SCHEMA_BLOCKED_NO_WRITE","crm_writes":0}
        fresh=_get(rest,card)
        if (fingerprint(first)!=fingerprint(fresh)
                or active_card_write(conn,card)
                or any(fresh.get(k)!=first.get(k) for k in patch)
                or (DECISION in patch and fresh.get(DECISION)!=MANAGER_PLACEHOLDER)):
            return {"status":"V37_PREWRITE_CHANGED_NO_WRITE","crm_writes":0}
        attempt=reserve(conn,card,source_hash,
                        {k:first.get(k) for k in patch})
        salt,proof=mark(conn,card,attempt,"WRITE_INTENT",patch)
        try:
            scoped_subset_update(rest,card,patch)
            after=_get(rest,card)
            need(all(after.get(k)==v or (k==DECISION and v=="" and after.get(k) is None)
                     for k,v in patch.items())
                 and _non_target_unchanged(fresh,after,patch)
                 and secrets.compare_digest(_digest(patch,salt),proof),
                 "V37_READBACK_FAILED")
            mark(conn,card,attempt,"VERIFIED")
            return {"status":"V37_CRM_RECONCILED_VERIFIED","crm_writes":1,
                    "fields_updated":len(patch),
                    "public_reviewed":meta.get("reviewed") is True}
        except Exception:
            try:
                mark(conn,card,attempt,"UNCERTAIN")
            except Exception:
                pass
            return {"status":"V37_UNCERTAIN_NO_REPLAY","crm_writes":"UNVERIFIED"}
    finally:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)",(LOCK_ID,))
