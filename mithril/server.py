from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from mithril import __version__
from mithril.config import settings
from mithril.detectors import default_pipeline
from mithril.models import BlockResponse, ChatCompletionRequest, DetectionResult
from mithril.proxy import UpstreamClient
from mithril.storage import EventStore

TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pipeline = default_pipeline(threshold=settings.threshold)
    app.state.upstream = UpstreamClient(settings.upstream_url)
    app.state.store = EventStore(settings.db_path)
    yield
    await app.state.upstream.aclose()


app = FastAPI(
    title="PromptGuard",
    description="A firewall for LLMs — blocks prompt injection, jailbreaks, and PII exfil.",
    version=__version__,
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": __version__,
        "mode": settings.mode,
        "threshold": settings.threshold,
        "upstream": settings.upstream_url,
    }


@app.post("/v1/scan")
async def scan(payload: dict[str, Any]) -> dict[str, Any]:
    """Standalone scan endpoint — no upstream forwarding.

    Body: {"text": "..."} or {"messages": [{"role": "...", "content": "..."}]}
    """
    pipeline = app.state.pipeline
    if "text" in payload:
        result = pipeline.scan(str(payload["text"]))
    elif "messages" in payload:
        texts = [str(m.get("content", "")) for m in payload["messages"]]
        result = pipeline.scan_messages(texts)
    else:
        raise HTTPException(400, "Provide 'text' or 'messages'.")
    return result.model_dump()


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Any:
    """OpenAI-compatible chat completions endpoint.

    Drop-in replacement: point your existing OpenAI SDK at http://<host>:<port>/v1
    and PromptGuard will scan every request before it reaches the upstream model.
    """
    body = await request.json()
    try:
        parsed = ChatCompletionRequest.model_validate(body)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Invalid chat completions request: {exc}")

    texts = [m.text() for m in parsed.messages]
    result: DetectionResult = app.state.pipeline.scan_messages(texts)
    snippet = " | ".join(t[:120] for t in texts if t)

    if result.blocked and settings.mode == "block":
        app.state.store.record(
            action="block", model=parsed.model, result=result, snippet=snippet
        )
        return JSONResponse(
            status_code=403,
            content=BlockResponse.from_result(result).model_dump(),
        )

    # Either clean, or in 'log' mode — forward upstream.
    action = "log" if result.blocked else "allow"
    app.state.store.record(
        action=action, model=parsed.model, result=result, snippet=snippet
    )

    headers = {k: v for k, v in request.headers.items()}
    if parsed.stream:
        upstream_resp = await app.state.upstream.forward_stream(body, headers)
        return StreamingResponse(
            upstream_resp.aiter_raw(),
            status_code=upstream_resp.status_code,
            media_type=upstream_resp.headers.get("content-type", "text/event-stream"),
            background=None,
        )

    upstream_resp = await app.state.upstream.forward_chat(body, headers)
    return JSONResponse(
        status_code=upstream_resp.status_code,
        content=upstream_resp.json(),
    )


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    store: EventStore = app.state.store
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "version": __version__,
            "stats": store.stats(),
            "events": store.recent(50),
            "settings": settings,
            "now": time.time(),
        },
    )


@app.get("/api/events")
async def api_events(limit: int = 100) -> dict[str, Any]:
    return {
        "stats": app.state.store.stats(),
        "events": app.state.store.recent(limit),
    }
