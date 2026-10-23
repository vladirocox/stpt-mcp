"""Internal provider records and results."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Generic, TypeVar

from stpt_mcp.models import Direction, VehicleMode

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ProviderResult(Generic[T]):
    value: T
    fetched_at: datetime
    warnings: tuple[str, ...] = ()
    complete: bool = True
    stale: bool = False
    observed_at: datetime | None = None
    skew_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class LiveArrival:
    line: str
    destination: str
    mode: VehicleMode
    eta_minutes: int
    """ETA in minutes as reported by the feed at fetch time."""

    arrival_at: datetime
    """Absolute arrival instant anchored to the fetch time, in UTC."""

    vehicle_id: str | None
    feed_time: str | None = None
    """Raw ``HH:MM`` display time from the feed. Kept only for freshness checks."""


@dataclass(frozen=True, slots=True)
class ScheduleDeparture:
    line: str
    destination: str
    direction: Direction
    mode: VehicleMode
    shape_id: str
    departure_at: datetime


@dataclass(frozen=True, slots=True)
class AlertRecord:
    alert_id: str
    title: str
    description: str
    cause: str | None
    effect: str | None
    periods: tuple[tuple[datetime | None, datetime | None], ...] = ()
    routes: tuple[str, ...] = ()
    stop_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class VehicleRecord:
    vehicle_id: str
    line: str
    direction_id: str | None
    headsign: str | None
    latitude: float
    longitude: float
    bearing: float | None
    speed_kmh: float | None
    current_stop: str | None
    accessible: bool | None
    route_id: str | None
    trip_id: str | None
    shape_id: str | None
    observed_at: datetime | None


def parse_epoch(value: object) -> datetime | None:
    """Parse GTFS-style epoch seconds/milliseconds or an ISO 8601 timestamp."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        epoch = float(value)
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            epoch = float(stripped)
        except ValueError:
            try:
                return datetime.fromisoformat(stripped.replace("Z", "+00:00"))
            except ValueError:
                return None
    else:
        return None
    if epoch <= 0:
        return None
    if epoch > 100_000_000_000:
        epoch /= 1000.0
    try:
        return datetime.fromtimestamp(epoch, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None
