"""MCP protocol behavior using the official SDK client in-process."""

from __future__ import annotations

from mcp import Client

from stpt_mcp.config import Settings
from stpt_mcp.server import build_server
from tests.support import FakeSTPT, ServiceFactory

EXPECTED_TOOLS = {
    "search_stops",
    "get_stop",
    "list_routes",
    "get_arrivals",
    "get_alerts",
    "get_vehicles",
    "get_service_status",
}


async def test_tools_are_listed_with_schemas(
    service_factory: ServiceFactory, fake: FakeSTPT, settings: Settings
) -> None:
    service, _ = await service_factory(fake=fake)
    server = build_server(settings, service=service)
    async with Client(server) as client:
        assert client.protocol_version == "2026-07-28"
        listed = await client.list_tools()
        assert {tool.name for tool in listed.tools} == EXPECTED_TOOLS
        for tool in listed.tools:
            assert tool.description
            assert tool.input_schema["type"] == "object"
            assert tool.output_schema is not None
            assert tool.annotations is not None
            assert tool.annotations.read_only_hint is True
            assert tool.annotations.destructive_hint is False
        arrivals = next(tool for tool in listed.tools if tool.name == "get_arrivals")
        assert arrivals.input_schema["required"] == ["stop_id"]
        assert arrivals.input_schema["properties"]["limit"]["maximum"] == 50


async def test_every_tool_returns_structured_content(
    service_factory: ServiceFactory, fake: FakeSTPT, settings: Settings
) -> None:
    service, _ = await service_factory(fake=fake)
    server = build_server(settings, service=service)
    async with Client(server) as client:
        search = await client.call_tool("search_stops", {"query": "Catedrala", "limit": 2})
        assert search.is_error is False
        assert search.structured_content is not None
        assert search.structured_content["status"] == "ok"
        assert search.structured_content["count"] == 2
        assert len(search.structured_content["stops"]) == 2

        stop = await client.call_tool("get_stop", {"stop_id": "85"})
        assert stop.structured_content is not None
        assert stop.structured_content["stop"]["stop_id"] == "85"
        assert stop.structured_content["stop"]["directions"]

        routes = await client.call_tool("list_routes", {"mode": "tram"})
        assert routes.structured_content is not None
        assert routes.structured_content["count"] == 6

        arrivals = await client.call_tool("get_arrivals", {"stop_id": "85", "line": "7"})
        assert arrivals.structured_content is not None
        assert arrivals.structured_content["status"] == "ok"
        assert arrivals.structured_content["count"] == 2
        assert arrivals.structured_content["provenance"]["source"] == "live"

        alerts = await client.call_tool("get_alerts", {"line": "7"})
        assert alerts.structured_content is not None
        assert alerts.structured_content["count"] == 2

        vehicles = await client.call_tool("get_vehicles", {"line": "7"})
        assert vehicles.structured_content is not None
        assert vehicles.structured_content["count"] == 1

        status = await client.call_tool("get_service_status", {})
        assert status.structured_content is not None
        assert status.structured_content["ready"] is True
        assert status.structured_content["version"]
        assert status.structured_content["network_feed_version"]


async def test_tool_errors_are_protocol_results_not_tracebacks(
    service_factory: ServiceFactory, fake: FakeSTPT, settings: Settings
) -> None:
    service, _ = await service_factory(fake=fake)
    server = build_server(settings, service=service)
    async with Client(server) as client:
        result = await client.call_tool("get_arrivals", {"stop_id": "does-not-exist"})
        assert result.is_error is False
        assert result.structured_content is not None
        assert result.structured_content["status"] == "error"
        assert result.structured_content["error"]["code"] == "unknown_stop"


async def test_unknown_tool_is_a_protocol_error(
    service_factory: ServiceFactory, fake: FakeSTPT, settings: Settings
) -> None:
    service, _ = await service_factory(fake=fake)
    server = build_server(settings, service=service)
    async with Client(server) as client:
        result = await client.call_tool("not_a_tool", {})
        assert result.is_error is True
        assert result.structured_content is None
        assert result.content
        assert "Unknown tool" in str(result.content[0])
