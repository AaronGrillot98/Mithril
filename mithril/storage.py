"""Lightweight SQLite event log.

Sync core (`record`/`recent`/`stats`) is kept for tests and out-of-band uses
(CLI, benchmark scripts). Async wrappers (`arecord`/`arecent`/`astats`)
offload the actual SQLite work to a worker thread so the FastAPI event
loop never blocks on disk I/O.

WAL mode is enabled at init so readers don't block writers — important
once the dashboard issues queries against a busy live event log.
"""

from __future__ import annotations

import asyncio
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

CREATE INDEX IF NOT EXISTS idx_events_ts        ON events(ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_action    ON events(action);
CREATE INDEX IF NOT EXISTS idx_events_severity  ON events(severity);
"""


class EventStore:
    """Thread- and async-safe SQLite event log.

    Concurrency model: every operation opens a short-lived connection. A
    process-wide write lock serializes inserts (SQLite's own lock would
    serialize them anyway; an explicit Python lock just avoids a thread
    bouncing on SQLITE_BUSY). Reads use a separate path with no lock.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._write_lock = threading.Lock()
        self._init()

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_SCHEMA)
            conn.commit()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # `check_same_thread=False` is safe here because we open a fresh
        # connection per operation; no connection is shared across threads.
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # ----- sync API -----------------------------------------------------------

    def record(
        self,
        *,
        action: str,
        model: str,
        result: DetectionResult,
        snippet: str,
    ) -> None:
        with self._write_lock, self._connect() as conn:
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
        try:
            from mithril.metrics import EVENT_LOG_WRITES

            EVENT_LOG_WRITES.inc()
        # Metrics must never break the write path.
        except Exception:  # noqa: BLE001  # nosec B110
            pass

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            # One round-trip instead of three: tally actions and severities
            # in a pair of queries that share the connection.
            total = 0
            blocked = 0
            for row in conn.execute(
                "SELECT action, COUNT(*) AS c FROM events GROUP BY action"
            ):
                total += row["c"]
                if row["action"] == "block":
                    blocked = row["c"]
            by_severity = {
                row["severity"]: row["c"]
                for row in conn.execute(
                    "SELECT severity, COUNT(*) AS c FROM events GROUP BY severity"
                )
            }
            return {
                "total": total,
                "blocked": blocked,
                "allowed": total - blocked,
                "by_severity": by_severity,
            }

    # ----- async API ----------------------------------------------------------
    # Each method offloads the sync work to the default executor so the FastAPI
    # event loop never blocks on disk I/O. Use these from async request handlers.

    async def arecord(
        self,
        *,
        action: str,
        model: str,
        result: DetectionResult,
        snippet: str,
    ) -> None:
        await asyncio.to_thread(
            self.record,
            action=action,
            model=model,
            result=result,
            snippet=snippet,
        )

    async def arecent(self, limit: int = 100) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.recent, limit)

    async def astats(self) -> dict[str, Any]:
        return await asyncio.to_thread(self.stats)

    async def aclose(self) -> None:
        """No-op placeholder; we don't hold a persistent connection."""
        return None

    # ----- helpers ------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        try:
            d["findings"] = json.loads(d["findings"])
        except (TypeError, json.JSONDecodeError):
            d["findings"] = []
        return d
