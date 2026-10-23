"""Typed, machine-readable models returned by every MCP tool."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from stpt_mcp.errors import ErrorCode


class Status(StrEnum):
    OK = "ok"
    PARTIAL = "partial"
    ERROR = "error"


class DataSource(StrEnum):
    LIVE = "live"
    SCHEDULE = "schedule"
    NETWORK = "network"


class VehicleMode(StrEnum):
    TRAM = "tram"
    TROLLEYBUS = "trolleybus"
    BUS = "bus"
    VAPORETTO = "vaporetto"
    UNKNOWN = "unknown"


class Direction(StrEnum):
    OUTBOUND = "tur"
    INBOUND = "retur"


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorInfo(Model):
    code: ErrorCode = Field(description="Stable machine-readable error code.")
    message: str = Field(description="Human-readable, non-sensitive error message.")
    retryable: bool = Field(description="Whether a later retry may succeed.")
    upstream: str | None = Field(default=None, description="Upstream subsystem that failed.")


class Provenance(Model):
    source: DataSource = Field(description="Source of the returned data.")
    fetched_at: AwareDatetime = Field(description="When this server retrieved the data, in UTC.")
    observed_at: AwareDatetime | None = Field(
        default=None,
        description=(
            "Upstream observation timestamp in UTC. Live arrivals derive it from the feed's "
            "own display times and remaining minutes; it is null when the feed carries no "
            "clock reference or is rejected as stale. Schedules describe future service and "
            "leave it null; vehicles use their upstream timestamp."
        ),
    )
    is_stale: bool = Field(
        description="True when stale data is being returned, whether from cache or upstream."
    )
    data_age_seconds: float | None = Field(
        default=None,
        ge=0,
        description=(
            "Age of the upstream observation when returned, in seconds. Null when the age "
            "cannot be measured."
        ),
    )
    fallback_reason: str | None = Field(
        default=None,
        description="Why the primary source was not used, if applicable.",
    )
    warnings: list[str] = Field(default_factory=list, description="Data-quality warnings.")


class BaseResult(Model):
    status: Status = Field(description="Overall result status.")
    error: ErrorInfo | None = Field(default=None, description="Structured failure, if any.")
    provenance: Provenance | None = Field(
        default=None, description="Source and freshness metadata."
    )


class StopSummary(Model):
    stop_id: str = Field(description="STPT numeric stop ID.")
    name: str
    latitude: float
    longitude: float
    routes: list[str] = Field(default_factory=list, description="Lines serving this stop.")
    is_terminal: bool = False


class StopDirection(Model):
    line: str
    direction: Direction
    shape_id: str
    destination: str = Field(description="Terminal stop for this direction.")
    stop_sequence: int = Field(description="Zero-based position of the stop in the shape.")


class StopInfo(StopSummary):
    directions: list[StopDirection] = Field(default_factory=list)
    neighbors: list[str] = Field(default_factory=list)


class StopSearchResult(BaseResult):
    query: str
    count: int
    stops: list[StopSummary] = Field(default_factory=list)


class StopResult(BaseResult):
    stop: StopInfo | None = None


class RouteInfo(Model):
    line: str
    name: str
    long_name: str
    category: str
    mode: VehicleMode
    type_name: str
    color: str | None = None
    text_color: str | None = None


class RoutesResult(BaseResult):
    count: int
    routes: list[RouteInfo] = Field(default_factory=list)


class Arrival(Model):
    line: str
    destination: str
    direction: Direction | None = None
    mode: VehicleMode = VehicleMode.UNKNOWN
    eta_minutes: int = Field(ge=0, description="Minutes until arrival as reported by this source.")
    arrival_time: str | None = Field(
        default=None,
        description="Local HH:MM display time; not a guaranteed timestamp.",
    )
    live: bool = Field(description="True for real-time data, false for schedule fallback.")
    vehicle_id: str | None = None
    shape_id: str | None = None


class ArrivalsResult(BaseResult):
    stop_id: str
    stop_name: str
    line_filter: str | None = None
    lookahead_minutes: int | None = None
    count: int
    arrivals: list[Arrival] = Field(default_factory=list)


class AlertPeriod(Model):
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None


class TransitAlert(Model):
    id: str
    title: str
    description: str = ""
    cause: str | None = None
    effect: str | None = None
    active: bool = Field(description="Whether the alert is active at the requested instant.")
    active_periods: list[AlertPeriod] = Field(default_factory=list)
    affected_routes: list[str] = Field(default_factory=list)
    affected_stop_ids: list[str] = Field(default_factory=list)


class AlertsResult(BaseResult):
    count: int
    active_only: bool
    stop_id: str | None = None
    line: str | None = None
    alerts: list[TransitAlert] = Field(default_factory=list)


class Vehicle(Model):
    vehicle_id: str
    line: str
    direction_id: str | None = None
    headsign: str | None = None
    latitude: float
    longitude: float
    bearing: float | None = None
    speed_kmh: float | None = None
    current_stop: str | None = None
    accessible: bool | None = None
    route_id: str | None = None
    trip_id: str | None = None
    shape_id: str | None = None
    observed_at: AwareDatetime | None = None
    age_seconds: float | None = Field(default=None, ge=0)
    is_stale: bool = False


class VehiclesResult(BaseResult):
    count: int
    line_filter: str | None = None
    vehicles: list[Vehicle] = Field(default_factory=list)


class CacheStats(Model):
    name: str
    entries: int
    max_entries: int
    hits: int
    misses: int
    coalesced: int
    stale_hits: int
    evictions: int
    loads: int
    failures: int
    inflight: int


class ServiceStatus(BaseResult):
    ready: bool
    version: str
    current_time: AwareDatetime
    timezone: str
    started_at: AwareDatetime | None = None
    network_feed_version: str
    network_generated_at: AwareDatetime
    network_service_range: dict[str, str] = Field(default_factory=dict)
    caches: list[CacheStats] = Field(default_factory=list)


class HealthResponse(Model):
    status: Literal["ok"] = "ok"
    service: str = "stpt-mcp"
    version: str
