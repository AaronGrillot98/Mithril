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
from mithril.models import BlockResponse, ChatCompletionRequest, DetectionResult
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
        return await _proxy_stream(app.state.upstream, body, headers)
    return await _proxy_blocking(app.state.upstream, body, headers)


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


async def _proxy_blocking(upstream: UpstreamClient, body: dict[str, Any], headers: dict[str, str]) -> Response:
    """Forward a non-streaming request and return the upstream response verbatim.

    Importantly: if upstream returns a non-JSON body (HTML 502, etc.) we pass
    bytes + content-type through unchanged instead of crashing on json().
    """
    try:
        upstream_resp = await upstream.forward_chat(body, headers)
    except httpx.HTTPError as exc:
        logger.warning("upstream forward_chat failed: %s", exc, exc_info=False)
        return JSONResponse(
            status_code=502,
            content={"error": {"type": "upstream_unreachable", "message": str(exc)}},
        )
    return Response(
        content=upstream_resp.content,
        status_code=upstream_resp.status_code,
        headers=_filter_response_headers(upstream_resp.headers),
        media_type=upstream_resp.headers.get("content-type"),
    )


async def _proxy_stream(upstream: UpstreamClient, body: dict[str, Any], headers: dict[str, str]) -> Response:
    """Forward a streaming request and proxy the upstream byte stream back."""
    try:
        upstream_resp = await upstream.forward_stream(body, headers)
    except httpx.HTTPError as exc:
        logger.warning("upstream forward_stream failed: %s", exc, exc_info=False)
        return JSONResponse(
            status_code=502,
            content={"error": {"type": "upstream_unreachable", "message": str(exc)}},
        )
    return StreamingResponse(
        upstream_resp.aiter_raw(),
        status_code=upstream_resp.status_code,
        headers=_filter_response_headers(upstream_resp.headers),
        media_type=upstream_resp.headers.get("content-type", "text/event-stream"),
        # Critical: close the upstream response when the client disconnects.
        background=BackgroundTask(upstream_resp.aclose),
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
