from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask

from mithril import __version__
from mithril import metrics as _metrics
from mithril.config import settings
from mithril.detectors import default_pipeline
from mithril.judges import build_judge
from mithril.middleware import RequestIDMiddleware
from mithril.models import (
    BlockResponse,
    ChatCompletionRequest,
    DetectionResult,
    OutputBlockResponse,
    OutputScanResult,
)
from mithril.output import IncrementalStreamScanner, default_output_scanner
from mithril.output.streaming import truncation_event as _streaming_truncation_event
from mithril.proxy import UpstreamClient
from mithril.storage import EventStore

TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

logger = logging.getLogger("mithril.server")


# Hop-by-hop headers + headers we must strip when forwarding a request body
# whose framing we've changed (or might change). Lowercase, compared
# case-insensitively against incoming names.
_STRIP_REQUEST_HEADERS = frozenset({
    "host",
    "content-length",
    "transfer-encoding",
    "connection",
    "keep-alive",
    "te",
    "trailer",
    "trailers",
    "upgrade",
    "proxy-authenticate",
    "proxy-authorization",
    "cookie",
})

# Headers we forward back to the client from the upstream response. Everything
# else (Server, Set-Cookie, hop-by-hop) is dropped.
_FORWARD_RESPONSE_HEADERS = frozenset({
    "content-type",
    "content-encoding",
    "cache-control",
    "openai-organization",
    "openai-model",
    "openai-version",
    "x-ratelimit-limit-requests",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-remaining-tokens",
    "x-request-id",
})


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    judge = build_judge(settings)

    extra_detectors: list[Any] = []
    if settings.embedding_enabled:
        try:
            from mithril.embeddings import EmbeddingSimilarityDetector

            corpus_path = settings.embedding_corpus_path or None
            extra_detectors.append(
                EmbeddingSimilarityDetector(
                    corpus_path=corpus_path,
                    model_name=settings.embedding_model,
                    threshold=settings.embedding_threshold,
                )
            )
            logger.info(
                "Embedding similarity detector enabled (model=%s, threshold=%s)",
                settings.embedding_model,
                settings.embedding_threshold,
            )
        except ImportError as exc:
            logger.warning(
                "MITHRIL_EMBEDDING_ENABLED=true but the [embeddings] extra is not "
                "installed (%s). Continuing without embedding detection. "
                "Install with: pip install mithril-llm[embeddings]",
                exc,
            )

    app.state.pipeline = default_pipeline(
        threshold=settings.threshold,
        judge=judge,
        extra_detectors=extra_detectors,
    )

    # Warm up any detectors with expensive one-time setup (e.g. the embedding
    # layer's model load) BEFORE the proxy starts accepting requests. Without
    # this, the first incoming request would pay the 1–2s model-load cost
    # synchronously, blocking the asyncio loop.
    for det in app.state.pipeline.detectors:
        warmup = getattr(det, "warmup", None)
        if callable(warmup):
            try:
                await asyncio.to_thread(warmup)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Detector %s warmup failed: %s", det.name, exc)
    app.state.pipeline.judge_low = settings.judge_low_threshold
    app.state.pipeline.judge_high = settings.judge_high_threshold
    app.state.pipeline.fail_mode = settings.judge_fail_mode
    app.state.upstream = UpstreamClient(settings.upstream_url)
    app.state.store = EventStore(settings.db_path)
    app.state.output_scanner = (
        default_output_scanner(
            threshold=settings.output_scan_threshold,
            mode=settings.output_scan_mode,
            marker_format=settings.output_scan_marker,
        )
        if settings.output_scan_enabled
        else None
    )
    yield
    await app.state.upstream.aclose()
    await app.state.pipeline.aclose()
    await app.state.store.aclose()


app = FastAPI(
    title="Mithril",
    description="A firewall for LLMs — blocks prompt injection, jailbreaks, and PII exfil.",
    version=__version__,
    lifespan=lifespan,
)
app.add_middleware(RequestIDMiddleware)

if settings.metrics_enabled:
    try:
        from prometheus_fastapi_instrumentator import Instrumentator

        Instrumentator(
            should_group_status_codes=True,
            should_ignore_untemplated=True,
            excluded_handlers=["/metrics", "/health"],
        ).instrument(app).expose(
            app, include_in_schema=False, endpoint="/metrics"
        )
    except ImportError:
        logger.warning(
            "prometheus-fastapi-instrumentator not installed; /metrics disabled."
        )


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": __version__,
        "mode": settings.mode,
        "threshold": settings.threshold,
        "upstream": settings.upstream_url,
        "judge": {
            "enabled": settings.judge_enabled,
            "provider": settings.judge_provider,
            "model": settings.judge_model if settings.judge_enabled else None,
            "low": settings.judge_low_threshold,
            "high": settings.judge_high_threshold,
            "fail_mode": settings.judge_fail_mode,
        },
        "output_scan": {
            "enabled": settings.output_scan_enabled,
            "mode": settings.output_scan_mode if settings.output_scan_enabled else None,
            "threshold": settings.output_scan_threshold,
        },
    }


async def _read_body_with_limit(request: Request, limit_bytes: int) -> bytes:
    """Read the request body but enforce a hard cap to prevent DoS.

    We cap based on Content-Length when present, and then again based on the
    actual bytes read (a malicious client can lie about Content-Length).
    """
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > limit_bytes:
                raise HTTPException(413, f"Request body exceeds {limit_bytes} bytes.")
        except ValueError as exc:
            raise HTTPException(400, "Invalid Content-Length header.") from exc

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit_bytes:
            raise HTTPException(413, f"Request body exceeds {limit_bytes} bytes.")
        chunks.append(chunk)
    return b"".join(chunks)


@app.post("/v1/scan")
async def scan(request: Request) -> dict[str, Any]:
    """Standalone scan endpoint — no upstream forwarding.

    Body:
      {"text": "..."}                                            or
      {"messages": [{"role": "...", "content": "..."}]}
      `judge`: optional bool (default true if judge enabled).
    """
    raw = await _read_body_with_limit(request, settings.max_body_bytes)
    import json as _json

    try:
        payload = _json.loads(raw)
    except _json.JSONDecodeError as exc:
        raise HTTPException(400, "Invalid JSON.") from exc

    if not isinstance(payload, dict):
        raise HTTPException(400, "Body must be a JSON object.")

    pipeline = app.state.pipeline
    use_judge = bool(payload.get("judge", True))

    if "text" in payload:
        text = str(payload["text"])
        result = await pipeline.evaluate(text) if use_judge else pipeline.scan(text)
    elif "messages" in payload:
        messages = payload["messages"]
        if not isinstance(messages, list):
            raise HTTPException(400, "'messages' must be a list.")
        texts = [str(m.get("content", "")) if isinstance(m, dict) else str(m) for m in messages]
        result = (
            await pipeline.evaluate_messages(texts)
            if use_judge
            else pipeline.scan_messages(texts)
        )
    else:
        raise HTTPException(400, "Provide 'text' or 'messages'.")
    return result.model_dump()


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Any:
    """OpenAI-compatible chat completions endpoint.

    Drop-in replacement: point your existing OpenAI SDK at http://<host>:<port>/v1
    and Mithril will scan every request before it reaches the upstream model.
    """
    raw = await _read_body_with_limit(request, settings.max_body_bytes)
    import json as _json

    try:
        body = _json.loads(raw)
    except _json.JSONDecodeError as exc:
        raise HTTPException(400, "Invalid JSON.") from exc

    try:
        parsed = ChatCompletionRequest.model_validate(body)
    except Exception as exc:  # pydantic ValidationError or others
        # Don't leak full pydantic stack — just a short summary.
        raise HTTPException(400, "Invalid chat completions request.") from exc

    texts = [m.text() for m in parsed.messages]
    _t0 = time.perf_counter()
    result: DetectionResult = await app.state.pipeline.evaluate_messages(texts)
    _metrics.SCAN_DURATION.observe(time.perf_counter() - _t0)
    _metrics.record_input_result(result)
    snippet = " | ".join(t[:120] for t in texts if t)

    if result.blocked and settings.mode == "block":
        await app.state.store.arecord(
            action="block", model=parsed.model, result=result, snippet=snippet
        )
        return JSONResponse(
            status_code=403,
            content=BlockResponse.from_result(result).model_dump(),
        )

    # Either clean, or in 'log' mode — forward upstream.
    action = "log" if result.blocked else "allow"
    await app.state.store.arecord(
        action=action, model=parsed.model, result=result, snippet=snippet
    )

    headers = _build_upstream_headers(request.headers)

    if parsed.stream:
        return await _proxy_stream(
            app.state.upstream,
            body,
            headers,
            output_scanner=app.state.output_scanner,
            store=app.state.store,
            model=parsed.model,
        )
    return await _proxy_blocking(
        app.state.upstream,
        body,
        headers,
        output_scanner=app.state.output_scanner,
        store=app.state.store,
        model=parsed.model,
    )


def _build_upstream_headers(incoming: Any) -> dict[str, str]:
    """Strip hop-by-hop and host-leaking headers before forwarding."""
    return {
        k: v
        for k, v in incoming.items()
        if k.lower() not in _STRIP_REQUEST_HEADERS
    }


def _filter_response_headers(headers: httpx.Headers) -> dict[str, str]:
    return {
        k: v
        for k, v in headers.items()
        if k.lower() in _FORWARD_RESPONSE_HEADERS
    }


async def _proxy_blocking(
    upstream: UpstreamClient,
    body: dict[str, Any],
    headers: dict[str, str],
    *,
    output_scanner: Any = None,
    store: EventStore | None = None,
    model: str = "",
) -> Response:
    """Forward a non-streaming request and return the upstream response.

    If output scanning is enabled and the response is valid JSON in the
    OpenAI chat-completions shape, scan ``choices[].message.content`` and
    apply the configured action (block / redact / log). If upstream returns
    a non-JSON body (HTML 502, etc.) we pass it through unchanged.
    """
    try:
        upstream_resp = await upstream.forward_chat(body, headers)
    except httpx.HTTPError as exc:
        logger.warning("upstream forward_chat failed: %s", exc, exc_info=False)
        return JSONResponse(
            status_code=502,
            content={"error": {"type": "upstream_unreachable", "message": str(exc)}},
        )

    body_bytes: bytes | Response = upstream_resp.content
    if output_scanner is not None and upstream_resp.status_code == 200:
        body_bytes = await _apply_output_scan_blocking(
            upstream_resp.content, output_scanner, store, model
        )
        if isinstance(body_bytes, Response):
            return body_bytes

    return Response(
        content=body_bytes,
        status_code=upstream_resp.status_code,
        headers=_filter_response_headers(upstream_resp.headers),
        media_type=upstream_resp.headers.get("content-type"),
    )


async def _apply_output_scan_blocking(
    content_bytes: bytes,
    scanner: Any,
    store: EventStore | None,
    model: str,
) -> bytes | Response:
    """Scan a non-streaming chat completion response.

    Returns either the (possibly rewritten) body bytes to forward, or a
    Response object the caller should return directly when the output was
    blocked.
    """
    import json as _json

    if len(content_bytes) > settings.max_response_bytes:
        logger.warning(
            "upstream response exceeded max_response_bytes (%d > %d); refusing to scan",
            len(content_bytes),
            settings.max_response_bytes,
        )
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "type": "response_too_large",
                    "message": (
                        f"Upstream response exceeded {settings.max_response_bytes} bytes; "
                        "Mithril refused to scan it. Raise MITHRIL_MAX_RESPONSE_BYTES "
                        "if this was intentional."
                    ),
                }
            },
        )

    try:
        payload = _json.loads(content_bytes)
    except _json.JSONDecodeError:
        return content_bytes

    if not isinstance(payload, dict) or not isinstance(payload.get("choices"), list):
        return content_bytes

    # Aggregate text across all choices/messages so one scan covers the
    # whole response. Track which choice each text belonged to so we can
    # rewrite in place on redact.
    texts: list[tuple[int, str]] = []
    for i, choice in enumerate(payload["choices"]):
        msg = choice.get("message") if isinstance(choice, dict) else None
        if isinstance(msg, dict):
            content = msg.get("content")
            if isinstance(content, str) and content:
                texts.append((i, content))

    if not texts:
        return content_bytes

    combined = "\n\n".join(t for _, t in texts)
    result: OutputScanResult = scanner.scan(combined)

    if result.action == "allow":
        return content_bytes

    if result.action == "block":
        if store is not None:
            await store.arecord(
                action="block",
                model=model,
                result=DetectionResult(
                    blocked=True, score=result.score, findings=result.findings
                ),
                snippet=f"[output] {combined[:120]}",
            )
        return JSONResponse(
            status_code=403,
            content=OutputBlockResponse.from_result(result).model_dump(),
        )

    # redact — rewrite each choice's content individually so we don't
    # mangle structure. We re-scan each choice text instead of slicing the
    # combined redaction, because indexes in `result.findings` are relative
    # to the combined buffer.
    if result.redacted_text is not None:
        for i, original in texts:
            sub_result = scanner.scan(original)
            if sub_result.redacted_text is not None:
                payload["choices"][i]["message"]["content"] = sub_result.redacted_text
        if store is not None:
            await store.arecord(
                action="log",
                model=model,
                result=DetectionResult(
                    blocked=False, score=result.score, findings=result.findings
                ),
                snippet=f"[output redacted] {combined[:120]}",
            )
        return _json.dumps(payload).encode("utf-8")

    return content_bytes


async def _proxy_stream(
    upstream: UpstreamClient,
    body: dict[str, Any],
    headers: dict[str, str],
    *,
    output_scanner: Any = None,
    store: EventStore | None = None,
    model: str = "",
) -> Response:
    """Forward a streaming request and proxy the upstream byte stream back.

    When output scanning is enabled, this falls back to buffer-then-scan
    mode: the entire SSE stream is collected into memory, scanned as a
    whole, and only then re-emitted. This sacrifices streaming UX for
    safety; true incremental scanning is on the v0.5 roadmap.
    """
    try:
        upstream_resp = await upstream.forward_stream(body, headers)
    except httpx.HTTPError as exc:
        logger.warning("upstream forward_stream failed: %s", exc, exc_info=False)
        return JSONResponse(
            status_code=502,
            content={"error": {"type": "upstream_unreachable", "message": str(exc)}},
        )

    if output_scanner is None:
        return StreamingResponse(
            upstream_resp.aiter_raw(),
            status_code=upstream_resp.status_code,
            headers=_filter_response_headers(upstream_resp.headers),
            media_type=upstream_resp.headers.get("content-type", "text/event-stream"),
            background=BackgroundTask(upstream_resp.aclose),
        )

    # Incremental mode preserves streaming UX. It works for block + log but
    # falls back to buffer-then-scan for redact (true streaming redaction
    # needs trail-buffer logic — v0.6 roadmap).
    can_incremental = (
        settings.output_scan_stream_mode == "incremental"
        and output_scanner.mode in {"block", "log"}
    )
    if can_incremental:
        return await _incremental_stream_with_scan(
            upstream_resp, output_scanner, store, model
        )
    return await _buffered_stream_with_scan(upstream_resp, output_scanner, store, model)


async def _incremental_stream_with_scan(
    upstream_resp: httpx.Response,
    scanner: Any,
    store: EventStore | None,
    model: str,
) -> Response:
    """Forward the upstream stream chunk-by-chunk while scanning in the
    background. Truly streaming — no buffer-then-scan UX hit.

    Supports ``block`` (cuts the stream on a hit) and ``log`` (records
    findings without altering the stream). ``redact`` is dispatched to
    the buffered path before we get here.
    """
    incremental = IncrementalStreamScanner(scanner=scanner, mode=scanner.mode)

    # Tracked by source(); read by relay() to decide whether to append a
    # truncation error frame after the source iterator exits.
    truncated_state = {"hit": False}

    async def source() -> Any:
        # Bound the total stream size to prevent OOM, same as the buffered
        # path. When exceeded we stop yielding chunks and set the flag so
        # relay() can append a structured error frame — without that the
        # client just sees an abrupt cut with no explanation.
        total = 0
        try:
            async for chunk in upstream_resp.aiter_raw():
                total += len(chunk)
                if total > settings.max_response_bytes:
                    logger.warning(
                        "upstream stream exceeded max_response_bytes (> %d); aborting",
                        settings.max_response_bytes,
                    )
                    truncated_state["hit"] = True
                    return
                yield chunk
        except httpx.StreamConsumed:
            # MockTransport / pre-buffered response.
            content = upstream_resp.content
            if len(content) <= settings.max_response_bytes:
                yield content
            else:
                logger.warning(
                    "upstream pre-buffered response exceeded max_response_bytes "
                    "(%d > %d); aborting",
                    len(content),
                    settings.max_response_bytes,
                )
                truncated_state["hit"] = True

    async def relay() -> Any:
        try:
            async for emitted in incremental.process_chunks(source()):
                yield emitted
            # If we stopped iterating because the upstream blew past the
            # size cap, surface a structured error frame to the client
            # — equivalent to the 502 the non-streaming path returns.
            if truncated_state["hit"]:
                yield _streaming_truncation_event(
                    f"Upstream stream exceeded {settings.max_response_bytes} bytes "
                    "while output scanning was enabled. Raise "
                    "MITHRIL_MAX_RESPONSE_BYTES or disable output scanning if this "
                    "was intentional."
                )
        finally:
            # After the stream ends, run a final scan so log-mode catches
            # whatever was below the scan-interval threshold at the very end.
            result = await incremental.finalize()
            if store is not None and result is not None and result.findings:
                await store.arecord(
                    action="log" if not incremental.is_blocked else "block",
                    model=model,
                    result=DetectionResult(
                        blocked=incremental.is_blocked,
                        score=result.score,
                        findings=result.findings,
                    ),
                    snippet=f"[output stream] {incremental.accumulated[:120]}",
                )
            await upstream_resp.aclose()

    return StreamingResponse(
        relay(),
        status_code=upstream_resp.status_code,
        headers=_filter_response_headers(upstream_resp.headers),
        media_type=upstream_resp.headers.get("content-type", "text/event-stream"),
    )


async def _buffered_stream_with_scan(
    upstream_resp: httpx.Response,
    scanner: Any,
    store: EventStore | None,
    model: str,
) -> Response:
    """Buffer an SSE stream, scan it, then re-emit (possibly redacted)."""
    full, too_large = await _drain_with_cap(upstream_resp, settings.max_response_bytes)

    if too_large:
        logger.warning(
            "upstream stream exceeded max_response_bytes (> %d); aborting",
            settings.max_response_bytes,
        )
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "type": "response_too_large",
                    "message": (
                        f"Upstream stream exceeded {settings.max_response_bytes} bytes "
                        "while output scanning was enabled. Raise "
                        "MITHRIL_MAX_RESPONSE_BYTES or disable output scanning if this "
                        "was intentional."
                    ),
                }
            },
        )

    accumulated = _extract_sse_content(full)

    if not accumulated:
        return Response(
            content=full,
            status_code=upstream_resp.status_code,
            headers=_filter_response_headers(upstream_resp.headers),
            media_type=upstream_resp.headers.get("content-type", "text/event-stream"),
        )

    result: OutputScanResult = scanner.scan(accumulated)

    if result.action == "block":
        if store is not None:
            await store.arecord(
                action="block",
                model=model,
                result=DetectionResult(
                    blocked=True, score=result.score, findings=result.findings
                ),
                snippet=f"[output stream] {accumulated[:120]}",
            )
        return JSONResponse(
            status_code=403,
            content=OutputBlockResponse.from_result(result).model_dump(),
        )

    if result.action == "redact":
        if store is not None:
            await store.arecord(
                action="log",
                model=model,
                result=DetectionResult(
                    blocked=False, score=result.score, findings=result.findings
                ),
                snippet=f"[output stream redacted] {accumulated[:120]}",
            )
        return _build_redacted_sse_response(
            result.redacted_text or accumulated, upstream_resp
        )

    return Response(
        content=full,
        status_code=upstream_resp.status_code,
        headers=_filter_response_headers(upstream_resp.headers),
        media_type=upstream_resp.headers.get("content-type", "text/event-stream"),
    )


async def _drain_with_cap(
    upstream_resp: httpx.Response, cap: int
) -> tuple[bytes, bool]:
    """Drain an httpx Response body into bytes, enforcing a size cap.

    Returns ``(full_bytes, too_large)``. When ``too_large`` is True the caller
    should not use the bytes. The upstream response is closed on every path.

    Works against both real streamed responses and pre-buffered ones (e.g.
    ``httpx.MockTransport``), which raise ``StreamConsumed`` from
    ``aiter_raw()``. In the pre-buffered case we fall back to ``.content``
    and apply the cap post-hoc.
    """
    chunks: list[bytes] = []
    total = 0
    too_large = False
    try:
        try:
            async for chunk in upstream_resp.aiter_raw():
                total += len(chunk)
                if total > cap:
                    too_large = True
                    break
                chunks.append(chunk)
        except httpx.StreamConsumed:
            content = upstream_resp.content
            if len(content) > cap:
                too_large = True
            else:
                chunks = [content]
                total = len(content)
    finally:
        await upstream_resp.aclose()

    return (b"" if too_large else b"".join(chunks)), too_large


def _extract_sse_content(raw: bytes) -> str:
    """Concatenate every ``delta.content`` string in an SSE stream."""
    import json as _json

    parts: list[str] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            obj = _json.loads(data)
        except _json.JSONDecodeError:
            continue
        for choice in obj.get("choices", []) if isinstance(obj, dict) else []:
            delta = choice.get("delta") if isinstance(choice, dict) else None
            if isinstance(delta, dict):
                content = delta.get("content")
                if isinstance(content, str):
                    parts.append(content)
    return "".join(parts)


def _build_redacted_sse_response(redacted: str, upstream_resp: httpx.Response) -> Response:
    """Emit the redacted content as a single non-chunked SSE message + [DONE]."""
    import json as _json

    chunk = {
        "id": "mithril-redacted",
        "object": "chat.completion.chunk",
        "choices": [
            {"index": 0, "delta": {"role": "assistant", "content": redacted}, "finish_reason": "stop"}
        ],
    }
    body = (
        f"data: {_json.dumps(chunk)}\n\n".encode("utf-8")
        + b"data: [DONE]\n\n"
    )
    return Response(
        content=body,
        status_code=upstream_resp.status_code,
        headers=_filter_response_headers(upstream_resp.headers),
        media_type=upstream_resp.headers.get("content-type", "text/event-stream"),
    )


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    store: EventStore = app.state.store
    stats = await store.astats()
    events = await store.arecent(50)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "version": __version__,
            "stats": stats,
            "events": events,
            "settings": settings,
            "now": time.time(),
        },
    )


@app.get("/api/events")
async def api_events(limit: int = 100) -> dict[str, Any]:
    store: EventStore = app.state.store
    if limit < 1 or limit > 1000:
        raise HTTPException(400, "limit must be between 1 and 1000.")
    return {
        "stats": await store.astats(),
        "events": await store.arecent(limit),
    }
