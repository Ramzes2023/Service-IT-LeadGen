"""Free exact-event Bitrix24 -> manager AI/HOLD workflow, independent of sources.

A Bitrix24 outgoing webhook is authenticated by a separately deployed
owner-only Apps Script HTTPS bridge. The bridge sends a signed *ID only*
repository_dispatch to GitHub Actions. This worker checks that HMAC,
then atomically writes an encrypted never-replay reservation in GitHub before
ANY live REST or AI side effect. It processes only a new ID of CRM 1062/21,
not a CRM listing or existing historic items.

Absent independent evidence/permission, the nine AI fields are a truthful
manager HOLD report. Real GigaChat on a public approved title is opt-in.
No source processing, task, stage, contact, outreach, or Score fabrication.
"""
from __future__ import annotations

import base64
from contextlib import nullcontext
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import tempfile
import time

from cryptography.fernet import Fernet
import httpx

from .ai import AIProvider, config_from_env
from .serviceit_enrichment_store import Store
from .serviceit_enrichment_worker import Rest
from .serviceit_windows_autofill import preliminary_once

REPOSITORY = "Ramzes2023/Service-IT-LeadGen"
PORTAL = "serviseit.bitrix24.ru"
BRANCH = "serviceit-ai-encrypted-ledger"
FOLDER = ".serviceit/new-card-events"
EVENT = "serviceit_bitrix_item_add_v1"
LIVE_FLAG = "YES_1062_21_CLOUD"
MIN_CARD = 8
MAX_CARD = 2_000_000_000
MAX_AGE_SECONDS = 1800


class EventCloudBlocked(ValueError):
    pass


def require(test, code):
    if not test:
        raise EventCloudBlocked(code)


def verify_event(event: dict, secret: str, *, now: int | None = None) -> int:
    require(type(event) is dict and event.get("action") == EVENT,
            "EVENT_TYPE_INVALID")
    repo = event.get("repository")
    require(type(repo) is dict and repo.get("full_name") == REPOSITORY,
            "REPOSITORY_INVALID")
    payload = event.get("client_payload")
    require(type(payload) is dict and set(payload) == {
        "version", "portal", "entity_type_id", "item_id", "timestamp",
        "nonce", "signature",
    }, "EVENT_SCHEMA_INVALID")
    require(payload.get("version") == 1 and payload.get("portal") == PORTAL
            and type(payload.get("entity_type_id")) is int
            and payload["entity_type_id"] == 1062,
            "EVENT_SCOPE_INVALID")
    card = payload.get("item_id")
    require(type(card) is int and MIN_CARD <= card <= MAX_CARD
            and card not in (3, 7), "EVENT_CARD_ID_INVALID")
    timestamp = payload.get("timestamp")
    require(type(timestamp) is int
            and abs((int(time.time()) if now is None else now) - timestamp)
            <= MAX_AGE_SECONDS, "EVENT_EXPIRED")
    nonce = payload.get("nonce")
    require(type(nonce) is str and
            re.fullmatch(r"[0-9a-f-]{32,40}", nonce) is not None,
            "EVENT_NONCE_INVALID")
    require(type(secret) is str and len(secret) >= 32,
            "EVENT_HMAC_SECRET_MISSING")
    signature = payload.get("signature")
    require(type(signature) is str and
            re.fullmatch(r"sha256=[0-9a-f]{64}", signature) is not None,
            "EVENT_SIGNATURE_INVALID")
    message = f"v1|{PORTAL}|1062|{card}|{timestamp}|{nonce}"
    expected = "sha256=" + hmac.new(secret.encode("utf-8"),
                                     message.encode("utf-8"), sha256).hexdigest()
    require(hmac.compare_digest(signature, expected), "EVENT_HMAC_MISMATCH")
    return card


class EncryptedEventFence:
    """GitHub Contents branch: no overwrite on intake; ambiguous writes stay fenced."""

    def __init__(self, client: httpx.Client, token: str, key: str):
        require(isinstance(client, httpx.Client), "GITHUB_CLIENT_INVALID")
        require(type(token) is str and len(token) >= 12, "GITHUB_TOKEN_MISSING")
        try:
            self.cipher = Fernet(key.encode("ascii"))
        except Exception:
            raise EventCloudBlocked("FENCE_ENCRYPTION_KEY_INVALID") from None
        # Use an unguessable keyed path: plain SHA256 of sequential CRM IDs
        # is enumerable in a PUBLIC repository.
        self.path_key = base64.urlsafe_b64decode(key.encode("ascii"))
        self.client = client
        self.headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self.file_sha = None
        self.reservation_url = None

    def url(self, card: int) -> str:
        # Public branch filenames must not reveal guessable numeric CRM IDs.
        token = hmac.new(
            self.path_key, f"serviceit-v1:1062:21:{card}".encode(),
            sha256).hexdigest()
        return ("https://api.github.com/repos/" + REPOSITORY
                + "/contents/" + FOLDER + "/" + token + ".enc")

    def reserve(self, card: int) -> str:
        url = self.url(card)
        try:
            existing = self.client.get(
                url, params={"ref": BRANCH}, headers=self.headers,
                follow_redirects=False, timeout=20)
            if existing.status_code == 200:
                return "ALREADY_FENCED_NO_RETRY"
            require(existing.status_code == 404,
                    "FENCE_REMOTE_STATE_UNVERIFIED")
            payload = {"schema": "serviceit-event-v1", "card": card,
                       "status": "RESERVED_UNVERIFIED"}
            encoded = self.cipher.encrypt(
                json.dumps(payload, separators=(",", ":")).encode("utf-8"))
            result = self.client.put(url, headers=self.headers,
                                     follow_redirects=False, timeout=25,
                                     json={
                                         "branch": BRANCH,
                                         "message": "checkpoint(serviceit): new Bitrix event reserved",
                                         "content": base64.b64encode(encoded).decode("ascii"),
                                     })
            require(result.status_code == 201, "FENCE_WRITE_UNCONFIRMED_NO_RETRY")
            body = result.json()
            require(type(body) is dict and type(body.get("content")) is dict
                    and type(body["content"].get("sha")) is str,
                    "FENCE_ACK_UNVERIFIED")
            self.reservation_url, self.file_sha = url, body["content"]["sha"]
            return "RESERVED"
        except httpx.HTTPError:
            raise EventCloudBlocked("FENCE_TRANSPORT_UNVERIFIED") from None

    def finalize(self, card: int, result: str) -> bool:
        """Best-effort status; original RESERVE file still blocks duplicates."""
        if self.file_sha is None or self.reservation_url is None:
            return False
        safe_statuses = {
            "HOLD_9_WRITTEN", "HOLD", "UNCERTAIN", "LEASE_LOST",
            "IDLE", "NEEDS_REVIEW",
        }
        status = result if result in safe_statuses else "NEEDS_REVIEW"
        payload = {"schema": "serviceit-event-v1", "card": card,
                   "status": status}
        ciphertext = self.cipher.encrypt(
            json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        try:
            response = self.client.put(
                self.reservation_url, headers=self.headers, follow_redirects=False,
                timeout=25, json={
                    "branch": BRANCH, "sha": self.file_sha,
                    "message": "checkpoint(serviceit): exact Bitrix result",
                    "content": base64.b64encode(ciphertext).decode("ascii"),
                })
            return response.status_code == 200
        except httpx.HTTPError:
            return False


def gate(env: dict):
    if env.get("SERVICEIT_BITRIX_EVENT_AI_ENABLED") != LIVE_FLAG:
        return "DISABLED"
    if (env.get("GITHUB_REPOSITORY") != REPOSITORY
            or env.get("GITHUB_REF") != "refs/heads/main"
            or env.get("GITHUB_EVENT_NAME") != "repository_dispatch"):
        return "GITHUB_SCOPE_INVALID"
    for name in ("SERVICEIT_BITRIX_EVENT_BRIDGE_KEY", "SERVICEIT_GH_LEDGER_KEY",
                 "SERVICEIT_BITRIX_WEBHOOK", "GITHUB_TOKEN"):
        if not env.get(name):
            return "CONFIG_" + name + "_MISSING"
    decision = env.get("SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED", "NO")
    if decision not in ("YES", "NO"):
        return "PRIVACY_DECISION_REQUIRED"
    if decision == "YES":
        if (env.get("AI_ENABLE") != "1" or env.get("AI_PROVIDER") != "gigachat"
                or not env.get("GIGACHAT_CREDENTIALS")):
            return "MODEL_PRIVACY_OR_CONFIG_INCOMPLETE"
    return "READY"


def run(event: dict, env: dict, *, client: httpx.Client) -> dict:
    status = gate(env)
    if status != "READY":
        return {"status": status, "crm_writes": 0}
    card = verify_event(event, env["SERVICEIT_BITRIX_EVENT_BRIDGE_KEY"])
    remote = EncryptedEventFence(
        client, env["GITHUB_TOKEN"], env["SERVICEIT_GH_LEDGER_KEY"])
    reserved = remote.reserve(card)
    if reserved != "RESERVED":
        return {"status": reserved, "crm_writes": 0}
    # IMPORTANT: after the remote reservation, any missing/ambiguous response
    # is a permanent REVIEW, not an automatic replay. This is deliberate.
    observed = "NEEDS_REVIEW"
    with tempfile.TemporaryDirectory(prefix="serviceit-exact-event-") as temp:
        store = Store(Path(temp) / "new-card-events.sqlite3")
        store.enqueue(card)
        provider = None
        try:
            rest = Rest(env["SERVICEIT_BITRIX_WEBHOOK"], client)
            model = None
            public_hosts = frozenset()
            if env.get("SERVICEIT_PRELIMINARY_PUBLIC_TITLE_AI_ENABLED") == "YES":
                require(env.get("SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED") == "YES",
                        "MODEL_PRIVACY_CONSENT_REQUIRED")
                from .serviceit_bitrix_event_autofill_app import runtime_config
                # Only the hostname allowlist parser is needed; no remote server
                # instance is started. Validate externally configured public
                # domains in a strict inline form to avoid fake hosts.
                raw = env.get("SERVICEIT_PRELIMINARY_PUBLIC_HOSTS", "")
                names = raw.split(",")
                require(0 < len(names) <= 20 and all(
                    bool(re.fullmatch(r"(?:[a-z0-9-]{1,63}\.)+[a-z]{2,20}", x))
                    and not x.endswith((".test", ".invalid", ".local", ".localhost"))
                    for x in names), "PUBLIC_HOST_ALLOWLIST_INVALID")
                public_hosts = frozenset(names)
                provider = AIProvider(
                    config_from_env({**env, "AI_ENABLE": "1",
                                     "AI_PROVIDER": "gigachat"}),
                    retry_unauthorized=False)
                model = provider.classify
            observed = preliminary_once(
                store, rest, card, model=model,
                public_ai_hosts=public_hosts)
        except Exception:
            observed = "NEEDS_REVIEW"
        finally:
            if provider is not None:
                provider.close()
    finalized = remote.finalize(card, observed)
    return {
        "status": ("SERVICEIT_NEW_CARD_EVENT_PROCESSED"
                   if observed == "HOLD_9_WRITTEN"
                   else "SERVICEIT_NEW_CARD_NEEDS_REVIEW"),
        "result": observed,
        "remote_fence_finalized": finalized,
        "replay_allowed": False,
        # This is NOT a claim that a CRM write happened for every outcome.
        "crm_writes": 1 if observed == "HOLD_9_WRITTEN" else "UNVERIFIED",
        "qualified_leads": 0,
    }


def main() -> int:
    env = dict(os.environ)
    safe = {"status": "SERVICEIT_NEW_CARD_EVENT_BLOCKED",
            "crm_writes": "UNVERIFIED", "replay_allowed": False}
    try:
        path = Path(env.get("GITHUB_EVENT_PATH", ""))
        require(path.is_file() and path.stat().st_size < 16_384,
                "GITHUB_EVENT_MISSING")
        event = json.loads(path.read_text(encoding="utf-8"))
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            safe = run(event, env, client=client)
        code = 0 if safe["status"] in (
            "DISABLED", "ALREADY_FENCED_NO_RETRY",
            "SERVICEIT_NEW_CARD_EVENT_PROCESSED") else 2
    except Exception:
        code = 2
    print(json.dumps(safe, ensure_ascii=False), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
