"""MCP server construction and tool definitions."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Literal, cast

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from stpt_mcp import __version__
from stpt_mcp.config import Settings
from stpt_mcp.models import (
    AlertsResult,
    ArrivalsResult,
    RoutesResult,
    ServiceStatus,
    StopResult,
    StopSearchResult,
    VehicleMode,
    VehiclesResult,
)
from stpt_mcp.oauth import build_auth_settings, build_oauth_provider
from stpt_mcp.service import TransitService

SERVER_INSTRUCTIONS = """\
This server exposes read-only, unofficial STPT Timișoara transit information.

Start with search_stops if you only know a stop name, or list_routes for lines.
get_arrivals accepts a numeric STPT stop ID and prefers live predictions, but
only when their freshness is verified against the real clock. Otherwise it
returns departures from the bundled GTFS timetable for today. Always inspect the
top-level `status`, `provenance`, and `error` fields: status is "ok", "partial",
or "error"; provenance identifies live, schedule, or bundled network data, and
`provenance.freshness` is "fresh" only for checked real-time data ("stale" or
"unverified" otherwise); error is structured. An arrival is real-time only when
its `live` field is true.

Times use Europe/Bucharest local time. This project is not affiliated with STPT,
and upstream availability or accuracy is not guaranteed. Do not use it for
safety-critical decisions.
"""

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)


@dataclass(slots=True)
class AppContext:
    service: TransitService
    settings: Settings


def build_server(
    settings: Settings,
    *,
    service: TransitService | None = None,
) -> MCPServer[AppContext]:
    """Create a fully wired server without starting its network resources."""
    transit = service or TransitService(settings)

    @asynccontextmanager
    async def lifespan(_server: MCPServer[AppContext]) -> AsyncIterator[AppContext]:
        await transit.start()
        try:
            yield AppContext(service=transit, settings=settings)
        finally:
            await transit.close()

    oauth_provider = build_oauth_provider(settings)
    auth_settings = build_auth_settings(settings) if oauth_provider is not None else None
    mcp: MCPServer[AppContext] = MCPServer(
        name="stpt-mcp",
        title="STPT Timișoara Transit",
        description="Unofficial live arrivals, schedules, alerts, and vehicles for STPT Timișoara.",
        instructions=SERVER_INSTRUCTIONS,
        version=__version__,
        log_level=cast(
            Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], settings.log_level
        ),
        lifespan=lifespan,
        auth=auth_settings,
        auth_server_provider=oauth_provider,
    )

    @mcp.tool(
        name="search_stops",
        title="Search STPT stops",
        description=(
            "Search the bundled STPT network by stop ID, stop name, or line. "
            "Uses diacritic-insensitive matching. Returns up to 50 stops."
        ),
        annotations=READ_ONLY,
    )
    def search_stops(
        query: Annotated[
            str,
            Field(
                min_length=1,
                max_length=100,
                description="Stop code, stop name fragment, or line number.",
                examples=["Catedrala", "326", "E3"],
            ),
        ],
        limit: Annotated[
            int,
            Field(ge=1, le=50, description="Maximum number of stops to return."),
        ] = 10,
    ) -> StopSearchResult:
        return transit.search_stops(query, limit)

    @mcp.tool(
        name="get_stop",
        title="Get STPT stop details",
        description=(
            "Get coordinates, serving lines, directions, terminals, and neighboring "
            "stops for a numeric STPT stop ID."
        ),
        annotations=READ_ONLY,
    )
    def get_stop(
        stop_id: Annotated[
            str,
            Field(min_length=1, max_length=20, description="Numeric STPT stop ID."),
        ],
    ) -> StopResult:
        return transit.get_stop(stop_id)

    @mcp.tool(
        name="list_routes",
        title="List STPT routes",
        description=(
            "List STPT tram, trolleybus, bus, metropolitan, express, and vaporetto "
            "routes from the bundled GTFS-derived network snapshot."
        ),
        annotations=READ_ONLY,
    )
    def list_routes(
        mode: Annotated[
            VehicleMode | None,
            Field(description="Optional mode filter."),
        ] = None,
    ) -> RoutesResult:
        return transit.list_routes(mode)

    @mcp.tool(
        name="get_arrivals",
        title="Get STPT arrivals",
        description=(
            "Get upcoming arrivals at an STPT stop. Live data is used only when its "
            "freshness is verified; otherwise departures come from the bundled GTFS "
            "timetable. Check provenance.freshness, provenance.fallback_reason, and "
            "each arrival's live flag."
        ),
        annotations=READ_ONLY,
    )
    async def get_arrivals(
        stop_id: Annotated[
            str,
            Field(min_length=1, max_length=20, description="Numeric STPT stop ID."),
        ],
        line: Annotated[
            str | None,
            Field(
                min_length=1,
                max_length=10,
                description="Optional line filter such as '2', 'E3', or 'M32'.",
            ),
        ] = None,
        limit: Annotated[
            int,
            Field(ge=1, le=50, description="Maximum number of arrivals to return."),
        ] = 10,
    ) -> ArrivalsResult:
        return await transit.get_arrivals(stop_id, line=line, limit=limit)

    @mcp.tool(
        name="get_alerts",
        title="Get STPT service alerts",
        description=(
            "Get current or historical STPT service alerts. Filters by stop ID "
            "and/or line when supplied. Active periods are evaluated in "
            "Europe/Bucharest."
        ),
        annotations=READ_ONLY,
    )
    async def get_alerts(
        stop_id: Annotated[
            str | None,
            Field(min_length=1, max_length=20, description="Optional STPT stop ID."),
        ] = None,
        line: Annotated[
            str | None,
            Field(min_length=1, max_length=10, description="Optional line filter."),
        ] = None,
        active_only: Annotated[
            bool,
            Field(description="Return only alerts active at the current instant."),
        ] = True,
        limit: Annotated[
            int,
            Field(ge=1, le=100, description="Maximum number of alerts."),
        ] = 50,
    ) -> AlertsResult:
        return await transit.get_alerts(
            stop_id=stop_id,
            line=line,
            active_only=active_only,
            limit=limit,
        )

    @mcp.tool(
        name="get_vehicles",
        title="Get STPT vehicle positions",
        description=(
            "Get current STPT vehicle positions, optionally filtered by line. Each "
            "vehicle includes its upstream observation timestamp and staleness flag."
        ),
        annotations=READ_ONLY,
    )
    async def get_vehicles(
        line: Annotated[
            str | None,
            Field(min_length=1, max_length=10, description="Optional line filter."),
        ] = None,
        limit: Annotated[
            int,
            Field(ge=1, le=200, description="Maximum number of vehicles."),
        ] = 100,
    ) -> VehiclesResult:
        return await transit.get_vehicles(line=line, limit=limit)

    @mcp.tool(
        name="get_service_status",
        title="Get STPT MCP service status",
        description=(
            "Report server readiness, version, bundled network feed metadata, and "
            "bounded cache statistics. Contains no secrets."
        ),
        annotations=READ_ONLY,
    )
    async def get_service_status() -> ServiceStatus:
        return await transit.get_status()

    @mcp.custom_route(  # type: ignore[untyped-decorator]
        "/healthz", methods=["GET"], include_in_schema=False
    )
    async def healthz(_request: Request) -> Response:
        if not transit.ready:
            return JSONResponse({"status": "starting", "service": "stpt-mcp"}, status_code=503)
        return JSONResponse(
            {"status": "ok", "service": "stpt-mcp", "version": __version__},
            status_code=200,
        )

    if oauth_provider is not None:
        provider = oauth_provider

        @mcp.custom_route(  # type: ignore[untyped-decorator]
            "/oauth/login", methods=["GET", "POST"], include_in_schema=False
        )
        async def oauth_login(request: Request) -> Response:
            return await provider.handle_login(request)

    return mcp
