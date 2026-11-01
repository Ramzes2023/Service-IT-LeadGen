"""Regression: Windows cannot delete temporary DB while any SQLite handle is open.

A SQLite Connection context manager commits/rolls back but does NOT call
close(). These tests deliberately track explicit close() calls, independent
of OS-specific garbage collection timing. No real sources, CRM, or network.
"""
import sqlite3

from leadpilot.architecture_demo import simulate
from leadpilot.core import initialize, get_leads, metrics, ingest, qualify


class TrackedSQLiteConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.explicitly_closed = False

    def close(self):
        self.explicitly_closed = True
        return super().close()


def _track(monkeypatch):
    original = sqlite3.connect
    observed = []

    def tracked(*args, **kwargs):
        assert "factory" not in kwargs
        obj = original(*args, factory=TrackedSQLiteConnection, **kwargs)
        observed.append(obj)
        return obj

    monkeypatch.setattr(sqlite3, "connect", tracked)
    return observed


def test_core_closes_initialize_get_leads_metrics_even_when_no_rows(tmp_path, monkeypatch):
    handles = _track(monkeypatch)
    db = tmp_path / "read.sqlite3"
    initialize(db)
    assert get_leads(db) == []
    assert metrics(db)["companies"] == 0
    assert len(handles) >= 4
    assert all(h.explicitly_closed for h in handles), [
        n for n, h in enumerate(handles) if not h.explicitly_closed
    ]
    db.unlink()
    assert not db.exists()


def test_all_architecture_demo_sqlite_connections_closed(tmp_path, monkeypatch):
    handles = _track(monkeypatch)
    result = simulate()
    assert result["mode"] == "SYNTHETIC_ARCHITECTURE_DEMO_ONLY"
    assert result["crm_writes"] == "NO"
    assert len(handles) >= 8, "Test must exercise a complete demo, not a stub"
    leaked = [index for index, h in enumerate(handles) if not h.explicitly_closed]
    assert leaked == [], f"SQLite connections not explicitly closed: {leaked}"


def test_core_closes_after_realistic_ingest_and_qualification(tmp_path, monkeypatch):
    handles = _track(monkeypatch)
    db = tmp_path / "pipeline.sqlite3"
    initialize(db)
    ingest(db, {
        "inn": "7700000009", "name": "TEST ONLY", "region": "Москва",
        "industry": "торговля", "source_name": "synthetic-fixture",
        "source_url": "https://example.invalid/signal",
        "signal_type": "procurement", "evidence": "Synthetic buyer need",
        "signal_date": "2026-10-31",
    })
    qualify(db, today="2026-10-31")
    assert get_leads(db)
    assert metrics(db)["companies"] == 1
    assert all(h.explicitly_closed for h in handles)
    db.unlink()
    assert not db.exists()
