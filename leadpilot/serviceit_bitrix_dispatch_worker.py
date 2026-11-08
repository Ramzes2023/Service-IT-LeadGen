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
from .serviceit_enrichment_worker import Worker, normalized, validate_identity, AI_FIELDS, FIELDS

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
    """GitHub CAS receipt: ambiguous writes allow observation, never blind replay."""

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
        self.receipt = None

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
                body = existing.json()
                if isinstance(body.get("content"), str):
                    receipt = json.loads(self.cipher.decrypt(base64.b64decode(body["content"])))
                    require(receipt.get("card") == card, "FENCE_IDENTITY_CONFLICT")
                    self.receipt = receipt
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
            "IDLE", "NEEDS_REVIEW", "VERIFIED", "HOLD_9_PRESENT",
        }
        status = result if result in safe_statuses else "NEEDS_REVIEW"
        payload = {"schema": "serviceit-event-v1", "card": card,
                   "status": status}
        if self.receipt and "expected" in self.receipt:
            payload["expected"] = self.receipt["expected"]
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
            if response.status_code == 200:
                self.file_sha = response.json()["content"]["sha"]
                return True
            return False
        except httpx.HTTPError:
            return False

    def checkpoint_write(self, card, fields):
        require(set(fields) in (AI_FIELDS, FIELDS), "CHECKPOINT_FIELDS_INVALID")
        self.receipt = {"expected": fields}
        require(self.finalize(card, "UNCERTAIN"), "WRITE_CHECKPOINT_UNVERIFIED")

    def reconcile(self, card, rest):
        """Retry observation only; never replay an ambiguous model or CRM write."""
        expected = (self.receipt or {}).get("expected")
        if not isinstance(expected, dict) or set(expected) not in (AI_FIELDS, FIELDS):
            return "ALREADY_FENCED_NO_RETRY"
        item = normalized(rest("crm.item.get", {
            "entityTypeId": 1062, "id": card, "useOriginalUfNames": "Y"})["item"])
        validate_identity(item, card)
        return ("EXACT_TARGET_READBACK" if all(item.get(k) == v for k, v in expected.items())
                else "UNCERTAIN_REMAINS")

    def observe(self, card, rest):
        """Owner-requested recovery: only GETs, even if no receipt exists."""
        require(type(card) is int and MIN_CARD <= card <= MAX_CARD, "RECOVERY_ID_INVALID")
        response = self.client.get(self.url(card), params={"ref": BRANCH},
                                   headers=self.headers, follow_redirects=False, timeout=20)
        require(response.status_code == 200, "RECOVERY_RECEIPT_NOT_FOUND")
        self.receipt = json.loads(self.cipher.decrypt(
            base64.b64decode(response.json()["content"])))
        require(self.receipt.get("card") == card, "RECOVERY_IDENTITY_CONFLICT")
        return {"status": self.reconcile(card, rest), "crm_writes": 0}


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
        if env.get("AI_ENABLE") != "1":
            return "MODEL_PRIVACY_OR_CONFIG_INCOMPLETE"
        try:
            cfg = config_from_env(env)
        except (RuntimeError, ValueError):
            return "MODEL_PRIVACY_OR_CONFIG_INCOMPLETE"
        if cfg.provider not in ("gigachat", "routerai"):
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
        return {"status": remote.reconcile(card, Rest(env["SERVICEIT_BITRIX_WEBHOOK"], client)),
                "crm_writes": 0}
    return _run_reserved_exact_card(card, env, client=client, remote=remote)


def _run_reserved_exact_card(card: int, env: dict, *, client: httpx.Client,
                             remote) -> dict:
    """Shared exact-card implementation; fence is chosen before entering.

    Cloud runs the legacy GitHub Fernet fence. VPS calls this only with
    the PostgreSQL fence, after independent native-only cutover gates.
    """
    # After reservation, ambiguity permits GET-only recovery, never replay.
    observed = "NEEDS_REVIEW"
    with tempfile.TemporaryDirectory(prefix="serviceit-exact-event-") as temp:
        store = Store(Path(temp) / "new-card-events.sqlite3")
        store.enqueue(card)
        provider = None
        standby = None
        try:
            raw_rest = Rest(env["SERVICEIT_BITRIX_WEBHOOK"], client)
            def rest(method, params):
                if method == "crm.item.update":
                    remote.checkpoint_write(card, params["fields"])
                return raw_rest(method, params)
            model = None
            public_hosts = frozenset()
            researcher = None
            # External investigation reads only approved PUBLIC hosts. It
            # NEVER sends a Bitrix webhook, client INN or private CRM fields.
            if (env.get("SERVICEIT_PUBLIC_RESEARCH_ENABLED") == "YES"
                    and env.get("SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED") == "YES"):
                raw_hosts = ",".join((
                    env.get("SERVICEIT_PRELIMINARY_PUBLIC_HOSTS", ""),
                    env.get("SERVICEIT_PUBLIC_RESEARCH_COMPANY_HOSTS", ""),
                ))
                names = [h.strip().lower() for h in raw_hosts.split(",") if h.strip()]
                require(len(names) <= 80 and all(
                    re.fullmatch(r"(?:[a-z0-9-]{1,63}\.)+[a-z]{2,20}", h)
                    and not h.endswith((".test", ".invalid", ".local", ".localhost"))
                    for h in names), "PUBLIC_RESEARCH_HOSTS_INVALID")
                # When the public host allowlist is empty, research is OFF.
                # This must not prevent a truthful 9-field HOLD card or send
                # any unapproved information to public sites.
                if names:
                    from .serviceit_public_evidence_research import research_public
                    approved = frozenset(names)
                    browser_render = None
                    # Optional browser is never invoked by legacy cloud workers,
                    # shadow workers or without external network firewall proof.
                    # The HTTP bridge receives ONLY the allowlisted source URL.
                    if (env.get("SERVICEIT_VPS_WRITER_MODE") == "LIVE"
                            and env.get("SERVICEIT_VPS_BROWSER_RESEARCH_ENABLED") == "YES"
                            and env.get("SERVICEIT_VPS_BROWSER_EGRESS_FIREWALL_VERIFIED") == "YES"):
                        from .serviceit_vps_browser_client import render_approved_url
                        def browser_render(url):
                            return render_approved_url(url, approved, client=client)
                    def researcher(item):
                        return research_public(
                            item, client, approved,
                            deep_search_enabled=(
                                env.get("SERVICEIT_DEEP_COMPANY_SEARCH_ENABLED") == "YES"),
                            deep_search_key=env.get("SERVICEIT_BRAVE_SEARCH_API_KEY", ""),
                            deep_llm_context_enabled=(
                                env.get("SERVICEIT_BRAVE_LLM_CONTEXT_ENABLED") == "YES"),
                            browser_render=browser_render,
                        )
            approvals = json.loads(env.get("SERVICEIT_CORPORATE_APPROVALS_JSON") or "{}")
            require(type(approvals) is dict, "APPROVAL_ADAPTER_INVALID")
            approval = approvals.get(str(card))
            # No approved public source host: do not call RouterAI merely
            # because the global model account itself is authorized.
            if (approval is not None or
                    (env.get("SERVICEIT_PRELIMINARY_PUBLIC_TITLE_AI_ENABLED") == "YES"
                     and bool(env.get("SERVICEIT_PRELIMINARY_PUBLIC_HOSTS", "").strip()))):
                require(env.get("SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED") == "YES",
                        "MODEL_PRIVACY_CONSENT_REQUIRED")
                # Only the hostname allowlist parser is needed; no remote server
                # instance is started. Validate externally configured public
                # domains in a strict inline form to avoid fake hosts.
                raw = env.get("SERVICEIT_PRELIMINARY_PUBLIC_HOSTS", "")
                names = raw.split(",") if raw else []
                require((approval is not None and not names) or (0 < len(names) <= 20 and all(
                    bool(re.fullmatch(r"(?:[a-z0-9-]{1,63}\.)+[a-z]{2,20}", x))
                    and not x.endswith((".test", ".invalid", ".local", ".localhost"))
                    for x in names)), "PUBLIC_HOST_ALLOWLIST_INVALID")
                public_hosts = frozenset(names)
                provider = AIProvider(
                    config_from_env({**env, "AI_ENABLE": "1"}),
                    retry_unauthorized=False)
                # RouterAI primary is selected only by approved workflow settings.
                # A secondary GigaChat call is made only for the identical
                # already privacy-approved title/prose, before any CRM AI write.
                # Neither provider receives the full Bitrix item or private data
                # unless an independent corporate approval permits it.
                if (provider.config.provider == "routerai"
                        and env.get("SERVICEIT_ROUTERAI_FALLBACK_TO_GIGACHAT") == "YES"
                        and env.get("GIGACHAT_CREDENTIALS")):
                    standby = AIProvider(
                        config_from_env({**env, "AI_ENABLE": "1",
                                         "AI_PROVIDER": "gigachat"}),
                        retry_unauthorized=False)
                def model(text):
                    try:
                        first = provider.classify(text)
                    except (RuntimeError, ValueError):
                        if standby is None:
                            raise
                        return standby.classify(text)
                    if (standby is not None and isinstance(first, dict)
                            and first.get("quality_status") != "complete"):
                        return standby.classify(text)
                    return first
                model.provider = provider.config.provider
            if approval is not None:
                observed = Worker(store, rest, lambda _: approval, model, enabled=True).once(card)
            else:
                kwargs = {"model": model, "public_ai_hosts": public_hosts}
                if researcher is not None:
                    kwargs["researcher"] = researcher
                observed = preliminary_once(store, rest, card, **kwargs)
        except Exception:
            observed = "NEEDS_REVIEW"
        finally:
            if standby is not None:
                standby.close()
            if provider is not None:
                provider.close()
    finalized = remote.finalize(card, observed)
    return {
        "status": ("SERVICEIT_NEW_CARD_EVENT_PROCESSED"
                   if observed in ("HOLD_9_WRITTEN", "HOLD_9_PRESENT", "VERIFIED")
                   else "SERVICEIT_NEW_CARD_NEEDS_REVIEW"),
        "result": observed,
        "remote_fence_finalized": finalized,
        "replay_allowed": False,
        # This is NOT a claim that a CRM write happened for every outcome.
        "crm_writes": 1 if observed in ("HOLD_9_WRITTEN", "VERIFIED") else "UNVERIFIED",
        "qualified_leads": int(observed == "VERIFIED"),
    }


def run_vps_native(card: int, env: dict, *, client: httpx.Client, connection) -> dict:
    """Server-only exact-card call, never requires GitHub/HMAC ledger secrets."""
    from .serviceit_vps_native_fence import PostgresNativeFence
    from .serviceit_vps_pg_queue import VpsBlocked
    if (env.get("SERVICEIT_VPS_WRITER_MODE") != "LIVE"
            or env.get("SERVICEIT_VPS_CUTOVER_AUTHORIZED") != "YES"
            or env.get("SERVICEIT_VPS_OLD_GH_WRITERS_DISABLED_ACK") != "YES"
            or env.get("SERVICEIT_VPS_INTAKE_ENABLED", "NO") != "NO"
            or env.get("SERVICEIT_VPS_POLL_ENABLED") != "YES"
            or env.get("AI_PROVIDER") != "routerai"
            or env.get("SERVICEIT_ROUTERAI_FALLBACK_TO_GIGACHAT") != "NO"
            or env.get("SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED") != "YES"
            or env.get("SERVICEIT_ROUTERAI_PRIVACY_APPROVED") != "YES_1062_21"
            or env.get("AI_ENABLE") != "1"):
        raise VpsBlocked("NATIVE_LIVE_CUTOVER_GATES_REQUIRED")
    from .ai import config_from_env
    config = config_from_env(env)
    if config.provider != "routerai":
        raise VpsBlocked("NATIVE_ROUTERAI_ONLY")
    # A queue entry alone is NOT authorization.  Re-read this exact CRM item
    # before reserving it, and independently prove creation after cutover.
    from datetime import datetime, timezone, timedelta
    from .serviceit_bitrix_poll_fallback import after_activation, date_value
    PostgresNativeFence.validate_card(card)
    now = datetime.now(timezone.utc)
    activation = env.get("SERVICEIT_VPS_NATIVE_ACTIVATION_UTC")
    if not activation:
        raise VpsBlocked("NATIVE_ACTIVATION_UTC_REQUIRED")
    started = after_activation({**env, "SERVICEIT_BITRIX_POLL_START_UTC": activation}, now)
    before = Rest(env["SERVICEIT_BITRIX_WEBHOOK"], client)(
        "crm.item.get", {"entityTypeId": 1062, "id": card,
                         "useOriginalUfNames": "Y"})
    item = normalized(before["item"])
    validate_identity(item, card)
    created = date_value(item.get("createdTime"))
    if not started <= created <= now + timedelta(minutes=2):
        raise VpsBlocked("NATIVE_HISTORICAL_OR_FUTURE_CARD_BLOCKED")
    fence = PostgresNativeFence(connection)
    status = fence.reserve(card)
    if status != "RESERVED":
        # Never repeat an existing uncertain/possibly successful CRM write.
        return {"status": fence.reconcile(
            card, Rest(env["SERVICEIT_BITRIX_WEBHOOK"], client)),
            "crm_writes": 0, "replay_allowed": False}
    return _run_reserved_exact_card(card, env, client=client, remote=fence)


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
            if env.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
                require(env.get("GITHUB_REPOSITORY") == REPOSITORY
                        and env.get("GITHUB_REF") == "refs/heads/main", "RECOVERY_SCOPE_INVALID")
                raw_id = event.get("inputs", {}).get("item_id", "")
                require(type(raw_id) is str and re.fullmatch(r"[1-9][0-9]{0,9}", raw_id),
                        "RECOVERY_ID_INVALID")
                remote = EncryptedEventFence(client, env["GITHUB_TOKEN"], env["SERVICEIT_GH_LEDGER_KEY"])
                safe = remote.observe(int(raw_id), Rest(env["SERVICEIT_BITRIX_WEBHOOK"], client))
            else:
                safe = run(event, env, client=client)
        code = 0 if safe["status"] in (
            "DISABLED", "ALREADY_FENCED_NO_RETRY", "EXACT_TARGET_READBACK",
            "SERVICEIT_NEW_CARD_EVENT_PROCESSED") else 2
    except Exception:
        code = 2
    print(json.dumps(safe, ensure_ascii=False), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
