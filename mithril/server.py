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
from mithril.output import default_output_scanner
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
    judge = build_judge(settings)
    app.state.pipeline = default_pipeline(
        threshold=settings.threshold,
        judge=judge,
    )
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
    result: DetectionResult = await app.state.pipeline.evaluate_messages(texts)
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

    content_bytes = upstream_resp.content
    if output_scanner is not None and upstream_resp.status_code == 200:
        content_bytes = await _apply_output_scan_blocking(
            content_bytes, output_scanner, store, model
        )
        if isinstance(content_bytes, Response):
            return content_bytes

    return Response(
        content=content_bytes,
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
            from mithril.models import DetectionResult

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
            from mithril.models import DetectionResult

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

    return await _buffered_stream_with_scan(upstream_resp, output_scanner, store, model)


async def _buffered_stream_with_scan(
    upstream_resp: httpx.Response,
    scanner: Any,
    store: EventStore | None,
    model: str,
) -> Response:
    """Buffer an SSE stream, scan it, then re-emit (possibly redacted)."""
    try:
        # `aread()` works whether or not the response was started in stream
        # mode — it's the safest way to fully drain the body once.
        full = await upstream_resp.aread()
    finally:
        await upstream_resp.aclose()

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
            from mithril.models import DetectionResult

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
            from mithril.models import DetectionResult

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
