"""FastAPI integration.

Two ways to use Mithril in a FastAPI app:

1. **Middleware** (zero-touch on existing routes). Scans bodies of POST/PUT
   requests on configured paths and returns HTTP 403 with a structured
   reason if the request contains a prompt that fails the firewall.

       from fastapi import FastAPI
       from mithril.integrations.fastapi import MithrilMiddleware

       app = FastAPI()
       app.add_middleware(
           MithrilMiddleware,
           paths=["/v1/chat", "/api/ask"],   # only scan these
           json_field="message",              # path to the prompt in the JSON body
       )

2. **Dependency** (explicit per-route). Cleaner when you only need to scan
   a single endpoint's text.

       from fastapi import FastAPI, Body
       from mithril.integrations.fastapi import MithrilGuard

       app = FastAPI()
       guard = MithrilGuard()

       @app.post("/chat")
       async def chat(message: str = Body(..., embed=True), _=Depends(guard)):
           # message has already been scanned and approved when we get here
           return llm.invoke(message)

Both approaches return HTTP 403 with a JSON body modeled after the
proxy server's BlockResponse so error-handling stays consistent across
deployments.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from mithril.detectors.pipeline import DetectionPipeline
from mithril.integrations._shared import (
    MithrilBlocked,
    build_default_pipeline,
    extract_message_texts,
)
from mithril.models import BlockResponse


def _extract_by_field(body: Any, field: str | None) -> list[str]:
    """Pull text from a parsed JSON body using a simple dotted field path.

    Supported shapes:
      - field == None: scan all string leaves we can find under common keys
                       ("messages", "input", "text", "prompt", "content").
      - field == "message": body["message"] (string).
      - field == "messages": body["messages"] (list of chat-style messages).
      - field == "a.b": nested key.
    """
    if body is None:
        return []
    if isinstance(body, str):
        return [body]

    # No explicit field — best-effort.
    if field is None:
        out: list[str] = []
        if isinstance(body, dict):
            if isinstance(body.get("messages"), list):
                out.extend(extract_message_texts(body["messages"]))
            for k in ("input", "text", "prompt", "content", "message", "query"):
                v = body.get(k)
                if isinstance(v, str):
                    out.append(v)
                elif isinstance(v, list):
                    out.extend(extract_message_texts(v))
        return out

    # Explicit field path.
    cursor: Any = body
    for part in field.split("."):
        if isinstance(cursor, dict) and part in cursor:
            cursor = cursor[part]
        else:
            return []
    if isinstance(cursor, str):
        return [cursor]
    if isinstance(cursor, list):
        return extract_message_texts(cursor)
    return []


class MithrilMiddleware(BaseHTTPMiddleware):
    """ASGI middleware that scans request bodies on configured paths."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        pipeline: DetectionPipeline | None = None,
        paths: list[str] | None = None,
        methods: list[str] | None = None,
        json_field: str | None = None,
        use_judge: bool = False,
    ):
        super().__init__(app)
        self.pipeline = pipeline or build_default_pipeline(use_judge=use_judge)
        self.paths = paths
        self.methods = {m.upper() for m in (methods or ["POST", "PUT", "PATCH"])}
        self.json_field = json_field
        self.use_judge = use_judge

    def _should_scan(self, request: Request) -> bool:
        if request.method.upper() not in self.methods:
            return False
        if self.paths is None:
            return True
        path = request.url.path
        return any(path == p or path.startswith(p.rstrip("/") + "/") for p in self.paths)

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        if not self._should_scan(request):
            return await call_next(request)

        raw = await request.body()
        if not raw:
            return await call_next(request)

        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            # Not JSON — scan the raw body as text.
            texts = [raw.decode("utf-8", errors="replace")]
        else:
            texts = _extract_by_field(body, self.json_field)

        if texts:
            if self.use_judge:
                result = await self.pipeline.evaluate_messages(texts)
            else:
                result = self.pipeline.scan_messages(texts)
            if result.blocked:
                return JSONResponse(
                    status_code=403,
                    content=BlockResponse.from_result(result).model_dump(),
                )

        # Re-attach the body so the downstream handler can read it.
        async def receive():  # noqa: ANN202
            return {"type": "http.request", "body": raw, "more_body": False}

        request._receive = receive  # type: ignore[attr-defined]
        return await call_next(request)


class MithrilGuard:
    """FastAPI dependency that scans a configured JSON body field.

    Used as `Depends(MithrilGuard("message"))`. On block, raises HTTPException
    with status 403 and the structured BlockResponse body.
    """

    def __init__(
        self,
        field: str | None = None,
        *,
        pipeline: DetectionPipeline | None = None,
        use_judge: bool = False,
    ):
        self.field = field
        self.pipeline = pipeline or build_default_pipeline(use_judge=use_judge)
        self.use_judge = use_judge

    async def __call__(self, request: Request) -> None:
        raw = await request.body()
        if not raw:
            return
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            texts = [raw.decode("utf-8", errors="replace")]
        else:
            texts = _extract_by_field(body, self.field)

        if not texts:
            return

        if self.use_judge:
            result = await self.pipeline.evaluate_messages(texts)
        else:
            result = self.pipeline.scan_messages(texts)
        if result.blocked:
            raise HTTPException(
                status_code=403,
                detail=BlockResponse.from_result(result).model_dump()["error"],
            )

        # Re-attach so FastAPI can re-read the body for the actual handler.
        async def receive():  # noqa: ANN202
            return {"type": "http.request", "body": raw, "more_body": False}

        request._receive = receive  # type: ignore[attr-defined]


__all__ = ["MithrilMiddleware", "MithrilGuard", "MithrilBlocked"]
