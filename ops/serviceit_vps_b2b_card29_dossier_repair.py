"""Guarded one-use real card #29 dossier repair, source-only, no paid model.

The signed official B2B notice is checked against PostgreSQL and CRM.
No source retry / blind CRM replay; nine-field old snapshot and write intent
are persisted BEFORE any Bitrix update. Never print customer text or tokens.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sys
from datetime import datetime, timezone

import httpx

from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX
from leadpilot.serviceit_b2b_routerai_deep import public_documents, b2b_primary_detail_facts
from leadpilot.serviceit_vps_b2b_deep_research import (
    verified_original_b2b_details, safe_notice
)
from leadpilot.serviceit_enrichment_worker import (
    Rest, normalized, validate_identity, fingerprint
)
from leadpilot.serviceit_vps_pg_queue import db, secret_file
from leadpilot.serviceit_vps_worker import exact_environment, live_permitted
from leadpilot.bitrix_metadata_audit import validate_webhook

CARD = 29
SOURCE = "B2B-CENTER-4677923"
TABLE = "serviceit_vps_b2b_card29_primary_v1_receipt"
from leadpilot.serviceit_vps_b2b_dossier_v11_worker import LOCK_ID as DOSSIER_LOCK_ID
LOCK_ID = DOSSIER_LOCK_ID
STATUS = PREFIX + "AI_STATUS"
REPORT = PREFIX + "AI_FULL_REPORT"
BRIEF = PREFIX + "AI_MANAGER_BRIEF"
SUMMARY = PREFIX + "AI_SUMMARY"
DDL = """
CREATE TABLE IF NOT EXISTS serviceit_vps_b2b_card29_primary_v1_receipt (
 card_id BIGINT PRIMARY KEY CHECK(card_id=29),
 source_hash CHAR(64) NOT NULL,
 status TEXT NOT NULL CHECK(status IN
   ('RESERVED','WRITE_INTENT','VERIFIED','CONFLICT','UNCERTAIN')),
 before_fields JSONB NOT NULL,
 after_fields JSONB,
 salt BYTEA,
 digest BYTEA,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


class Blocked(RuntimeError):
    pass


def need(ok, reason):
    if not ok:
        raise Blocked(reason)


def emit(name, value):
    value = str(value).upper()
    if re.fullmatch(r"[A-Z0-9_]{1,90}", value):
        print("CARD29_REPAIR_" + name + "=" + value, flush=True)


def chosen(facts, category, *, marker=""):
    for row in facts:
        if row.get("category") == category and marker.casefold() in row.get("quote", "").casefold():
            quote = row["quote"]
            need(isinstance(quote, str) and 15 <= len(quote) <= 220, "FACT_UNVERIFIED")
            return quote
    return ""


def prepare(facts, docs, original, url):
    """Pure deterministic source-cited manager-friendly nine-field patch."""
    need(len(docs) == 1 and docs[0]["url"] == url
         and 30 <= len(docs[0]["text"]) <= 4800, "SOURCE_DOCUMENT_NOT_SINGLE_OR_VERIFIED")
    text = docs[0]["text"]
    deadline = chosen(facts, "DEADLINE", marker="20.11.2026")
    payment = chosen(facts, "PAYMENT", marker="отсроч")
    delivery = chosen(facts, "LOCATION", marker="электронная")
    address = chosen(facts, "LOCATION", marker="Москва")
    organizer = re.search(r'(?iu)\bорганизатор\s+(АО\s+(?:«АЗОТ-ВЗРЫВ»|"АЗОТ-ВЗРЫВ"))', text)
    need(deadline and "13:59" in deadline, "PUBLIC_DEADLINE_NOT_FOUND")
    need(payment and delivery and address and "Можайский" in address,
         "PUBLIC_PAYMENT_DELIVERY_INCOMPLETE")
    need(organizer is not None, "PUBLIC_ORGANIZER_NOT_VERIFIED")
    need("4677923" in text and "teams" in text.casefold(), "PUBLIC_TITLE_ID_NOT_VERIFIED")

    quantity = re.search(r"(?iu)количество\s+позиций\s*[—: -]*\s*3\b", text)
    participation = [x for x in (
        "Подача заявок по всем позициям лота",
        "Подача альтернативных заявок",
        "Подача аналогов",
        "Проведение переторжки",
        "Досрочное завершение приёма заявок и документов организатором",
    ) if x.casefold() in text.casefold()]
    full_old = original[REPORT]
    need(isinstance(full_old,str)
         and ("ТЕНДЕРНОЕ ДОСЬЕ V11" in full_old
              or full_old.startswith("СЕРВИС ИТ | V37"))
         and 700 <= len(full_old) <= 9000
         and "ПЕРВИЧНЫЙ ИСТОЧНИК · ОБНОВЛЕНО" not in full_old,
         "OLDER_DOSSIER_NOT_UNMODIFIED")
    organizer_name = organizer.group(1)
    overview = [
        "СЕРВИС ИТ | №4677923 | КЛЮЧЕВАЯ ИНФОРМАЦИЯ",
        "ПЕРВИЧНЫЙ ИСТОЧНИК · ОБНОВЛЕНО И ПРОВЕРЕНО АВТОМАТИЧЕСКИ",
        "Предмет: приобретение лицензий Microsoft Teams (продление)",
        "Организатор по публичной странице: " + organizer_name,
        "Заказчик: отдельно не подтверждён (организатор не приравнивается автоматически)",
        "ИНН заказчика: нет проверенной юридической связки",
        "Количество позиций в лоте: " + ("3 (не количество лицензий)" if quantity else "не подтверждено"),
        "Срок подачи [публичный первоисточник]: " + deadline,
        "Бюджет: не раскрыт в открытой карточке; НЕ 0 ₽",
        "Место исполнения: " + address,
        "Способ поставки: " + delivery,
        "Условия оплаты: " + payment,
        "Тип процедуры: проверить оригинальную публикацию",
        "Lead Score: прежняя оценка сохраняется без пересчёта",
        "AI-статус: исходное исследование RouterAI; источник дополнительно проверен",
        "",
        "ПОДАЧА И УЧАСТИЕ (публичная страница)",
    ]
    overview += ["• " + x for x in participation] or ["• проверить условия в оригинале"]
    overview += [
        "",
        "ДОКУМЕНТАЦИЯ И РИСКИ",
        "Публичные документы модуль TDI не обнаружил; детальные SKU и количество лицензий не подтверждены.",
        "Не установлены ИНН именно заказчика, договор/ТЗ, полная цена и контакт уполномоченного лица.",
        "Рекомендация: запросить официальные материалы, проверить допустимость поставки и условия лицензирования.",
        "Коммерческий контакт, статус сделки, STOP и решение менеджера НЕ изменялись.",
        "",
        "ПЕРВОИСТОЧНИК: " + url,
        "",
        "АРХИВ РАННЕГО АВТОМАТИЧЕСКОГО ДОСЬЕ (историческая версия, не актуальный паспорт)",
        "—" * 48,
        full_old,
    ]
    report = "\n".join(overview)
    need(len(report) <= 12000, "REPORT_TOO_LONG")
    brief = "\n".join([
        "КЛЮЧЕВАЯ ИНФОРМАЦИЯ · B2B-Center №4677923",
        "Заказчик и ИНН: не подтверждены (не путать с организатором)",
        "Организатор: " + organizer_name,
        "Предмет: продление Microsoft Teams · " + ("3 позиции лота" if quantity else "количество позиций уточнить"),
        "Сумма: не указана",
        "Дедлайн: 20.11.2026 13:59 МСК (из первоисточника)",
        "География исполнения: Москва · Можайский Вал, д. 8",
        "Поставка: электронная",
        "Оплата: предпочтительно отсрочка 30 календарных дней (сверить договор)",
        "Lead Score: прежний, без пересчёта · AI: RouterAI + проверенный первоисточник",
        "Действие: изучить SKU/ТЗ и лицензирование; сверить статус заказчика; не контактировать без согласования.",
        "Ссылка: " + url,
    ])
    summary = "\n".join([
        "B2B-Center №4677923 · Microsoft Teams, продление",
        "Организатор: " + organizer_name + " · заказчик/ИНН не подтверждены",
        "Дедлайн: 20.11.2026 13:59 МСК",
        "Поставка: Москва, Можайский Вал, д. 8 · электронно",
        "Оплата: предпочтительно отсрочка 30 к.д. · сумма не указана",
        "Действие: проверить точные лицензии и документы",
    ])
    updated = dict(original)
    updated.update({
        REPORT:report,
        BRIEF:brief,
        SUMMARY:summary,
        PREFIX+"AI_LIMITATIONS":(
            "Сведения из публичного оригинала. Заказчик и его ИНН юридически не подтверждены; "
            "нет полной спецификации SKU, цены и приложений к договору. "
            "Наличие процедуры не означает право на контакт."),
        PREFIX+"AI_NEXT_ACTION":(
            "Проверить три позиции Microsoft Teams, конкретные SKU и условия продления; "
            "подтвердить цену, документы, заказчика и статус допуска. "
            "Срок подачи по источнику: 20.11.2026 13:59 МСК."),
        PREFIX+"AI_RECOMMENDATION":(
            "Рассмотреть только после проверки возможности официальной поставки Microsoft Teams, "
            "правомерности лицензирования и фактического покупателя. "
            "Использовать подтверждённые условия на странице B2B-Center."),
    })
    need(set(updated) == AI_FIELDS
         and all(isinstance(x, str) and x and len(x) <= (12000 if k==REPORT else 4096)
                 for k,x in updated.items())
         and updated[STATUS] == original[STATUS], "PATCH_SHAPE_INVALID")
    return updated


def digest(payload,salt):
    raw=json.dumps(payload,ensure_ascii=False,sort_keys=True,
                   separators=(",",":")).encode("utf-8")
    return hashlib.sha256(salt+raw).digest()


def attested_current_chain(conn, signed_source, v11_fields):
    """Reconstruct ONLY verified versioned AI mutations, never an arbitrary edit.

    V19/V20 publish complete nine-field post-images; V37 stores scoped patch
    before/after and a salted digest. Unknown changes remain a hard blocker.
    """
    need(set(v11_fields)==AI_FIELDS, "V11_FIELDS_NOT_EXACT")
    expected=dict(v11_fields)
    source_hash=hashlib.sha256(signed_source.encode("ascii")).hexdigest()
    for table in ("serviceit_vps_b2b_v19_research_receipts",
                  "serviceit_vps_b2b_v20_revision_receipts"):
        exists=conn.execute("SELECT to_regclass(%s)",("public."+table,)).fetchone()[0]
        if not exists:
            continue
        rows=conn.execute(
            "SELECT before_fields,after_fields FROM "+table+
            " WHERE card_id=%s AND source_hash=%s AND status='VERIFIED'"
            " ORDER BY updated_at ASC",(CARD,source_hash)
        ).fetchall()
        for before,after in rows:
            need(type(before) is dict and type(after) is dict
                 and set(before)==AI_FIELDS and set(after)==AI_FIELDS
                 and all(expected[k]==before[k] for k in AI_FIELDS),
                 "V19_V20_ATTESTATION_CHAIN_CONFLICT")
            expected=dict(after)
    table="serviceit_vps_b2b_dossier_consistency_v37_receipts"
    exists=conn.execute("SELECT to_regclass(%s)",("public."+table,)).fetchone()[0]
    if exists:
        rows=conn.execute("""
           SELECT status,before_fields,after_fields,salt,digest
           FROM serviceit_vps_b2b_dossier_consistency_v37_receipts
           WHERE card_id=%s AND source_hash=%s
           ORDER BY attempt_no ASC
        """,(CARD,source_hash)).fetchall()
        for status,before,after,salt,proof in rows:
            need(status!="UNCERTAIN","V37_PRIOR_UNCERTAIN_NO_REPLAY")
            if status!="VERIFIED":
                continue
            need(type(before) is dict and type(after) is dict
                 and set(before)==set(after)
                 and isinstance(salt,(bytes,memoryview))
                 and isinstance(proof,(bytes,memoryview))
                 and secrets.compare_digest(digest(after,bytes(salt)),bytes(proof)),
                 "V37_RECEIPT_DIGEST_INVALID")
            need(all(expected[k]==before[k] for k in before if k in AI_FIELDS),
                 "V37_BEFORE_CHAIN_CONFLICT")
            expected.update({k:after[k] for k in after if k in AI_FIELDS})
    return expected


def read_card(rest):
    x=normalized(rest("crm.item.get",{
        "entityTypeId":1062,"id":CARD,"useOriginalUfNames":"Y"
    })["item"])
    validate_identity(x,CARD)
    need(x.get(PREFIX+"SIGNAL_ID")==SOURCE,"CRM_SOURCE_ID_MISMATCH")
    return x


def main():
    mode=sys.argv[1:] if len(sys.argv)>=2 else []
    need(mode in (["--preview"],["--apply"]), "MODE_EXPLICIT_REQUIRED")
    env=dict(os.environ)
    need(live_permitted(env) and env.get("SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED")=="YES"
         and env.get("SERVICEIT_ROUTERAI_PRIVACY_APPROVED")=="YES_1062_21",
         "PRODUCTION_GATES_MISSING")
    effective=exact_environment(env)
    need(effective.get("SERVICEIT_VPS_B2B_PRIMARY_DETAIL_ENABLED")=="YES",
         "NEW_SOURCE_PARSER_NOT_INSTALLED")
    webhook=secret_file(env,"SERVICEIT_BITRIX_WEBHOOK")
    validate_webhook(webhook)
    with httpx.Client(trust_env=False,follow_redirects=False,timeout=18) as client:
        rest=Rest(webhook,client)
        with db(env) as conn:
            with conn.transaction():
                conn.execute("SET TRANSACTION READ ONLY")
                rows=conn.execute("""
                    SELECT state,card_id,notice_json FROM serviceit_vps_b2b_inbox
                    WHERE source_id=%s
                """,(SOURCE,)).fetchall()
                need(len(rows)==1 and rows[0][0]=="VERIFIED" and rows[0][1]==CARD,
                     "SIGNED_RECEIPT_NOT_EXACT")
                notice=rows[0][2]
                item=read_card(rest)
                url,title=safe_notice(notice,CARD,SOURCE,item)
                need(url==notice["source_url"] and bool(title),
                     "SIGNED_NOTICE_IDENTITY_UNVERIFIED")
                v11=conn.execute("""
                    SELECT after_fields FROM serviceit_vps_b2b_dossier_v11_receipts
                    WHERE card_id=%s AND status='VERIFIED'
                """,(CARD,)).fetchone()
                need(v11 and isinstance(v11[0],dict) and set(v11[0])==AI_FIELDS,
                     "V11_BASE_NOT_ATTESTED")
                original={k:item.get(k) for k in AI_FIELDS}
                expected=attested_current_chain(conn,SOURCE,v11[0])
                need(original==expected,
                     "CRM_HAS_UNATTESTED_EDITS_OR_CONCURRENT_WRITER")
                native=conn.execute("""
                    SELECT to_regclass(%s)
                """,("public."+TABLE,)).fetchone()[0]
                if native:
                    old=conn.execute("SELECT status FROM "+TABLE+" WHERE card_id=%s",(CARD,)).fetchone()
                    need(old is None,"ALREADY_ATTEMPTED_NO_REPLAY")
            source=verified_original_b2b_details(
                {"status":"PUBLIC_SOURCE_NOT_READABLE","pages":[]},notice,client)
            need(source.get("original_b2b_detail_independently_read") is True,
                 "PUBLIC_ORIGINAL_NOT_VERIFIED")
            docs=public_documents(source)
            facts=b2b_primary_detail_facts(docs)
            patch=prepare(facts,docs,original,url)
            need(patch!=original,"NO_NEW_EVIDENCE")
            emit("CHAIN_ATTESTED","YES")
            emit("EVIDENCE_READY","YES")
            emit("DATED_SOURCE_FIELDS",len(facts))
            emit("UPDATED_FIELD_COUNT",sum(patch[k]!=original[k] for k in AI_FIELDS))
            emit("REPORT_CHARS",len(patch[REPORT]))
            if mode==["--preview"]:
                emit("PREVIEW_NO_WRITES","YES")
                return

            conn.execute(DDL)
            with conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s)",(LOCK_ID,))
                locked=bool(cur.fetchone()[0])
            need(locked,"CARD29_REPAIR_ALREADY_RUNNING")
            try:
                fresh=read_card(rest)
                need(fingerprint(fresh)==fingerprint(item)
                     and all(fresh.get(k)==original[k] for k in AI_FIELDS),
                     "CRM_CONCURRENT_MANAGER_CHANGE")
                with conn.transaction():
                    cur=conn.execute("""
                      INSERT INTO serviceit_vps_b2b_card29_primary_v1_receipt
                        (card_id,source_hash,status,before_fields)
                      VALUES(%s,%s,'RESERVED',%s::jsonb)
                      ON CONFLICT DO NOTHING RETURNING card_id
                    """,(CARD,hashlib.sha256(SOURCE.encode("ascii")).hexdigest(),
                         json.dumps(original,ensure_ascii=False)))
                    need(cur.fetchone() is not None,"DUPLICATE_REPAIR_FENCE")
                salt=secrets.token_bytes(16)
                proof=digest(patch,salt)
                with conn.transaction():
                    cur=conn.execute("""
                      UPDATE serviceit_vps_b2b_card29_primary_v1_receipt
                      SET status='WRITE_INTENT',after_fields=%s::jsonb,
                          salt=%s,digest=%s,updated_at=now()
                      WHERE card_id=%s AND status='RESERVED'
                      RETURNING card_id
                    """,(json.dumps(patch,ensure_ascii=False),salt,proof,CARD))
                    need(cur.fetchone() is not None,"WRITE_INTENT_NOT_DURABLE")
                try:
                    rest("crm.item.update",{
                        "entityTypeId":1062,"id":CARD,
                        "useOriginalUfNames":"Y","fields":patch
                    })
                    observed=read_card(rest)
                    need(all(observed.get(k)==patch[k] for k in AI_FIELDS)
                         and secrets.compare_digest(digest(patch,salt),proof)
                         and all(observed.get(k)==v for k,v in fresh.items()
                                 if k not in AI_FIELDS|{"updatedTime","updatedBy",
                                     "lastActivityTime","lastActivityBy"}),
                         "CRM_READBACK_OR_EXTERNAL_FIELD_CONFLICT")
                    with conn.transaction():
                        cur=conn.execute("""
                          UPDATE serviceit_vps_b2b_card29_primary_v1_receipt
                          SET status='VERIFIED',updated_at=now()
                          WHERE card_id=%s AND status='WRITE_INTENT'
                          RETURNING card_id
                        """,(CARD,))
                        need(cur.fetchone() is not None,"RECEIPT_FINALIZE_FAILED")
                    emit("CRM_READBACK","VERIFIED")
                    emit("REPLAY","BLOCKED")
                except Exception:
                    with conn.transaction():
                        conn.execute("""
                          UPDATE serviceit_vps_b2b_card29_primary_v1_receipt
                          SET status='UNCERTAIN',updated_at=now()
                          WHERE card_id=%s AND status='WRITE_INTENT'
                        """,(CARD,))
                    emit("CRM_OUTCOME","UNCERTAIN_NO_REPLAY")
                    raise
            finally:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s)",(LOCK_ID,))


if __name__=="__main__":
    try:
        main()
    except Exception as exc:
        emit("FAILURE",type(exc).__name__.upper() if not isinstance(exc,Blocked)
             else str(exc))
        raise SystemExit(2) from None
