#!/usr/bin/env python3
"""VPS-only, four existing B2B opportunities -> source-anchored manager dossiers.

Owner-gated one-time operation. NEVER creates cards, tasks, outreach, company INN,
FNS results, changes stage, score, STOP or old immutable V1-V8 receipts.
Public pages alone are sent to RouterAI; source emails stay inside the VPS.
A V9 PostgreSQL digest receipt is persisted before any Bitrix update, with
one attempted update per card and no automatic replay of uncertain outcomes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import secrets
from urllib.parse import urlsplit

import httpx

from leadpilot.ai import config_from_env
from leadpilot.bitrix_metadata_audit import validate_webhook
from leadpilot.serviceit_b2b_hold_ai_fields import PREFIX, AI_FIELDS
from leadpilot.serviceit_b2b_routerai_deep import (
    public_documents, investigate_routerai, source_only_investigation,
)
from leadpilot.serviceit_enrichment_worker import (
    Rest, normalized, validate_identity, fingerprint,
)
from leadpilot.serviceit_vps_b2b_deep_research import (
    safe_notice, source_findings, public_buyer_candidate,
    published_buyer_inn_evidence,
)
from leadpilot.serviceit_vps_native_fence import PostgresNativeFence
from leadpilot.serviceit_vps_pg_queue import db, secret_file
from leadpilot.serviceit_vps_worker import live_permitted, exact_environment, INTERVAL_SECONDS

VERSION = "B2B_DOSSIER_V9"
STATUS = PREFIX + "AI_STATUS"
REPORT = PREFIX + "AI_FULL_REPORT"
BRIEF = PREFIX + "AI_MANAGER_BRIEF"
INN = PREFIX + "COMPANY_INN"
MAX_REPORT = 12000
MAX_TEXT = 4096
MAX_CARDS = 4
_ALLOWED_GROUPS = frozenset({
    "IT_STAFFING", "1C", "IT_SUPPORT", "CRM", "KKT_EDO", "SERVERS", "INTEGRATION"
})
DDL = """
CREATE TABLE IF NOT EXISTS serviceit_vps_b2b_dossier_v9_receipts (
    card_id BIGINT PRIMARY KEY CHECK (card_id BETWEEN 8 AND 2000000000),
    source_hash TEXT NOT NULL CHECK (source_hash ~ '^[a-f0-9]{64}$'),
    status TEXT NOT NULL CHECK (status IN (
       'RESERVED','WRITE_INTENT','VERIFIED','UNCERTAIN','NO_CHANGE','CONFLICT')),
    digest_salt BYTEA, expected_digest BYTEA,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((digest_salt IS NULL AND expected_digest IS NULL) OR
           (octet_length(digest_salt)=16 AND octet_length(expected_digest)=32))
);
"""

class DossierBlocked(ValueError):
    pass

def require(condition, reason):
    if not condition:
        raise DossierBlocked(reason)

def clean(value, maximum=220):
    if not isinstance(value, str):
        return "не указано"
    value = " ".join(value.split())
    value = "".join("•" if ord(c) > 0xFFFF else c for c in value
                    if ord(c) not in (0xFE0F, 0x200D, 0x20E3)
                    and ord(c) >= 32)
    return value[:maximum] or "не указано"

def old_fields(item):
    out = {key: item.get(key) for key in AI_FIELDS}
    require(set(out) == AI_FIELDS and all(
        type(v) is str and v.strip()
        and len(v) <= (MAX_REPORT if k == REPORT else MAX_TEXT)
        for k, v in out.items()), "EXISTING_NINE_UNVERIFIED")
    return out

def sha_source(source):
    return hashlib.sha256(source.encode("ascii")).hexdigest()

def digest(fields, salt):
    require(set(fields) == AI_FIELDS and type(salt) is bytes and len(salt) == 16,
            "DIGEST_INPUT_INVALID")
    raw = json.dumps(fields, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf8")
    return hashlib.sha256(salt + raw).digest()

def list_exact_four(conn):
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("""
              SELECT b.card_id,b.source_id,b.notice_json
              FROM serviceit_vps_b2b_inbox b
              WHERE b.state='VERIFIED' AND b.card_id IS NOT NULL
              ORDER BY b.received_at,b.source_id LIMIT 5
            """)
            rows = cur.fetchall()
            require(len(rows) == MAX_CARDS, "EXACT_FOUR_VERIFIED_INBOX_REQUIRED")
            cards = [v[0] for v in rows]
            require(len(set(cards)) == MAX_CARDS, "DUPLICATE_CARD_ID")
            for card, source, notice in rows:
                PostgresNativeFence.validate_card(card)
                require(type(source) is str
                        and re.fullmatch(r"B2B-CENTER-\d{5,12}", source)
                        and type(notice) is dict, "IMMUTABLE_B2B_SOURCE_INVALID")
            # An existing completed task is retained; this operation never
            # writes to or creates task records.
            cur.execute("""
              SELECT COUNT(DISTINCT card_id)
              FROM serviceit_vps_native_receipts
              WHERE namespace='TASK_V1' AND status='VERIFIED'
                AND card_id = ANY(%s)
            """, (cards,))
            require(cur.fetchone()[0] == MAX_CARDS, "FOUR_TASK_RECEIPTS_REQUIRED")
    return rows

def reserve(conn, card, source):
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("""
              INSERT INTO serviceit_vps_b2b_dossier_v9_receipts
              (card_id,source_hash,status) VALUES(%s,%s,'RESERVED')
              ON CONFLICT(card_id) DO NOTHING RETURNING card_id
            """, (card, sha_source(source)))
            return cur.fetchone() is not None

def finish(conn, card, before, after):
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("""
              UPDATE serviceit_vps_b2b_dossier_v9_receipts
              SET status=%s,updated_at=now()
              WHERE card_id=%s AND status=%s RETURNING card_id
            """, (after, card, before))
            require(cur.fetchone() is not None, "RECEIPT_FINALIZE_MISMATCH")

def intent(conn, card, fields):
    salt = secrets.token_bytes(16)
    proof = digest(fields, salt)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("""
              UPDATE serviceit_vps_b2b_dossier_v9_receipts
              SET status='WRITE_INTENT',digest_salt=%s,
                  expected_digest=%s,updated_at=now()
              WHERE card_id=%s AND status='RESERVED'
              RETURNING card_id
            """, (salt, proof, card))
            require(cur.fetchone() is not None, "WRITE_INTENT_NOT_DURABLE")
    return salt, proof

def feature_actions(groups, subject):
    g = set(groups) & _ALLOWED_GROUPS
    if "IT_STAFFING" in g or re.search(r"(?iu)(?:ит|it)[\s-]*специалист", subject):
        return (
            "ИТ-аутстаффинг / предоставление специалистов; отдельно проверить 1С, DevOps, ИБ.",
            "Получить состав ролей и уровни Junior–Lead, количество часов/FTE, "
            "формат работы, требования к допускам и информационной безопасности, "
            "ставки, критерии отбора и допустимость субподряда.")
    if "SERVERS" in g or "сервер" in subject.casefold():
        return (
            "Серверное оборудование, возможный монтаж, настройка и сопровождение.",
            "Получить спецификацию/BOM, параметры CPU/RAM/дисков, количество, "
            "гарантии, сроки поставки, адрес, лицензии и допустимость услуг интегратора.")
    if "1C" in g or re.search(r"(?i)\b1[сc]\b", subject):
        return (
            "Разработка, внедрение или поддержка 1С — уточнить фактический предмет.",
            "Выяснить конфигурации, лицензии, интеграции, пользователей, объём "
            "доработок, SLA и критерии приёмки.")
    if "CRM" in g or "INTEGRATION" in g:
        return (
            "CRM/Битрикс24 и интеграции — коммерческое соответствие требует проверки.",
            "Запросить текущие системы, API, обмен данными, число пользователей, "
            "модель внедрения и условия технической поддержки.")
    if "KKT_EDO" in g:
        return (
            "ККТ/маркировка/ЭДО — проверить, что требуется по техническому заданию.",
            "Уточнить количество точек/устройств, оператора ЭДО, требования "
            "к маркировке и интеграции с учётными системами.")
    return (
        "ИТ-закупка-кандидат; конкретная услуга не установлена.",
        "Изучить детальное ТЗ и сопоставить предмет закупки с портфелем «Сервис ИТ».")

def credible_contacts(findings, buyer):
    independent = findings.get("independent_company_research")
    ind = independent if isinstance(independent, dict) else {}
    corporate = ind.get("public_inn")
    public_inn = published_buyer_inn_evidence(findings)
    if not (isinstance(corporate, dict) and isinstance(public_inn, dict)
            and corporate.get("buyer_name") == buyer
            and public_inn.get("buyer_name") == buyer
            and corporate.get("inn") == public_inn.get("inn")
            and corporate.get("provenance") == "PUBLIC_CORPORATE_SELF_DECLARATION"):
        return []
    output = []
    for row in (ind.get("contacts") or [])[:12]:
        if not isinstance(row, dict) or row.get("origin") != "LEGAL_NAME_AND_INN_ON_PUBLIC_PAGE_NOT_FNS":
            continue
        val, url = row.get("value"), row.get("url")
        if not isinstance(val, str) or not isinstance(url, str):
            continue
        if not re.fullmatch(r"https://[A-Za-z0-9.-]+/[^\s]{0,500}", url):
            continue
        if row.get("type") == "email" and re.fullmatch(
                r"[A-Za-z0-9_.+-]{1,64}@[A-Za-z0-9.-]{2,100}\.[A-Za-z]{2,15}", val):
            output.append(("Email", val, url))
        elif row.get("type") == "phone" and re.fullmatch(r"[+\d() -]{8,26}", val):
            output.append(("Телефон", val, url))
    return list(dict.fromkeys(output))[:5]

def compose_dossier(item, notice, source, signed_title, findings, inference):
    base = old_fields(item)
    assert type(source) is str
    tender = clean(notice.get("tender_id"), 24)
    title = clean(signed_title, 240)
    organizer = clean(notice.get("organizer_as_named_in_email"), 180)
    deadline = clean(notice.get("deadline_as_stated_in_email"), 150)
    email_time = clean(notice.get("email_received_at"), 100)
    groups = notice.get("source_service_groups")
    groups = [x for x in groups[:10] if type(x) is str and x in _ALLOWED_GROUPS] if isinstance(groups, list) else []
    fit, questions = feature_actions(groups, title)
    buyer = public_buyer_candidate(findings)
    buyer_text = (buyer + " (из читаемой публикации; НЕ ФНС)") if buyer else "не установлен в читаемой публикации"
    public_inn = published_buyer_inn_evidence(findings) if buyer else None
    inn_text = (public_inn["inn"] + " — публичный кандидат, НЕ ФНС, " +
                public_inn["public_source"]) if public_inn else "не подтверждён публичным источником"
    company_contacts = credible_contacts(findings, buyer) if buyer else []
    contact_text = ("\n".join(f"{kind}: {clean(val,140)} | {url}"
                              for kind, val, url in company_contacts)
                    if company_contacts else "проверенных корпоративных контактов нет")
    docs = public_documents(findings)
    facts = (inference.get("facts", [])[:10] if isinstance(inference,dict) else [])
    facts = [f for f in facts if isinstance(f,dict) and isinstance(f.get("quote"),str)
             and isinstance(f.get("url"),str)][:10]
    quote_lines = [
        f"[{clean(f.get('category'),20)}] «{clean(f['quote'],220)}»\nURL: {f['url']}"
        for f in facts
    ]
    origin = clean(notice.get("source_url"), 700)
    since = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    context = [
        "СЕРВИС ИТ | КОММЕРЧЕСКОЕ ДОСЬЕ V9 (четыре существующие карточки)",
        "Время проверки: " + since,
        "1. СИГНАЛ, ИСТОЧНИК И ТРЕБУЕМОЕ ДЕЙСТВИЕ",
        f"B2B-Center, номер {tender}; предмет из подписанного уведомления: {title}",
        "Ссылка на оригинал: " + origin + " (сама ссылка НЕ доказывает доступность закупки)",
        "Дата получения уведомления: " + email_time,
        "Срок, заявленный в уведомлении: " + deadline + " (проверить по актуальному оригиналу)",
        "2. ЗАКАЗЧИК И ЮРИДИЧЕСКАЯ ИДЕНТИФИКАЦИЯ",
        "Организатор, заявленный в B2B-уведомлении: " + organizer,
        "Заказчик, прямо названный на читаемой публичной странице: " + buyer_text,
        "ИНН: " + inn_text,
        "Официальное подтверждение ФНС/ЕГРЮЛ, МСП и CRM/STOP — отдельно; "
        "не путать организатора, оператора площадки и конечного заказчика.",
        "3. ПРЕДМЕТ И СООТВЕТСТВИЕ УСЛУГАМ «СЕРВИС ИТ»",
        "Группа по исходному заголовку: " + (", ".join(groups) if groups else "не определена"),
        "Коммерческая гипотеза: " + fit,
        "Список вопросов по ТЗ: " + questions,
        "4. РЕАЛЬНЫЕ ЦИТАТЫ ИЗ НЕЗАВИСИМО ПРОЧИТАННЫХ ПУБЛИКАЦИЙ",
        "Читаемых публичных URL: " + str(len({d["url"] for d in docs})) +
        "; проверенных цитат: " + str(len(quote_lines)) + ".",
        "5. КОНТАКТЫ, ОПУБЛИКОВАННЫЕ НА САЙТЕ ЮРЛИЦА",
        contact_text,
        "Контакты — кандидаты для проверки, не согласие на рассылку или звонок.",
        "6. ДОПУСК К ПРОЦЕДУРЕ / ФИНАНСОВЫЕ И СРОЧНЫЕ УСЛОВИЯ",
        "Цена/НМЦ, объёмы, оплата, депозит, критерии отбора и полный перечень "
        "документов указываются как подтверждённые только если они есть в цитатах ниже.",
        "7. ПЛАН ПРОРАБОТКИ МЕНЕДЖЕРА",
        "Проверить актуальный статус и срок подачи в оригинале; запросить/скачать "
        "доступное ТЗ, проверить заказчика+ИНН+ФНС+STOP+дубли, сопоставить ресурсы "
        "«Сервис ИТ» с требованиями и допуском субподрядчика. Не отправлять "
        "КП и письма без проверки прав и условий.",
        "8. ГРАНИЦЫ ДОКАЗАТЕЛЬСТВ",
        "Данные уведомления — отдельный уровень достоверности от страниц, "
        "прочитанных без входа. Brave только обнаруживает URL. Нет публичного "
        "факта — нет придуманной даты, бюджета, ФИО, email или ИНН.",
        "9. ПОДРОБНЫЕ ПУБЛИЧНЫЕ ЦИТАТЫ С ПРОИСХОЖДЕНИЕМ:",
    ]
    evidence = "\n\n".join(quote_lines) if quote_lines else (
        "Нет пригодных для независимого подтверждения цитат. Оригинал может "
        "требовать авторизации; это не отменяет реальное B2B-уведомление.")
    archived = "\n\n10. ПРЕДЫДУЩИЙ AI-ОТЧЁТ (СОХРАНЁН БЕЗ ПОТЕРЬ)\n" + base[REPORT]
    head = "\n".join(context)
    require(len(head) + len(archived) + 6 < MAX_REPORT, "OLD_REPORT_CAPACITY_EXHAUSTED")
    room = MAX_REPORT - len(head) - len(archived) - 6
    if len(evidence) > room:
        keep = []
        for line in quote_lines:
            if len("\n\n".join(keep + [line])) <= room - 65:
                keep.append(line)
        evidence = "\n\n".join(keep) if keep else "Дополнительные цитаты не уместились без потери старого отчёта."
    full = head + "\n" + evidence + archived
    require(len(full) <= MAX_REPORT and base[REPORT] in full,
            "OLD_CITED_REPORT_MUST_BE_PRESERVED")
    short_deadline = (deadline if deadline!="не указано" else "срок ещё не выявлен")
    summary = (f"B2B №{tender}. {title}. Срок из уведомления: {short_deadline}. "
               f"Организатор по письму: {organizer}. Заказчик по публичному источнику: "
               f"{buyer or 'не выявлен'}. {len(quote_lines)} цитат, {len(docs)} "
               f"читаемых страниц. Возможное направление: {fit}. "
               "Коммерческое решение остаётся на проверке.")
    brief = ("\n".join([
        f"ЗАКУПКА №{tender} | {title}",
        "Ссылка: " + origin, "Получено: " + email_time,
        "Дедлайн по уведомлению (не перепроверен): " + deadline,
        "Организатор из письма: " + organizer,
        "Публичный заказчик: " + (buyer or "не найден"),
        "ИНН-кандидат: " + (public_inn["inn"] if public_inn else "не установлен"),
        "Проверенные контакты: " + (", ".join(v for _,v,_ in company_contacts) if company_contacts else "нет"),
        f"Доказательства: {len(quote_lines)} цитат / {len({d['url'] for d in docs})} URL.",
        "Рекомендуемая услуга: " + fit,
        "Что выяснить: " + questions,
        "Следующий шаг: проверить оригинал, срок, ТЗ и правомерность участия.",
        "Отчёт не квалифицирует покупателя и не разрешает исходящий контакт."
    ]))
    limitations = ("Роль организатора из email не доказывает юрзаказчика; "
        "срок из email требует проверки актуальной редакции; "
        "публикация и контакты не подтверждают право связи; "
        "без независимого источника не установлены бюджет, полный объём, "
        "условия оплаты, допуск к закупке, ФНС/МСП, STOP и отсутствие дублей.")
    fields = dict(base)
    fields[PREFIX+"AI_SUMMARY"] = summary
    fields[PREFIX+"AI_HYPOTHESIS"] = "Возможный коммерческий запрос: " + fit + " Это гипотеза до чтения полного ТЗ."
    fields[PREFIX+"AI_LIMITATIONS"] = limitations
    fields[PREFIX+"AI_RECOMMENDATION"] = "Приоритет — проверить предмет и сроки; " + questions
    fields[PREFIX+"AI_NEXT_ACTION"] = ("Проверить статус и дедлайн B2B №" + tender
        + "; затем ТЗ, заказчика/ИНН, критерии участия и ресурсы для КП.")
    fields[BRIEF] = brief
    fields[REPORT] = full
    if facts and isinstance(facts[0].get("quote"),str):
        fields[PREFIX+"AI_QUOTE"] = "Публично: " + clean(facts[0]["quote"],280)
    # Keep existing AI_STATUS as a truthful original model/provenance status,
    # while the new V9 provenance is explicit at the top of the dossier.
    # Preserve line breaks in manager memo and limitations; a dossier must
    # remain scannable on the actual Bitrix detail form.
    fields = {
        k: (v if k == REPORT else
            "\n".join(clean(line, MAX_TEXT) for line in v.splitlines())[:MAX_TEXT]
            if k in (BRIEF, PREFIX+"AI_LIMITATIONS") else clean(v, MAX_TEXT))
        for k, v in fields.items()
    }
    require(set(fields) == AI_FIELDS and all(
        type(v) is str and v.strip() and len(v) <= (
            MAX_REPORT if k==REPORT else MAX_TEXT)
        for k,v in fields.items()), "NINE_FIELD_SCHEMA_INVALID")
    return fields, len(docs), len(facts), bool(buyer)

def verify_readback(env):
    """Read-only reconciliation: V9 receipt hash against all 9 live CRM fields."""
    env=exact_environment(env)
    with db(env) as conn:
        rows=list_exact_four(conn)
        with httpx.Client(trust_env=False,follow_redirects=False,timeout=22) as client:
            rest=Rest(secret_file(env,"SERVICEIT_BITRIX_WEBHOOK"),client)
            confirmed=0
            for ix,(card,source,notice) in enumerate(rows,1):
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute("SET TRANSACTION READ ONLY")
                        cur.execute("""
                            SELECT status,digest_salt,expected_digest,source_hash
                            FROM serviceit_vps_b2b_dossier_v9_receipts
                            WHERE card_id=%s
                        """,(card,))
                        rec=cur.fetchone()
                item=normalized(rest("crm.item.get",{
                    "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y"})["item"])
                validate_identity(item,card)
                old_fields(item)
                ok=(rec is not None and rec[0]=="VERIFIED" and
                    isinstance(rec[1],(bytes,memoryview)) and
                    isinstance(rec[2],(bytes,memoryview)) and
                    rec[3]==sha_source(source))
                if ok:
                    observed={key:item.get(key) for key in AI_FIELDS}
                    ok=(secrets.compare_digest(
                            digest(observed,bytes(rec[1])),bytes(rec[2]))
                        and "КОММЕРЧЕСКОЕ ДОСЬЕ V9" in item[REPORT]
                        and item.get(PREFIX+"SIGNAL_ID")==source)
                confirmed+=int(ok)
                print(f"DOSSIER_V9_CARD_{ix}="+(
                    "READBACK_VERIFIED" if ok else "READBACK_UNVERIFIED"),flush=True)
            print("DOSSIER_V9_ALL_FOUR_READBACK="+(
                "YES" if confirmed==MAX_CARDS else "NO"),flush=True)
            require(confirmed==MAX_CARDS,"FOUR_CRM_READBACK_INCOMPLETE")

def run(apply):
    env = dict(os.environ)
    require(INTERVAL_SECONDS == 300, "EXPECTED_FIVE_MINUTE_CORE")
    require(live_permitted(env), "NATIVE_LIVE_CUTOVER_NOT_AUTHORIZED")
    require(env.get("SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED")=="YES"
            and env.get("SERVICEIT_VPS_BROWSER_RESEARCH_ENABLED")=="YES"
            and env.get("SERVICEIT_VPS_BROWSER_PROXY_ISOLATION_VERIFIED")=="YES",
            "PUBLIC_RESEARCH_GATES_NOT_READY")
    validate_webhook(secret_file(env, "SERVICEIT_BITRIX_WEBHOOK"))
    if apply:
        require(env.get("SERVICEIT_AI_PROVIDER") == "routerai"
                and env.get("SERVICEIT_ROUTERAI_PRIVACY_APPROVED")=="YES_1062_21",
                "ROUTERAI_PRIVACY_GATE_NOT_APPROVED")
    env = exact_environment(env)
    with db(env) as connection:
        rows = list_exact_four(connection)
        if apply:
            with connection.transaction():
                with connection.cursor() as cur:
                    cur.execute(DDL)
        print("DOSSIER_V9_EXACT_FOUR=YES", flush=True)
        print("DOSSIER_V9_MODE=" + ("APPLY" if apply else "PREVIEW"),flush=True)
        with httpx.Client(trust_env=False,follow_redirects=False,timeout=24) as client:
            rest = Rest(secret_file(env,"SERVICEIT_BITRIX_WEBHOOK"), client)
            for ix,(card,source,notice) in enumerate(rows,1):
                phase="NONE"
                if apply:
                    # Reserve first; never replay a one-time paid model request.
                    if not reserve(connection, card, source):
                        print(f"DOSSIER_V9_CARD_{ix}=ALREADY_FENCED_NO_RETRY",flush=True)
                        continue
                    phase="RESERVED"
                try:
                    item=normalized(rest("crm.item.get",{
                        "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y"})["item"])
                    validate_identity(item,card)
                    old = old_fields(item)
                    source_url,signed_title=safe_notice(notice,card,source,item)
                    require(bool(signed_title) and source_url==notice["source_url"],
                            "SIGNED_B2B_TITLE_UNSAFE")
                    try:
                        findings=source_findings(item,notice,env,client)
                    except Exception:
                        findings={"status":"PUBLIC_SOURCE_UNVERIFIED","pages":[],
                                  "checked_at":datetime.now(timezone.utc).isoformat()}
                    docs=public_documents(findings)
                    inferred=source_only_investigation(findings)
                    if docs and apply:
                        try:
                            cfg=config_from_env({**env,"AI_PROVIDER":"routerai",
                                                 "AI_ENABLE":"1"})
                            require(cfg.provider=="routerai","ROUTERAI_REQUIRED")
                            inferred=investigate_routerai(client,cfg,findings)
                        except Exception:
                            # Public source-only fallback; never call model again.
                            inferred=source_only_investigation(findings)
                    fields,pages,fact_count,buyer=compose_dossier(
                        item,notice,source,signed_title,findings,inferred)
                    print(f"DOSSIER_V9_CARD_{ix}_PAGES={min(pages,9)}",flush=True)
                    print(f"DOSSIER_V9_CARD_{ix}_CITATIONS={min(fact_count,10)}",flush=True)
                    print(f"DOSSIER_V9_CARD_{ix}_BUYER_PUBLIC="+("YES" if buyer else "NO"),flush=True)
                    if not apply:
                        print(f"DOSSIER_V9_CARD_{ix}=PREVIEW_VALID",flush=True)
                        continue
                    fresh=normalized(rest("crm.item.get",{
                        "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y"})["item"])
                    validate_identity(fresh,card)
                    require(fingerprint(item)==fingerprint(fresh)
                            and all(fresh.get(k)==old[k] for k in AI_FIELDS)
                            and fresh.get(INN)==item.get(INN),
                            "CARD_CHANGED_BEFORE_UPDATE")
                    if fields == old:
                        finish(connection,card,"RESERVED","NO_CHANGE")
                        print(f"DOSSIER_V9_CARD_{ix}=NO_CHANGE",flush=True)
                        continue
                    salt,expected=intent(connection,card,fields)
                    phase="WRITE_INTENT"
                    rest("crm.item.update",{
                        "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y",
                        "fields":fields})
                    after=normalized(rest("crm.item.get",{
                        "entityTypeId":1062,"id":card,"useOriginalUfNames":"Y"})["item"])
                    validate_identity(after,card)
                    observed={k:after.get(k) for k in AI_FIELDS}
                    require(all(observed[k]==fields[k] for k in AI_FIELDS)
                            and secrets.compare_digest(digest(observed,salt),expected)
                            and after.get(INN)==item.get(INN),
                            "CRM_EXACT_READBACK_NOT_VERIFIED")
                    finish(connection,card,"WRITE_INTENT","VERIFIED")
                    phase="VERIFIED"
                    print(f"DOSSIER_V9_CARD_{ix}=VERIFIED",flush=True)
                except Exception:
                    if apply and phase in ("RESERVED","WRITE_INTENT"):
                        try:
                            finish(connection,card,phase,
                                   "CONFLICT" if phase=="RESERVED" else "UNCERTAIN")
                        except Exception:
                            pass
                    print(f"DOSSIER_V9_CARD_{ix}="+(
                        "BLOCKED_NO_WRITE" if phase in ("NONE","RESERVED") else
                        "UNCERTAIN_NO_RETRY"),flush=True)
        print("DOSSIER_V9_DONE=YES",flush=True)

def main():
    parser=argparse.ArgumentParser()
    mx=parser.add_mutually_exclusive_group(required=True)
    mx.add_argument("--preview",action="store_true")
    mx.add_argument("--apply",action="store_true")
    mx.add_argument("--verify",action="store_true")
    args=parser.parse_args()
    try:
        if args.verify:
            verify_readback(dict(os.environ))
        else:
            run(args.apply)
    except Exception:
        print("DOSSIER_V9_GLOBAL_BLOCKED=YES",flush=True)
        return 2
    return 0

if __name__=="__main__":
    raise SystemExit(main())
