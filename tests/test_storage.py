"""Tests for the SQLite event log."""

from __future__ import annotations

import asyncio

import pytest

from mithril.models import DetectionResult, Finding
from mithril.storage import EventStore


def _make_result(score: float = 0.97, severity: str = "critical") -> DetectionResult:
    return DetectionResult(
        blocked=score >= 0.7,
        score=score,
        findings=[
            Finding(
                detector="jailbreak",
                rule_id="JB008",
                severity=severity,
                confidence=score,
                message="Test finding",
                excerpt="test",
            )
        ],
    )


# --- schema + WAL -------------------------------------------------------------


def test_init_creates_schema_and_enables_wal(tmp_path):
    db = tmp_path / "mithril.db"
    store = EventStore(db)
    # Verify WAL mode is set.
    with store._connect() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
        # Schema table + all three indexes exist.
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "events" in tables
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert "idx_events_ts" in indexes
        assert "idx_events_action" in indexes
        assert "idx_events_severity" in indexes


# --- sync API -----------------------------------------------------------------


def test_record_and_recent_roundtrip(tmp_path):
    store = EventStore(tmp_path / "m.db")
    store.record(action="block", model="gpt-4o-mini", result=_make_result(), snippet="bad prompt")
    rows = store.recent()
    assert len(rows) == 1
    r = rows[0]
    assert r["action"] == "block"
    assert r["model"] == "gpt-4o-mini"
    assert r["severity"] == "critical"
    assert r["finding_count"] == 1
    assert isinstance(r["findings"], list)
    assert r["findings"][0]["rule_id"] == "JB008"


def test_recent_orders_by_time_desc(tmp_path):
    store = EventStore(tmp_path / "m.db")
    for i in range(5):
        store.record(
            action="allow",
            model=f"model-{i}",
            result=_make_result(score=0.0, severity="info"),
            snippet=f"event {i}",
        )
    rows = store.recent(limit=10)
    assert [r["snippet"] for r in rows] == [f"event {i}" for i in (4, 3, 2, 1, 0)]


def test_recent_respects_limit(tmp_path):
    store = EventStore(tmp_path / "m.db")
    for i in range(20):
        store.record(
            action="allow", model="m", result=_make_result(score=0.0, severity="info"), snippet=str(i)
        )
    assert len(store.recent(limit=7)) == 7


def test_stats_one_round_trip_counts(tmp_path):
    store = EventStore(tmp_path / "m.db")
    for sev, action in [
        ("critical", "block"),
        ("critical", "block"),
        ("high", "block"),
        ("info", "allow"),
        ("info", "allow"),
        ("info", "allow"),
    ]:
        store.record(
            action=action,
            model="m",
            result=_make_result(score=0.9 if sev != "info" else 0.0, severity=sev),
            snippet="s",
        )
    s = store.stats()
    assert s["total"] == 6
    assert s["blocked"] == 3
    assert s["allowed"] == 3
    assert s["by_severity"]["critical"] == 2
    assert s["by_severity"]["high"] == 1
    assert s["by_severity"]["info"] == 3


def test_snippet_is_truncated_to_500_chars(tmp_path):
    store = EventStore(tmp_path / "m.db")
    big = "x" * 1000
    store.record(action="block", model="m", result=_make_result(), snippet=big)
    assert len(store.recent()[0]["snippet"]) == 500


# --- async API ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_arecord_and_arecent(tmp_path):
    store = EventStore(tmp_path / "m.db")
    await store.arecord(action="block", model="m", result=_make_result(), snippet="async event")
    rows = await store.arecent()
    assert rows[0]["snippet"] == "async event"


@pytest.mark.asyncio
async def test_astats(tmp_path):
    store = EventStore(tmp_path / "m.db")
    await store.arecord(action="block", model="m", result=_make_result(), snippet="x")
    s = await store.astats()
    assert s["total"] == 1
    assert s["blocked"] == 1


@pytest.mark.asyncio
async def test_arecord_does_not_block_event_loop(tmp_path):
    """Concurrent arecord() calls must not serialize through the event loop.

    We measure that 20 parallel writes complete in less than 5 seconds even
    though SQLite is serializing at the disk level — the async wrapper
    yielding to the loop is what makes this acceptable in a real server.
    """
    store = EventStore(tmp_path / "m.db")

    async def writer(i: int):
        await store.arecord(
            action="block",
            model="m",
            result=_make_result(),
            snippet=f"event {i}",
        )

    import time

    t0 = time.perf_counter()
    await asyncio.gather(*(writer(i) for i in range(20)))
    elapsed = time.perf_counter() - t0
    assert elapsed < 5.0, f"async writes took too long: {elapsed:.2f}s"
    s = await store.astats()
    assert s["total"] == 20


# --- error tolerance ----------------------------------------------------------


def test_row_to_dict_handles_corrupt_findings(tmp_path):
    """If findings JSON is corrupt, recent() should still return rows with an empty list."""
    store = EventStore(tmp_path / "m.db")
    store.record(action="block", model="m", result=_make_result(), snippet="x")
    # Corrupt the findings column directly.
    with store._connect() as conn:
        conn.execute("UPDATE events SET findings = 'not-json'")
        conn.commit()
    rows = store.recent()
    assert rows[0]["findings"] == []
