"""Record externally verified public buyer evidence for three signed tenders."""
import hashlib
import os
from pathlib import Path
import secrets
import sys

import httpx

from leadpilot import serviceit_b2b_v19_backfill as v19
from leadpilot import serviceit_b2b_v20_versioned as v20
from leadpilot.serviceit_b2b_dossier_v11 import REPORT, sync_confirmed_passport
from leadpilot.serviceit_b2b_hold_ai_fields import PREFIX
from leadpilot.serviceit_vps_b2b_document_worker import LOCK_ID as DOCUMENT_LOCK
from leadpilot.serviceit_vps_pg_queue import db
from leadpilot.serviceit_vps_worker import exact_environment, live_permitted

BUYERS = {
    19: ("4676478", "АО «Тандер»", "2310031475",
         "https://www.b2b-center.ru/app/market-next/servernoe-oborudovanie-dlia-data/tender-4676478/",
         "https://www.b2b-center.ru/firms/ao-tander/260966/"),
    21: ("4676643", "АО «Издательство „Просвещение“»", "7715995942",
         "https://zakupki.kontur.ru/4676643_458", "https://zakupki.kontur.ru/4676643_458"),
    23: ("4677404", "ООО УК «Промышленно-металлургический холдинг»", "0411075740",
         "https://zakupki.kontur.ru/4677404_40", "https://zakupki.kontur.ru/4677404_40"),
}


def out(key, value):
    print(f"BUYER_HANDOFF_{key}={value}", flush=True)


def read(rest, card):
    item = v20.normalized(rest("crm.item.get", {
        "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y",
    })["item"])
    v20.validate_identity(item, card)
    return item


def prepare(before, buyer, inn, source, inn_source):
    values = {
        "BUYER": f"{buyer} [публичная карточка закупки: {source}]",
        "INN": f"{inn} [публичные реквизиты: {inn_source}]",
    }
    result = sync_confirmed_passport(before, values)
    a = result[REPORT].index("\n01  ПАСПОРТ ЗАКУПКИ\n")
    b = result[REPORT].index("\n02  ПРЕДМЕТ И ТЕХНИЧЕСКОЕ ЗАДАНИЕ\n", a)
    lines = result[REPORT][a:b].splitlines()
    assert any(line.startswith("Заказчик: ") and buyer in line for line in lines)
    assert any(line.startswith("ИНН: ") and inn in line for line in lines)
    budget = f"Бюджет: сумма в открытой карточке не указана [{source}]"
    lines = [budget if line.startswith("Бюджет: ") else line for line in lines]
    result = {**result, REPORT: result[REPORT][:a] + "\n".join(lines) + result[REPORT][b:]}
    header = f"Заказчик по открытым источникам: {buyer}, ИНН {inn}.\nИсточник: {source}\n"
    for field in (PREFIX + "AI_SUMMARY", PREFIX + "AI_MANAGER_BRIEF"):
        if not result[field].startswith(header):
            lines = result[field].splitlines()
            lines = [line for line in lines if line not in {
                "Заказчик: не установлен", "ЗАКАЗЧИК  не установлен",
            }]
            result[field] = header + "\n".join(lines)
    assert all(0 < len(value) <= (12000 if field == REPORT else 4096)
               for field, value in result.items())
    return result


def apply(conn, rest, card, source, notice):
    tid, buyer, inn, proof, inn_proof = BUYERS[card]
    assert source == f"B2B-CENTER-{tid}"
    first = read(rest, card)
    original_url, _ = v20.safe_notice(notice, card, source, first)
    assert original_url == notice["source_url"]
    if v19.active_card_write(conn, card):
        return "OTHER_WRITE_IN_FLIGHT"
    before = v20.original_nine(first)
    desired = prepare(before, buyer, inn, proof, inn_proof)
    if desired == before:
        return "ALREADY_VERIFIED"
    source_hash = hashlib.sha256(source.encode("ascii")).hexdigest()
    for table in (v19.TABLE, v20.TABLE):
        previous = conn.execute(
            f"SELECT status,source_hash FROM {table} WHERE card_id=%s "
            "ORDER BY updated_at DESC LIMIT 1", (card,)).fetchone()
        if previous and (previous[0] not in {"VERIFIED", "NO_NEW_EVIDENCE", "INSUFFICIENT_CAPACITY"}
                         or previous[1] != source_hash):
            return "PRIOR_RECEIPT_REQUIRES_REVIEW"
    fresh = read(rest, card)
    if v20.fingerprint(first) != v20.fingerprint(fresh):
        return "CARD_CHANGED"
    attempt = v20.reserve(conn, card, source_hash, before)
    salt, digest = v20.transition(conn, card, attempt, "RESERVED", "WRITE_INTENT", desired=desired)
    try:
        rest("crm.item.update", {
            "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y", "fields": desired,
        })
        after = read(rest, card)
        assert v20.original_nine(after) == desired and v20.non_ai_unchanged(fresh, after)
        assert secrets.compare_digest(v20.compute_digest(desired, salt), digest)
    except Exception:
        v20.transition(conn, card, attempt, "WRITE_INTENT", "UNCERTAIN")
        return "WRITE_UNCERTAIN"
    v20.transition(conn, card, attempt, "WRITE_INTENT", "VERIFIED")
    return "WRITTEN_AND_READ_BACK"


def main():
    env = dict(os.environ)
    assert live_permitted(env)
    assert hashlib.sha256(Path("/app/leadpilot/serviceit_enrichment_worker.py").read_bytes()).hexdigest() == sys.argv[1]
    exact = exact_environment(env)
    out("FIVE_MINUTE_AUTOMATIC_ENRICHMENT", "YES" if
        exact.get("SERVICEIT_VPS_B2B_V19_BACKFILL_ENABLED") == "YES"
        and exact.get("SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED") == "YES" else "NO")
    with db(env) as conn, httpx.Client(trust_env=False, follow_redirects=False, timeout=20) as client:
        for lock in (v20.LOCK_ID, DOCUMENT_LOCK):
            if not conn.execute("SELECT pg_try_advisory_lock(%s)", (lock,)).fetchone()[0]:
                out("LOCK_BUSY", "YES")
                return 2
        rows = conn.execute("""
            SELECT b.card_id,b.source_id,b.notice_json
            FROM serviceit_vps_b2b_inbox b
            JOIN serviceit_vps_b2b_dossier_v11_receipts v
              ON v.card_id=b.card_id AND v.status='VERIFIED'
            WHERE b.state='VERIFIED' AND b.card_id=ANY(%s)
            ORDER BY b.card_id DESC
        """, (list(BUYERS),)).fetchall()
        assert len(rows) == len(BUYERS)
        rest = v20.Rest(exact["SERVICEIT_BITRIX_WEBHOOK"], client)
        ok = True
        for card, source, notice in rows:
            try:
                status = apply(conn, rest, card, source, notice)
            except Exception as error:
                status = type(error).__name__.upper()
            out(f"CARD_{card}", status)
            ok &= status in {"WRITTEN_AND_READ_BACK", "ALREADY_VERIFIED"}
        return 0 if ok else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        out("FAILURE", type(error).__name__.upper())
        raise SystemExit(2) from None
