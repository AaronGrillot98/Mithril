"""Tests for the v0.5 IncrementalStreamScanner — true streaming output scan."""

from __future__ import annotations

import json
from typing import AsyncIterator

import httpx
import pytest
from fastapi.testclient import TestClient

from mithril.output import IncrementalStreamScanner, default_output_scanner


# --- helpers ----------------------------------------------------------------


async def _async_iter(chunks: list[bytes]) -> AsyncIterator[bytes]:
    for c in chunks:
        yield c


def _sse(events: list[dict]) -> bytes:
    out = b""
    for e in events:
        out += f"data: {json.dumps(e)}\n\n".encode("utf-8")
    out += b"data: [DONE]\n\n"
    return out


def _delta(content: str) -> dict:
    return {"choices": [{"delta": {"content": content}}]}


# --- unit tests on IncrementalStreamScanner ---------------------------------


@pytest.mark.asyncio
async def test_clean_stream_forwards_chunks_unchanged():
    scanner = default_output_scanner(mode="block")
    inc = IncrementalStreamScanner(scanner=scanner, mode="block", scan_interval_chars=1)
    chunks = [
        f"data: {json.dumps(_delta('Hello '))}\n\n".encode("utf-8"),
        f"data: {json.dumps(_delta('world.'))}\n\n".encode("utf-8"),
        b"data: [DONE]\n\n",
    ]
    forwarded = b""
    async for emitted in inc.process_chunks(_async_iter(chunks)):
        forwarded += emitted
    # The two content chunks should be forwarded verbatim; the upstream
    # [DONE] is suppressed and replaced with our own (single) terminator.
    assert chunks[0] in forwarded
    assert chunks[1] in forwarded
    assert forwarded.count(b"[DONE]") == 1
    final = await inc.finalize()
    assert final is not None
    assert final.action == "allow"


@pytest.mark.asyncio
async def test_block_mode_cuts_stream_on_pii_hit():
    """When the accumulated content trips a rule, emit an error frame and stop."""
    scanner = default_output_scanner(mode="block")
    inc = IncrementalStreamScanner(scanner=scanner, mode="block", scan_interval_chars=1)

    # The full content will be "Your SSN is 123-45-6789." — PII001 fires.
    chunks = [
        f"data: {json.dumps(_delta('Your SSN is '))}\n\n".encode("utf-8"),
        f"data: {json.dumps(_delta('123-45-6789.'))}\n\n".encode("utf-8"),
        b"data: [DONE]\n\n",
    ]
    forwarded = b""
    async for emitted in inc.process_chunks(_async_iter(chunks)):
        forwarded += emitted

    # The first two chunks should have been forwarded as-is.
    assert b"Your SSN is" in forwarded
    # And the synthesized block event should be appended.
    assert b"mithril_output_blocked" in forwarded
    assert b"data: [DONE]" in forwarded
    # The original [DONE] from upstream should NOT have been reached because
    # the iterator returned early.
    assert forwarded.count(b"[DONE]") == 1


@pytest.mark.asyncio
async def test_log_mode_never_alters_stream():
    """Log mode forwards chunks even when something fires; findings are
    surfaced only via finalize() so the caller can record them."""
    scanner = default_output_scanner(mode="log")
    # log mode: scanner.scan returns action="allow" even when findings fire
    # (so the stream-level "block" path won't trigger).
    inc = IncrementalStreamScanner(scanner=scanner, mode="log", scan_interval_chars=1)

    chunks = [
        f"data: {json.dumps(_delta('SSN: 123-45-6789'))}\n\n".encode("utf-8"),
        b"data: [DONE]\n\n",
    ]
    forwarded = b""
    async for emitted in inc.process_chunks(_async_iter(chunks)):
        forwarded += emitted

    # No block event was injected; original [DONE] preserved.
    assert b"mithril_output_blocked" not in forwarded
    assert b"123-45-6789" in forwarded

    # finalize still sees the findings even though the stream wasn't altered.
    result = await inc.finalize()
    assert result is not None
    assert result.findings  # PII001 fired


@pytest.mark.asyncio
async def test_handles_chunk_split_mid_sse_line():
    """A chunk boundary that lands inside an SSE record must not lose content.

    We split a single SSE line across two chunks and verify the accumulator
    reassembles it correctly.
    """
    scanner = default_output_scanner(mode="block")
    inc = IncrementalStreamScanner(scanner=scanner, mode="block", scan_interval_chars=1)

    full_event = f"data: {json.dumps(_delta('hello world'))}\n\n".encode("utf-8")
    half = len(full_event) // 2
    chunks = [full_event[:half], full_event[half:], b"data: [DONE]\n\n"]
    async for _ in inc.process_chunks(_async_iter(chunks)):
        pass
    assert inc._state.accumulated == "hello world"


@pytest.mark.asyncio
async def test_scan_interval_chars_throttles_scans(monkeypatch):
    """Scans should only run after `scan_interval_chars` new content has
    accumulated, not on every byte."""
    scanner = default_output_scanner(mode="block")
    scan_calls = {"n": 0}
    orig = scanner.scan

    def counting_scan(text):
        scan_calls["n"] += 1
        return orig(text)

    scanner.scan = counting_scan  # type: ignore[method-assign]
    inc = IncrementalStreamScanner(scanner=scanner, mode="block", scan_interval_chars=20)

    # Generate 10 chunks of 5 chars each = 50 chars total. With interval 20,
    # the scan should fire 2-3 times, not 10.
    chunks = [
        f"data: {json.dumps(_delta('abcde'))}\n\n".encode("utf-8") for _ in range(10)
    ]
    chunks.append(b"data: [DONE]\n\n")
    async for _ in inc.process_chunks(_async_iter(chunks)):
        pass

    assert scan_calls["n"] < 10
    assert scan_calls["n"] >= 2


@pytest.mark.asyncio
async def test_non_sse_chunks_pass_through_silently():
    """If upstream sends garbage that isn't SSE, we should forward it but
    nothing accumulates."""
    scanner = default_output_scanner(mode="block")
    inc = IncrementalStreamScanner(scanner=scanner, mode="block", scan_interval_chars=1)
    chunks = [b"random binary or non-sse content\n", b"more garbage", b"\n"]
    forwarded = b""
    async for emitted in inc.process_chunks(_async_iter(chunks)):
        forwarded += emitted
    # Garbage chunks are forwarded verbatim, plus our own terminator at the end.
    for c in chunks:
        assert c in forwarded
    assert inc._state.accumulated == ""


# --- server integration tests ----------------------------------------------


@pytest.fixture
def stream_block_client(tmp_path, monkeypatch):
    monkeypatch.setenv("MITHRIL_DB_PATH", str(tmp_path / "m.db"))
    monkeypatch.setenv("MITHRIL_OUTPUT_SCAN_ENABLED", "true")
    monkeypatch.setenv("MITHRIL_OUTPUT_SCAN_MODE", "block")
    monkeypatch.setenv("MITHRIL_OUTPUT_SCAN_STREAM_MODE", "incremental")

    from mithril import config as _config
    from mithril.config import Settings

    fresh = Settings()
    monkeypatch.setattr(_config, "settings", fresh)
    from mithril import server as _server

    monkeypatch.setattr(_server, "settings", fresh)

    with TestClient(_server.app) as c:
        yield c


def _stub(client: TestClient, responder):
    client.app.state.upstream._client._transport = httpx.MockTransport(responder)


def test_server_incremental_block_cuts_stream_on_pii(stream_block_client):
    sse_body = _sse([
        _delta("Your "),
        _delta("SSN "),
        _delta("is "),
        _delta("123-45-6789"),
        _delta("."),
    ])

    def responder(request):
        return httpx.Response(
            200,
            content=sse_body,
            headers={"content-type": "text/event-stream"},
        )

    _stub(stream_block_client, responder)
    r = stream_block_client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "tell me my SSN"}],
            "stream": True,
        },
        headers={"Authorization": "Bearer sk-fake"},
    )
    assert r.status_code == 200
    text = r.text
    # The SSN appears in the early chunks (we forwarded them) — that's
    # expected behavior for block mode: it's mitigation, not interception.
    # But a block error event must appear after, before exactly one [DONE].
    assert "mithril_output_blocked" in text
    assert text.rstrip().endswith("data: [DONE]")
    # We strip the upstream [DONE] and emit our own — exactly one terminator.
    assert text.count("[DONE]") == 1
    # PII is in the stream (it was forwarded before the scan fired), but the
    # block event comes AFTER, so a real SSE client that respects [DONE]
    # never sees content after our terminator.
    assert text.index("mithril_output_blocked") < text.index("data: [DONE]")


def test_server_incremental_passes_clean_stream(stream_block_client):
    sse_body = _sse([
        _delta("The "),
        _delta("capital "),
        _delta("of "),
        _delta("France "),
        _delta("is "),
        _delta("Paris."),
    ])

    def responder(request):
        return httpx.Response(
            200,
            content=sse_body,
            headers={"content-type": "text/event-stream"},
        )

    _stub(stream_block_client, responder)
    r = stream_block_client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Capital of France?"}],
            "stream": True,
        },
        headers={"Authorization": "Bearer sk-fake"},
    )
    assert r.status_code == 200
    text = r.text
    assert "Paris" in text
    assert "mithril_output_blocked" not in text


def test_server_redact_mode_falls_back_to_buffered(tmp_path, monkeypatch):
    """Redact mode + streaming = buffered path (true streaming redact is v0.6)."""
    monkeypatch.setenv("MITHRIL_DB_PATH", str(tmp_path / "m.db"))
    monkeypatch.setenv("MITHRIL_OUTPUT_SCAN_ENABLED", "true")
    monkeypatch.setenv("MITHRIL_OUTPUT_SCAN_MODE", "redact")
    monkeypatch.setenv("MITHRIL_OUTPUT_SCAN_STREAM_MODE", "incremental")  # ignored for redact

    from mithril import config as _config
    from mithril.config import Settings

    fresh = Settings()
    monkeypatch.setattr(_config, "settings", fresh)
    from mithril import server as _server

    monkeypatch.setattr(_server, "settings", fresh)

    sse_body = _sse([
        _delta("Your SSN is "),
        _delta("123-45-6789"),
        _delta("."),
    ])

    def responder(request):
        return httpx.Response(
            200,
            content=sse_body,
            headers={"content-type": "text/event-stream"},
        )

    with TestClient(_server.app) as client:
        _stub(client, responder)
        r = client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "tell me"}],
                "stream": True,
            },
            headers={"Authorization": "Bearer sk-fake"},
        )
    assert r.status_code == 200
    text = r.text
    # Buffered redact emits a single redacted-content SSE message
    assert "[REDACTED:PII001]" in text
    assert "123-45-6789" not in text
