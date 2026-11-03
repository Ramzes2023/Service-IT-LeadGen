"""One-process HTTPS Bitrix24 event receiver + exact-ID AI card worker.

Use ONE Uvicorn worker with a durable mounted SQLite directory. It receives
onCrmDynamicItemAdd, persists the exact ID, and polls ONLY the event queue,
not crm.item.list. New cards receive manager-ready HOLD reports; separately
corporate-approved evidence receives GigaChat + explainable Score.

OFF by default. No network, disk mutations or model calls at import time.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import os
import re
from pathlib import Path
import threading
import time

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .ai import AIProvider, config_from_env
from .serviceit_bitrix_event_receiver import app as receiver
from .serviceit_bitrix_event_autofill import run_event_cycle
from .serviceit_enrichment_store import Store
from .serviceit_enrichment_worker import Rest

LIVE_FLAG = "YES_1062_21_EVENT"
MAX_POLL_SECONDS = 15


class RuntimeBlocked(ValueError):
    pass


def runtime_config(env: dict, *, is_mount=None) -> dict:
    if env.get("SERVICEIT_EVENT_AUTOFILL_ENABLED") != LIVE_FLAG:
        return {"enabled": False}
    require = lambda condition, code: condition or (_ for _ in ()).throw(RuntimeBlocked(code))
    require(
        isinstance(env.get("SERVICEIT_BITRIX_APPLICATION_TOKEN"), str)
        and len(env["SERVICEIT_BITRIX_APPLICATION_TOKEN"]) >= 24,
        "BITRIX_EVENT_TOKEN_REQUIRED",
    )
    require(bool(env.get("SERVICEIT_BITRIX_WEBHOOK")), "BITRIX_REST_WEBHOOK_REQUIRED")
    raw_path = env.get("SERVICEIT_BITRIX_EVENT_QUEUE_DB", "")
    db = Path(raw_path)
    require(db.is_absolute() and db.name == "new-card-events.sqlite3",
            "DEDICATED_EVENT_QUEUE_REQUIRED")
    mount_check = is_mount or (lambda path: path.is_mount())
    require(mount_check(db.parent), "DURABLE_MOUNT_REQUIRED")
    approvals = Path(env.get("SERVICEIT_APPROVAL_DIR", ""))
    require(approvals.is_absolute() and approvals.is_dir(),
            "CORPORATE_APPROVAL_DIRECTORY_REQUIRED")
    privacy = env.get("SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED")
    require(privacy in ("YES", "NO"), "MODEL_PRIVACY_DECISION_REQUIRED")
    if privacy == "YES":
        require(env.get("AI_ENABLE") == "1" and env.get("AI_PROVIDER") == "gigachat",
                "GIGACHAT_OPT_IN_REQUIRED")
        # Do not log or return any credentials. No HTTP calls here.
        config_from_env(env)
    preliminary = env.get("SERVICEIT_PRELIMINARY_PUBLIC_TITLE_AI_ENABLED", "NO")
    require(preliminary in ("YES", "NO"), "PRELIMINARY_AI_CONSENT_INVALID")
    require(not (preliminary == "YES" and privacy != "YES"),
            "PRELIMINARY_AI_PRIVACY_REQUIRED")
    hosts = frozenset()
    if preliminary == "YES":
        raw_hosts = env.get("SERVICEIT_PRELIMINARY_PUBLIC_HOSTS", "")
        entries = [x.strip().lower() for x in raw_hosts.split(",") if x.strip()]
        require(0 < len(entries) <= 20 and all(
            re.fullmatch(r"(?:[a-z0-9-]{1,63}\\.)+[a-z]{2,20}", x)
            and not x.endswith((".test", ".invalid", ".localhost", ".example", ".local"))
            and not x.startswith(("localhost.", "127.", "0."))
            for x in entries
        ), "PRELIMINARY_AI_PUBLIC_HOST_ALLOWLIST_REQUIRED")
        hosts = frozenset(entries)
    return {
        "enabled": True,
        "db_path": db,
        "approvals_dir": approvals,
        "webhook": env["SERVICEIT_BITRIX_WEBHOOK"],
        "model_enabled": privacy == "YES",
        "public_ai_hosts": hosts,
    }


class Runtime:
    def __init__(self):
        self.status = "DISABLED"
        self.stop = threading.Event()
        self.worker_thread: threading.Thread | None = None

    def work(self, cfg: dict, env: dict):
        provider = None
        try:
            with httpx.Client(trust_env=False, follow_redirects=False, timeout=20) as client:
                rest = Rest(cfg["webhook"], client)
                store = Store(cfg["db_path"])
                model = None
                if cfg["model_enabled"]:
                    provider = AIProvider(config_from_env(env), retry_unauthorized=False)
                    model = provider.classify
                while not self.stop.is_set():
                    try:
                        run_event_cycle(
                            store, rest, approvals_dir=cfg["approvals_dir"],
                            model=model, public_ai_hosts=cfg["public_ai_hosts"],
                            max_items=10,
                        )
                        # Only a numeric heartbeat, no CRM/customer/model data.
                        with store.db() as db:
                            db.execute(
                                "INSERT OR REPLACE INTO worker_meta VALUES ('event_worker_heartbeat',?)",
                                (str(time.time()),),
                            )
                        self.status = "RUNNING"
                    except Exception:
                        self.status = "REVIEW_REQUIRED"
                    self.stop.wait(MAX_POLL_SECONDS)
        except Exception:
            self.status = "REVIEW_REQUIRED"
        finally:
            if provider is not None:
                provider.close()

    def start(self, cfg: dict, env: dict):
        if not cfg["enabled"]:
            return
        self.stop.clear()
        # Initialize the on-disk store before accepting Bitrix callbacks.
        Store(cfg["db_path"])
        self.status = "STARTING"
        self.worker_thread = threading.Thread(
            target=self.work, args=(cfg, env),
            daemon=True, name="serviceit-bitrix-exact-event-worker",
        )
        self.worker_thread.start()

    def shutdown(self):
        self.stop.set()
        if self.worker_thread is not None:
            self.worker_thread.join(timeout=20)


_runtime = Runtime()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    cfg = runtime_config(dict(os.environ))
    _runtime.start(cfg, dict(os.environ))
    try:
        yield
    finally:
        _runtime.shutdown()


app = FastAPI(
    title="ServiceIT event-only Bitrix manager automation",
    docs_url=None, redoc_url=None, openapi_url=None,
    lifespan=lifespan,
)
app.include_router(receiver.router)


@app.get("/integrations/serviceit/bitrix24/worker-health")
async def worker_health():
    # No secrets, event IDs, customer data, source titles or queue contents.
    status = _runtime.status
    ok = status in ("RUNNING", "DISABLED")
    return JSONResponse(
        {"status": status, "cloud_worker": status == "RUNNING"},
        status_code=200 if ok else 503,
    )
