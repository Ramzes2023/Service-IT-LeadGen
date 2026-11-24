"""B2B-only diagnostic: no writes, replay, source fetch or model call."""
import hashlib
import hmac
import os
from pathlib import Path
import re
import sys

TARGETS = (4677923, 4679263, 4677837, 4674919, 4693159, 4692643, 4692350, 4693220)
STATES = frozenset(("ABSENT", "PENDING", "POST_UNCERTAIN", "VERIFIED", "EXISTING",
                    "REVIEW", "RESERVED", "WRITE_INTENT", "NO_CHANGE", "CONFLICT",
                    "UNCERTAIN", "NONE", "YES", "NO"))
MODULES = ("serviceit_vps_b2b_dossier_v11_worker.py", "serviceit_vps_b2b_bridge.py")


def emit(key, value):
    raw = str(value)
    if not re.fullmatch(r"[A-Z0-9_]{1,80}", key):
        raise ValueError("UNSAFE_KEY")
    if raw not in STATES and not re.fullmatch(r"[0-9]{1,10}", raw):
        raise ValueError("UNSAFE_VALUE")
    print("B2B_EXACT_" + key + "=" + raw, flush=True)


def proof(fields, salt, digest, compute):
    if fields is None and salt is None and digest is None:
        return "NONE"
    try:
        if len(bytes(salt)) != 16 or len(bytes(digest)) != 32:
            return "NO"
        return "YES" if hmac.compare_digest(compute(fields, bytes(salt)), bytes(digest)) else "NO"
    except Exception:
        return "NO"


def source_matches(source, saved):
    if not isinstance(source, str) or not re.fullmatch(r"B2B-CENTER-[0-9]{5,12}", source):
        return False
    if not isinstance(saved, str) or not re.fullmatch(r"[0-9a-f]{64}", saved):
        return False
    return hmac.compare_digest(hashlib.sha256(source.encode("ascii")).hexdigest(), saved)


def main():
    import httpx
    from leadpilot.serviceit_vps_pg_queue import db, secret_file
    from leadpilot.serviceit_vps_b2b_dossier_v11_worker import compute_digest
    from leadpilot.serviceit_vps_b2b_bridge import exact_duplicate, positive_duplicate_control, validate_webhook
    from leadpilot.serviceit_enrichment_worker import Rest, normalized, validate_identity
    from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX

    hashes = sys.argv[1:]
    exact = len(hashes) == len(MODULES) and all(
        re.fullmatch(r"[0-9a-f]{64}", expected) and
        hashlib.sha256((Path("/app/leadpilot") / name).read_bytes()).hexdigest() == expected
        for name, expected in zip(MODULES, hashes))
    emit("LIVE_SHA_MATCH", "YES" if exact else "NO")
    if not exact:
        return 2
    env = dict(os.environ)
    with db(env) as conn, conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        rows = conn.execute("""
            SELECT v.card_id,v.status,v.source_hash,v.after_fields,v.salt,v.digest,b.source_id
            FROM serviceit_vps_b2b_dossier_v11_receipts v
            LEFT JOIN serviceit_vps_b2b_inbox b ON b.card_id=v.card_id AND b.state='VERIFIED'
            WHERE v.status IN ('CONFLICT','UNCERTAIN','WRITE_INTENT')
               OR (v.status='RESERVED' AND v.updated_at < now()-interval '30 minutes')
            ORDER BY v.card_id LIMIT 31
        """).fetchall()
        emit("BLOCKER_SCAN_COMPLETE", "YES" if len(rows) <= 30 else "NO")
        emit("BLOCKER_ROWS_SEEN", len(rows))
        for i, (card, status, saved, fields, salt, digest, source) in enumerate(rows[:30], 1):
            emit(f"BLOCKER_{i}_CARD", card)
            emit(f"BLOCKER_{i}_STATUS", status)
            emit(f"BLOCKER_{i}_SOURCE_MATCH", "YES" if source_matches(source, saved) else "NO")
            emit(f"BLOCKER_{i}_STORED_PROOF", proof(fields, salt, digest, compute_digest))
        data = {}
        for tender in TARGETS:
            source = f"B2B-CENTER-{tender}"
            row = conn.execute("""
                SELECT b.state,b.card_id,v.status,v.source_hash,v.after_fields,v.salt,v.digest,
                       EXISTS (SELECT 1 FROM serviceit_vps_b2b_dossier_v12_continuity_receipts a
                               WHERE a.card_id=b.card_id)
                FROM serviceit_vps_b2b_inbox b
                LEFT JOIN serviceit_vps_b2b_dossier_v11_receipts v ON v.card_id=b.card_id
                WHERE b.source_id=%s
            """, (source,)).fetchone()
            data[tender] = row
            emit(f"TENDER_{tender}_PG", row[0] if row else "ABSENT")
            if row and row[1] is not None:
                emit(f"TENDER_{tender}_PG_CARD", row[1])
                emit(f"TENDER_{tender}_V11", row[2] or "NONE")
                emit(f"TENDER_{tender}_V11_PROOF", proof(row[4], row[5], row[6], compute_digest))
                emit(f"TENDER_{tender}_V11_SOURCE_MATCH", "YES" if source_matches(source, row[3]) else "NO")
                emit(f"TENDER_{tender}_V12_PRESENT", "YES" if row[7] else "NO")
    secret = secret_file(env, "SERVICEIT_BITRIX_WEBHOOK")
    base = validate_webhook(secret)
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=12) as client:
        if not positive_duplicate_control(client, base):
            emit("DUPLICATE_CONTROL", "NO")
            return 2
        emit("DUPLICATE_CONTROL", "YES")
        rest = Rest(secret, client)
        complete = True
        def read_card(card):
            item = normalized(rest("crm.item.get", {
                "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y"
            })["item"])
            validate_identity(item, card)
            return item
        for i, (card, status, saved, fields, salt, digest, source) in enumerate(rows[:30], 1):
            if not source_matches(source, saved):
                continue
            try:
                item = read_card(card)
                same = item.get(PREFIX+"SIGNAL_ID") == source
                emit(f"BLOCKER_{i}_CRM_IDENTITY", "YES" if same else "NO")
                if same:
                    current = {key: item.get(key) for key in AI_FIELDS}
                    emit(f"BLOCKER_{i}_CRM_PROOF", proof(current, salt, digest, compute_digest))
            except Exception:
                complete = False
                emit(f"BLOCKER_{i}_CRM_READ_COMPLETE", "NO")
        for tender, row in data.items():
            label = f"TENDER_{tender}_"
            source = f"B2B-CENTER-{tender}"
            try:
                matches = exact_duplicate(client, base, source)
                emit(label+"CRM_COUNT", len(matches))
                if len(matches) != 1:
                    continue
                card = int(matches[0]["id"])
                emit(label+"CRM_CARD", card)
                item = read_card(card)
                same = item.get(PREFIX+"SIGNAL_ID") == source and bool(row and row[1] == card)
                emit(label+"CRM_IDENTITY", "YES" if same else "NO")
                current = {key: item.get(key) for key in AI_FIELDS}
                emit(label+"AI_FIELDS_NONEMPTY", sum(isinstance(v, str) and bool(v.strip()) for v in current.values()))
                emit(label+"AI_FIELDS_TOTAL", len(AI_FIELDS))
                if row and row[2] == "VERIFIED" and same:
                    emit(label+"V11_CURRENT_FIELDS_EQUAL", "YES" if current == row[4] else "NO")
                    # Later legitimate revisions may supersede raw V11 fields.
                    emit(label+"V11_CURRENT_PROOF", proof(current, row[5], row[6], compute_digest))
            except Exception:
                complete = False
                emit(label+"CRM_READ_COMPLETE", "NO")
        emit("CRM_READ_COMPLETE", "YES" if complete else "NO")
    emit("CRM_WRITES", 0)
    emit("PG_WRITES", 0)
    return 0 if complete and len(rows) <= 30 else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        emit("AUDIT_COMPLETE", "NO")
        raise SystemExit(2) from None
