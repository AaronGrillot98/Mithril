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
from mithril.judges import build_judge
from mithril.models import BlockResponse, ChatCompletionRequest, DetectionResult
from mithril.proxy import UpstreamClient
from mithril.storage import EventStore

TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


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


app = FastAPI(
    title="Mithril",
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
        "judge": {
            "enabled": settings.judge_enabled,
            "provider": settings.judge_provider,
            "model": settings.judge_model if settings.judge_enabled else None,
            "low": settings.judge_low_threshold,
            "high": settings.judge_high_threshold,
            "fail_mode": settings.judge_fail_mode,
        },
    }


@app.post("/v1/scan")
async def scan(payload: dict[str, Any]) -> dict[str, Any]:
    """Standalone scan endpoint — no upstream forwarding.

    Body:
      {"text": "..."}                    or
      {"messages": [{"role": "...", "content": "..."}]}
      ?judge=true|false (default true if judge enabled in settings)
    """
    pipeline = app.state.pipeline
    use_judge = bool(payload.get("judge", True))

    if "text" in payload:
        text = str(payload["text"])
        result = await pipeline.evaluate(text) if use_judge else pipeline.scan(text)
    elif "messages" in payload:
        texts = [str(m.get("content", "")) for m in payload["messages"]]
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
    body = await request.json()
    try:
        parsed = ChatCompletionRequest.model_validate(body)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Invalid chat completions request: {exc}")

    texts = [m.text() for m in parsed.messages]
    result: DetectionResult = await app.state.pipeline.evaluate_messages(texts)
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
