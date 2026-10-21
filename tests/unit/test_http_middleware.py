"""HTTP authentication and concurrency middleware."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from starlette.responses import JSONResponse

from stpt_mcp.http_app import BearerAuthMiddleware, ConcurrencyLimitMiddleware
from tests.support import asgi_request


async def ok_app(scope: Any, receive: Any, send: Any) -> None:
    response = JSONResponse({"ok": True})
    await response(scope, receive, send)


async def test_bearer_auth_rejects_missing_and_wrong_tokens() -> None:
    app = BearerAuthMiddleware(
        ok_app,
        "test-token-1234567890",
        exempt_paths=frozenset({"/healthz"}),
    )
    missing = await asgi_request(app, path="/mcp", method="POST")
    assert missing.status == 401
    assert missing.headers["www-authenticate"] == 'Bearer realm="stpt-mcp"'

    wrong = await asgi_request(
        app,
        path="/mcp",
        method="POST",
        headers={"authorization": "Bearer nope"},
    )
    assert wrong.status == 401

    basic = await asgi_request(
        app,
        path="/mcp",
        method="POST",
        headers={"authorization": "Basic dGVzdDp0ZXN0"},
    )
    assert basic.status == 401


async def test_bearer_auth_accepts_valid_token_and_exempts_health() -> None:
    app = BearerAuthMiddleware(
        ok_app,
        "test-token-1234567890",
        exempt_paths=frozenset({"/healthz"}),
    )
    valid = await asgi_request(
        app,
        path="/mcp",
        method="POST",
        headers={"authorization": "Bearer test-token-1234567890"},
    )
    assert valid.status == 200
    assert json.loads(valid.body) == {"ok": True}

    health = await asgi_request(app, path="/healthz")
    assert health.status == 200


async def test_bearer_token_comparison_accepts_case_insensitive_scheme() -> None:
    app = BearerAuthMiddleware(
        ok_app,
        "test-token-1234567890",
        exempt_paths=frozenset(),
    )
    response = await asgi_request(
        app,
        path="/mcp",
        headers={"authorization": "bearer test-token-1234567890"},
    )
    assert response.status == 200


async def test_concurrency_limit_rejects_excess_requests() -> None:
    release = asyncio.Event()
    entered = asyncio.Event()

    async def slow_app(scope: Any, receive: Any, send: Any) -> None:
        entered.set()
        await release.wait()
        response = JSONResponse({"ok": True})
        await response(scope, receive, send)

    app = ConcurrencyLimitMiddleware(slow_app, max_concurrent=1)
    first = asyncio.create_task(asgi_request(app, path="/mcp"))
    await entered.wait()
    second = await asgi_request(app, path="/mcp")
    assert second.status == 503
    assert second.headers["retry-after"] == "1"
    assert json.loads(second.body)["error"] == "busy"
    release.set()
    assert (await first).status == 200


async def test_concurrency_limit_recovers_after_request_completes() -> None:
    app = ConcurrencyLimitMiddleware(ok_app, max_concurrent=1)
    assert (await asgi_request(app, path="/mcp")).status == 200
    assert (await asgi_request(app, path="/mcp")).status == 200
