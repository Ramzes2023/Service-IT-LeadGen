"""Free GitHub Actions 5-minute batch runner for *new* ServiceIT AI cards.

Only main scheduled workflows may enable this job. The SQLite ledger is an
authenticated encrypted blob in a dedicated GitHub branch, not an ephemeral
Actions artifact. Every MODEL and CRM WRITE reservation is uploaded to the
remote branch *before* the external side effect. If GitHub state persistence
fails, execution aborts rather than retrying a possibly costly write.

A separate trusted approval adapter is still required for real GigaChat/Score:
without one the existing business worker writes honest preliminary HOLD only.
"""
from __future__ import annotations

import base64
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

from cryptography.fernet import Fernet
import httpx

from .ai import AIProvider, config_from_env
from .serviceit_enrichment_store import Store
from .serviceit_enrichment_worker import Rest
from .serviceit_windows_autofill import run_cycle

REPOSITORY = "Ramzes2023/Service-IT-LeadGen"
STATE_BRANCH = "serviceit-ai-encrypted-ledger"
STATE_PATH = ".serviceit/encrypted-ledger.txt"
INITIAL_MARKER = b"SERVICEIT_GITHUB_STATE_INITIALIZATION_REQUIRED_V1\n"
MAX_ENCRYPTED = 8 * 1024 * 1024
LIVE_FLAG = "YES_1062_21_FREE_GITHUB"


class FreeCloudBlocked(ValueError):
    pass


def require(test: bool, reason: str):
    if not test:
        raise FreeCloudBlocked(reason)


def gate(env: dict) -> str:
    if env.get("SERVICEIT_FREE_CLOUD_ENABLED") != LIVE_FLAG:
        return "FREE_CLOUD_NOT_ENABLED"
    if env.get("GITHUB_REPOSITORY") != REPOSITORY or env.get("GITHUB_REF") != "refs/heads/main":
        return "GITHUB_SCOPE_BLOCKED"
    if not env.get("GITHUB_TOKEN"):
        return "GITHUB_TOKEN_MISSING"
    if not env.get("SERVICEIT_BITRIX_WEBHOOK"):
        return "BITRIX_SECRET_MISSING"
    if not env.get("SERVICEIT_GH_LEDGER_KEY"):
        return "LEDGER_ENCRYPTION_KEY_MISSING"
    number = env.get("SERVICEIT_GH_START_AFTER_ID", "")
    if not number.isascii() or not number.isdecimal() or not 7 <= int(number) <= 2_000_000_000:
        return "CUTOVER_ID_REQUIRED"
    if env.get("SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED") not in ("YES", "NO"):
        return "PRIVACY_DECISION_REQUIRED"
    if env["SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED"] == "YES":
        if not env.get("GIGACHAT_CREDENTIALS"):
            return "GIGACHAT_SECRET_MISSING"
        if env.get("GIGACHAT_SCOPE") not in (
                "GIGACHAT_API_PERS", "GIGACHAT_API_B2B", "GIGACHAT_API_CORP"):
            return "GIGACHAT_SCOPE_MISSING"
    return "CONFIG_READY"


class RemoteLedger:
    """GitHub Contents API CAS: one encrypted blob and explicit SHA preconditions."""
    def __init__(self, client: httpx.Client, token: str, encryption_key: str):
        try:
            self.cipher = Fernet(encryption_key.encode("ascii"))
        except (ValueError, TypeError, UnicodeError) as exc:
            raise FreeCloudBlocked("ENCRYPTION_KEY_INVALID") from exc
        self.client = client
        self.sha: str | None = None
        self.last_digest: str | None = None
        self.url = "https://api.github.com/repos/" + REPOSITORY + "/contents/" + STATE_PATH
        self.headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def fetch(self) -> bytes:
        response = self.client.get(
            self.url, params={"ref": STATE_BRANCH}, headers=self.headers,
            follow_redirects=False, timeout=25)
        require(response.status_code == 200, "LEDGER_NOT_AVAILABLE_NO_RESEED")
        raw = response.json()
        require(isinstance(raw, dict) and raw.get("type") == "file"
                and isinstance(raw.get("sha"), str)
                and raw.get("encoding") == "base64"
                and isinstance(raw.get("content"), str), "LEDGER_RESPONSE_INVALID")
        self.sha = raw["sha"]
        try:
            data = base64.b64decode(raw["content"], validate=False)
        except ValueError as exc:
            raise FreeCloudBlocked("LEDGER_CONTENT_INVALID") from exc
        require(0 < len(data) < MAX_ENCRYPTED, "LEDGER_SIZE_INVALID")
        return data

    def persist(self, file: Path):
        require(self.sha is not None, "LEDGER_CAS_SHA_MISSING")
        require(file.is_file() and 0 < file.stat().st_size < MAX_ENCRYPTED // 2,
                "LEDGER_LOCAL_DB_MISSING_OR_TOO_LARGE")
        payload = file.read_bytes()
        digest = sha256(payload).hexdigest()
        if digest == self.last_digest:
            return
        encrypted = self.cipher.encrypt(payload)
        require(len(encrypted) < MAX_ENCRYPTED, "LEDGER_CIPHERTEXT_TOO_LARGE")
        body = {
            "branch": STATE_BRANCH,
            "sha": self.sha,
            "message": "checkpoint(serviceit): encrypted CRM model/write fence",
            "content": base64.b64encode(encrypted).decode("ascii"),
        }
        response = self.client.put(
            self.url, headers=self.headers, json=body,
            follow_redirects=False, timeout=30)
        require(response.status_code == 200, "LEDGER_CHECKPOINT_NOT_CONFIRMED")
        result = response.json()
        require(isinstance(result, dict)
                and isinstance(result.get("content"), dict)
                and isinstance(result["content"].get("sha"), str),
                "LEDGER_CHECKPOINT_ACK_INVALID")
        self.sha = result["content"]["sha"]
        self.last_digest = digest

    def restore(self, db_file: Path, start_after_id: int):
        raw = self.fetch()
        if raw == INITIAL_MARKER:
            # Durable cutover first. Only exact initial marker may initialize.
            store = Store(db_file)
            with store.db() as db:
                db.execute("BEGIN IMMEDIATE")
                require(db.execute("SELECT COUNT(*) FROM new_cards").fetchone()[0] == 0,
                        "INITIAL_QUEUE_NOT_EMPTY")
                db.execute("INSERT INTO worker_meta(key,value) VALUES ('cursor',?)",
                           (str(start_after_id),))
                db.execute("INSERT INTO worker_meta(key,value) VALUES ('github_cutover',?)",
                           (str(start_after_id),))
            self.persist(db_file)
            return store

        try:
            decrypted = self.cipher.decrypt(raw)
        except Exception as exc:
            raise FreeCloudBlocked("LEDGER_DECRYPT_FAILED_NO_RESEED") from exc
        require(decrypted.startswith(b"SQLite format 3\x00"),
                "LEDGER_SQLITE_HEADER_INVALID")
        db_file.write_bytes(decrypted)
        self.last_digest = sha256(decrypted).hexdigest()
        # Validate BEFORE constructing Store, since Store would create absent
        # tables and accidentally mask damaged or mismatched state.
        with sqlite3.connect(db_file) as db:
            require(db.execute("PRAGMA quick_check").fetchone()[0] == "ok",
                    "LEDGER_SQLITE_CORRUPT")
            baseline = db.execute(
                "SELECT value FROM worker_meta WHERE key='github_cutover'"
            ).fetchone()
            require(baseline is not None and baseline[0] == str(start_after_id),
                    "LEDGER_CUTOVER_MISMATCH")
            require(db.execute(
                "SELECT value FROM worker_meta WHERE key='cursor'"
            ).fetchone() is not None, "LEDGER_CURSOR_MISSING")
        return Store(db_file)


class DurableStore(Store):
    """Synchronously checkpoint a write-ahead fence before each side effect."""
    def __init__(self, path: Path, remote: RemoteLedger):
        super().__init__(path)
        self.remote = remote

    def reserve(self, item, owner, phase, **kwargs):
        super().reserve(item, owner, phase, **kwargs)
        # Never call CRM / GigaChat if the durable checkpoint cannot be ACKed.
        self.remote.persist(self.path)


def run(env: dict | None = None) -> dict:
    env = dict(os.environ if env is None else env)
    status = gate(env)
    if status != "CONFIG_READY":
        return {"status": status, "crm_writes": 0, "gigachat_calls": 0}
    # Do not transmit secrets to outputs or workflows. Temporary directory
    # is destroyed after each run. Protected GitHub branch contains ciphertext.
    with tempfile.TemporaryDirectory(prefix="serviceit-cloud-") as temp:
        path = Path(temp) / "new-card-events.sqlite3"
        approval_dir = Path(temp) / "approvals"
        approval_dir.mkdir()
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            remote = RemoteLedger(
                client, env["GITHUB_TOKEN"], env["SERVICEIT_GH_LEDGER_KEY"])
            remote.restore(path, int(env["SERVICEIT_GH_START_AFTER_ID"]))
            store = DurableStore(path, remote)
            provider = None
            try:
                model = None
                if env["SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED"] == "YES":
                    config_env = {**env, "AI_ENABLE": "1", "AI_PROVIDER": "gigachat"}
                    provider = AIProvider(config_from_env(config_env), retry_unauthorized=False)
                    model = provider.classify
                rest = Rest(env["SERVICEIT_BITRIX_WEBHOOK"], client)
                result = run_cycle(store, rest, approvals_dir=approval_dir,
                                   model=model, max_items=10)
                # Always retain dedup cursor and finalized terminal states.
                remote.persist(path)
                return {"status": "FREE_CLOUD_CYCLE_OK",
                        "new_items_discovered": result["new_items_discovered"],
                        "processed_count": len(result["processed"]),
                        "historical_cards_touched": False}
            finally:
                if provider:
                    provider.close()


def main() -> int:
    try:
        result = run()
    except Exception:
        # Intentionally no exception detail: secrets, external URLs and
        # customer evidence must never be written to public Actions logs.
        result = {"status": "FREE_CLOUD_BLOCKED_REVIEW_REQUIRED",
                  "crm_writes": "UNVERIFIED",
                  "gigachat_calls": "UNVERIFIED"}
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["status"] in ("FREE_CLOUD_CYCLE_OK", "FREE_CLOUD_NOT_ENABLED") else 2


if __name__ == "__main__":
    sys.exit(main())
