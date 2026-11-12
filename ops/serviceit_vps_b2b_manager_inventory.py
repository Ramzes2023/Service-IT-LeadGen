"""Read-only inventory of real B2B card facts and manager schema."""
import hashlib
import os
import traceback
from pathlib import Path

import httpx

from leadpilot.bitrix_1062_mapping import _is_valid_inn
from leadpilot.serviceit_b2b_hold_ai_fields import PREFIX
from leadpilot.serviceit_b2b_dossier_consistency_v37 import (
    BRIEF, DECISION, INN, MANAGER_HEADER, MANAGER_PLACEHOLDER, RELATED_PUBLIC,
    RECOMMENDATION, REPORT,
)
from leadpilot.serviceit_b2b_dossier_consistency_worker_v37 import ENABLED
from leadpilot.serviceit_vps_b2b_dossier_v11_worker import VERSION as DOSSIER_VERSION
from leadpilot.serviceit_enrichment_worker import Rest, normalized
from leadpilot.serviceit_kanban_presentation import SCORE_FIELD, BADGE_FIELD, VERSION_FIELD
from leadpilot.serviceit_vps_pg_queue import db, secret_file
from leadpilot.serviceit_vps_worker import exact_environment

def emit(k,v):
    print(f"MANAGER_{k}={v}", flush=True)

def main():
    env = dict(os.environ)
    emit("DEEP_RESEARCH", "YES" if env.get("SERVICEIT_VPS_B2B_DEEP_RESEARCH_ENABLED") == "YES" else "NO")
    emit("MANAGER_WORKER", "YES" if exact_environment(env).get(ENABLED) == "YES" else "NO")
    emit("DOSSIER_VERSION", DOSSIER_VERSION)
    for name, key in (("serviceit_b2b_dossier_consistency_worker_v37.py","WORKER"),
                      ("serviceit_b2b_dossier_consistency_v37.py","FORM"),
                      ("serviceit_vps_b2b_dossier_v11_worker.py","V11")):
        p=Path("/app/leadpilot")/name
        emit("SHA_"+key, hashlib.sha256(p.read_bytes()).hexdigest().upper())
    with db(env) as conn, conn.transaction(), httpx.Client(trust_env=False,follow_redirects=False,timeout=16) as client:
        conn.execute("SET TRANSACTION READ ONLY")
        rest=Rest(secret_file(env,"SERVICEIT_BITRIX_WEBHOOK"),client)
        fields=rest("crm.item.fields",{"entityTypeId":1062,"useOriginalUfNames":"Y"})["fields"]
        for label, key in (("SCORE",SCORE_FIELD),("BADGE",BADGE_FIELD),("VERSION",VERSION_FIELD),
            ("REVIEW_DECISION",PREFIX+"REVIEW_DECISION"),("AI_RECOMMENDATION",PREFIX+"AI_RECOMMENDATION"),
            ("BUYER",PREFIX+"COMPANY_NAME"),("INN",PREFIX+"COMPANY_INN"),("REGION",PREFIX+"REGION")):
            detail=fields.get(key,{})
            emit("FIELD_"+label, str(detail.get("type","MISSING")).upper())
        rows=conn.execute("""
            SELECT b.card_id,b.source_id,COALESCE(v.status,'NONE')
            FROM serviceit_vps_b2b_inbox b LEFT JOIN serviceit_vps_b2b_dossier_v11_receipts v
            ON v.card_id=b.card_id
            WHERE b.state='VERIFIED' AND b.card_id IS NOT NULL ORDER BY b.card_id
        """).fetchall()
        signed = {card: (source, state) for card, source, state in rows}
        emit("SIGNED", len(signed))
        ids = set(signed)
        cursor = 0
        try:
            while True:
                records = rest("crm.item.list", {
                    "entityTypeId": 1062, "useOriginalUfNames": "Y",
                    "filter": {"categoryId": 21, ">id": cursor}, "order": {"id": "ASC"},
                    "select": ["id", "categoryId"], "start": 0,
                })["items"]
                if not records:
                    break
                page = [int(row["id"]) for row in records
                        if isinstance(row, dict) and row.get("id") is not None
                        and str(row.get("categoryId")) == "21"]
                if not page or min(page) <= cursor:
                    raise ValueError("INVALID_CRM_PAGINATION")
                ids.update(page)
                cursor = max(page)
            emit("CRM_INVENTORY", "COMPLETE")
        except (ValueError, TypeError, KeyError, httpx.HTTPError) as error:
            emit("CRM_INVENTORY", "SIGNED_PLUS_PARTIAL")
            emit("CRM_INVENTORY_ERROR", type(error).__name__.upper())
            emit("CRM_INVENTORY_LINE", traceback.extract_tb(error.__traceback__)[-1].lineno)
        emit("TOTAL", len(ids))
        items = {}
        for card in sorted(ids):
            if card in signed:
                source, state = signed[card]
                tender_id = source.split("-")[-1]
                if tender_id.isdecimal():
                    emit(f"CARD_{card}_TENDER", tender_id)
                emit(f"CARD_{card}_V11", state)
            try:
                result = rest("crm.item.get", {
                    "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y",
                })
                item = normalized(result["item"])
                if str(item["id"]) != str(card) or str(item["categoryId"]) != "21":
                    raise ValueError("CARD_SCOPE_MISMATCH")
                items[card] = item
            except Exception:
                emit(f"CARD_{card}_CRM", "READ_UNVERIFIED")
        for card, item in items.items():
            brief = str(item.get(BRIEF) or "")
            report = str(item.get(REPORT) or "")
            inn = item.get(INN)
            decision = item.get(DECISION)
            decision_state = ("EMPTY" if not decision else "AUTOMATIC_PLACEHOLDER"
                              if decision == MANAGER_PLACEHOLDER else "NONEMPTY_UNATTRIBUTED")
            emit(f"CARD_{card}_DECISION_STATE", decision_state)
            tender_id = signed.get(card, ("", ""))[0].split("-")[-1]
            related_public = [row[0] for row in RELATED_PUBLIC.get(inn, ()) if row[0] != tender_id]
            peers = [other for other, peer in items.items()
                     if other != card and _is_valid_inn(inn) and peer.get(INN) == inn]
            emit(f"CARD_{card}_SAME_INN_CARDS", len(peers))
            emit(f"CARD_{card}_LINKED_SAME_INN_CARDS", sum(
                f"Карточка №{peer} ·" in brief for peer in peers))
            emit(f"CARD_{card}_PUBLIC_RELATED_EXPECTED", len(related_public))
            emit(f"CARD_{card}_PUBLIC_RELATED_SHOWN", sum(f"№{tid} ·" in brief for tid in related_public))
            if tender_id == "4677160":
                emit(f"CARD_{card}_BASHKIR_LINK_4672286", "YES" if "№4672286 ·" in brief else "NO")
                emit(f"CARD_{card}_BASHKIR_BUDGET", "YES" if "11 028 400" in brief else "NO")
            score = str(item.get(SCORE_FIELD) if item.get(SCORE_FIELD) is not None else "")
            if score.isdecimal():
                emit(f"CARD_{card}_SCORE_VALUE", score)
            checks = {
                "INN": _is_valid_inn(inn),
                "REGION": bool(item.get(PREFIX+"REGION")),
                "BRIEF": brief.startswith(MANAGER_HEADER),
                "REPORT": bool(report),
                "BUYER_AND_ORGANIZER": all(k in brief for k in ("Заказчик:", "Организатор:")),
                "CHECKS": all(k in brief for k in ("Дубль закупки", "Действующий клиент:",
                                                    "Стоп-лист:", "ФНС / ЕГРЮЛ:")),
                "COMPLETENESS": "Полнота:" in brief,
                "SCORE": score.isdecimal() and 0 <= int(score) <= 100,
                "VERSION": bool(item.get(VERSION_FIELD)),
                "BADGE": bool(item.get(BADGE_FIELD)),
                "RECOMMENDATION": bool(item.get(RECOMMENDATION)),
            }
            for key, value in checks.items():
                emit(f"CARD_{card}_{key}", "YES" if value else "NO")

if __name__=="__main__":
    try:
        main()
    except Exception as e:
        emit("ERROR",type(e).__name__.upper())
        emit("ERROR_LINE", traceback.extract_tb(e.__traceback__)[-1].lineno)
        raise SystemExit(2) from None
