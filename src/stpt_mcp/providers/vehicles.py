"""Live vehicle-position provider."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from stpt_mcp.config import Settings
from stpt_mcp.errors import ErrorCode, StptError
from stpt_mcp.network import NetworkIndex
from stpt_mcp.providers.base import ProviderResult, VehicleRecord, parse_epoch
from stpt_mcp.upstream import UpstreamClient


class VehiclesProvider:
    def __init__(self, client: UpstreamClient, settings: Settings, network: NetworkIndex) -> None:
        self._client = client
        self._settings = settings
        self._network = network

    async def fetch(self) -> ProviderResult[tuple[VehicleRecord, ...]]:
        raw, fetched_at = await self._client.get_json(
            self._settings.vehicles_url,
            source="vehicles",
            headers={"Referer": "https://live.stpt.ro/"},
        )
        return parse_vehicles(raw, fetched_at=fetched_at, network=self._network)


def parse_vehicles(
    raw: Any,
    *,
    fetched_at: datetime,
    network: NetworkIndex,
) -> ProviderResult[tuple[VehicleRecord, ...]]:
    if not isinstance(raw, dict) or not raw.get("success"):
        raise StptError(
            ErrorCode.UPSTREAM_INVALID_RESPONSE,
            "vehicles response is missing success/data",
            retryable=True,
            upstream="vehicles",
        )
    data = raw.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("vehicles"), list):
        raise StptError(
            ErrorCode.UPSTREAM_INVALID_RESPONSE,
            "vehicles response does not contain a vehicle list",
            retryable=True,
            upstream="vehicles",
        )

    vehicles: list[VehicleRecord] = []
    warnings: list[str] = []
    skipped = 0
    missing_timestamps = 0
    for item in data["vehicles"]:
        if not isinstance(item, dict):
            skipped += 1
            continue
        line = _text(item.get("route") or item.get("line"))
        vehicle_id = _text(item.get("id") or item.get("vehicleId"))
        try:
            latitude = float(item["lat"])
            longitude = float(item["lng"])
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        if not line or not vehicle_id:
            skipped += 1
            continue
        observed_at = parse_epoch(item.get("timestamp"))
        if observed_at is None:
            missing_timestamps += 1
        vehicles.append(
            VehicleRecord(
                vehicle_id=vehicle_id,
                line=line,
                direction_id=_optional_text(item.get("directionId")),
                headsign=_optional_text(item.get("headsign")),
                latitude=latitude,
                longitude=longitude,
                bearing=_optional_float(item.get("bearing")),
                speed_kmh=_optional_float(item.get("speed")),
                current_stop=_optional_text(item.get("stop")),
                accessible=_optional_bool(item.get("isAccessible", item.get("accessible"))),
                route_id=_optional_text(item.get("routeId")),
                trip_id=_optional_text(item.get("tripId")),
                shape_id=_optional_text(item.get("shapeId")),
                observed_at=observed_at,
            )
        )
    if skipped:
        warnings.append(f"Ignored {skipped} malformed vehicle record(s).")
    if missing_timestamps:
        warnings.append(
            f"{missing_timestamps} vehicle record(s) had no valid observation timestamp."
        )
    vehicles.sort(key=lambda item: (_natural_line_key(item.line), item.vehicle_id))
    return ProviderResult(
        value=tuple(vehicles),
        fetched_at=fetched_at or datetime.now(UTC),
        warnings=tuple(warnings),
        complete=not skipped,
    )


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _optional_text(value: object) -> str | None:
    text = _text(value)
    return text or None


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _optional_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return None


def _natural_line_key(line: str) -> tuple[int, str]:
    digits = ""
    for char in line:
        if char.isdigit():
            digits += char
        else:
            break
    return (int(digits) if digits else 10**6, line.casefold())
