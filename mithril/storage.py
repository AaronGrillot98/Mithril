"""Lightweight SQLite event log. No ORM, no migrations — just two tables."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from mithril.models import DetectionResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    action      TEXT    NOT NULL,         -- 'block' | 'allow' | 'log'
    model       TEXT    NOT NULL,
    score       REAL    NOT NULL,
    severity    TEXT    NOT NULL,
    finding_count INTEGER NOT NULL,
    findings    TEXT    NOT NULL,          -- JSON blob
    snippet     TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_action ON events(action);
"""


class EventStore:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._lock = threading.Lock()
        self._init()

    def _init(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def record(
        self,
        *,
        action: str,
        model: str,
        result: DetectionResult,
        snippet: str,
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO events
                  (ts, action, model, score, severity, finding_count, findings, snippet)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    time.time(),
                    action,
                    model,
                    result.score,
                    result.top_severity,
                    len(result.findings),
                    json.dumps([f.model_dump() for f in result.findings]),
                    snippet[:500],
                ),
            )
            conn.commit()

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            blocked = conn.execute(
                "SELECT COUNT(*) FROM events WHERE action='block'"
            ).fetchone()[0]
            by_severity = {
                row["severity"]: row["c"]
                for row in conn.execute(
                    "SELECT severity, COUNT(*) AS c FROM events GROUP BY severity"
                ).fetchall()
            }
            return {
                "total": total,
                "blocked": blocked,
                "allowed": total - blocked,
                "by_severity": by_severity,
            }

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        try:
            d["findings"] = json.loads(d["findings"])
        except (TypeError, json.JSONDecodeError):
            d["findings"] = []
        return d
