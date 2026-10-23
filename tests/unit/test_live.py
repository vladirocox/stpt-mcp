"""Live arrivals parser behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from stpt_mcp.errors import ErrorCode, StptError
from stpt_mcp.models import VehicleMode
from stpt_mcp.network import NetworkIndex
from stpt_mcp.providers.base import LiveArrival, ProviderResult
from stpt_mcp.providers.live import parse_live_arrivals
from tests.support import load_json

FETCHED_AT = datetime(2026, 10, 21, 10, 30, tzinfo=UTC)  # 13:30 in Bucharest


def parse(raw: Any, network: NetworkIndex) -> ProviderResult[tuple[LiveArrival, ...]]:
    return parse_live_arrivals(raw, fetched_at=FETCHED_AT, network=network)


def test_parses_fixture(network: NetworkIndex) -> None:
    result = parse(load_json("live_85.json"), network)
    assert result.fetched_at == FETCHED_AT
    assert result.complete is True
    assert result.stale is False
    assert result.observed_at == FETCHED_AT
    assert result.warnings == ("Removed 1 duplicate live arrival record(s).",)
    assert [(arrival.line, arrival.eta_minutes) for arrival in result.value] == [
        ("7", 3),
        ("2", 5),
        ("4", 8),
        ("7", 10),
    ]
    assert all(arrival.mode is VehicleMode.TRAM for arrival in result.value)
    assert result.value[0].feed_time == "13:33"
    assert result.value[0].vehicle_id == "3512"


def test_empty_list_is_valid(network: NetworkIndex) -> None:
    result = parse([], network)
    assert result.value == ()
    assert result.complete is True
    assert result.warnings == ()


@pytest.mark.parametrize("wrapper", ["arrivals", "data", "times"])
def test_supported_dict_wrappers(network: NetworkIndex, wrapper: str) -> None:
    payload = {
        wrapper: [
            {
                "route": "2",
                "type": "tv",
                "destination": "Calea Torontalului",
                "times": ["14:05|5"],
            }
        ]
    }
    result = parse(payload, network)
    assert result.value[0].line == "2"


def test_non_list_payload_is_an_error(network: NetworkIndex) -> None:
    with pytest.raises(StptError) as caught:
        parse({"unexpected": "shape"}, network)
    assert caught.value.code is ErrorCode.UPSTREAM_INVALID_RESPONSE


def test_minutes_fallback_and_alternate_fields(network: NetworkIndex) -> None:
    result = parse(
        [
            {
                "line": "M32",
                "headsign": "Chișoda",
                "minutes": 7,
                "vehicle_id": "V1",
            },
            {"route": "E3", "destination": "Comtim", "minutes": [2, 12]},
        ],
        network,
    )
    assert [(arrival.line, arrival.eta_minutes) for arrival in result.value] == [
        ("E3", 2),
        ("M32", 7),
        ("E3", 12),
    ]
    assert result.value[1].vehicle_id == "V1"
    assert result.value[0].mode is VehicleMode.BUS


def test_time_entry_objects(network: NetworkIndex) -> None:
    result = parse(
        [
            {
                "route": "5c",
                "type": "bu",
                "destination": "Ronaț",
                "times": [
                    {"time": "14:10", "minutes": 10},
                    {"time": "14:20"},
                    {"minutes": 25},
                ],
            }
        ],
        network,
    )
    assert [(arrival.feed_time, arrival.eta_minutes) for arrival in result.value] == [
        ("14:20", 0),
        ("14:10", 10),
        (None, 25),
    ]
    assert result.value[0].mode is VehicleMode.BUS


def test_malformed_records_are_skipped_with_warning(network: NetworkIndex) -> None:
    result = parse(
        [
            "not an object",
            {"route": "", "times": ["14:05|5"]},
            {"route": "2", "times": []},
            {"route": "2", "times": ["bad"], "destination": "X"},
            {"route": "2", "times": [{"time": "bad"}], "destination": "X"},
            {"route": "2", "times": ["14:05|not-int"], "destination": "X"},
        ],
        network,
    )
    assert result.value == ()
    assert result.complete is False
    assert "Ignored 6 malformed" in result.warnings[0]


def test_negative_eta_is_clamped_and_times_validated(network: NetworkIndex) -> None:
    result = parse(
        [
            {"route": "2", "times": ["25:99|5"], "destination": "A"},
            {"route": "2", "times": ["07:05|-4"], "destination": "B"},
            {"route": "2", "times": ["07:05|bad"], "destination": "C"},
        ],
        network,
    )
    assert [(arrival.feed_time, arrival.eta_minutes) for arrival in result.value] == [
        ("07:05", 0),
        (None, 5),
    ]


def test_mode_can_come_from_network(network: NetworkIndex) -> None:
    result = parse(
        [{"route": "7", "destination": "Ciocanul", "times": ["14:05|5"]}],
        network,
    )
    assert result.value[0].mode is VehicleMode.TRAM


def test_unknown_route_mode_is_unknown(network: NetworkIndex) -> None:
    result = parse(
        [{"route": "ZZZ", "type": "hovercraft", "times": ["14:05|5"]}],
        network,
    )
    assert result.value[0].mode is VehicleMode.UNKNOWN


def test_frozen_feed_is_not_labelled_live(network: NetworkIndex) -> None:
    fetched_at = datetime(2026, 10, 23, 9, 2, tzinfo=UTC)  # 12:02 local
    result = parse_live_arrivals(
        [
            {
                "route": "14",
                "type": "tv",
                "destination": "Gheorghe Barițiu",
                "times": ["16:10|10", "16:26|26"],
            }
        ],
        fetched_at=fetched_at,
        network=network,
    )
    assert result.stale is True
    assert result.observed_at is None
    assert [arrival.eta_minutes for arrival in result.value] == [10, 26]
    assert any("look stale" in warning for warning in result.warnings)
    assert any("16:00" in warning and "12:02" in warning for warning in result.warnings)


def test_feed_without_a_clock_reference_is_unverified(network: NetworkIndex) -> None:
    result = parse(
        [{"route": "M32", "headsign": "Chișoda", "minutes": 7}],
        network,
    )
    assert result.stale is False
    assert result.observed_at is None
    assert any("freshness cannot be verified" in warning for warning in result.warnings)


@pytest.mark.parametrize(
    ("fetched_at", "feed_time"),
    [
        # Summer time: Europe/Bucharest is UTC+3.
        (datetime(2026, 7, 1, 9, 0, tzinfo=UTC), "12:10|10"),
        # Winter time: Europe/Bucharest is UTC+2.
        (datetime(2026, 12, 1, 10, 0, tzinfo=UTC), "12:10|10"),
        # Spring-forward day: 03:00 jumps to 04:00 local.
        (datetime(2026, 3, 29, 1, 0, tzinfo=UTC), "04:10|10"),
        # Fall-back day, second pass through 03:xx (EET, fold=1).
        (datetime(2026, 10, 25, 1, 30, tzinfo=UTC), "03:45|15"),
        # Fall-back day, 23:50 EET, predictions after midnight.
        (datetime(2026, 10, 25, 21, 50, tzinfo=UTC), "00:05|15"),
        # Fall-back day, 00:30 EEST, after-midnight GTFS hour.
        (datetime(2026, 10, 24, 21, 30, tzinfo=UTC), "24:40|10"),
        # Spring-forward day, 23:50 EEST, predictions after midnight.
        (datetime(2026, 3, 29, 20, 50, tzinfo=UTC), "00:05|15"),
    ],
)
def test_daylight_saving_offsets_are_respected(
    network: NetworkIndex, fetched_at: datetime, feed_time: str
) -> None:
    result = parse_live_arrivals(
        [{"route": "2", "times": [feed_time], "destination": "X"}],
        fetched_at=fetched_at,
        network=network,
    )
    assert result.stale is False
    assert result.observed_at == fetched_at


def test_after_midnight_hour_anchors_to_the_next_day(network: NetworkIndex) -> None:
    fetched_at = datetime(2026, 7, 1, 20, 55, tzinfo=UTC)  # 23:55 local
    result = parse_live_arrivals(
        [{"route": "2", "times": ["24:05|10"], "destination": "X"}],
        fetched_at=fetched_at,
        network=network,
    )
    assert result.stale is False
    arrival = result.value[0]
    assert arrival.feed_time == "24:05"
    assert arrival.arrival_at == datetime(2026, 7, 1, 21, 5, tzinfo=UTC)  # 00:05 next day


def test_midnight_crossing_uses_the_nearest_occurrence(network: NetworkIndex) -> None:
    fetched_at = datetime(2026, 7, 1, 20, 50, tzinfo=UTC)  # 23:50 local
    result = parse_live_arrivals(
        [{"route": "2", "times": ["00:05|15"], "destination": "X"}],
        fetched_at=fetched_at,
        network=network,
    )
    assert result.stale is False
    assert result.observed_at == fetched_at
    assert result.value[0].arrival_at == datetime(2026, 7, 1, 21, 5, tzinfo=UTC)


def test_eta_is_anchored_to_the_fetch_instant(network: NetworkIndex) -> None:
    result = parse(load_json("live_85.json"), network)
    for arrival in result.value:
        assert arrival.arrival_at == FETCHED_AT + timedelta(minutes=arrival.eta_minutes)
