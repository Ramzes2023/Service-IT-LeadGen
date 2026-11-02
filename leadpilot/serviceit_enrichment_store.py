"""Dedicated production queue/ledger. Never opens historical one-shot ledgers."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time
import uuid

from .serviceit_bitrix_new_card_events import enqueue_new_item, TYPE


class ModelFence(ValueError):
    pass


class Store:
    def __init__(self, path: Path):
        self.path = path
        # Reuse intake's schema, without inserting a synthetic job.
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS new_cards (
                  entity_type_id INTEGER NOT NULL, item_id INTEGER NOT NULL,
                  status TEXT NOT NULL, received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  PRIMARY KEY(entity_type_id,item_id));
                CREATE TABLE IF NOT EXISTS enrichment (
                  item_id INTEGER PRIMARY KEY, owner TEXT, lease REAL,
                  attempts INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '',
                  model_state TEXT, write_state TEXT, expected TEXT, before_hash TEXT);
                CREATE TABLE IF NOT EXISTS worker_meta (key TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE IF NOT EXISTS model_reservations (
                  source_key TEXT PRIMARY KEY, item_id INTEGER NOT NULL,
                  reserved_at REAL NOT NULL, output_hash TEXT);
                CREATE TABLE IF NOT EXISTS enrichment_audit (
                  sequence INTEGER PRIMARY KEY AUTOINCREMENT, item_id INTEGER NOT NULL,
                  phase TEXT NOT NULL, reason TEXT NOT NULL, recorded_at REAL NOT NULL);
            """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        db.execute('PRAGMA busy_timeout=10000')
        try:
            with db:
                yield db
        finally:
            db.close()

    def claim(self, item_id=None):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            # Expired operations with any external side-effect reservation never retry.
            db.execute("""UPDATE new_cards SET status=CASE
              WHEN item_id IN (SELECT item_id FROM enrichment WHERE write_state IS NOT NULL)
              THEN 'UNCERTAIN' WHEN item_id IN
              (SELECT item_id FROM enrichment WHERE model_state IS NOT NULL)
              THEN 'NEEDS_REVIEW' ELSE 'PENDING_SOURCE_CHECK' END
              WHERE status='RUNNING' AND item_id IN
              (SELECT item_id FROM enrichment WHERE lease < ?)""", (time.time(),))
            row = db.execute("""SELECT item_id FROM new_cards WHERE entity_type_id=1062
                AND status='PENDING_SOURCE_CHECK' AND (? IS NULL OR item_id=?)
                ORDER BY item_id LIMIT 1""", (item_id,item_id)).fetchone()
            if row is None:
                return None
            item = row[0]
            db.execute('INSERT OR IGNORE INTO enrichment(item_id) VALUES (?)', (item,))
            tries = db.execute('SELECT attempts FROM enrichment WHERE item_id=?', (item,)).fetchone()[0]
            if tries >= 3:
                db.execute("UPDATE new_cards SET status='NEEDS_REVIEW' WHERE item_id=?", (item,))
                return None
            owner = uuid.uuid4().hex
            db.execute("UPDATE new_cards SET status='RUNNING' WHERE item_id=?", (item,))
            db.execute('UPDATE enrichment SET owner=?,lease=?,attempts=attempts+1 WHERE item_id=?',
                       (owner, time.time()+300, item))
            return item, owner

    def reserve(self, item, owner, phase, *, expected=None, before_hash=None, source_key=None):
        assert phase in ('model', 'write')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM enrichment WHERE item_id=?', (item,)).fetchone()
            status = db.execute('SELECT status FROM new_cards WHERE item_id=?', (item,)).fetchone()[0]
            if (not row or row['owner'] != owner or row['lease'] <= time.time()
                    or status != 'RUNNING' or row[phase+'_state'] is not None):
                raise ValueError('RESERVATION_FENCED')
            if phase == 'model':
                key = source_key or str(item)
                count = db.execute('SELECT COUNT(*) FROM model_reservations WHERE reserved_at>?',
                                   (time.time()-86400,)).fetchone()[0]
                if count >= 10:
                    raise ModelFence('MODEL_DAILY_BUDGET_HOLD')
                if db.execute('SELECT 1 FROM model_reservations WHERE source_key=?',(key,)).fetchone():
                    raise ModelFence('MODEL_SOURCE_ALREADY_RESERVED_HOLD')
                db.execute('INSERT INTO model_reservations VALUES (?,?,?,NULL)', (key,item,time.time()))
            db.execute(f'UPDATE enrichment SET {phase}_state=? WHERE item_id=?', ('RESERVED', item))
            db.execute('INSERT INTO enrichment_audit(item_id,phase,reason,recorded_at) VALUES (?,?,?,?)',
                       (item,phase,'RESERVED',time.time()))
            if phase == 'write':
                db.execute('UPDATE enrichment SET expected=?,before_hash=? WHERE item_id=?',
                           (json.dumps(expected, ensure_ascii=False), before_hash, item))

    def finish(self, item, owner, state, reason):
        assert state in ('HOLD','NEEDS_REVIEW','VERIFIED','UNCERTAIN','PENDING_SOURCE_CHECK')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            # A slow completion must never replace crash recovery's terminal
            # UNCERTAIN/NEEDS_REVIEW decision, even if owner has not changed.
            changed = db.execute("UPDATE new_cards SET status=? WHERE item_id=? "
                       "AND entity_type_id=1062 AND status='RUNNING' AND EXISTS "
                       "(SELECT 1 FROM enrichment WHERE item_id=? AND owner=? AND lease>?)",
                       (state, item, item, owner, time.time()))
            if changed.rowcount != 1:
                return False
            db.execute('UPDATE enrichment SET reason=? WHERE item_id=? AND owner=?', (reason,item,owner))
            db.execute('INSERT INTO enrichment_audit(item_id,phase,reason,recorded_at) VALUES (?,?,?,?)',
                       (item,state,reason,time.time()))
            return True

    def model_output(self, item, output_hash):
        with self.db() as db:
            db.execute('UPDATE model_reservations SET output_hash=? WHERE item_id=?', (output_hash,item))

    def inspect(self):
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT n.item_id,n.status,e.reason,e.attempts,'
                'e.model_state,e.write_state FROM new_cards n LEFT JOIN enrichment e USING(item_id) '
                'ORDER BY n.item_id LIMIT 100')]

    def metrics(self):
        with self.db() as db:
            return dict(db.execute('SELECT status,COUNT(*) FROM new_cards GROUP BY status').fetchall())

    def enqueue(self, item):
        return enqueue_new_item(self.path, {'status':'ACCEPTED_NEW_ITEM',
                                'entity_type_id':TYPE,'item_id':item})
