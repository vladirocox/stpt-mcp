"""Live arrivals parser behavior."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from stpt_mcp.errors import ErrorCode, StptError
from stpt_mcp.models import VehicleMode
from stpt_mcp.network import NetworkIndex
from stpt_mcp.providers.base import LiveArrival, ProviderResult
from stpt_mcp.providers.live import parse_live_arrivals
from tests.support import load_json

FETCHED_AT = datetime(2026, 10, 21, 10, 30, tzinfo=UTC)


def parse(raw: Any, network: NetworkIndex) -> ProviderResult[tuple[LiveArrival, ...]]:
    return parse_live_arrivals(raw, fetched_at=FETCHED_AT, network=network)


def test_parses_fixture(network: NetworkIndex) -> None:
    result = parse(load_json("live_85.json"), network)
    assert result.fetched_at == FETCHED_AT
    assert result.complete is True
    assert result.warnings == ("Removed 1 duplicate live arrival record(s).",)
    assert [(arrival.line, arrival.eta_minutes) for arrival in result.value] == [
        ("7", 3),
        ("2", 5),
        ("4", 8),
        ("7", 10),
    ]
    assert all(arrival.mode is VehicleMode.TRAM for arrival in result.value)
    assert result.value[0].arrival_time == "14:03"
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
    assert [(arrival.arrival_time, arrival.eta_minutes) for arrival in result.value] == [
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
    assert [(arrival.arrival_time, arrival.eta_minutes) for arrival in result.value] == [
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
