"""Bound request bodies before multipart parsing can spool an unlimited file."""

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse


class RequestBodyLimitMiddleware:
    def __init__(self, app, max_bytes):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") in {"GET", "HEAD", "OPTIONS"}:
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            response = JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)
            return await response(scope, receive, send)
        if length < 0 or length > self.max_bytes:
            response = JSONResponse({"detail": "Request body exceeds the upload limit"}, status_code=413)
            return await response(scope, receive, send)

        received = 0
        exceeded = False
        rejected = False

        async def limited_receive():
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    exceeded = True
                    # Starlette closes partially parsed multipart files on
                    # stream errors. Its HTTP handler returns this as 413.
                    raise HTTPException(413, "Request body exceeds the upload limit")
            return message

        async def limited_send(message):
            nonlocal rejected
            if exceeded and not rejected:
                # BaseHTTPMiddleware can wrap a receive exception, causing a
                # multipart parser to turn it into a generic 400. The outer
                # body limiter retains the actual reason and returns 413.
                response = JSONResponse({"detail": "Request body exceeds the upload limit"}, status_code=413)
                rejected = True
                await response(scope, receive, send)
            elif not rejected:
                await send(message)

        try:
            return await self.app(scope, limited_receive, limited_send)
        except Exception:
            # BaseHTTPMiddleware can also propagate a receive exception as an
            # ExceptionGroup instead of producing a parser response.
            if not exceeded:
                raise
            if not rejected:
                response = JSONResponse({"detail": "Request body exceeds the upload limit"}, status_code=413)
                await response(scope, receive, send)
