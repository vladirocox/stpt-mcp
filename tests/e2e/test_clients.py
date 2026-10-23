"""End-to-end tests through the official MCP stdio and HTTP clients."""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from mcp import Client, StdioServerParameters
from mcp.client import streamable_http
from mcp.shared._httpx_utils import create_mcp_http_client

from stpt_mcp.http_app import create_http_app
from stpt_mcp.providers.schedule import load_timetable
from tests.support import (
    FakeSTPT,
    FakeUpstreamHTTPServer,
    ServiceFactory,
    make_test_settings,
    run_uvicorn,
)

ROOT = Path(__file__).resolve().parents[2]


def subprocess_env(upstream: FakeUpstreamHTTPServer) -> dict[str, str]:
    base = upstream.base_url
    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": str(ROOT / "src"),
            "STPT_MCP_TRANSPORT": "stdio",
            "STPT_MCP_LOG_LEVEL": "ERROR",
            "STPT_MCP_LIVE_URL": f"{base}/proxy-smtt-cache.php",
            "STPT_MCP_ALERTS_URL": f"{base}/proxy-alerts.php",
            "STPT_MCP_VEHICLES_URL": f"{base}/gtfs-vehicles.php",
            "STPT_MCP_MAX_RETRIES": "1",
            "STPT_MCP_BACKOFF_BASE_SECONDS": "0.001",
            "NO_COLOR": "1",
        }
    )
    return env


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def wait_for_health(url: str, *, attempts: int = 200) -> None:
    async with httpx.AsyncClient(timeout=1.0) as client:
        for _ in range(attempts):
            try:
                response = await client.get(f"{url}/healthz")
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.05)
    raise AssertionError("server did not become healthy")


def assert_schedule_fallback(content: dict[str, Any] | None) -> None:
    """These subprocess tests use the real clock, so honour the timetable's expiry."""

    assert content is not None
    today = datetime.now(ZoneInfo("Europe/Bucharest")).date()
    if today > load_timetable().end + timedelta(days=1):
        assert content["error"]["code"] == "schedule_unavailable"
    else:
        assert content["provenance"]["source"] == "schedule"


@pytest.mark.e2e
async def test_stdio_client_end_to_end() -> None:
    with FakeUpstreamHTTPServer() as upstream:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "stpt_mcp", "serve"],
            env=subprocess_env(upstream),
            cwd=ROOT,
        )
        async with Client(params) as client:
            assert client.protocol_version == "2026-07-28"
            tools = await client.list_tools()
            assert "get_arrivals" in {tool.name for tool in tools.tools}

            live = await client.call_tool("get_arrivals", {"stop_id": "85"})
            assert live.structured_content is not None
            assert live.structured_content["status"] == "ok"
            assert live.structured_content["provenance"]["source"] == "live"
            assert live.structured_content["count"] == 4

            fallback = await client.call_tool("get_arrivals", {"stop_id": "59"})
            assert fallback.structured_content is not None
            assert fallback.structured_content["stop_id"] == "59"
            assert_schedule_fallback(fallback.structured_content)
            assert fallback.structured_content["count"] == len(
                fallback.structured_content["arrivals"]
            )

            stop = await client.call_tool("get_stop", {"stop_id": "85"})
            assert stop.structured_content is not None
            assert stop.structured_content["stop"]["name"] == "Catedrala Mitropolitană"

        # The schedule fallback reads the bundled timetable, not the network.
        assert upstream.path_counts() == {"/proxy-smtt-cache.php": 2}


@pytest.mark.e2e
async def test_http_client_end_to_end_without_auth(
    service_factory: ServiceFactory, fake: FakeSTPT
) -> None:
    service, _ = await service_factory(fake=fake)
    settings = make_test_settings()
    app = create_http_app(settings, service=service)
    async with run_uvicorn(app) as base:
        async with httpx.AsyncClient(base_url=base, timeout=5.0) as http:
            health = await http.get("/healthz")
            assert health.status_code == 200
            assert health.json()["status"] == "ok"

        async with Client(f"{base}/mcp") as client:
            result = await client.call_tool("get_arrivals", {"stop_id": "85"})
            assert result.structured_content is not None
            assert result.structured_content["count"] == 4
            status = await client.call_tool("get_service_status", {})
            assert status.structured_content is not None
            assert status.structured_content["ready"] is True


@pytest.mark.e2e
async def test_http_auth_host_origin_and_body_limits(
    service_factory: ServiceFactory, fake: FakeSTPT
) -> None:
    token = "test-token-1234567890"
    service, _ = await service_factory(fake=fake)
    settings = make_test_settings(auth_token=token, max_request_body_size=1024)
    app = create_http_app(settings, service=service)
    initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    base_headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
    }
    authorized = {**base_headers, "authorization": f"Bearer {token}"}

    async with run_uvicorn(app) as base:
        async with httpx.AsyncClient(base_url=base, timeout=5.0) as http:
            assert (await http.get("/healthz")).status_code == 200
            assert (
                await http.post("/mcp", json=initialize, headers=base_headers)
            ).status_code == 401
            assert (
                await http.post(
                    "/mcp",
                    json=initialize,
                    headers={**base_headers, "authorization": "Bearer wrong-token"},
                )
            ).status_code == 401
            assert (
                await http.post(
                    "/mcp",
                    json=initialize,
                    headers={**authorized, "host": "evil.example.com"},
                )
            ).status_code == 421
            assert (
                await http.post(
                    "/mcp",
                    json=initialize,
                    headers={**authorized, "origin": "http://evil.example.com"},
                )
            ).status_code == 403
            assert (
                await http.post(
                    "/mcp",
                    content=b"x" * 5000,
                    headers={**authorized, "content-type": "application/json"},
                )
            ).status_code == 413

        http_client = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
        async with http_client:
            transport = streamable_http.streamable_http_client(
                f"{base}/mcp", http_client=http_client
            )
            async with Client(transport) as client:
                result = await client.call_tool("search_stops", {"query": "Catedrala", "limit": 2})
                assert result.structured_content is not None
                assert result.structured_content["count"] == 2


@pytest.mark.e2e
async def test_cli_http_subprocess() -> None:
    port = free_port()
    with FakeUpstreamHTTPServer() as upstream:
        env = subprocess_env(upstream)
        env["STPT_MCP_TRANSPORT"] = "http"
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "stpt_mcp",
                "serve",
                "--transport",
                "http",
                "--port",
                str(port),
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            base = f"http://127.0.0.1:{port}"
            await wait_for_health(base)
            async with Client(f"{base}/mcp") as client:
                result = await client.call_tool("get_arrivals", {"stop_id": "85"})
                assert result.structured_content is not None
                assert result.structured_content["status"] == "ok"
                fallback = await client.call_tool("get_arrivals", {"stop_id": "59"})
                assert fallback.structured_content is not None
                assert_schedule_fallback(fallback.structured_content)
        finally:
            process.terminate()
            try:
                _stdout, stderr = await asyncio.to_thread(process.communicate, timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                _stdout, stderr = await asyncio.to_thread(process.communicate)
            if process.returncode not in {0, -15}:
                raise AssertionError(f"HTTP CLI process failed: {stderr}")
