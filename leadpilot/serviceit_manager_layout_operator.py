"""GitHub Actions-only Bitrix shared layout inspector / guarded one-time operator.

Default is READ ONLY. Shared scope requires Bitrix administrative REST rights.
A live set happens only after durable encrypted complete layout backup is
ACKed in a dedicated GitHub branch, with scoped exact before/after readbacks.
Never log webhook, admin identity, CRM layout/fields or GitHub token.
"""
from __future__ import annotations

import base64
from hashlib import sha256
import hmac
import json
import os
import sys

from cryptography.fernet import Fernet
import httpx

from .bitrix_metadata_audit import validate_webhook
from .serviceit_manager_layout_safe import (
    guarded_apply_shared_layout, ManagerLayoutBlocked,
)

REPO = "Ramzes2023/Service-IT-LeadGen"
BRANCH = "serviceit-ai-encrypted-ledger"
GATE = "YES_1062_21_SHARED_MANAGER_LAYOUT"
PATH = ".serviceit/layout-backups"


class LayoutOperatorBlocked(ValueError):
    pass


def operator_gate(env, *, apply=False):
    if env.get("GITHUB_REPOSITORY") != REPO or env.get("GITHUB_REF") != "refs/heads/main":
        return "REPOSITORY_SCOPE_BLOCKED"
    if not env.get("SERVICEIT_BITRIX_WEBHOOK"):
        return "BITRIX_WEBHOOK_MISSING"
    if not apply:
        return "READ_ONLY_READY"
    if env.get("SERVICEIT_MANAGER_LAYOUT_APPLY") != GATE:
        return "OWNER_LAYOUT_OPT_IN_MISSING"
    if env.get("SERVICEIT_MANAGER_LAYOUT_ADMIN_APPROVED") != "YES":
        return "ADMIN_SHARED_SCOPE_NOT_APPROVED"
    if not env.get("GITHUB_TOKEN") or not env.get("SERVICEIT_GH_LEDGER_KEY"):
        return "ENCRYPTED_BACKUP_CREDENTIALS_MISSING"
    return "CONFIG_READY"


def _api_client(client, webhook):
    base = validate_webhook(webhook)
    def call(method, params):
        if method not in (
            "crm.item.details.configuration.get",
            "crm.item.details.configuration.set",
            "crm.item.fields",
        ):
            raise LayoutOperatorBlocked("REST_METHOD_FORBIDDEN")
        if (type(params) is not dict or params.get("entityTypeId") != 1062):
            raise LayoutOperatorBlocked("REST_ENTITY_TYPE_FORBIDDEN")
        if method == "crm.item.fields":
            if params != {"entityTypeId": 1062, "useOriginalUfNames": "Y"}:
                raise LayoutOperatorBlocked("REST_FIELDS_SCOPE_FORBIDDEN")
        elif (params.get("scope") != "C"
              or params.get("extras") != {"categoryId": 21}
              or set(params) != (
                  {"entityTypeId", "scope", "extras", "data"}
                  if method.endswith(".set") else
                  {"entityTypeId", "scope", "extras"})):
            raise LayoutOperatorBlocked("REST_LAYOUT_SCOPE_FORBIDDEN")
        if method.endswith(".set"):
            # Do not expose an unapproved layout write in this adapter:
            # guarded_apply_shared_layout calls only after the backup ACK.
            if type(params.get("data")) is not list:
                raise LayoutOperatorBlocked("REST_LAYOUT_SET_INVALID")
        try:
            response = client.post(
                base + "/" + method + ".json", json=params,
                follow_redirects=False, timeout=25,
            )
            if response.status_code != 200 or len(response.content) > 400000:
                raise LayoutOperatorBlocked("BITRIX_HTTP_NOT_VERIFIED")
            body = response.json()
            if type(body) is not dict or "error" in body or "result" not in body:
                raise LayoutOperatorBlocked("BITRIX_RESPONSE_NOT_VERIFIED")
            return body["result"]
        except httpx.HTTPError:
            raise LayoutOperatorBlocked("BITRIX_TRANSPORT_UNVERIFIED") from None
        except (ValueError, TypeError):
            raise LayoutOperatorBlocked("BITRIX_BODY_UNVERIFIED") from None
    return call


def encrypted_layout_backup(client, token, key, before, after, hashes):
    """Fail-close before any CRM set on GitHub failure, no repeated attempt."""
    if not isinstance(client, httpx.Client) or type(token) is not str or len(token) < 12:
        raise LayoutOperatorBlocked("GITHUB_BACKUP_TOKEN_MISSING")
    try:
        secret = base64.urlsafe_b64decode(key.encode("ascii"))
        if len(secret) != 32:
            raise ValueError("incorrect key")
        cipher = Fernet(key.encode("ascii"))
    except (ValueError, TypeError, UnicodeError):
        raise LayoutOperatorBlocked("GITHUB_BACKUP_CIPHER_INVALID") from None
    if set(hashes) != {"before_hash", "after_hash"}:
        raise LayoutOperatorBlocked("GITHUB_BACKUP_HASHES_INVALID")
    # HMAC prevents guessing admin layout/version from low entropy public path.
    digest = hmac.new(secret, (
        "serviceit-layout-v1:C:1062:21:" + hashes["before_hash"]
    ).encode("ascii"), "sha256").hexdigest()
    url = ("https://api.github.com/repos/" + REPO + "/contents/"
           + PATH + "/" + digest + ".enc")
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        existing = client.get(url, params={"ref": BRANCH},
                              headers=headers, follow_redirects=False, timeout=20)
        if existing.status_code == 200:
            raise LayoutOperatorBlocked("GITHUB_PRIOR_LAYOUT_ATTEMPT_EXISTS")
        if existing.status_code != 404:
            raise LayoutOperatorBlocked("GITHUB_BACKUP_STATE_UNKNOWN")
        body = json.dumps({
            "schema": "serviceit-layout-backup-v1",
            "entity_type_id": 1062, "category_id": 21, "scope": "C",
            **hashes, "before": before, "after": after,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        sealed = cipher.encrypt(body.encode("utf-8"))
        if len(sealed) > 250000:
            raise LayoutOperatorBlocked("GITHUB_BACKUP_TOO_LARGE")
        committed = client.put(url, headers=headers,
                               json={"branch": BRANCH,
                                     "message": "checkpoint(serviceit): encrypted form layout backup",
                                     "content": base64.b64encode(sealed).decode("ascii")},
                               follow_redirects=False, timeout=25)
        if committed.status_code != 201:
            raise LayoutOperatorBlocked("GITHUB_BACKUP_NOT_CONFIRMED")
        response = committed.json()
        if (not isinstance(response, dict)
                or not isinstance(response.get("content"), dict)
                or type(response["content"].get("sha")) is not str):
            raise LayoutOperatorBlocked("GITHUB_BACKUP_ACK_BAD")
        return True
    except httpx.HTTPError:
        raise LayoutOperatorBlocked("GITHUB_BACKUP_TRANSPORT_UNKNOWN") from None


def run(*, apply=False, env=None, client=None):
    settings = dict(os.environ if env is None else env)
    status = operator_gate(settings, apply=apply)
    if status not in ("READ_ONLY_READY", "CONFIG_READY"):
        return {"status": status, "crm_writes": 0}
    owned = client is None
    browser = client or httpx.Client(trust_env=False, follow_redirects=False, timeout=25)
    try:
        rest = _api_client(browser, settings["SERVICEIT_BITRIX_WEBHOOK"])
        reserve = lambda before, after, sha: encrypted_layout_backup(
            browser, settings.get("GITHUB_TOKEN", ""),
            settings.get("SERVICEIT_GH_LEDGER_KEY", ""), before, after, sha,
        )
        return guarded_apply_shared_layout(
            rest, reserve,
            apply=apply, admin_approved=status == "CONFIG_READY",
        )
    finally:
        if owned:
            browser.close()


def main():
    apply = "--apply" in sys.argv[1:]
    try:
        result = run(apply=apply)
        # Only stable bounded tokens / counts, not form contents.
        allowed = {
            "status", "added_fields", "crm_writes",
            "before_hash", "after_hash",
        }
        print(json.dumps({k: v for k, v in result.items() if k in allowed}))
        return 0 if result["status"] in (
            "PREVIEW_ONLY", "ALREADY_PRESENT", "MANAGER_LAYOUT_APPLIED_EXACT") else 2
    except Exception:
        # Do not report arbitrary exception detail (could include secret URL).
        print('{"status":"LAYOUT_BLOCKED_OR_UNCERTAIN_REVIEW_REQUIRED","crm_writes":"UNVERIFIED"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
