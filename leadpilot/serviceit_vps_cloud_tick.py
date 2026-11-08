"""Opt-in VPS scheduler: GitHub repository_dispatch tick, NO CRM or AI access.

GitHub Actions retains the original encrypted ledger and existing secrets.
This isolated VPS process reads only a repo-scoped GitHub token from a Docker
secret and sends a bounded poll request once per 600 seconds. It must never
receive Ledger/HMAC/Bitrix credentials or customer data.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import time

import httpx

from .serviceit_vps_pg_queue import VpsBlocked, secret_file

REPO = "Ramzes2023/Service-IT-LeadGen"
DISPATCH_URI = f"https://api.github.com/repos/{REPO}/dispatches"
EVENT_TYPE = "serviceit_vps_poll_tick_v1"
SOURCE = "serviceit-vps-cloud-tick-v1"
INTERVAL_SECONDS = 600


class CloudTickBlocked(ValueError):
    """Reject a tick without contacting GitHub."""


def fixed_log(status: str) -> None:
    """Never print API responses, tokens, customer info or URLs."""
    assert status in {
        "TICK_DRY_RUN_PASS", "TICK_DISABLED", "TICK_DISPATCH_ACCEPTED",
        "TICK_DISPATCH_UNVERIFIED", "TICK_TRANSPORT_UNVERIFIED",
        "TICK_CONFIG_BLOCKED",
    }
    print(json.dumps({"service": "SERVICEIT_VPS_CLOUD_TICK",
                      "status": status, "crm_writes": 0,
                      "ai_calls": 0}, sort_keys=True), flush=True)


def validate_env(env: dict) -> bool:
    """The new service starts disabled and NEVER switches CRM writer flags."""
    if env.get("SERVICEIT_VPS_CLOUD_TICK_ENABLED", "NO") == "NO":
        return False
    if env.get("SERVICEIT_VPS_CLOUD_TICK_ENABLED") != "YES":
        raise CloudTickBlocked("TICK_CONFIG_BLOCKED")
    if env.get("SERVICEIT_VPS_WRITER_MODE", "READONLY") != "READONLY":
        raise CloudTickBlocked("TICK_CONFIG_BLOCKED")
    if env.get("SERVICEIT_VPS_CUTOVER_AUTHORIZED", "NO") != "NO":
        raise CloudTickBlocked("TICK_CONFIG_BLOCKED")
    if env.get("SERVICEIT_VPS_POLL_ENABLED", "NO") != "NO":
        raise CloudTickBlocked("TICK_CONFIG_BLOCKED")
    if env.get("SERVICEIT_VPS_INTAKE_ENABLED", "NO") != "NO":
        raise CloudTickBlocked("TICK_CONFIG_BLOCKED")
    return True


def dispatch_once(env: dict, *, client: httpx.Client, now: int | None = None) -> str:
    if not validate_env(env):
        return "TICK_DISABLED"
    # Never accept plaintext tokens in Compose environment variables.
    if env.get("GITHUB_TOKEN"):
        raise CloudTickBlocked("TICK_CONFIG_BLOCKED")
    token = secret_file(env, "GITHUB_TOKEN")
    if not isinstance(token, str) or len(token) < 12:
        raise CloudTickBlocked("TICK_CONFIG_BLOCKED")
    seconds = int(datetime.now(timezone.utc).timestamp()) if now is None else now
    if type(seconds) is not int or seconds < 1_700_000_000:
        raise CloudTickBlocked("TICK_CONFIG_BLOCKED")
    try:
        res = client.post(
            DISPATCH_URI,
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={
                "event_type": EVENT_TYPE,
                "client_payload": {
                    "source": SOURCE,
                    "kind": "ID_ONLY_POLL",
                    "sent_at": seconds,
                },
            },
            timeout=20,
            follow_redirects=False,
        )
    except httpx.HTTPError:
        return "TICK_TRANSPORT_UNVERIFIED"
    return "TICK_DISPATCH_ACCEPTED" if res.status_code == 204 else "TICK_DISPATCH_UNVERIFIED"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--loop", action="store_true")
    args = parser.parse_args(argv)
    env = dict(os.environ)
    try:
        if args.dry_run:
            # No secret reads, HTTP calls, customer data or CRM writes.
            validate_env(env)
            fixed_log("TICK_DRY_RUN_PASS")
            return 0
        if not validate_env(env):
            fixed_log("TICK_DISABLED")
            if args.loop:
                while True:
                    time.sleep(3600)
            return 0
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            while True:
                try:
                    status = dispatch_once(env, client=client)
                except (CloudTickBlocked, VpsBlocked, ValueError, TypeError):
                    status = "TICK_CONFIG_BLOCKED"
                fixed_log(status)
                if args.once or status == "TICK_CONFIG_BLOCKED":
                    return 0 if status == "TICK_DISPATCH_ACCEPTED" else 2
                time.sleep(INTERVAL_SECONDS)
    except (CloudTickBlocked, VpsBlocked, ValueError, TypeError):
        fixed_log("TICK_CONFIG_BLOCKED")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
