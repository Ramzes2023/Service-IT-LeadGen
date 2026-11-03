"""Synthetic HTTP E2E of official Bitrix layout API and encrypted GitHub backup."""
import base64
import json

import httpx
from cryptography.fernet import Fernet
import pytest

from leadpilot.serviceit_manager_layout_operator import (
    run, operator_gate, GATE,
)
from leadpilot.serviceit_manager_layout_safe import (
    AI_REQUIRED, MANAGER_REQUIRED,
)


class Services:
    def __init__(self, *, deny_backup=False, prior_backup=False, changed=False):
        self.original = [{
            "name": "section_other", "title": "Сохранить текущие данные",
            "type": "section",
            "elements": [{"name": "TITLE", "optionFlags": 0},
                         {"name": "UF_CRM_13_SVCIT_SIGNAL_ID",
                          "optionFlags": 1, "options": {"extra": "keep"}}],
        }]
        self.current = json.loads(json.dumps(self.original))
        self.methods = []
        self.encrypted = None
        self.deny_backup = deny_backup
        self.prior_backup = prior_backup
        self.changed = changed
        self.layout_reads = 0

    def __call__(self, req):
        if req.url.host == "api.github.com":
            self.methods.append("GH_" + req.method)
            assert req.url.path.startswith(
                "/repos/Ramzes2023/Service-IT-LeadGen/contents/.serviceit/layout-backups/")
            assert "TITLE" not in str(req.url)
            if req.method == "GET":
                return httpx.Response(200 if self.prior_backup else 404,
                                      json={"message": "Exists" if self.prior_backup else "Not found"})
            assert req.method == "PUT"
            if self.deny_backup:
                return httpx.Response(503, json={"error": "unavailable"})
            msg = json.loads(req.content)
            assert msg["branch"] == "serviceit-ai-encrypted-ledger"
            assert "TITLE" not in msg["message"]
            self.encrypted = base64.b64decode(msg["content"])
            return httpx.Response(201, json={"content": {"sha": "synthetic-blob"}})
        assert req.url.host == "serviseit.bitrix24.ru"
        method = req.url.path.split("/")[-1].removesuffix(".json")
        self.methods.append(method)
        params = json.loads(req.content)
        assert params["entityTypeId"] == 1062
        if method == "crm.item.details.configuration.get":
            assert params["scope"] == "C" and params["extras"] == {"categoryId": 21}
            self.layout_reads += 1
            if self.changed and self.layout_reads == 2:
                self.current[0]["elements"].append({"name": "MANAGER_CHANGED"})
            return httpx.Response(200, json={"result": self.current})
        if method == "crm.item.fields":
            allowed = set(AI_REQUIRED) | set(MANAGER_REQUIRED) | {
                "TITLE", "UF_CRM_13_SVCIT_SIGNAL_ID",
            }
            return httpx.Response(200, json={"result": {
                "fields": {x: {"type": "string"} for x in allowed},
            }})
        assert method == "crm.item.details.configuration.set"
        assert self.encrypted is not None, "ENCRYPTED_BACKUP_MUST_PRECEDE_SET"
        assert params["scope"] == "C"
        self.current = params["data"]
        return httpx.Response(200, json={"result": True})


def config(key=None, **updates):
    result = {
        "GITHUB_REPOSITORY": "Ramzes2023/Service-IT-LeadGen",
        "GITHUB_REF": "refs/heads/main",
        "SERVICEIT_BITRIX_WEBHOOK": (
            "https://serviseit.bitrix24.ru/rest/2/SYNTHETICONLY123456"
        ),
        "SERVICEIT_MANAGER_LAYOUT_APPLY": GATE,
        "SERVICEIT_MANAGER_LAYOUT_ADMIN_APPROVED": "YES",
        "SERVICEIT_GH_LEDGER_KEY": key or Fernet.generate_key().decode(),
        "GITHUB_TOKEN": "synthetic-github-token-123456",
    }
    result.update(updates)
    return result


def test_default_gate_no_scope_or_secrets_never_networks():
    assert operator_gate({}) == "REPOSITORY_SCOPE_BLOCKED"
    assert operator_gate(config(GITHUB_REF="refs/heads/PR"), apply=True) == (
        "REPOSITORY_SCOPE_BLOCKED")
    assert operator_gate(config(SERVICEIT_MANAGER_LAYOUT_APPLY="NO"), apply=True) == (
        "OWNER_LAYOUT_OPT_IN_MISSING")
    assert operator_gate(config(SERVICEIT_MANAGER_LAYOUT_ADMIN_APPROVED="NO"),
                         apply=True) == "ADMIN_SHARED_SCOPE_NOT_APPROVED"


def test_default_dry_run_only_two_bitrix_reads_and_zero_changes():
    fake = Services()
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        result = run(env=config(), client=client)
    assert result["status"] == "PREVIEW_ONLY"
    assert result["added_fields"] == len(AI_REQUIRED) + len(MANAGER_REQUIRED)
    assert result["crm_writes"] == 0
    assert fake.encrypted is None
    assert fake.current == fake.original
    assert fake.methods == [
        "crm.item.details.configuration.get", "crm.item.fields",
    ]


def test_admin_authorized_shared_layout_backup_encrypted_before_exact_set():
    fake = Services()
    key = Fernet.generate_key().decode()
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        result = run(env=config(key), client=client, apply=True)
    assert result["status"] == "MANAGER_LAYOUT_APPLIED_EXACT"
    assert result["crm_writes"] == 1
    assert fake.methods == [
        "crm.item.details.configuration.get", "crm.item.fields",
        "crm.item.details.configuration.get", "GH_GET", "GH_PUT",
        "crm.item.details.configuration.set",
        "crm.item.details.configuration.get",
    ]
    assert fake.current[0] == fake.original[0]
    assert len(fake.current) == 3
    assert fake.encrypted is not None
    assert b"UF_CRM_" not in fake.encrypted and b"TITLE" not in fake.encrypted
    snapshot = json.loads(Fernet(key.encode()).decrypt(fake.encrypted))
    assert snapshot["before"] == fake.original
    assert snapshot["after"] == fake.current
    assert snapshot["category_id"] == 21 and snapshot["scope"] == "C"


def test_duplicate_backup_blocks_reapply_without_bitrix_write():
    fake = Services(prior_backup=True)
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        with pytest.raises(ValueError, match="MANAGER_LAYOUT_BACKUP_NOT_ACKED"):
            run(env=config(), client=client, apply=True)
    assert "crm.item.details.configuration.set" not in fake.methods
    assert fake.methods[-1] == "GH_GET"


def test_backup_failure_blocks_crm_write():
    fake = Services(deny_backup=True)
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        with pytest.raises(ValueError, match="MANAGER_LAYOUT_BACKUP_NOT_ACKED"):
            run(env=config(), client=client, apply=True)
    assert "crm.item.details.configuration.set" not in fake.methods


def test_racing_manager_ui_edit_blocks_before_remote_backup():
    fake = Services(changed=True)
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        with pytest.raises(ValueError, match="MANAGER_LAYOUT_CHANGED_BEFORE_WRITE"):
            run(env=config(), client=client, apply=True)
    assert not any(x.startswith("GH_") for x in fake.methods)
    assert "crm.item.details.configuration.set" not in fake.methods


def test_no_admin_gate_applies_no_gh_or_bitrix_writes():
    fake = Services()
    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        res = run(env=config(SERVICEIT_MANAGER_LAYOUT_ADMIN_APPROVED="NO"),
                  client=client, apply=True)
    assert res["status"] == "ADMIN_SHARED_SCOPE_NOT_APPROVED"
    assert res["crm_writes"] == 0 and fake.methods == []


def test_reference_urls_never_touch_customer_information():
    # There are no crm.item.get, crm.item.list, crm.item.update, tasks or
    # customer reads in the shared UI operator.
    import inspect
    import leadpilot.serviceit_manager_layout_operator as obj
    source = inspect.getsource(obj)
    assert "crm.item.list" not in source
    assert "crm.item.update" not in source
    assert "tasks.task.add" not in source
    assert "crm.item.details.configuration.set" in source
    assert "trust_env=False" in source
