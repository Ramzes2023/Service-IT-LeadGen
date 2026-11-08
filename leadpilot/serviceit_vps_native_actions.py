"""PostgreSQL one-use receipt for post-call activities and finance tasks.

Old GitHub reservations remain unchanged.  Native receipts use independent
operation+scope+subject+version keys, so a later call or a new approved finance
report version can be distinguished from a previous one. A reservation commits
BEFORE any external CRM/task request and is never automatically retried.
No customer text, passwords, report contents or private URLs are persisted.
"""
from __future__ import annotations

import re

from .serviceit_vps_pg_queue import VpsBlocked

OPERATIONS = frozenset({
    "POSTCALL",
    "FINANCE_REQUEST_ADD", "FINANCE_REPORT_ADD",
    "FINANCE_REPORT_UPDATE", "FINANCE_REQUEST_UPDATE",
})
STATUSES = frozenset({"RESERVED", "VERIFIED", "HOLD", "UNCERTAIN", "NEEDS_REVIEW"})
DDL = """
CREATE TABLE IF NOT EXISTS serviceit_vps_native_actions (
    operation TEXT NOT NULL CHECK (
      operation IN ('POSTCALL','FINANCE_REQUEST_ADD','FINANCE_REPORT_ADD',
                    'FINANCE_REPORT_UPDATE','FINANCE_REQUEST_UPDATE')),
    scope_id BIGINT NOT NULL CHECK(scope_id BETWEEN 1 AND 2000000000),
    subject_id BIGINT NOT NULL CHECK(subject_id BETWEEN 1 AND 2000000000),
    version TEXT NOT NULL CHECK(version = 'v1' OR version ~ '^[a-f0-9]{64}$'),
    status TEXT NOT NULL CHECK (
      status IN ('RESERVED','VERIFIED','HOLD','UNCERTAIN','NEEDS_REVIEW')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (operation, scope_id, subject_id, version)
);
"""


def migrate_actions(connection):
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute(DDL)


class PostgresActionFence:
    """Interface-compatible substitute for versioned cloud action fences."""

    def __init__(self, connection, operation: str, scope_id: int, version="v1"):
        if operation not in OPERATIONS:
            raise VpsBlocked("NATIVE_ACTION_OPERATION_INVALID")
        if type(scope_id) is not int or not 1 <= scope_id <= 2_000_000_000:
            raise VpsBlocked("NATIVE_ACTION_SCOPE_INVALID")
        if type(version) is not str or not re.fullmatch(
                r"v1|[a-f0-9]{64}", version):
            raise VpsBlocked("NATIVE_ACTION_VERSION_INVALID")
        self.connection = connection
        self.operation = operation
        self.scope_id = scope_id
        self.version = version
        self.subject_id = None
        self.active = False

    def reserve(self, subject_id) -> str:
        if (type(subject_id) is not int
                or not 1 <= subject_id <= 2_000_000_000):
            raise VpsBlocked("NATIVE_ACTION_SUBJECT_INVALID")
        if self.subject_id is not None:
            raise VpsBlocked("NATIVE_ACTION_FENCE_REUSE")
        self.subject_id = subject_id
        with self.connection.transaction():
            with self.connection.cursor() as cur:
                cur.execute(
                    "INSERT INTO serviceit_vps_native_actions "
                    "(operation,scope_id,subject_id,version,status) "
                    "VALUES(%s,%s,%s,%s,'RESERVED') "
                    "ON CONFLICT(operation,scope_id,subject_id,version) "
                    "DO NOTHING RETURNING subject_id",
                    (self.operation, self.scope_id, subject_id, self.version))
                self.active = cur.fetchone() is not None
        return "RESERVED" if self.active else "ALREADY_FENCED_NO_RETRY"

    def finalize(self, subject_id, result: str) -> bool:
        if not self.active or self.subject_id != subject_id:
            return False
        if result not in STATUSES - {"RESERVED"}:
            result = "NEEDS_REVIEW"
        with self.connection.transaction():
            with self.connection.cursor() as cur:
                cur.execute(
                    "UPDATE serviceit_vps_native_actions SET status=%s, "
                    "updated_at=now() WHERE operation=%s AND scope_id=%s "
                    "AND subject_id=%s AND version=%s AND status='RESERVED' "
                    "RETURNING subject_id",
                    (result, self.operation, self.scope_id,
                     self.subject_id, self.version))
                changed = cur.fetchone() is not None
        if changed:
            self.active = False
        return changed
