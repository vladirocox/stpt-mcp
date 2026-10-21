"""Live real-time arrivals provider."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from stpt_mcp.config import Settings
from stpt_mcp.errors import ErrorCode, StptError
from stpt_mcp.models import VehicleMode
from stpt_mcp.network import NetworkIndex
from stpt_mcp.providers.base import LiveArrival, ProviderResult
from stpt_mcp.upstream import UpstreamClient

_TIME_RE = re.compile(r"^(?P<hour>[01]?\d|2[0-3]):(?P<minute>[0-5]\d)$")
_TYPE_MODES = {
    "tv": VehicleMode.TRAM,
    "tram": VehicleMode.TRAM,
    "tramvai": VehicleMode.TRAM,
    "tb": VehicleMode.TROLLEYBUS,
    "trolley": VehicleMode.TROLLEYBUS,
    "trolleybus": VehicleMode.TROLLEYBUS,
    "troleibuz": VehicleMode.TROLLEYBUS,
    "bu": VehicleMode.BUS,
    "bus": VehicleMode.BUS,
    "autobuz": VehicleMode.BUS,
    "va": VehicleMode.VAPORETTO,
    "vaporetto": VehicleMode.VAPORETTO,
}


class LiveArrivalsProvider:
    def __init__(
        self,
        client: UpstreamClient,
        settings: Settings,
        network: NetworkIndex,
    ) -> None:
        self._client = client
        self._settings = settings
        self._network = network

    async def fetch(self, stop_id: str) -> ProviderResult[tuple[LiveArrival, ...]]:
        raw, fetched_at = await self._client.get_json(
            self._settings.live_url,
            source="live arrivals",
            params={"stopid": stop_id},
            headers={"Referer": "https://live.stpt.ro/"},
        )
        return parse_live_arrivals(raw, fetched_at=fetched_at, network=self._network)


def parse_live_arrivals(
    raw: Any,
    *,
    fetched_at: datetime,
    network: NetworkIndex,
) -> ProviderResult[tuple[LiveArrival, ...]]:
    if isinstance(raw, dict):
        raw = raw.get("arrivals", raw.get("data", raw.get("times")))
    if not isinstance(raw, list):
        raise StptError(
            ErrorCode.UPSTREAM_INVALID_RESPONSE,
            "live arrivals response is not a JSON array",
            retryable=True,
            upstream="live arrivals",
        )

    arrivals: list[LiveArrival] = []
    warnings: list[str] = []
    skipped = 0
    duplicates = 0
    seen: set[tuple[str, str, str | None, int]] = set()

    for item in raw:
        if not isinstance(item, dict):
            skipped += 1
            continue
        line = _text(item.get("line") or item.get("route"))
        if not line:
            skipped += 1
            continue
        destination = _text(item.get("headsign") or item.get("destination"))
        mode = _mode_for_item(item, line, network)
        vehicle_id = _optional_text(item.get("vehicleId") or item.get("vehicle_id"))
        entries = _arrival_entries(item)
        if not entries:
            skipped += 1
            continue
        for arrival_time, eta in entries:
            key = (line, destination, arrival_time, eta)
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            arrivals.append(
                LiveArrival(
                    line=line,
                    destination=destination,
                    mode=mode,
                    eta_minutes=max(0, eta),
                    arrival_time=arrival_time,
                    vehicle_id=vehicle_id,
                )
            )

    if skipped:
        warnings.append(f"Ignored {skipped} malformed live arrival record(s).")
    if duplicates:
        warnings.append(f"Removed {duplicates} duplicate live arrival record(s).")
    arrivals.sort(key=lambda item: (item.eta_minutes, item.line.casefold(), item.destination))
    return ProviderResult(
        value=tuple(arrivals),
        fetched_at=fetched_at or datetime.now(UTC),
        warnings=tuple(warnings),
        complete=not skipped,
    )


def _arrival_entries(item: dict[str, Any]) -> list[tuple[str | None, int]]:
    entries: list[tuple[str | None, int]] = []
    raw_times = item.get("times")
    if isinstance(raw_times, str):
        raw_times = [raw_times]
    if isinstance(raw_times, list):
        for value in raw_times:
            parsed = _parse_time_entry(value)
            if parsed is not None:
                entries.append(parsed)
    if entries:
        return entries

    raw_minutes = item.get("minutes")
    if isinstance(raw_minutes, (int, float)):
        return [(None, max(0, int(raw_minutes)))]
    if isinstance(raw_minutes, list):
        for value in raw_minutes:
            if isinstance(value, (int, float)):
                entries.append((None, max(0, int(value))))
    return entries


def _parse_time_entry(value: Any) -> tuple[str | None, int] | None:
    if isinstance(value, dict):
        raw_time = value.get("time")
        raw_minutes = value.get("minutes")
        arrival_time = _valid_time(raw_time)
        if isinstance(raw_minutes, (int, float)):
            return arrival_time, max(0, int(raw_minutes))
        if arrival_time is not None:
            return arrival_time, 0
        return None
    if not isinstance(value, str):
        return None
    parts = value.split("|", 1)
    arrival_time = _valid_time(parts[0])
    if len(parts) == 2:
        try:
            return arrival_time, max(0, int(parts[1].strip()))
        except ValueError:
            return None
    return None


def _valid_time(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    match = _TIME_RE.match(value.strip())
    if not match:
        return None
    return f"{int(match.group('hour')):02d}:{match.group('minute')}"


def _mode_for_item(item: dict[str, Any], line: str, network: NetworkIndex) -> VehicleMode:
    raw_type = _text(item.get("type") or item.get("vehicle_type")).casefold()
    if raw_type in _TYPE_MODES:
        return _TYPE_MODES[raw_type]
    route = network.get_route(line)
    return route.mode if route is not None else VehicleMode.UNKNOWN


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _optional_text(value: object) -> str | None:
    text = _text(value)
    return text or None
