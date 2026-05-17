"""Incremental output scanner for streaming chat-completions responses.

The v0.4 implementation buffers the entire SSE stream, scans it as a whole,
and only then re-emits — which sacrifices the streaming UX for safety.
This module preserves streaming UX in two of the three output modes:

  - ``block``  — forward chunks as they arrive while accumulating their
                 text content. Scan the accumulator after each chunk. On
                 the first finding that exceeds the threshold, emit a
                 final SSE error event and close the stream.
  - ``log``    — forward chunks as they arrive, scan after each, and
                 record any findings to the event log.
  - ``redact`` — still goes through buffer-then-scan (v0.4 behavior).
                 Doing this incrementally requires a trail-buffer
                 algorithm that is on the v0.6 roadmap.

SSE byte handling: we maintain a byte-level emit-residual buffer separate
from the text-level content parser. Only **complete** SSE lines (ending
in ``\\n``) are forwarded to the client — any trailing partial bytes are
held back until the next chunk arrives. This guarantees that a ``data:
[DONE]`` line split across TCP packets is still detected and stripped
before it reaches the client (otherwise the client would stop reading
before our injected block event).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from mithril.models import OutputScanResult

logger = logging.getLogger("mithril.output.streaming")

# Matches an SSE `data: [DONE]` terminator line (with or without a trailing
# blank line). We strip these from upstream chunks and emit our own [DONE]
# at the end of the stream — otherwise real OpenAI-SSE clients stop reading
# at the first [DONE] and miss our block / error event that follows it.
_DONE_LINE = re.compile(rb"data:\s*\[DONE\]\s*\n+")
_OUR_DONE = b"data: [DONE]\n\n"


@dataclass
class _ParseState:
    """Mutable state carried across `process` calls for one stream."""

    text_residual: str = ""        # incomplete trailing SSE line for content parser
    emit_residual: bytes = b""     # bytes held back from emit until line is complete
    accumulated: str = ""          # all delta.content seen so far
    blocked: bool = False          # latched once we've decided to block
    last_scan_len: int = 0         # accumulated length at last scan (cheap rate limit)


def _block_event_no_done(result: OutputScanResult) -> bytes:
    """Build a single SSE chunk explaining why the stream was cut off.

    Does NOT include the [DONE] terminator — the caller emits that
    separately, ensuring there's exactly one [DONE] at the end of the
    stream (whether the upstream sent one or not)."""
    error = {
        "error": {
            "type": "mithril_output_blocked",
            "message": "Response blocked by Mithril output filter mid-stream.",
            "score": result.score,
            "severity": result.top_severity,
            "findings": [f.model_dump() for f in result.findings],
        }
    }
    return f"data: {json.dumps(error)}\n\n".encode("utf-8")


def truncation_event(reason: str) -> bytes:
    """An SSE error chunk emitted when the stream was cut for a non-scan reason
    (e.g. the upstream response exceeded ``MITHRIL_MAX_RESPONSE_BYTES``)."""
    error = {
        "error": {
            "type": "response_too_large",
            "message": reason,
        }
    }
    return f"data: {json.dumps(error)}\n\n".encode("utf-8")


def _extract_content_delta(line: str) -> str | None:
    """Pull `delta.content` out of one SSE `data:` line. Returns None if there
    isn't a content delta to extract from this line."""
    if not line.startswith("data:"):
        return None
    payload = line[5:].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    choices = obj.get("choices")
    if not isinstance(choices, list):
        return None
    parts: list[str] = []
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            continue
        content = delta.get("content")
        if isinstance(content, str):
            parts.append(content)
    return "".join(parts) if parts else None


@dataclass
class IncrementalStreamScanner:
    """Per-stream incremental scanner.

    Construct once per request, then drive with `process_chunks(async_iter)`
    which yields bytes to forward to the client.
    """

    scanner: Any
    mode: str = "block"
    # Don't re-scan on every byte — wait until at least this many new
    # characters of content have accumulated since the last scan. Keeps
    # CPU usage sane on token-by-token streams. Default 16 ~ 4–8 tokens.
    scan_interval_chars: int = 16

    _state: _ParseState = field(default_factory=_ParseState)
    last_result: OutputScanResult | None = None

    @property
    def is_blocked(self) -> bool:
        """Whether process_chunks has decided to block this stream."""
        return self._state.blocked

    @property
    def accumulated(self) -> str:
        """Accumulated text content seen so far across all chunks."""
        return self._state.accumulated

    def _scan_if_due(self) -> OutputScanResult | None:
        """Run a scan if enough new content has accumulated since the last scan."""
        if len(self._state.accumulated) - self._state.last_scan_len < self.scan_interval_chars:
            return None
        self._state.last_scan_len = len(self._state.accumulated)
        result = self._safe_scan(self._state.accumulated)
        self.last_result = result
        return result

    def _safe_scan(self, text: str) -> OutputScanResult:
        """Run the configured scanner, never letting an exception break the stream.

        On scanner failure we log and return an "allow" result so the stream
        continues unaltered — failing closed would convert a transient detector
        bug into a user-visible outage. The exception is recorded so operators
        can spot it in logs.
        """
        try:
            return self.scanner.scan(text)
        except Exception as exc:  # noqa: BLE001 — we genuinely want to swallow any
            logger.exception("Output scanner raised; failing open: %s", exc)
            return OutputScanResult(action="allow", score=0.0)

    def _absorb_text(self, complete_bytes: bytes) -> None:
        """Append complete-line bytes to internal state, advancing the
        delta.content accumulator. ``complete_bytes`` MUST end at a line
        boundary — partial trailing lines should be held back by the caller."""
        text = self._state.text_residual + complete_bytes.decode("utf-8", errors="replace")
        lines = text.split("\n")
        # If complete_bytes ended in \n, lines[-1] is "" (no residual).
        # Otherwise we accidentally received a partial line — keep residual.
        self._state.text_residual = lines.pop()

        for line in lines:
            content = _extract_content_delta(line)
            if content:
                self._state.accumulated += content

    async def process_chunks(
        self, source: AsyncIterator[bytes]
    ) -> AsyncIterator[bytes]:
        """Iterate the upstream byte stream and yield bytes to forward.

        In ``block`` mode: forward chunks as they arrive until a scan
        fires; from that point on, swallow any further upstream bytes and
        yield the synthesized block-event tail.

        In ``log`` mode: forward every chunk; scan in the background; just
        store findings.

        Upstream ``data: [DONE]`` lines are stripped before forwarding —
        we emit a single ``[DONE]`` of our own at the end so any block /
        error event we inject is guaranteed to reach OpenAI-SSE clients
        (which stop reading at the first ``[DONE]``).

        Only complete SSE lines are forwarded; trailing partial bytes are
        held in ``_state.emit_residual`` until they're complete. This
        prevents a TCP-sliced ``[DONE]`` from leaking through.
        """
        async for raw in source:
            if self._state.blocked:
                # Already decided to block — swallow any further upstream bytes.
                continue

            combined = self._state.emit_residual + raw
            last_nl = combined.rfind(b"\n")
            if last_nl < 0:
                # No newline anywhere — entire combined buffer is partial.
                self._state.emit_residual = combined
                continue

            complete = combined[: last_nl + 1]
            self._state.emit_residual = combined[last_nl + 1 :]

            self._absorb_text(complete)
            # Strip [DONE] from the emittable bytes; emit only complete lines.
            emittable = _DONE_LINE.sub(b"", complete)
            if emittable:
                yield emittable

            result = self._scan_if_due()
            if result is None:
                continue

            if self.mode == "block" and result.action == "block":
                self._state.blocked = True
                yield _block_event_no_done(result)
                yield _OUR_DONE
                return  # don't iterate further

        # Source exhausted. Flush whatever's left in emit_residual (after
        # absorbing it into the content accumulator + stripping any DONE).
        if not self._state.blocked:
            if self._state.emit_residual:
                self._absorb_text(self._state.emit_residual)
                emittable = _DONE_LINE.sub(b"", self._state.emit_residual)
                if emittable:
                    yield emittable
                self._state.emit_residual = b""

            # Run a final scan to catch content that was below the throttle
            # threshold at the very end — without this, short responses where
            # the last interesting chars are <scan_interval_chars away from
            # the end of the stream would slip through.
            if self._state.accumulated:
                final = self._safe_scan(self._state.accumulated)
                self.last_result = final
                if self.mode == "block" and final.action == "block":
                    self._state.blocked = True
                    yield _block_event_no_done(final)

        yield _OUR_DONE

    async def finalize(self) -> OutputScanResult | None:
        """Run one final scan after the source iterator has completed. Useful
        for `log` mode so the last few chunks of content (smaller than the
        scan interval) still get analyzed."""
        if self._state.blocked:
            return self.last_result
        result = self._safe_scan(self._state.accumulated)
        self.last_result = result
        return result
