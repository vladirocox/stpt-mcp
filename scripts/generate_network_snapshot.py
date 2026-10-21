#!/usr/bin/env python3
"""Generate the bundled offline network snapshot from STPT's public GTFS JSON.

The server never downloads network data at runtime. Maintainers run this script
when STPT publishes a new feed. Tests validate the checked-in snapshot but do not
call the network.

Examples:
    python scripts/generate_network_snapshot.py --from-dir /tmp/opencode
    python scripts/generate_network_snapshot.py  # download the public sources
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SOURCE_URLS = {
    "route-manifest.json": "https://live.stpt.ro/gtfs-data/route-manifest.json",
    "stops-index.json": "https://live.stpt.ro/gtfs-data/stops-index.json",
    "stop-sequences.json": "https://live.stpt.ro/gtfs-data/stop-sequences.json",
}
ATTRIBUTION = (
    "Public transit data derived from STPT Timișoara's public GTFS feed. "
    "Unofficial; see NOTICE for attribution and disclaimer."
)
TARGET = Path(__file__).resolve().parents[1] / "src/stpt_mcp/data/network.json"


def _download(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "stpt-mcp-network-snapshot/0.1 (+https://github.com/vladirocox/stpt-mcp)"
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"{url} returned HTTP {response.status}")
        return response.read()


def _load_sources(from_dir: Path | None) -> dict[str, bytes]:
    sources: dict[str, bytes] = {}
    for name, url in SOURCE_URLS.items():
        if from_dir is None:
            print(f"downloading {url}", file=sys.stderr)
            raw = _download(url)
        else:
            print(f"reading {from_dir / name}", file=sys.stderr)
            raw = (from_dir / name).read_bytes()
        json.loads(raw)  # fail early with a useful position
        sources[name] = raw
    return sources


def _compact_stops(raw: dict[str, Any]) -> dict[str, Any]:
    stops: dict[str, Any] = {}
    for stop_id, stop in raw.items():
        stops[str(stop_id)] = {
            "name": stop["stop_name"],
            "lat": round(float(stop["lat"]), 6),
            "lon": round(float(stop["lon"]), 6),
            "routes": list(stop.get("routes") or []),
            "shapes": list(stop.get("shape_ids") or []),
            "terminal": bool(stop.get("is_terminal")),
            "terminal_for": list(stop.get("terminal_for") or []),
            "neighbors": [str(value) for value in stop.get("neighbors") or []],
        }
    return stops


def _compact_shapes(raw: dict[str, list[dict[str, Any]]]) -> dict[str, list[list[Any]]]:
    shapes: dict[str, list[list[Any]]] = {}
    for shape_id, entries in raw.items():
        ordered = sorted(entries, key=lambda entry: int(entry["sequence"]))
        shapes[str(shape_id)] = [
            [str(entry["stop_id"]), str(entry["stop_name"]), int(entry["sequence"])]
            for entry in ordered
        ]
    return shapes


def build_snapshot(sources: dict[str, bytes], generated_at: datetime) -> dict[str, Any]:
    manifest = json.loads(sources["route-manifest.json"])
    stops = json.loads(sources["stops-index.json"])
    sequences = json.loads(sources["stop-sequences.json"])

    if not isinstance(manifest, dict) or not isinstance(manifest.get("routes"), dict):
        raise ValueError("route manifest does not contain routes")
    if not isinstance(stops, dict) or not stops:
        raise ValueError("stops index is empty")
    if not isinstance(sequences, dict) or not sequences:
        raise ValueError("stop sequences are empty")

    routes = {}
    for route_id, route in manifest["routes"].items():
        routes[str(route_id)] = {
            "name": route["route_short_name"],
            "long_name": route["route_long_name"],
            "type": str(route.get("route_type", "")),
            "type_name": route.get("route_type_name", ""),
            "category": route.get("category", "other"),
            "color": route.get("route_color", ""),
            "text_color": route.get("route_text_color", ""),
        }

    snapshot = {
        "schema_version": 1,
        "generated_at": generated_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "feed_version": str(manifest.get("gtfs_feed_version", "")),
        "service_range": manifest.get("service_date_range", {}),
        "counts": {
            "stops": len(stops),
            "routes": len(routes),
            "shapes": len(sequences),
            "trips": int(manifest.get("trip_count", 0)),
        },
        "source": {
            "name": "STPT public GTFS-derived JSON",
            "urls": SOURCE_URLS,
            "gtfs_source_file": manifest.get("gtfs_source_file", ""),
            "attribution": ATTRIBUTION,
        },
        "source_sha256": {
            name: hashlib.sha256(raw).hexdigest() for name, raw in sorted(sources.items())
        },
        "routes": routes,
        "stops": _compact_stops(stops),
        "shapes": _compact_shapes(sequences),
    }
    _validate_snapshot(snapshot)
    return snapshot


def _validate_snapshot(snapshot: dict[str, Any]) -> None:
    stops = snapshot["stops"]
    routes = snapshot["routes"]
    shapes = snapshot["shapes"]
    if len(stops) < 100:
        raise ValueError("implausibly few stops")
    missing_routes = {
        route for stop in stops.values() for route in stop["routes"] if route not in routes
    }
    if missing_routes:
        raise ValueError(f"stops reference unknown routes: {sorted(missing_routes)!r}")
    missing_shapes = {
        shape for stop in stops.values() for shape in stop["shapes"] if shape not in shapes
    }
    if missing_shapes:
        raise ValueError(f"stops reference unknown shapes: {sorted(missing_shapes)!r}")
    referenced_stops = {
        stop_id for entries in shapes.values() for stop_id, _name, _sequence in entries
    }
    missing_stops = referenced_stops - stops.keys()
    if missing_stops:
        raise ValueError(f"shapes reference unknown stops: {sorted(missing_stops)!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-dir", type=Path, help="directory with the three source JSON files")
    parser.add_argument("--output", type=Path, default=TARGET)
    parser.add_argument(
        "--generated-at",
        help="ISO 8601 timestamp to record (defaults to now)",
    )
    args = parser.parse_args()

    generated_at = (
        datetime.fromisoformat(args.generated_at.replace("Z", "+00:00"))
        if args.generated_at
        else datetime.now(UTC)
    )
    sources = _load_sources(args.from_dir)
    snapshot = build_snapshot(sources, generated_at)
    rendered = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(
        f"wrote {args.output} ({len(rendered):,} bytes, "
        f"{snapshot['counts']['stops']} stops, {snapshot['counts']['routes']} routes, "
        f"{snapshot['counts']['shapes']} shapes)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
