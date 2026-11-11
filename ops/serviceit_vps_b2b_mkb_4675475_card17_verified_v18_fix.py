#!/usr/bin/env python3
"""One-use additive public-source supplement for REAL CRM entity 1062/21 ID 17.

This is a HUMAN-VERIFIED SOURCE HANDOFF, not a claim that the VPS fetched
Kontur itself. Exact tender number, buyer, timing and address were independently
read by the operator on 2026-11-11 from two public sites:
  https://zakupki.kontur.ru/4675475_40
  https://www.b2b-center.ru/search/industry/servernoe-oborudovanie/
The VPS automated fetch was blocked at the robots policy check; never claim
otherwise. Do not write INN as FNS-certified or invent a budget/specification.

ONLY three narrative AI fields may change. All prior report text retained,
commercial HOLD/Score/STOP/FNS, categories, tasks and other fields unchanged.
PG receipt prevents duplicate or uncertain replays and ensures exact readback.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import re
import secrets
import sys

import httpx

VERSION = "MKB_4675475_PUBLIC_OPERATOR_20261111_V1"
CARD = 17
ENTITY = 1062
CATEGORY = 21
LOCK_ID = 6754819001  # same historical supplement lock
TABLE = "serviceit_vps_b2b_mkb_4675475_manual_receipts"
# GitHub SHA256 is passed from the exact V18-reviewed main files and
# rechecked against runtime before any CRM action.
EXPECTED_MIRROR_SHA = None
URL = "https://zakupki.kontur.ru/4675475_40"
OFFICIAL = "https://www.b2b-center.ru/search/industry/servernoe-oborudovanie/"
DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE}(
 card_id BIGINT PRIMARY KEY CHECK(card_id=17),
 version TEXT NOT NULL CHECK(version='MKB_4675475_PUBLIC_OPERATOR_20261111_V1'),
 status TEXT NOT NULL CHECK(status IN
  ('RESERVED','WRITE_INTENT','VERIFIED','CONFLICT','UNCERTAIN')),
 before_fields JSONB NOT NULL,
 after_fields JSONB,
 salt BYTEA,
 digest BYTEA,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CHECK ((salt IS NULL AND digest IS NULL) OR
        (octet_length(salt)=16 AND octet_length(digest)=32))
);
"""

class GuardFailure(RuntimeError):
    pass


def require(ok, code):
    if not ok:
        raise GuardFailure(code)


def say(key, value):
    require(re.fullmatch(r"[A-Z0-9_]{3,60}", key) is not None,
            "OUTPUT_KEY_INVALID")
    require(value in ("YES", "NO", "VERIFIED", "CONFLICT", "UNCERTAIN"),
            "OUTPUT_VALUE_INVALID")
    print("MKB_CARD17_" + key + "=" + value, flush=True)


def factual_addendum():
    # Public tender page and B2B official index corroborate the number,
    # named buyer, procedure type and deadline. Values are intentionally
    # limited to facts present on those pages, not document-only conditions.
    return (
        "12  РЕАЛЬНЫЕ СВЕДЕНИЯ ПО ТЕНДЕРУ № 4675475 (внешняя проверка 11.11.2026)\n"
        "Заказчик в опубликованной карточке: ПАО «Московский Кредитный Банк».\n"
        "ИНН из публичной карточки: 7734202860; КПП, указанный для данной закупки: 775001001. "
        "Проверка ФНС данным дополнением НЕ проводилась.\n"
        "Предмет: приобретение серверного оборудования для нужд МКБ.\n"
        "Тип: предварительный квалификационный отбор — ещё не присуждённый договор поставки.\n"
        "Опубликовано: 10.11.2026 14:53 МСК. Срок подачи: 16.11.2026 16:00 МСК.\n"
        "Место поставки (не юридический адрес): 111141, Москва, проезд 2-й Перова Поля, д. 2, стр. 4.\n"
        "Начальная цена: НЕ РАСКРЫТА (в агрегаторе «сумма не задана»). "
        "Нуль на странице каталога нельзя считать бюджетом.\n"
        "ТЗ, перечень серверов, количество, параметры, критерии квалификации, "
        "условия оплаты и обеспечения здесь НЕ опубликованы; документы предлагается "
        "смотреть на B2B-Center после надлежащего доступа.\n"
        "Уровень источника: публичные страницы подтверждены при внешней веб-проверке; "
        "сам VPS не смог прочитать Контур из-за проверки доступа, поэтому это "
        "ограниченное операторское дополнение, НЕ автоматическое AI-исследование.\n"
        "Источник 1: " + URL + "\n"
        "Источник 2 (официальный каталог B2B-Center): " + OFFICIAL + "\n"
        "Действие Михаила: сначала подтвердить допустимость участия, открыть "
        "документацию и запросить спецификацию серверов, условия поставки, гарантии, "
        "оплаты и критерии отбора. Только затем оценивать коммерческую пригодность."
    )


def prepare(before):
    from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX
    report = PREFIX + "AI_FULL_REPORT"
    brief = PREFIX + "AI_MANAGER_BRIEF"
    summary = PREFIX + "AI_SUMMARY"
    require(type(before) is dict and set(before) == AI_FIELDS
            and all(type(x) is str and x.strip() for x in before.values()),
            "AI_BASE_INVALID")
    require(VERSION not in before[report] and
            "12  РЕАЛЬНЫЕ СВЕДЕНИЯ ПО ТЕНДЕРУ № 4675475" not in before[report],
            "ALREADY_SUPPLEMENTED")
    result = dict(before)
    fragment = factual_addendum()
    require(len(before[report]) + len(fragment) + 2 <= 12000,
            "REPORT_NO_APPEND_CAPACITY")
    result[report] = before[report] + "\n\n" + fragment

    summary_first = (
        "ПОДТВЕРЖДЕНО ИЗ ПУБЛИЧНЫХ ИСТОЧНИКОВ: МКБ, ПАО «Московский Кредитный Банк», "
        "ИНН 7734202860 (не ФНС). Предквалификация №4675475, срок "
        "16.11.2026 16:00 МСК, поставка: Москва, 2-й проезд Перова Поля, 2/4. "
        "Цена не раскрыта. ТЗ/критерии/оплата требуют доступа к документам. "
        "Источник: " + URL + "\n"
    )
    brief_first = (
        "МЕНЕДЖЕРУ — ВЫВОД ПО №4675475: заказчик ПАО «Московский Кредитный Банк» "
        "(публичный ИНН 7734202860), предварительный квалификационный отбор "
        "до 16.11.2026 16:00 МСК. Поставка Москва. Цена и состав серверов не опубликованы. "
        "Получить ТЗ/критерии/оплату через B2B-Center; проверить возможности поставки "
        "и право участия. Контакт с заказчиком без разрешения не инициировать.\n"
        + URL + "\n"
    )
    require(len(summary_first)+len(before[summary]) <= 4096
            and len(brief_first)+len(before[brief]) <= 4096,
            "BRIEF_OR_SUMMARY_CAPACITY")
    result[summary] = summary_first + "\n" + before[summary]
    result[brief] = brief_first + "\n" + before[brief]
    require(set(result)==AI_FIELDS
            and all(type(v) is str and v.strip() and
                    len(v) <= (12000 if k == report else 4096)
                    for k,v in result.items())
            and all(result[k] == before[k] for k in AI_FIELDS if k not in (
                report, summary, brief)), "UNRELATED_AI_FIELD_DRIFT")
    return result


def reserve(conn, before):
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(f"""
              INSERT INTO {TABLE}(card_id,version,status,before_fields)
              VALUES(%s,%s,'RESERVED',%s::jsonb)
              ON CONFLICT(card_id) DO NOTHING RETURNING card_id
            """,(CARD,VERSION,json.dumps(before,ensure_ascii=False)))
            return cur.fetchone() is not None


def state(conn, prior, following):
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(f"""
              UPDATE {TABLE} SET status=%s,updated_at=now()
              WHERE card_id=%s AND status=%s RETURNING card_id
            """,(following,CARD,prior))
            require(cur.fetchone() is not None, "RECEIPT_STATE_CONFLICT")


def intent(conn, proposed):
    from leadpilot.serviceit_vps_b2b_dossier_v11_worker import compute_digest
    salt = secrets.token_bytes(16)
    digest = compute_digest(proposed,salt)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(f"""
              UPDATE {TABLE} SET status='WRITE_INTENT',
                after_fields=%s::jsonb,salt=%s,digest=%s,updated_at=now()
              WHERE card_id=%s AND status='RESERVED' RETURNING card_id
            """,(json.dumps(proposed,ensure_ascii=False),salt,digest,CARD))
            require(cur.fetchone() is not None,"WRITE_INTENT_NOT_DURABLE")
    return salt,digest


def active_writer(conn):
    with conn.cursor() as cur:
        # A single unrelated 24h-old durable AI_REPAIR_SUMMARY_V1 intent
        # is already known from live read-only audit (Nov 11, 12:34 UTC).
        # It belongs to a DIFFERENT card and MUST NOT be altered/replayed.
        # Only this one exact old category can be ignored for an independent
        # evidence-only update to CRM #17. Any other current or related
        # intent still blocks the update.
        cur.execute("""
            SELECT count(*) FROM serviceit_vps_native_receipts
            WHERE status IN ('RESERVED','WRITE_INTENT','AI_INTENT')
              AND NOT (
                namespace='AI_REPAIR_SUMMARY_V1' AND card_id<>%s
                AND status='WRITE_INTENT'
                AND updated_at < now()-interval '24 hours'
                AND field_names IS NOT NULL AND digest_salt IS NOT NULL
                AND expected_digest IS NOT NULL
              )
        """,(CARD,))
        count=int(cur.fetchone()[0])
        for table in ("serviceit_vps_b2b_dossier_v11_receipts",
                      "serviceit_vps_b2b_tender_doc_receipts"):
            cur.execute("SELECT to_regclass(%s)", ("public."+table,))
            if cur.fetchone()[0] is None:
                continue
            cur.execute("SELECT count(*) FROM "+table+
                        " WHERE status IN ('RESERVED','WRITE_INTENT','AI_INTENT')")
            count += int(cur.fetchone()[0])
    return count > 0


def run():
    from pathlib import Path
    from leadpilot.bitrix_metadata_audit import validate_webhook
    from leadpilot.serviceit_enrichment_worker import (
        Rest, normalized, validate_identity, fingerprint,
    )
    from leadpilot.serviceit_vps_pg_queue import db, secret_file
    from leadpilot.serviceit_vps_b2b_dossier_v11_worker import (
        compute_digest, non_ai_unchanged,
    )
    from leadpilot.serviceit_b2b_dossier_v11 import original_nine
    from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX
    from leadpilot.serviceit_vps_worker import live_permitted

    require(len(sys.argv)==2 and re.fullmatch(r"[0-9a-f]{64}",sys.argv[1]),
            "REVIEWED_SHA_REQUIRED")
    expected = sys.argv[1]
    live=Path("/app/leadpilot/serviceit_b2b_public_mirrors.py")
    require(live.is_file() and not live.is_symlink()
            and hashlib.sha256(live.read_bytes()).hexdigest()
            == expected, "LIVE_V18_CODE_DRIFT")
    require(Path("/app/leadpilot/serviceit_b2b_tender_index_v18.py").is_file(),
            "V18_SEARCH_MODULE_NOT_INSTALLED")
    env=dict(os.environ)
    require(live_permitted(env),"LIVE_GATES_NOT_READY")
    from leadpilot.serviceit_vps_worker import exact_environment
    require(exact_environment(env).get("SERVICEIT_B2B_V18_SEARCH_ENABLED")
            == "YES","V18_WORKER_NOT_ENABLED")
    webhook=secret_file(env,"SERVICEIT_BITRIX_WEBHOOK")
    validate_webhook(webhook)
    with httpx.Client(trust_env=False,follow_redirects=False,
                      timeout=18) as client:
        rest=Rest(webhook,client)
        with db(env) as conn:
            conn.execute(DDL)
            with conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_ID,))
                locked=bool(cur.fetchone()[0])
            require(locked,"OTHER_SUPPLEMENT_IN_FLIGHT")
            try:
                require(not active_writer(conn),"OTHER_WRITE_IN_FLIGHT")
                item=normalized(rest("crm.item.get",{
                    "entityTypeId":ENTITY,"id":CARD,"useOriginalUfNames":"Y"
                })["item"])
                validate_identity(item,CARD)
                require(str(item.get("categoryId"))==str(CATEGORY),"CATEGORY_MISMATCH")
                before=original_nine(item)
                require("4675475" in before[PREFIX+"AI_FULL_REPORT"],
                        "NOT_THE_MKB_TENDER")
                orig=str(item.get(PREFIX+"SIGNAL_URL") or "")
                require("b2b-center.ru" in orig and "4675475" in orig,
                        "ORIGINAL_SOURCE_NOT_MATCHING")
                # No blind replay, even if previous CRM operation was uncertain.
                require(reserve(conn,before),"EXISTING_RECEIPT_REQUIRES_AUDIT")
                fresh=normalized(rest("crm.item.get",{
                    "entityTypeId":ENTITY,"id":CARD,"useOriginalUfNames":"Y"
                })["item"])
                require(fingerprint(fresh)==fingerprint(item),
                        "CRM_CHANGED_BEFORE_WRITE")
                proposed=prepare(before)
                salt,digest=intent(conn,proposed)
                phase="WRITE_INTENT"
                try:
                    rest("crm.item.update",{
                        "entityTypeId":ENTITY,"id":CARD,
                        "useOriginalUfNames":"Y","fields":proposed
                    })
                    after=normalized(rest("crm.item.get",{
                        "entityTypeId":ENTITY,"id":CARD,"useOriginalUfNames":"Y"
                    })["item"])
                    validate_identity(after,CARD)
                    require(original_nine(after)==proposed
                            and secrets.compare_digest(
                                compute_digest(proposed,salt),digest)
                            and non_ai_unchanged(fresh,after),
                            "CRM_NINE_AND_NON_AI_READBACK_MISMATCH")
                    state(conn,phase,"VERIFIED")
                    say("VERIFIED_WRITE","YES")
                    return 0
                except Exception:
                    try:
                        state(conn,phase,"UNCERTAIN")
                    except Exception:
                        pass
                    say("VERIFIED_WRITE","NO")
                    return 2
            finally:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s)",(LOCK_ID,))


if __name__=="__main__":
    try:
        sys.exit(run())
    except Exception as exc:
        # Codes only; no secret, URL, corporate/private CRM data or
        # exception details in GitHub Actions output.
        code=str(exc) if isinstance(exc,GuardFailure) else "UNEXPECTED_STOP"
        if not re.fullmatch(r"[A-Z0-9_]{4,72}",code):
            code="UNEXPECTED_STOP"
        print("MKB_CARD17_REASON="+code,flush=True)
        say("VERIFIED_WRITE","NO")
        sys.exit(2)
