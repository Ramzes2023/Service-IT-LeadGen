"""Apply verified public evidence to the one requested signed tender."""
import hashlib
import os
import secrets
import re
import sys
from urllib.parse import urlsplit

import httpx

from leadpilot.serviceit_b2b_hold_ai_fields import PREFIX
from leadpilot.serviceit_enrichment_worker import Rest, normalized, validate_identity
from leadpilot.serviceit_vps_b2b_deep_research import safe_notice
from leadpilot.serviceit_vps_pg_queue import db, secret_file
from leadpilot.serviceit_public_evidence_research import _page, ResearchBlocked

from leadpilot import serviceit_b2b_dossier_consistency_worker_v37 as writer
from leadpilot import serviceit_b2b_v20_versioned as v20
from leadpilot.serviceit_b2b_dossier_v11 import REPORT, sync_confirmed_passport
from leadpilot.serviceit_vps_b2b_document_worker import LOCK_ID as DOCUMENT_LOCK
from leadpilot.serviceit_vps_worker import live_permitted

CARD = 27
TENDER = "4677160"
BUYER = "ООО «Башкирэнерго»"
INN = "0277071467"
DETAIL = ("https://www.b2b-energo.ru/market/"
          "priobretenie-po-i-litsenzii-1s-dlia-ooo-bashkirenergo-26-005731/tender-4677160/")
PROFILE = "https://www.b2b-energo.ru/firms/ooo-bashkirenergo/49070/"
REGISTER = "https://zakupki.kontur.ru/32616566539"
HEADING = "СВЕРКА Б2Б-ЭНЕРГО И ЕИС №32616566539"


def desired_patch(card):
    before = v20.original_nine(card)
    brief = (f"Заказчик: {BUYER}, ИНН {INN}. Приём заявок до 16.11.2026 10:00 МСК. "
             "Цена на Б2Б-Энерго — 11 028 400 ₽ без НДС.")
    values = {
        "BUYER": f"{BUYER} [{DETAIL}]",
        "INN": f"{INN} [{PROFILE}]",
        "DEADLINE": f"16.11.2026 10:00 МСК [{DETAIL}]",
        "BUDGET": f"11 028 400 ₽ без НДС [{DETAIL}]",
        "LOCATION": f"Уфа, б-р Ибрагимова, 86 [{DETAIL}]",
    }
    result = sync_confirmed_passport(before, values)
    report = result[REPORT]
    if HEADING not in report:
        block = (
            f"{HEADING}\n{brief}\n"
            "Полное наименование заказчика — ООО «Башкирские распределительные электрические сети», "
            "ОГРН 1050204504558. Организатор — АО «БЭСК».\n"
            "Поставка в Уфу, б-р Ибрагимова, 86. Запрос документации до 16.11.2026 13:00 МСК. "
            "Предложение на весь лот. Вложения к заявке обязательны. "
            "При оценке сопоставляют цены без НДС.\n"
            "В сторонней выдаче встречается 13 454 648 ₽. В досье указана сумма первичной площадки "
            "без НДС. Полное ТЗ, количество лицензий и условия оплаты требуют сверки с документацией.\n"
            f"Закупка: {DETAIL}\nРеквизиты: {PROFILE}\nСверка извещения: {REGISTER}\n"
        )
        # Keep the verified evidence near the top and preserve the original dossier.
        report = block + "\n" + report
    changes = {REPORT: report, PREFIX + "COMPANY_INN": INN}
    for field in ("AI_SUMMARY", "AI_MANAGER_BRIEF"):
        key = PREFIX + field
        if not before[key].startswith(brief):
            changes[key] = brief + "\n" + before[key]
    return {k: v for k, v in changes.items() if card.get(k) != v}


def apply(conn, rest, card, source):
    existing_inn = card.get(PREFIX + "COMPANY_INN")
    assert existing_inn in (None, "", INN)
    source_hash = hashlib.sha256(source.encode("ascii")).hexdigest()
    for table in (writer.TABLE, v20.TABLE):
        receipt = conn.execute(
            f"SELECT status,source_hash FROM {table} WHERE card_id=%s "
            "ORDER BY attempt_no DESC LIMIT 1", (CARD,)).fetchone()
        if receipt and (receipt[0] in ("RESERVED", "WRITE_INTENT", "UNCERTAIN")
                        or receipt[1] != source_hash):
            raise ValueError("Prior write requires review")
    patch = desired_patch(card)
    if not patch:
        print("CARD27_WEB_WRITE=ALREADY_VERIFIED", flush=True)
        return
    writer.verify_patch_schema(rest, patch)
    fresh = writer._get(rest, CARD)
    assert fresh == card and not writer.active_card_write(conn, CARD)
    attempt = writer.reserve(conn, CARD, source_hash, {k: fresh.get(k) for k in patch})
    salt, digest = writer.mark(conn, CARD, attempt, "WRITE_INTENT", patch)
    try:
        writer.scoped_subset_update(rest, CARD, patch)
        after = writer._get(rest, CARD)
        observed = {key: after.get(key) for key in patch}
        assert observed == patch and writer._non_target_unchanged(fresh, after, patch)
        assert secrets.compare_digest(writer._digest(observed, salt), digest)
    except Exception:
        writer.mark(conn, CARD, attempt, "UNCERTAIN")
        raise
    writer.mark(conn, CARD, attempt, "VERIFIED")
    print("CARD27_WEB_WRITE=VERIFIED_READBACK", flush=True)
    print(f"CARD27_WEB_FIELDS_UPDATED={len(patch)}", flush=True)


def main():
    env = dict(os.environ)
    assert live_permitted(env)
    with db(env) as conn, httpx.Client(
            trust_env=False, follow_redirects=False, timeout=16) as client:
        # The authorized one-off write is complete. Future runs are read-only.
        if "--apply" in sys.argv:
            for lock in dict.fromkeys((v20.LOCK_ID, DOCUMENT_LOCK, writer.LOCK_ID)):
                assert conn.execute("SELECT pg_try_advisory_lock(%s)", (lock,)).fetchone()[0]
        row = conn.execute("""
            SELECT source_id, notice_json FROM serviceit_vps_b2b_inbox
            WHERE card_id=%s AND state='VERIFIED'
        """, (CARD,)).fetchone()
        if row is None:
            sys.exit(2)
        source, notice = row
        rest = Rest(secret_file(env, "SERVICEIT_BITRIX_WEBHOOK"), client)
        card = normalized(rest("crm.item.get", {
            "entityTypeId": 1062, "id": CARD, "useOriginalUfNames": "Y",
        })["item"])
        validate_identity(card, CARD)
        url, _ = safe_notice(notice, CARD, source, card)
        tender = notice["tender_id"]
        assert tender == TENDER and source == f"B2B-CENTER-{TENDER}"
        print("CARD27_WEB_SLUG=" + ("YES" if re.fullmatch(
            r"/(?:market|app/market-next)/[a-z0-9-]+/tender-4677160/?", urlsplit(url).path
        ) else "NO"), flush=True)
        if "--apply" in sys.argv:
            apply(conn, rest, card, source)
            card = writer._get(rest, CARD)
        print("CARD27_WEB_SLUG_MATCH=" + ("YES" if urlsplit(url).path.replace(
            "/app/market-next/", "/market/", 1).rstrip("/") == urlsplit(DETAIL).path.rstrip("/")
            else "NO"), flush=True)
        for kind, link in (("DETAIL", DETAIL), ("PROFILE", PROFILE)):
            try:
                page = _page(client, link, frozenset({"b2b-energo.ru"}))
                status = "VERIFIED" if (TENDER if kind == "DETAIL" else INN) in page["text"] else "NO_MATCH"
            except (ResearchBlocked, httpx.HTTPError) as error:
                status = str(error) if isinstance(error, ResearchBlocked) else type(error).__name__.upper()
            print("CARD27_WEB_VPS_" + kind + "=" + status, flush=True)
        assert urlsplit(url).hostname in {"www.b2b-center.ru", "b2b-center.ru"}
        print("CARD27_WEB_TENDER=" + tender, flush=True)
        print("CARD27_WEB_INN_IN_CRM=" + ("YES" if card.get(PREFIX+"COMPANY_INN") else "NO"), flush=True)
        for name in ("TAVILY_SEARCH", "TAVILY_RESEARCH_MINI"):
            enabled = env.get("SERVICEIT_VPS_B2B_" + name + "_ENABLED") == "YES"
            print("CARD27_WEB_" + name + "_ENABLED=" + ("YES" if enabled else "NO"), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("CARD27_WEB_FAILURE_CLASS=" + type(error).__name__.upper(), flush=True)
        sys.exit(2)
