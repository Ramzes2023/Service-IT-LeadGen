"""B2B-only diagnostic: no writes, replay, source fetch or model call."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys

TARGETS = (4677923, 4679263, 4677837, 4674919, 4693159, 4692643, 4692350, 4693220, 4693314, 4692011, 4694125, 4691253, 4692609)
STATES = frozenset(("ABSENT", "PENDING", "POST_UNCERTAIN", "VERIFIED", "EXISTING",
                    "REVIEW", "RESERVED", "WRITE_INTENT", "NO_CHANGE", "CONFLICT",
                    "UNCERTAIN", "NONE", "YES", "NO"))
def exact_file(name, expected):
    path = Path("/app/leadpilot") / name
    return bool(re.fullmatch(r"[0-9a-f]{64}", expected or "") and path.is_file()
                and hashlib.sha256(path.read_bytes()).hexdigest() == expected)


def writer():
    """Run only inside the separate B2B writer; its duplicate probe is proven live."""
    emit("WRITER_START", "YES")
    exact = len(sys.argv) == 3 and exact_file("serviceit_vps_b2b_bridge.py", sys.argv[2])
    emit("WRITER_SHA_MATCH", "YES" if exact else "NO")
    if not exact:
        return 2
    import httpx
    from leadpilot.serviceit_vps_pg_queue import secret_file
    from leadpilot.serviceit_vps_b2b_bridge import exact_duplicate, positive_duplicate_control, validate_webhook
    emit("WRITER_IMPORTS", "YES")
    base = validate_webhook(secret_file(dict(os.environ), "SERVICEIT_BITRIX_WEBHOOK"))
    complete = True
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=12) as client:
        if not positive_duplicate_control(client, base):
            emit("DUPLICATE_CONTROL", "NO")
            return 2
        emit("DUPLICATE_CONTROL", "YES")
        for tender in TARGETS:
            label = f"TENDER_{tender}_"
            try:
                matches = exact_duplicate(client, base, f"B2B-CENTER-{tender}")
                emit(label+"CRM_COUNT", len(matches))
                if len(matches) == 1:
                    emit(label+"CRM_CARD", int(matches[0]["id"]))
            except Exception:
                complete = False
                emit(label+"CRM_DUPLICATE_READ_COMPLETE", "NO")
    emit("WRITER_CRM_READ_COMPLETE", "YES" if complete else "NO")
    emit("WRITER_CRM_WRITES", 0)
    return 0 if complete else 2


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


def revision_proof(current, source, revision, compute):
    """A later receipt is evidence only if source and both digests agree."""
    saved, fields, salt, digest = revision
    return (source_matches(source, saved) and
            proof(fields, salt, digest, compute) == "YES" and
            proof(current, salt, digest, compute) == "YES" and
            fields == current)


def v37_patch_digest(fields, salt):
    # V37 worker canonical JSON checksum, not a full V11 dossier digest.
    if type(fields) is not dict or not fields or type(salt) is not bytes or len(salt) != 16:
        raise ValueError("INVALID_PATCH")
    raw = json.dumps(fields, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(salt + raw).digest()


def v37_patch_proof(item, source, revision, decision_key):
    """Verify only the latest signed patch's own keys, never imply full dossier proof."""
    saved, fields, salt, digest = revision
    if not source_matches(source, saved) or proof(fields, salt, digest, v37_patch_digest) != "YES":
        return False
    if any(key not in item for key in fields):
        return False
    projected = {key: item[key] for key in fields}
    # The production V37 readback allows null for an explicitly emptied decision.
    if fields.get(decision_key) == "" and projected.get(decision_key) is None:
        projected[decision_key] = ""
    return projected == fields and proof(projected, salt, digest, v37_patch_digest) == "YES"


def source_matches(source, saved):
    if not isinstance(source, str) or not re.fullmatch(r"B2B-CENTER-[0-9]{5,12}", source):
        return False
    if not isinstance(saved, str) or not re.fullmatch(r"[0-9a-f]{64}", saved):
        return False
    return hmac.compare_digest(hashlib.sha256(source.encode("ascii")).hexdigest(), saved)


def main():
    emit("COORDINATOR_START", "YES")
    exact = len(sys.argv) == 2 and exact_file("serviceit_vps_b2b_dossier_v11_worker.py", sys.argv[1])
    emit("LIVE_SHA_MATCH", "YES" if exact else "NO")
    if not exact:
        return 2
    import httpx
    from leadpilot.serviceit_vps_pg_queue import db, secret_file
    from leadpilot.serviceit_vps_b2b_dossier_v11_worker import compute_digest
    from leadpilot.serviceit_enrichment_worker import Rest, normalized, validate_identity
    from leadpilot.serviceit_b2b_hold_ai_fields import AI_FIELDS, PREFIX
    emit("COORDINATOR_IMPORTS", "YES")
    env = dict(os.environ)
    with db(env) as conn, conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        rows = conn.execute("""
            SELECT v.card_id,v.status,v.source_hash,v.after_fields,v.salt,v.digest,b.source_id,v.before_fields
            FROM serviceit_vps_b2b_dossier_v11_receipts v
            LEFT JOIN serviceit_vps_b2b_inbox b ON b.card_id=v.card_id AND b.state='VERIFIED'
            WHERE v.status IN ('CONFLICT','UNCERTAIN','WRITE_INTENT')
               OR (v.status='RESERVED' AND v.updated_at < now()-interval '30 minutes')
            ORDER BY v.card_id LIMIT 31
        """).fetchall()
        emit("BLOCKER_SCAN_COMPLETE", "YES" if len(rows) <= 30 else "NO")
        emit("BLOCKER_ROWS_SEEN", len(rows))
        for i, (card, status, saved, fields, salt, digest, source, before) in enumerate(rows[:30], 1):
            emit(f"BLOCKER_{i}_CARD", card)
            emit(f"BLOCKER_{i}_STATUS", status)
            emit(f"BLOCKER_{i}_SOURCE_MATCH", "YES" if source_matches(source, saved) else "NO")
            emit(f"BLOCKER_{i}_STORED_PROOF", proof(fields, salt, digest, compute_digest))
        data = {}
        revisions = {}
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
            if row and row[1] is not None:
                revisions[tender] = {}
                # Static table names only; SELECT in the existing read-only transaction.
                for label, table in (
                    ("V19", "serviceit_vps_b2b_v19_research_receipts"),
                    ("V20", "serviceit_vps_b2b_v20_revision_receipts"),
                ):
                    if conn.execute("SELECT to_regclass(%s)", ("public."+table,)).fetchone()[0]:
                        latest = conn.execute(
                            f"SELECT source_hash,after_fields,salt,digest FROM {table} "
                            "WHERE card_id=%s AND status='VERIFIED' "
                            "ORDER BY updated_at DESC LIMIT 1", (row[1],)
                        ).fetchone()
                        if latest:
                            revisions[tender][label] = latest
                if conn.execute("SELECT to_regclass(%s)", (
                    "public.serviceit_vps_b2b_dossier_consistency_v37_receipts",
                )).fetchone()[0]:
                    latest = conn.execute("""
                        SELECT source_hash,after_fields,salt,digest
                        FROM serviceit_vps_b2b_dossier_consistency_v37_receipts
                        WHERE card_id=%s AND status='VERIFIED'
                        ORDER BY attempt_no DESC LIMIT 1
                    """, (row[1],)).fetchone()
                    if latest:
                        revisions[tender]["V37"] = latest
            emit(f"TENDER_{tender}_PG", row[0] if row else "ABSENT")
            if row and row[1] is not None:
                emit(f"TENDER_{tender}_PG_CARD", row[1])
                emit(f"TENDER_{tender}_V11", row[2] or "NONE")
                emit(f"TENDER_{tender}_V11_PROOF", proof(row[4], row[5], row[6], compute_digest))
                emit(f"TENDER_{tender}_V11_SOURCE_MATCH", "YES" if source_matches(source, row[3]) else "NO")
                emit(f"TENDER_{tender}_V12_PRESENT", "YES" if row[7] else "NO")
    emit("PG_READ_COMPLETE", "YES")
    secret = secret_file(env, "SERVICEIT_BITRIX_WEBHOOK")
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=12) as client:
        rest = Rest(secret, client)
        complete = True
        def read_card(card):
            item = normalized(rest("crm.item.get", {
                "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y"
            })["item"])
            validate_identity(item, card)
            return item
        for i, (card, status, saved, fields, salt, digest, source, before) in enumerate(rows[:30], 1):
            if not source_matches(source, saved):
                continue
            try:
                item = read_card(card)
                same = item.get(PREFIX+"SIGNAL_ID") == source
                emit(f"BLOCKER_{i}_CRM_IDENTITY", "YES" if same else "NO")
                if same:
                    current = {key: item.get(key) for key in AI_FIELDS}
                    emit(f"BLOCKER_{i}_CRM_PROOF", proof(current, salt, digest, compute_digest))
                    emit(f"BLOCKER_{i}_BEFORE_FIELDS_EQUAL", "YES" if current == before else "NO")
            except Exception:
                complete = False
                emit(f"BLOCKER_{i}_CRM_READ_COMPLETE", "NO")
        for tender, row in data.items():
            label = f"TENDER_{tender}_"
            source = f"B2B-CENTER-{tender}"
            try:
                if not row or row[1] is None:
                    continue
                card = int(row[1])
                emit(label+"CRM_READ_CARD", card)
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
                    for version in ("V19", "V20"):
                        revision = revisions.get(tender, {}).get(version)
                        emit(label+version+"_VERIFIED_PRESENT", "YES" if revision else "NO")
                        if revision:
                            emit(label+version+"_CURRENT_PROOF", "YES" if revision_proof(
                                current, source, revision, compute_digest) else "NO")
                    v37 = revisions.get(tender, {}).get("V37")
                    emit(label+"V37_VERIFIED_PRESENT", "YES" if v37 else "NO")
                    if v37:
                        emit(label+"V37_SOURCE_MATCH", "YES" if source_matches(source, v37[0]) else "NO")
                        emit(label+"V37_STORED_PROOF", proof(v37[1], v37[2], v37[3], v37_patch_digest))
                        emit(label+"V37_CURRENT_PATCH_PROOF", "YES" if v37_patch_proof(
                            item, source, v37, PREFIX+"REVIEW_DECISION") else "NO")
                        if isinstance(v37[1], dict):
                            emit(label+"V37_AI_FIELDS_COVERED", len(set(v37[1]) & set(AI_FIELDS)))
            except Exception:
                complete = False
                emit(label+"CRM_READ_COMPLETE", "NO")
        emit("CRM_READ_COMPLETE", "YES" if complete else "NO")
    emit("CRM_WRITES", 0)
    emit("PG_WRITES", 0)
    return 0 if complete and len(rows) <= 30 else 2


if __name__ == "__main__":
    try:
        raise SystemExit(writer() if "--writer" in sys.argv else main())
    except Exception as exc:
        # Fixed categorical codes only. No exception text, SQL, tokens or data.
        code = 1 if isinstance(exc, ImportError) else 2 if isinstance(exc, OSError) else 3 if isinstance(exc, ValueError) else 4
        emit("AUDIT_ERROR_CLASS", code)
        emit("AUDIT_COMPLETE", "NO")
        raise SystemExit(2) from None
