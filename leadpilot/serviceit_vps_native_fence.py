"""Local PostgreSQL one-use fence for VPS-only AI cards.

The old GitHub Fernet ledger is NOT copied, replaced, or modified.  This
fence may be used ONLY after independent proof that GitHub writers have
stopped and a fresh, future-only activation date is set.

No plaintext AI fields are stored: a random per-item salt and SHA-256 digest
allow read-only reconciliation after uncertain CRM writes.  No implicit
retry after a reservation, even when the worker or the network crashes.
"""
from __future__ import annotations

import hashlib
import json
import secrets

from .serviceit_vps_pg_queue import CARD_MIN, CARD_MAX, VpsBlocked

NAMESPACE = "AI_NINE_V1"
NATIVE_NAMESPACES = frozenset({
    "AI_NINE_V1", "OWN_STOP_V1", "CRM_DUP_V1",
    "KANBAN_TITLE_V1", "TASK_V1", "FNS_CRM_V1",
    "PROVISIONAL_SCORE_V1", "FREE_MSP_SCORE_V1",
    "B2B_RESEARCH_V1", "B2B_RESEARCH_V2",
})
ALLOWED_STATUSES = frozenset({
    "HOLD_9_WRITTEN", "HOLD", "UNCERTAIN", "LEASE_LOST",
    "IDLE", "NEEDS_REVIEW", "VERIFIED", "HOLD_9_PRESENT",
})

DDL = """
CREATE TABLE IF NOT EXISTS serviceit_vps_native_receipts (
    namespace TEXT NOT NULL CHECK (namespace ~ '^[A-Z0-9_]{3,48}$'),
    card_id BIGINT NOT NULL CHECK (card_id BETWEEN 8 AND 2000000000),
    status TEXT NOT NULL CHECK (status IN
      ('RESERVED', 'WRITE_INTENT', 'HOLD_9_WRITTEN', 'HOLD',
       'UNCERTAIN', 'LEASE_LOST', 'IDLE', 'NEEDS_REVIEW',
       'VERIFIED', 'HOLD_9_PRESENT')),
    field_names TEXT[],
    digest_salt BYTEA,
    expected_digest BYTEA,
    reserved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(namespace, card_id),
    CHECK(
      (field_names IS NULL AND digest_salt IS NULL AND expected_digest IS NULL)
      OR (cardinality(field_names) IN (1, 2, 3, 9, 12)
          AND octet_length(digest_salt) = 16
          AND octet_length(expected_digest) = 32)
    )
);
"""


def exact_fields(fields: dict) -> bytes:
    if type(fields) is not dict or not fields:
        raise VpsBlocked("NATIVE_FIELDS_INVALID")
    if any(not isinstance(k, str) for k in fields):
        raise VpsBlocked("NATIVE_FIELDS_INVALID")
    return json.dumps(fields, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str).encode("utf-8")


def digested(fields: dict, salt: bytes) -> bytes:
    if type(salt) is not bytes or len(salt) != 16:
        raise VpsBlocked("NATIVE_SALT_INVALID")
    return hashlib.sha256(salt + exact_fields(fields)).digest()


class PostgresNativeFence:
    """Transactionally fenced, never-blind-replayed per-card AI writer."""

    def __init__(self, connection, namespace=NAMESPACE):
        if namespace not in NATIVE_NAMESPACES:
            raise VpsBlocked("NATIVE_NAMESPACE_INVALID")
        self.connection = connection
        self.namespace = namespace
        self.card = None
        self.active = False
        self.receipt = None
        self._prewrite_once = False

    @staticmethod
    def validate_card(card):
        if type(card) is not int or not CARD_MIN <= card <= CARD_MAX or card in (3, 7):
            raise VpsBlocked("NATIVE_CARD_OUT_OF_SCOPE")

    def reserve(self, card: int) -> str:
        self.validate_card(card)
        if self.card is not None:
            raise VpsBlocked("NATIVE_FENCE_REUSE_FORBIDDEN")
        self.card = card
        with self.connection.transaction():
            with self.connection.cursor() as cur:
                cur.execute(
                    "INSERT INTO serviceit_vps_native_receipts"
                    "(namespace, card_id, status) VALUES(%s, %s, 'RESERVED') "
                    "ON CONFLICT(namespace, card_id) DO NOTHING RETURNING card_id",
                    (self.namespace, card))
                new = cur.fetchone() is not None
        self.active = bool(new)
        return "RESERVED" if new else "ALREADY_FENCED_NO_RETRY"

    def checkpoint_write(self, card: int, fields: dict):
        """Persist exact pre-write fingerprint BEFORE crm.item.update."""
        if self.card != card or not self.active:
            raise VpsBlocked("NATIVE_CHECKPOINT_NO_RESERVATION")
        from .serviceit_bitrix_dispatch_worker import AI_FIELDS, FIELDS
        if set(fields) not in (AI_FIELDS, FIELDS):
            raise VpsBlocked("NATIVE_CHECKPOINT_FIELDS_INVALID")
        salt = secrets.token_bytes(16)
        digest = digested(fields, salt)
        with self.connection.transaction():
            with self.connection.cursor() as cur:
                cur.execute(
                    "UPDATE serviceit_vps_native_receipts "
                    "SET status='WRITE_INTENT', field_names=%s, "
                    "digest_salt=%s, expected_digest=%s, updated_at=now() "
                    "WHERE namespace=%s AND card_id=%s AND status='RESERVED' "
                    "RETURNING card_id",
                    (sorted(fields), salt, digest, self.namespace, card))
                if cur.fetchone() is None:
                    raise VpsBlocked("NATIVE_CHECKPOINT_UNVERIFIED")

    def finalize(self, card: int, result: str) -> bool:
        if not self.active or self.card != card:
            return False
        status = result if result in ALLOWED_STATUSES else "NEEDS_REVIEW"
        # For timeline/kanban workers, the old API checkpoints intent by
        # setting receipt.expected and finalizing UNCERTAIN BEFORE POST.
        # Preserve the same durable two-phase semantics in PostgreSQL.
        prewrite = (status == "UNCERTAIN" and self.receipt is not None
                    and not self._prewrite_once)
        if prewrite:
            expected = self.receipt.get("expected")
            if type(expected) is not dict or len(expected) not in (1, 2, 3):
                raise VpsBlocked("NATIVE_PREWRITE_EXPECTED_INVALID")
            salt = secrets.token_bytes(16)
            digest = digested(expected, salt)
            with self.connection.transaction():
                with self.connection.cursor() as cur:
                    cur.execute(
                        "UPDATE serviceit_vps_native_receipts "
                        "SET status='UNCERTAIN', field_names=%s, "
                        "digest_salt=%s, expected_digest=%s, updated_at=now() "
                        "WHERE namespace=%s AND card_id=%s AND status='RESERVED' "
                        "RETURNING card_id",
                        (sorted(expected), salt, digest, self.namespace, card))
                    ok = cur.fetchone() is not None
            self._prewrite_once = ok
            return ok
        allowed_from = ("'UNCERTAIN'" if self._prewrite_once
                        else "'RESERVED','WRITE_INTENT'")
        with self.connection.transaction():
            with self.connection.cursor() as cur:
                cur.execute(
                    "UPDATE serviceit_vps_native_receipts SET status=%s, "
                    "updated_at=now() WHERE namespace=%s AND card_id=%s "
                    "AND status IN (" + allowed_from + ") RETURNING card_id",
                    (status, self.namespace, card))
                ok = cur.fetchone() is not None
        if ok:
            self.active = False
        return ok

    def reconcile(self, card, rest):
        """GET-only, compare salted hash; never re-enter a CRM mutation."""
        self.validate_card(card)
        with self.connection.cursor() as cur:
            cur.execute(
                "SELECT field_names, digest_salt, expected_digest "
                "FROM serviceit_vps_native_receipts "
                "WHERE namespace=%s AND card_id=%s",
                (self.namespace, card))
            item = cur.fetchone()
        if not item or not item[0] or item[1] is None or item[2] is None:
            return "ALREADY_FENCED_NO_RETRY"
        try:
            from .serviceit_bitrix_dispatch_worker import (
                validate_identity, normalized,
            )
            response = rest("crm.item.get", {
                "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y",
            })
            observed = normalized(response["item"])
            validate_identity(observed, card)
            candidate = {name: observed.get(name) for name in item[0]}
            salt = bytes(item[1])
            return ("EXACT_TARGET_READBACK"
                    if secrets.compare_digest(digested(candidate, salt),
                                              bytes(item[2]))
                    else "UNCERTAIN_REMAINS")
        except Exception:
            return "UNCERTAIN_REMAINS"


def migrate_native(connection):
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute(DDL)
