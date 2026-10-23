"""The bundled GTFS timetable, its generator, and the schedule provider."""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import zipfile
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from stpt_mcp.errors import ErrorCode, StptError
from stpt_mcp.models import Direction, VehicleMode
from stpt_mcp.network import NetworkIndex
from stpt_mcp.providers.schedule import (
    ScheduleProvider,
    ServiceCalendar,
    Timetable,
    TimetableTrip,
)
from tests.support import make_test_settings

BUCHAREST = ZoneInfo("Europe/Bucharest")
ROOT = Path(__file__).resolve().parents[2]


def load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "generate_timetable", ROOT / "scripts/generate_timetable.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def provider(network: NetworkIndex, timetable: Timetable) -> ScheduleProvider:
    return ScheduleProvider(make_test_settings(), network, timetable)


def local_times(departures: Any, line: str) -> list[str]:
    return [
        departure.departure_at.astimezone(BUCHAREST).strftime("%H:%M")
        for departure in departures
        if departure.line == line
    ]


def test_bundled_timetable_matches_the_network_snapshot(
    network: NetworkIndex, timetable: Timetable
) -> None:
    assert timetable.feed_version == network.snapshot.feed_version
    assert timetable.start.strftime("%Y%m%d") == network.snapshot.service_range["start"]
    assert timetable.end.strftime("%Y%m%d") == network.snapshot.service_range["end"]
    assert sum(len(trips) for trips in timetable.trips.values()) == network.snapshot.counts["trips"]
    for shape_id, trips in timetable.trips.items():
        shape = network.snapshot.shapes[shape_id]
        for trip in trips:
            assert trip.service_id in timetable.services
            assert len(trip.offsets) == len(shape)
            assert list(trip.offsets) == sorted(trip.offsets)


def test_service_calendar_applies_weekdays_and_exceptions() -> None:
    weekdays = ServiceCalendar(
        days="1111100",
        start="20261019",
        end="20261231",
        added=frozenset({"20261024"}),
        removed=frozenset({"20261026"}),
    )
    assert weekdays.runs_on(date(2026, 10, 23))  # Friday
    assert weekdays.runs_on(date(2026, 10, 24))  # Saturday, added
    assert not weekdays.runs_on(date(2026, 10, 25))  # Sunday
    assert not weekdays.runs_on(date(2026, 10, 26))  # Monday, removed
    assert not weekdays.runs_on(date(2027, 1, 4))  # after the range
    only_added = ServiceCalendar("0000000", "", "", frozenset({"20261026"}), frozenset())
    assert only_added.runs_on(date(2026, 10, 26))
    assert not only_added.runs_on(date(2026, 10, 27))


async def test_stop_135_line_14_matches_the_published_timetable(
    network: NetworkIndex, timetable: Timetable
) -> None:
    # Reference check: around 12:55 on 2026-10-23 the official GTFS lists
    # line 14 at Piața Mărăști towards I. Ionescu de la Brad at 12:59.
    result = await provider(network, timetable).fetch_stop_schedule(
        network.require_stop("135"), date(2026, 10, 23)
    )
    times = local_times(result.value, "14")
    index = times.index("12:59")
    assert times[index - 2 : index + 3] == ["12:29", "12:44", "12:59", "13:14", "13:29"]
    line_14 = [departure for departure in result.value if departure.line == "14"]
    assert {departure.destination for departure in line_14} == {"Ion Ionescu de la Brad"}
    assert {departure.direction for departure in line_14} == {Direction.OUTBOUND}
    assert {departure.shape_id for departure in line_14} == {"14.1_T"}
    assert {departure.mode for departure in line_14} == {VehicleMode.TROLLEYBUS}
    assert result.fetched_at == timetable.generated_at
    assert result.complete is True
    assert result.stale is False


async def test_a_trip_final_stop_is_not_a_departure(
    network: NetworkIndex, timetable: Timetable
) -> None:
    terminal = network.snapshot.shapes["14.1_T"][-1].stop_id
    result = await provider(network, timetable).fetch_stop_schedule(
        network.require_stop(terminal), date(2026, 10, 23)
    )
    assert all(departure.shape_id != "14.1_T" for departure in result.value)


async def test_departures_span_adjacent_service_days(network: NetworkIndex) -> None:
    every_day = ServiceCalendar("1111111", "20261019", "20261231", frozenset(), frozenset())
    stop = network.require_stop("135")
    shape = network.snapshot.shapes["14.1_T"]
    index = next(i for i, entry in enumerate(shape) if entry.stop_id == "135")
    offsets = tuple(0 if i <= index else 1 for i in range(len(shape)))
    small = Timetable(
        generated_at=datetime(2026, 10, 21, 10, 15, tzinfo=UTC),
        feed_version="test",
        start=date(2026, 10, 19),
        end=date(2026, 12, 31),
        services={"daily": every_day},
        # 24:20 is twenty past midnight after the service day.
        trips={"14.1_T": (TimetableTrip("daily", 24 * 60 + 20, offsets),)},
    )
    result = await provider(network, small).fetch_stop_schedule(stop, date(2026, 10, 23))
    assert [departure.departure_at for departure in result.value] == [
        datetime(2026, 10, 23, 0, 20, tzinfo=BUCHAREST),
        datetime(2026, 10, 24, 0, 20, tzinfo=BUCHAREST),
        datetime(2026, 10, 25, 0, 20, tzinfo=BUCHAREST),
    ]


async def test_the_day_after_the_timetable_keeps_after_midnight_trips(
    network: NetworkIndex, timetable: Timetable
) -> None:
    result = await provider(network, timetable).fetch_stop_schedule(
        network.require_stop("109"), date(2027, 1, 1)
    )
    assert result.complete is False
    assert any("ended on 2026-12-31" in warning for warning in result.warnings)
    # GTFS: trip 5c.1_WDVC_T_01_14 of 2026-12-31 leaves stop 109 at 24:02.
    after_midnight = [
        departure
        for departure in result.value
        if departure.departure_at >= datetime(2027, 1, 1, tzinfo=BUCHAREST)
    ]
    assert [(d.line, d.departure_at.strftime("%H:%M")) for d in after_midnight] == [("5c", "00:02")]


async def test_the_last_timetable_day_warns_that_later_days_are_missing(
    network: NetworkIndex, timetable: Timetable
) -> None:
    result = await provider(network, timetable).fetch_stop_schedule(
        network.require_stop("135"), date(2026, 12, 31)
    )
    assert result.complete is True
    assert result.value
    assert any("ends on 2026-12-31" in warning for warning in result.warnings)


async def test_dates_outside_the_timetable_are_unavailable(
    network: NetworkIndex, timetable: Timetable
) -> None:
    with pytest.raises(StptError) as excinfo:
        await provider(network, timetable).fetch_stop_schedule(
            network.require_stop("135"), date(2027, 1, 2)
        )
    assert excinfo.value.code is ErrorCode.SCHEDULE_UNAVAILABLE
    assert excinfo.value.retryable is False
    assert "2026-12-31" in excinfo.value.message


async def test_stop_without_shapes_is_unavailable(
    network: NetworkIndex, timetable: Timetable
) -> None:
    stop = replace(network.require_stop("135"), shapes=())
    with pytest.raises(StptError) as excinfo:
        await provider(network, timetable).fetch_stop_schedule(stop, date(2026, 10, 23))
    assert excinfo.value.code is ErrorCode.SCHEDULE_UNAVAILABLE


def gtfs_zip(
    *, feed_version: str = "4.28", stops: tuple[str, ...] = (), seconds: str = "00"
) -> bytes:
    def table(rows: list[dict[str, str]]) -> str:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        return buffer.getvalue()

    stop_times = [
        {
            "trip_id": "t1",
            "stop_id": stop_id,
            "stop_sequence": str(sequence),
            "arrival_time": f"08:{sequence:02d}:{seconds}",
            "departure_time": f"08:{sequence:02d}:{seconds}",
        }
        for sequence, stop_id in enumerate(stops)
    ]
    files = {
        "feed_info.txt": table(
            [
                {
                    "feed_version": feed_version,
                    "feed_start_date": "20261019",
                    "feed_end_date": "20261231",
                }
            ]
        ),
        "calendar.txt": table(
            [
                {
                    "service_id": "s1",
                    **dict.fromkeys(("monday", "tuesday", "wednesday", "thursday", "friday"), "1"),
                    "saturday": "0",
                    "sunday": "0",
                    "start_date": "20261019",
                    "end_date": "20261231",
                }
            ]
        ),
        "calendar_dates.txt": table(
            [{"service_id": "s2", "date": "20261024", "exception_type": "1"}]
        ),
        "frequencies.txt": "trip_id,start_time,end_time,headway_secs\n",
        "trips.txt": table([{"trip_id": "t1", "service_id": "s1", "shape_id": "1.1_T"}]),
        "stop_times.txt": table(stop_times),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return buffer.getvalue()


SMALL_NETWORK: dict[str, Any] = {
    "feed_version": "4.28",
    "shapes": {"1.1_T": [["10", "A", 0], ["11", "B", 1], ["12", "C", 2]]},
}
GENERATED_AT = datetime(2026, 10, 23, 10, 30, tzinfo=UTC)


def test_generator_encodes_trips_as_shared_patterns() -> None:
    generator = load_generator()
    built = generator.build_timetable(
        gtfs_zip(stops=("10", "11", "12")), SMALL_NETWORK, GENERATED_AT
    )
    assert built["shapes"] == {"1.1_T": {"patterns": [[1, 1]], "trips": [["s1", 480, 0]]}}
    assert built["services"]["s1"]["days"] == "1111100"
    assert built["services"]["s2"] == {
        "days": "0000000",
        "start": "",
        "end": "",
        "added": ["20261024"],
        "removed": [],
    }
    assert built["generated_at"] == "2026-10-23T10:30:00Z"
    json.dumps(built)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"stops": ("10", "12", "11")}, "does not follow network shape"),
        ({"stops": ("10", "11", "12"), "feed_version": "4.29"}, "does not match"),
        ({"stops": ("10", "11", "12"), "seconds": "30"}, "has seconds"),
    ],
)
def test_generator_rejects_inconsistent_feeds(kwargs: dict[str, Any], message: str) -> None:
    generator = load_generator()
    with pytest.raises(ValueError, match=message):
        generator.build_timetable(gtfs_zip(**kwargs), SMALL_NETWORK, GENERATED_AT)
