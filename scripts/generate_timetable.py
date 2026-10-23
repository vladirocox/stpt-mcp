#!/usr/bin/env python3
"""Generate the bundled offline timetable from STPT's public GTFS zip.

The schedule fallback reads this file instead of scraping a website, so it keeps
working when every STPT web service is down. Maintainers run this script when
STPT publishes a new feed, together with ``generate_network_snapshot.py``. The
timetable must come from the same feed version as the network snapshot: every
trip's stop sequence is checked against the snapshot's shapes.

The official feed is published at ``SOURCE_URL``; the Mobility Database keeps a
mirror (feed mdb-2868) at ``MIRROR_URL`` for when the producer is unreachable.

Examples:
    python scripts/generate_timetable.py --gtfs-zip /tmp/smtt-timisoara-ro.zip
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import itertools
import json
import sys
import zipfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SOURCE_URL = "https://smtt.ro/transit/gtfs/maps/smtt-timisoara-ro.zip"
MIRROR_URL = "https://files.mobilitydatabase.org/mdb-2868/latest.zip"
ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src/stpt_mcp/data/timetable.json"
NETWORK = ROOT / "src/stpt_mcp/data/network.json"
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _rows(archive: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
    with archive.open(name) as handle:
        return list(csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8-sig")))


def _minutes(value: str) -> int:
    hours, minutes, seconds = (int(part) for part in value.strip().split(":"))
    if seconds:
        raise ValueError(f"GTFS time {value!r} has seconds; the timetable stores minutes")
    return hours * 60 + minutes


def build_timetable(
    raw_zip: bytes, network: dict[str, Any], generated_at: datetime
) -> dict[str, Any]:
    archive = zipfile.ZipFile(io.BytesIO(raw_zip))
    feed_info = _rows(archive, "feed_info.txt")[0]
    feed_version = feed_info["feed_version"].strip()
    if feed_version != network["feed_version"]:
        raise ValueError(
            f"GTFS feed version {feed_version} does not match network snapshot "
            f"{network['feed_version']}; regenerate both from the same feed"
        )

    services: dict[str, dict[str, Any]] = {}
    for row in _rows(archive, "calendar.txt"):
        services[row["service_id"]] = {
            "days": "".join("1" if row[day] == "1" else "0" for day in WEEKDAYS),
            "start": row["start_date"],
            "end": row["end_date"],
            "added": [],
            "removed": [],
        }
    for row in _rows(archive, "calendar_dates.txt"):
        service = services.setdefault(
            row["service_id"],
            {"days": "0000000", "start": "", "end": "", "added": [], "removed": []},
        )
        service["added" if row["exception_type"] == "1" else "removed"].append(row["date"])
    for service in services.values():
        service["added"].sort()
        service["removed"].sort()

    if any(row.get("trip_id") for row in _rows(archive, "frequencies.txt")):
        raise ValueError("frequency-based trips are not supported by the timetable format")

    trips = {row["trip_id"]: row for row in _rows(archive, "trips.txt")}
    stop_times: dict[str, list[tuple[int, str, int]]] = defaultdict(list)
    for row in _rows(archive, "stop_times.txt"):
        stop_times[row["trip_id"]].append(
            (int(row["stop_sequence"]), row["stop_id"], _minutes(row["departure_time"]))
        )

    shapes: dict[str, dict[str, list[Any]]] = {}
    for trip_id in sorted(stop_times):
        trip = trips[trip_id]
        shape_id = trip["shape_id"]
        rows = sorted(stop_times[trip_id])
        expected = [entry[0] for entry in network["shapes"].get(shape_id, [])]
        if [stop_id for _sequence, stop_id, _minute in rows] != expected:
            raise ValueError(f"trip {trip_id} does not follow network shape {shape_id}")
        if trip["service_id"] not in services:
            raise ValueError(f"trip {trip_id} references unknown service {trip['service_id']}")
        times = [minute for _sequence, _stop_id, minute in rows]
        deltas = [later - earlier for earlier, later in itertools.pairwise(times)]
        if any(delta < 0 for delta in deltas):
            raise ValueError(f"trip {trip_id} goes back in time")
        entry = shapes.setdefault(shape_id, {"patterns": [], "trips": []})
        if deltas not in entry["patterns"]:
            entry["patterns"].append(deltas)
        entry["trips"].append([trip["service_id"], times[0], entry["patterns"].index(deltas)])
    for entry in shapes.values():
        entry["trips"].sort()

    return {
        "schema_version": 1,
        "generated_at": generated_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "feed_version": feed_version,
        "service_range": {
            "start": feed_info["feed_start_date"],
            "end": feed_info["feed_end_date"],
        },
        "counts": {"services": len(services), "trips": len(stop_times), "shapes": len(shapes)},
        "source": {
            "name": "STPT public GTFS feed",
            "url": SOURCE_URL,
            "mirror": MIRROR_URL,
            "sha256": hashlib.sha256(raw_zip).hexdigest(),
        },
        "services": dict(sorted(services.items())),
        "shapes": dict(sorted(shapes.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gtfs-zip", type=Path, required=True, help="the STPT GTFS zip file")
    parser.add_argument("--network", type=Path, default=NETWORK)
    parser.add_argument("--output", type=Path, default=TARGET)
    parser.add_argument("--generated-at", help="ISO 8601 timestamp to record (defaults to now)")
    args = parser.parse_args()

    generated_at = (
        datetime.fromisoformat(args.generated_at.replace("Z", "+00:00"))
        if args.generated_at
        else datetime.now(UTC)
    )
    network = json.loads(args.network.read_text(encoding="utf-8"))
    timetable = build_timetable(args.gtfs_zip.read_bytes(), network, generated_at)
    rendered = json.dumps(timetable, ensure_ascii=False, separators=(",", ":")) + "\n"
    args.output.write_text(rendered, encoding="utf-8")
    counts = timetable["counts"]
    print(
        f"wrote {args.output} ({len(rendered):,} bytes, {counts['trips']} trips, "
        f"{counts['shapes']} shapes, {counts['services']} services)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
