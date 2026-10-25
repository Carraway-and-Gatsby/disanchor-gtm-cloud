from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import fcntl
import json
import os
import sqlite3

from .config import UTC, iso


SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
 id TEXT PRIMARY KEY, start_at TEXT, end_at TEXT, status TEXT NOT NULL DEFAULT 'NEW',
 mode TEXT NOT NULL, config_hash TEXT NOT NULL, authorization_hash TEXT,
 authorized_at TEXT, incident TEXT, final_collected INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS post_drafts (
 id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(id),
 platform TEXT NOT NULL CHECK(platform IN ('x','threads')), hypothesis TEXT NOT NULL,
 audience TEXT NOT NULL, hook_type TEXT NOT NULL, cta_type TEXT NOT NULL, variant TEXT NOT NULL,
 text TEXT NOT NULL, content_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
 scheduled_at TEXT NOT NULL, local_date TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('DRAFT','APPROVED','SCHEDULED','PUBLISHED','CONFIRMED','FAILED','UNKNOWN','CANCELLED')),
 approved_hash TEXT, provider_job_id TEXT, published_url TEXT,
 account_key TEXT, account_fingerprint TEXT,
 UNIQUE(experiment_id,platform,local_date));
CREATE TABLE IF NOT EXISTS publishing_jobs (
 id TEXT PRIMARY KEY, post_id TEXT NOT NULL UNIQUE REFERENCES post_drafts(id),
 created_at TEXT NOT NULL, state TEXT NOT NULL, reserved_quota INTEGER NOT NULL,
 provider_job_id TEXT, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS platform_posts (
 post_id TEXT PRIMARY KEY REFERENCES post_drafts(id), platform_post_id TEXT,
 provider_request_id TEXT, published_url TEXT, confirmed_at TEXT);
CREATE TABLE IF NOT EXISTS cost_ledger (
 id INTEGER PRIMARY KEY, event_key TEXT NOT NULL UNIQUE, observed_at TEXT NOT NULL,
 category TEXT NOT NULL, classification TEXT NOT NULL, amount_usd REAL CHECK(amount_usd>=0),
 source TEXT NOT NULL, period_start TEXT, period_end TEXT, note TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS execution_logs (
 id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL, event TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cost_reconciliations (
 original_key TEXT PRIMARY KEY REFERENCES cost_ledger(event_key),
 replacement_key TEXT UNIQUE NOT NULL REFERENCES cost_ledger(event_key),
 observed_at TEXT NOT NULL, CHECK(original_key<>replacement_key));
"""

JSON_TABLES = ("audience_hypotheses", "content_ideas", "post_metrics_snapshots", "website_metrics_snapshots",
               "lead_aggregate_snapshots", "clone_predictions", "experiment_decisions",
               "sentiment_snapshots", "marketing_feedback", "learning_events", "mcp_import_events")
IMMUTABLE = ("cost_ledger", "cost_reconciliations", "post_metrics_snapshots", "website_metrics_snapshots", "lead_aggregate_snapshots",
             "experiment_decisions", "clone_predictions", "execution_logs", "sentiment_snapshots",
             "marketing_feedback", "learning_events", "mcp_import_events")


@contextmanager
def process_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(str(path) + ".lock", "a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another worker owns the single-writer lock") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class Store:
    def __init__(self, path):
        path = Path(path)
        self.path = path.absolute()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(path, timeout=10, isolation_level=None)
        os.chmod(path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA recursive_triggers=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(SCHEMA)
        existing = {row[1] for row in self.db.execute("PRAGMA table_info(post_drafts)")}
        for column in ("account_key", "account_fingerprint"):
            if column not in existing:
                self.db.execute(f"ALTER TABLE post_drafts ADD COLUMN {column} TEXT")
        for table in JSON_TABLES:
            self.db.execute(f"CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY, event_key TEXT UNIQUE NOT NULL, observed_at TEXT NOT NULL, source TEXT NOT NULL, payload TEXT NOT NULL)")
        for table in IMMUTABLE:
            for action in ("UPDATE", "DELETE"):
                self.db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'append-only ledger'); END")

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def rows(self, sql, args=()):
        return [dict(row) for row in self.db.execute(sql, args)]

    def one(self, sql, args=()):
        row = self.db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def record(self, table, key, now, source, payload):
        if table not in JSON_TABLES:
            raise ValueError("Unknown record type")
        self.db.execute(f"INSERT OR IGNORE INTO {table}(event_key,observed_at,source,payload) VALUES(?,?,?,?)",
                        (key, iso(now), source, json.dumps(payload, sort_keys=True, allow_nan=False)))

    def records(self, table):
        if table not in JSON_TABLES:
            raise ValueError("Unknown record type")
        return [{**row, "payload": json.loads(row["payload"])} for row in self.rows(f"SELECT * FROM {table} ORDER BY id")]

    def log(self, event, detail="", now=None):
        # Callers supply only fixed safe status codes, never exceptions, HTTP bodies, prompts or credentials.
        allowed = {"initialized", "started", "paused", "resumed", "stopped", "authorized", "incident",
                   "submission_unknown", "submission_failed", "cancel_unknown", "collection_failed", "policy_rejected"}
        if event not in allowed:
            raise ValueError("Unregistered log event")
        self.db.execute("INSERT INTO execution_logs(observed_at,event,detail) VALUES(?,?,?)",
                        (iso(now or datetime.now(UTC)), event, detail))

    def cost(self, key, now, category, classification, amount, source, start=None, end=None, note="", supersedes=None):
        if category not in {"model", "external", "infrastructure"} or classification not in {"recorded", "confirmed", "pending", "estimate"}:
            raise ValueError("Invalid cost classification")
        with self.transaction():
            if supersedes:
                old = self.one("SELECT * FROM cost_ledger WHERE event_key=?", (supersedes,))
                if not old or old["classification"] == "confirmed" or old["category"] != category or key == supersedes or amount is None:
                    raise ValueError("Reconciliation requires an existing nonfinal same-category cost and a known replacement")
                if self.one("SELECT * FROM cost_ledger WHERE event_key=?", (key,)):
                    raise ValueError("Reconciliation replacement key must be new")
            self.db.execute("INSERT OR IGNORE INTO cost_ledger(event_key,observed_at,category,classification,amount_usd,source,period_start,period_end,note) VALUES(?,?,?,?,?,?,?,?,?)",
                            (key, iso(now), category, classification, amount, source, start, end, note))
            if supersedes:
                self.db.execute("INSERT INTO cost_reconciliations(original_key,replacement_key,observed_at) VALUES(?,?,?)", (supersedes, key, iso(now)))

    def close(self):
        self.db.close()
