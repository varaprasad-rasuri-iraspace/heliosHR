"""SQLite-backed local workflow state (production equivalent: DynamoDB/Postgres)."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class SQLiteStateStore:
    """Persist run, hire, activation, and approval state across prototype restarts."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    event_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS hires (
                    worker_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS activation_targets (
                    event_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    event_id TEXT NOT NULL,
                    entitlement_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (event_id, entitlement_id)
                );
                CREATE TABLE IF NOT EXISTS steps (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
            """)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    @contextmanager
    def _db(self):
        db = self._connect()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _decode_rows(rows):
        return {row[0]: json.loads(row[1]) for row in rows}

    def load_runs(self) -> dict[str, dict]:
        with self._db() as db:
            return self._decode_rows(db.execute("SELECT event_id, payload FROM runs"))

    def save_run(self, event_id: str, payload: dict) -> None:
        with self._db() as db:
            db.execute("INSERT INTO runs VALUES (?, ?) ON CONFLICT(event_id) DO UPDATE SET payload=excluded.payload",
                       (event_id, json.dumps(payload, sort_keys=True)))

    def load_hires(self) -> dict[str, dict]:
        with self._db() as db:
            return self._decode_rows(db.execute("SELECT worker_id, payload FROM hires"))

    def save_hire(self, worker_id: str, payload: dict) -> None:
        with self._db() as db:
            db.execute("INSERT INTO hires VALUES (?, ?) ON CONFLICT(worker_id) DO UPDATE SET payload=excluded.payload",
                       (worker_id, json.dumps(payload, sort_keys=True)))

    def load_activation_targets(self) -> dict[str, dict]:
        with self._db() as db:
            return self._decode_rows(db.execute("SELECT event_id, payload FROM activation_targets"))

    def save_activation_target(self, event_id: str, payload: dict) -> None:
        with self._db() as db:
            db.execute("INSERT INTO activation_targets VALUES (?, ?) ON CONFLICT(event_id) DO UPDATE SET payload=excluded.payload",
                       (event_id, json.dumps(payload, sort_keys=True)))

    def load_approvals(self) -> dict[tuple[str, str], dict]:
        with self._db() as db:
            return {(row[0], row[1]): json.loads(row[2])
                    for row in db.execute("SELECT event_id, entitlement_id, payload FROM approvals")}

    def save_approval(self, event_id: str, entitlement_id: str, payload: dict) -> None:
        with self._db() as db:
            db.execute("INSERT INTO approvals VALUES (?, ?, ?) ON CONFLICT(event_id, entitlement_id) DO UPDATE SET payload=excluded.payload",
                       (event_id, entitlement_id, json.dumps(payload, sort_keys=True)))

    def save_step(self, run_id: str, payload: dict) -> None:
        with self._db() as db:
            db.execute("INSERT INTO steps (run_id, payload) VALUES (?, ?)",
                       (run_id, json.dumps(payload, sort_keys=True)))
