"""Server middleware shared across Mithril's FastAPI app.

Right now we only have one: a request-ID + access-log middleware that
gives every request a stable correlation ID, surfaces it in response
headers, and writes a single structured log line per request.

Why this is its own module: the `integrations/fastapi.py` middleware is
*public API* for embedding Mithril in other people's apps. This file is
internal to the Mithril proxy server. Keeping them apart prevents
confusion and avoids accidentally exposing internal logging hooks to
end users.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

logger = logging.getLogger("mithril.access")

REQUEST_ID_HEADER = "X-Request-ID"


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Tag every request with a stable correlation ID and emit one access log line.

    The ID comes from the inbound `X-Request-ID` header if present (so callers
    can plumb a trace through their own systems), or we generate a UUID4.
    Either way, it's attached to `request.state.request_id`, included in the
    structured access log, and echoed back in the response headers.
    """

    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = request_id

        start = time.perf_counter()
        status = 500  # in case call_next raises
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "%s %s %d %.2fms id=%s",
                request.method,
                request.url.path,
                status,
                duration_ms,
                request_id,
            )
