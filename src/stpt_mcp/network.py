"""Offline stop, route, and shape lookup from the bundled GTFS snapshot."""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from difflib import get_close_matches
from importlib.resources import files
from typing import Any

from stpt_mcp.errors import ErrorCode, StptError
from stpt_mcp.models import (
    Direction,
    RouteInfo,
    StopDirection,
    StopInfo,
    StopSummary,
    VehicleMode,
)

_CATEGORY_MODES = {
    "tram": VehicleMode.TRAM,
    "trolleybus": VehicleMode.TROLLEYBUS,
    "vaporetto": VehicleMode.VAPORETTO,
    "urban": VehicleMode.BUS,
    "express": VehicleMode.BUS,
    "metropolitan": VehicleMode.BUS,
    "scolar": VehicleMode.BUS,
    "other": VehicleMode.UNKNOWN,
}


@dataclass(frozen=True, slots=True)
class StopRecord:
    stop_id: str
    name: str
    latitude: float
    longitude: float
    routes: tuple[str, ...]
    shapes: tuple[str, ...]
    is_terminal: bool
    terminal_for: tuple[str, ...]
    neighbors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RouteRecord:
    line: str
    name: str
    long_name: str
    category: str
    mode: VehicleMode
    type_name: str
    color: str | None
    text_color: str | None


@dataclass(frozen=True, slots=True)
class ShapeStop:
    stop_id: str
    name: str
    sequence: int


@dataclass(frozen=True, slots=True)
class NetworkSnapshot:
    generated_at: datetime
    feed_version: str
    service_range: dict[str, str]
    attribution: str
    counts: dict[str, int]
    stops: dict[str, StopRecord]
    routes: dict[str, RouteRecord]
    shapes: dict[str, tuple[ShapeStop, ...]]


def normalize_text(value: str) -> str:
    """Case- and diacritic-insensitive search key for Romanian names."""
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char)).strip()


def route_mode(category: str) -> VehicleMode:
    return _CATEGORY_MODES.get(category, VehicleMode.UNKNOWN)


def _load_snapshot(path: Any | None = None) -> NetworkSnapshot:
    raw_path = path or (files("stpt_mcp.data") / "network.json")
    try:
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("bundled network snapshot is missing or invalid") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise RuntimeError("unsupported network snapshot schema")

    stops: dict[str, StopRecord] = {}
    for stop_id, value in raw["stops"].items():
        stops[str(stop_id)] = StopRecord(
            stop_id=str(stop_id),
            name=str(value["name"]),
            latitude=float(value["lat"]),
            longitude=float(value["lon"]),
            routes=tuple(str(route) for route in value.get("routes", [])),
            shapes=tuple(str(shape) for shape in value.get("shapes", [])),
            is_terminal=bool(value.get("terminal", False)),
            terminal_for=tuple(str(shape) for shape in value.get("terminal_for", [])),
            neighbors=tuple(str(neighbor) for neighbor in value.get("neighbors", [])),
        )

    routes: dict[str, RouteRecord] = {}
    for line, value in raw["routes"].items():
        category = str(value.get("category", "other"))
        routes[str(line)] = RouteRecord(
            line=str(line),
            name=str(value.get("name") or line),
            long_name=str(value.get("long_name") or ""),
            category=category,
            mode=route_mode(category),
            type_name=str(value.get("type_name") or ""),
            color=str(value["color"]) if value.get("color") else None,
            text_color=str(value["text_color"]) if value.get("text_color") else None,
        )

    shapes: dict[str, tuple[ShapeStop, ...]] = {}
    for shape_id, entries in raw["shapes"].items():
        shapes[str(shape_id)] = tuple(
            ShapeStop(stop_id=str(entry[0]), name=str(entry[1]), sequence=int(entry[2]))
            for entry in entries
        )

    generated_at = datetime.fromisoformat(str(raw["generated_at"]).replace("Z", "+00:00"))
    return NetworkSnapshot(
        generated_at=generated_at,
        feed_version=str(raw.get("feed_version", "unknown")),
        service_range={str(k): str(v) for k, v in raw.get("service_range", {}).items()},
        attribution=str(raw.get("source", {}).get("attribution", "")),
        counts={str(k): int(v) for k, v in raw.get("counts", {}).items()},
        stops=stops,
        routes=routes,
        shapes=shapes,
    )


class NetworkIndex:
    """Immutable lookup index over the bundled snapshot."""

    def __init__(self, snapshot: NetworkSnapshot | None = None) -> None:
        self.snapshot = snapshot or _load_snapshot()
        self._normalized_names = {
            stop_id: normalize_text(stop.name) for stop_id, stop in self.snapshot.stops.items()
        }

    def get_stop(self, stop_id: str) -> StopRecord | None:
        return self.snapshot.stops.get(stop_id.strip())

    def require_stop(self, stop_id: str) -> StopRecord:
        stop = self.get_stop(stop_id)
        if stop is not None:
            return stop
        candidates = [
            f"{record.stop_id} ({record.name})" for record in self.snapshot.stops.values()
        ]
        close = get_close_matches(stop_id.strip(), candidates, n=3, cutoff=0.5)
        suffix = f" Did you mean: {', '.join(close)}?" if close else ""
        raise StptError(
            ErrorCode.UNKNOWN_STOP,
            f"Unknown STPT stop {stop_id!r}.{suffix}",
            upstream="network",
        )

    def get_route(self, line: str) -> RouteRecord | None:
        wanted = line.strip().casefold()
        for route in self.snapshot.routes.values():
            if route.line.casefold() == wanted:
                return route
        return None

    def require_route(self, line: str) -> RouteRecord:
        route = self.get_route(line)
        if route is None:
            raise StptError(
                ErrorCode.UNKNOWN_ROUTE,
                f"Unknown STPT route {line!r}.",
                upstream="network",
            )
        return route

    def search_stops(self, query: str, limit: int = 10) -> list[StopSummary]:
        needle = normalize_text(query)
        if not needle:
            return []
        scored: list[tuple[float, str, StopRecord]] = []
        for stop_id, stop in self.snapshot.stops.items():
            name = self._normalized_names[stop_id]
            code = stop_id.casefold()
            score: float | None = None
            if code == needle or name == needle:
                score = 0.0
            elif code.startswith(needle):
                score = 1.0
            elif name.startswith(needle):
                score = 2.0
            elif any(token.startswith(needle) for token in name.split()):
                score = 3.0
            elif needle in name or needle in code:
                score = 4.0
            elif any(normalize_text(route) == needle for route in stop.routes):
                score = 5.0
            if score is not None:
                scored.append((score, name, stop))

        scored.sort(
            key=lambda item: (
                item[0],
                item[1],
                int(item[2].stop_id) if item[2].stop_id.isdigit() else 10**9,
                item[2].stop_id,
            )
        )
        return [self.stop_summary(record) for _score, _name, record in scored[:limit]]

    def list_routes(self, *, mode: VehicleMode | None = None) -> list[RouteInfo]:
        routes = [
            route for route in self.snapshot.routes.values() if mode is None or route.mode == mode
        ]
        routes.sort(key=lambda route: _natural_route_key(route.line))
        return [self.route_info(route) for route in routes]

    def directions_for_stop(self, stop: StopRecord) -> list[StopDirection]:
        directions: list[StopDirection] = []
        seen: set[tuple[str, str]] = set()
        for shape_id in stop.shapes:
            shape = self.snapshot.shapes.get(shape_id)
            if not shape:
                continue
            route = self.route_for_shape(shape_id)
            direction = self.direction_for_shape(shape_id)
            if route is None or direction is None or not shape:
                continue
            key = (route.line, shape_id)
            if key in seen:
                continue
            seen.add(key)
            position = next(
                (entry.sequence for entry in shape if entry.stop_id == stop.stop_id),
                -1,
            )
            directions.append(
                StopDirection(
                    line=route.line,
                    direction=direction,
                    shape_id=shape_id,
                    destination=shape[-1].name,
                    stop_sequence=position,
                )
            )
        directions.sort(key=lambda entry: (_natural_route_key(entry.line), entry.direction.value))
        return directions

    def route_for_shape(self, shape_id: str) -> RouteRecord | None:
        line = shape_id.split(".", 1)[0]
        return self.snapshot.routes.get(line)

    def direction_for_shape(self, shape_id: str) -> Direction | None:
        suffix = shape_id.rsplit("_", 1)[-1]
        if suffix == "T":
            return Direction.OUTBOUND
        if suffix == "R":
            return Direction.INBOUND
        return None

    def destination_for_shape(self, shape_id: str) -> str | None:
        shape = self.snapshot.shapes.get(shape_id)
        return shape[-1].name if shape else None

    def target_shapes_for_stop(self, stop: StopRecord) -> dict[str, list[str]]:
        """Map schedule page suffix (`T`/`R`) to shape IDs serving this stop."""
        target: dict[str, list[str]] = {"T": [], "R": []}
        for shape_id in stop.shapes:
            direction = shape_id.rsplit("_", 1)[-1]
            if direction in target:
                target[direction].append(shape_id)
        return target

    def stop_summary(self, stop: StopRecord) -> StopSummary:
        return StopSummary(
            stop_id=stop.stop_id,
            name=stop.name,
            latitude=stop.latitude,
            longitude=stop.longitude,
            routes=list(stop.routes),
            is_terminal=stop.is_terminal,
        )

    def stop_info(self, stop: StopRecord) -> StopInfo:
        return StopInfo(
            **self.stop_summary(stop).model_dump(),
            directions=self.directions_for_stop(stop),
            neighbors=list(stop.neighbors),
        )

    def route_info(self, route: RouteRecord) -> RouteInfo:
        return RouteInfo(
            line=route.line,
            name=route.name,
            long_name=route.long_name,
            category=route.category,
            mode=route.mode,
            type_name=route.type_name,
            color=route.color,
            text_color=route.text_color,
        )


def _natural_route_key(line: str) -> tuple[int, str, int, str]:
    digits = ""
    for char in line:
        if char.isdigit():
            digits += char
        else:
            break
    number = int(digits) if digits else 10**6
    return (number, line.casefold(), len(line), line)
