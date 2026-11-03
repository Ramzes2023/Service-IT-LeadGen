"""Offline test of zero-cost GitHub Actions CRM worker; no external network."""
import base64
from pathlib import Path
from types import SimpleNamespace
import json
import sqlite3
import time

from cryptography.fernet import Fernet
import pytest

from leadpilot.serviceit_github_free_worker import (
    DurableStore, FreeCloudBlocked, INITIAL_MARKER, RemoteLedger, gate,
    LIVE_FLAG
)


def config(**updates):
    v = {
        "SERVICEIT_FREE_CLOUD_ENABLED": LIVE_FLAG,
        "GITHUB_REPOSITORY": "Ramzes2023/Service-IT-LeadGen",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_TOKEN": "synthetic-action-token-not-valid",
        "SERVICEIT_BITRIX_WEBHOOK": "synthetic-hook-not-valid",
        "SERVICEIT_GH_LEDGER_KEY": Fernet.generate_key().decode(),
        "SERVICEIT_GH_START_AFTER_ID": "7",
        "SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED": "NO",
    }
    v.update(updates)
    return v


class GitHubFake:
    def __init__(self, *, data=INITIAL_MARKER, fail_put=False):
        self.sha = "first-sha"
        self.data = data
        self.fail_put = fail_put
        self.put_count = 0

    def get(self, url, **kwargs):
        assert kwargs["params"]["ref"] == "serviceit-ai-encrypted-ledger"
        return SimpleNamespace(status_code=200, json=lambda: {
            "type": "file", "sha": self.sha, "encoding": "base64",
            "content": base64.b64encode(self.data).decode()
        })

    def put(self, url, **kwargs):
        self.put_count += 1
        body = kwargs["json"]
        assert body["sha"] == self.sha
        assert body["branch"] == "serviceit-ai-encrypted-ledger"
        if self.fail_put:
            return SimpleNamespace(status_code=500, json=lambda: {})
        self.data = base64.b64decode(body["content"])
        self.sha = f"sha-{self.put_count}"
        return SimpleNamespace(status_code=200, json=lambda: {
            "content": {"sha": self.sha}
        })


def test_default_off_and_unauthorized_locations_do_nothing():
    assert gate({}) == "FREE_CLOUD_NOT_ENABLED"
    assert gate(config(GITHUB_REF="refs/heads/some-pr")) == "GITHUB_SCOPE_BLOCKED"
    assert gate(config(GITHUB_REPOSITORY="fork/Service-IT-LeadGen")) == "GITHUB_SCOPE_BLOCKED"
    assert gate(config(SERVICEIT_GH_START_AFTER_ID="3")) == "CUTOVER_ID_REQUIRED"
    assert gate(config(SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED="YES")) == "GIGACHAT_SECRET_MISSING"


def test_initial_cutover_uses_exact_marker_and_is_encrypted(tmp_path):
    key = Fernet.generate_key().decode()
    gh = GitHubFake()
    remote = RemoteLedger(gh, "synthetic-token", key)
    db_path = tmp_path / "new-card-events.sqlite3"
    remote.restore(db_path, 7)
    assert gh.put_count == 1
    assert b"SQLite format 3" not in gh.data
    assert Fernet(key.encode()).decrypt(gh.data).startswith(b"SQLite format 3")
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT value FROM worker_meta WHERE key='cursor'").fetchone() == ("7",)
    remote.persist(db_path)
    assert gh.put_count == 1  # never commit unchanged sqlite every five minutes


def test_persistent_model_write_fence_remote_before_side_effect(tmp_path):
    key = Fernet.generate_key().decode()
    gh = GitHubFake()
    remote = RemoteLedger(gh, "token", key)
    path = tmp_path / "new-card-events.sqlite3"
    remote.restore(path, 7)
    store = DurableStore(path, remote)
    store.enqueue(101)
    item, owner = store.claim(101)
    store.reserve(item, owner, "model", source_key="synthetic-source-101")
    assert gh.put_count == 2
    assert b"SQLite format 3" not in gh.data
    encrypted_on_remote = Fernet(key.encode()).decrypt(gh.data)
    stored = tmp_path / "verified.sqlite3"
    stored.write_bytes(encrypted_on_remote)
    with sqlite3.connect(stored) as db:
        assert db.execute("SELECT model_state FROM enrichment WHERE item_id=101").fetchone() == ("RESERVED",)
    store.reserve(item, owner, "write", expected={"SYNTHETIC":"ONLY"}, before_hash="synthetic")
    assert gh.put_count == 3
    stored.write_bytes(Fernet(key.encode()).decrypt(gh.data))
    with sqlite3.connect(stored) as db:
        assert db.execute("SELECT write_state FROM enrichment WHERE item_id=101").fetchone() == ("RESERVED",)


def test_remote_failure_aborts_before_model_or_crm(tmp_path):
    key = Fernet.generate_key().decode()
    gh = GitHubFake()
    remote = RemoteLedger(gh, "token", key)
    path = tmp_path / "new-card-events.sqlite3"
    remote.restore(path, 7)
    store = DurableStore(path, remote)
    store.enqueue(101)
    item, owner = store.claim(101)
    gh.fail_put = True
    with pytest.raises(FreeCloudBlocked, match="LEDGER_CHECKPOINT_NOT_CONFIRMED"):
        store.reserve(item, owner, "write", expected={"ONLY":"TEST"}, before_hash="fake")
    assert gh.put_count == 2
    # The following outbound model/CRM code must never run after exception.


def test_wrong_key_never_resets_or_reseeds_ledger(tmp_path):
    correct = Fernet.generate_key().decode()
    gh = GitHubFake()
    first = RemoteLedger(gh, "token", correct)
    first.restore(tmp_path / "db1.sqlite3", 7)
    second = RemoteLedger(gh, "token", Fernet.generate_key().decode())
    with pytest.raises(FreeCloudBlocked, match="LEDGER_DECRYPT_FAILED"):
        second.restore(tmp_path / "db2.sqlite3", 7)
    assert gh.put_count == 1


def test_refuse_to_restart_with_other_cutover_id(tmp_path):
    key = Fernet.generate_key().decode()
    gh = GitHubFake()
    RemoteLedger(gh, "token", key).restore(tmp_path / "one.sqlite3", 7)
    with pytest.raises(FreeCloudBlocked, match="LEDGER_CUTOVER_MISMATCH"):
        RemoteLedger(gh, "token", key).restore(tmp_path / "two.sqlite3", 8)


def test_workflow_only_main_explicitly_enabled_and_concurrency():
    s = Path(".github/workflows/serviceit-free-cloud-ai-cards.yml").read_text()
    assert "serviceit-free-cloud-1062-21" in s
    assert "cancel-in-progress: false" in s
    assert "vars.SERVICEIT_FREE_CLOUD_ENABLED == 'YES'" in s
    assert "refs/heads/main" in s
    assert "workflow_dispatch:" in s
    assert "schedule:" not in s
    assert "3/5 * * * *" not in s
    assert "SERVICEIT_GH_LEDGER_KEY" in s
    assert "SERVICEIT_GH_START_AFTER_ID" in s
    assert "python -m leadpilot.serviceit_github_free_worker" in s
