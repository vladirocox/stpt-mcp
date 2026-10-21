"""Streamable HTTP app with authentication and request limits."""

from __future__ import annotations

import hmac

from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from stpt_mcp.config import Settings
from stpt_mcp.oauth import OAuthMetadataMiddleware
from stpt_mcp.server import build_server
from stpt_mcp.service import TransitService


class BearerAuthMiddleware:
    """Protect every HTTP route except the liveness endpoint with a static token."""

    def __init__(self, app: ASGIApp, token: str, *, exempt_paths: frozenset[str]) -> None:
        self.app = app
        self.token = token
        self.exempt_paths = exempt_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self.exempt_paths:
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        authorization = headers.get("authorization", "")
        scheme, _, credentials = authorization.partition(" ")
        if scheme.casefold() != "bearer" or not hmac.compare_digest(
            credentials.strip(), self.token
        ):
            response = JSONResponse(
                {"error": "unauthorized", "message": "A valid bearer token is required."},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="stpt-mcp"'},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


class ConcurrencyLimitMiddleware:
    """Reject excess concurrent HTTP requests without queueing unboundedly."""

    def __init__(self, app: ASGIApp, max_concurrent: int) -> None:
        self.app = app
        self.max_concurrent = max_concurrent
        self._active = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if self._active >= self.max_concurrent:
            response = JSONResponse(
                {"error": "busy", "message": "The server is at its concurrency limit."},
                status_code=503,
                headers={"Retry-After": "1"},
            )
            await response(scope, receive, send)
            return
        self._active += 1
        try:
            await self.app(scope, receive, send)
        finally:
            self._active -= 1


def create_http_app(settings: Settings, *, service: TransitService | None = None) -> ASGIApp:
    """Build the ASGI app used by the CLI and deployment examples."""
    mcp = build_server(settings, service=service)
    app: Starlette = mcp.streamable_http_app(
        streamable_http_path=settings.path,
        transport_security=settings.transport_security(),
        max_request_body_size=settings.max_request_body_size,
        max_sessions=settings.max_sessions,
        session_idle_timeout=settings.session_idle_timeout_seconds,
        stateless_http=True,
        host=settings.host,
    )
    wrapped: ASGIApp = app
    if settings.oauth_enabled():
        wrapped = OAuthMetadataMiddleware(wrapped)
    elif settings.auth_token is not None:
        wrapped = BearerAuthMiddleware(
            wrapped,
            settings.auth_token,
            exempt_paths=frozenset({"/healthz"}),
        )
    wrapped = ConcurrencyLimitMiddleware(wrapped, settings.max_concurrent_http_requests)
    return wrapped
