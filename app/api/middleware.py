import uuid

import structlog
import structlog.contextvars
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings


class RequestIDMiddleware:
    """ASGI middleware that attaches a request-scoped ID to every request.

    The ID is read from the configured header (default: ``X-Request-ID``).
    If the header is absent a UUID4 is generated.  The value is:

    * bound to structlog's contextvars so it propagates to all log calls
      made during the request lifetime;
    * echoed back in the response under the same header name.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.header = settings.request_id_header

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        request_id = request.headers.get(self.header) or str(uuid.uuid4())

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        async def send_with_header(message: dict) -> None:  # type: ignore[type-arg]
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append(
                    (self.header.lower().encode(), request_id.encode())
                )
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            structlog.contextvars.clear_contextvars()
