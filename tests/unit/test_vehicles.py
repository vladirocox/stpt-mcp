"""Vehicle feed parsing and timestamp normalization."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from stpt_mcp.errors import ErrorCode, StptError
from stpt_mcp.network import NetworkIndex
from stpt_mcp.providers.base import ProviderResult, VehicleRecord, parse_epoch
from stpt_mcp.providers.vehicles import parse_vehicles
from tests.support import load_json

FETCHED_AT = datetime(2026, 10, 21, 10, 30, tzinfo=UTC)


def parse(raw: Any, network: NetworkIndex) -> ProviderResult[tuple[VehicleRecord, ...]]:
    return parse_vehicles(raw, fetched_at=FETCHED_AT, network=network)


def test_parses_fixture(network: NetworkIndex) -> None:
    result = parse(load_json("vehicles.json"), network)
    assert result.fetched_at == FETCHED_AT
    assert result.complete is True
    assert [vehicle.vehicle_id for vehicle in result.value] == ["3512", "GLU", "3515", "GLR"]
    assert [vehicle.line for vehicle in result.value] == ["2", "7", "8", "M30"]
    fresh = next(vehicle for vehicle in result.value if vehicle.vehicle_id == "GLU")
    assert fresh.observed_at == datetime(2026, 10, 21, 10, 29, 55, tzinfo=UTC)
    assert fresh.accessible is True
    assert fresh.speed_kmh == 0.0
    stale = next(vehicle for vehicle in result.value if vehicle.vehicle_id == "3512")
    assert stale.speed_kmh == 12.5
    missing = next(vehicle for vehicle in result.value if vehicle.vehicle_id == "GLR")
    assert missing.observed_at is None
    assert missing.accessible is False
    assert "1 vehicle record(s) had no valid observation timestamp" in result.warnings[0]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"success": False},
        {"success": True},
        {"success": True, "data": {}},
        {"success": True, "data": {"vehicles": "nope"}},
    ],
)
def test_invalid_payload_is_structured(network: NetworkIndex, payload: Any) -> None:
    with pytest.raises(StptError) as caught:
        parse(payload, network)
    assert caught.value.code is ErrorCode.UPSTREAM_INVALID_RESPONSE


def test_malformed_vehicle_records_are_skipped(network: NetworkIndex) -> None:
    result = parse(
        {
            "success": True,
            "data": {
                "vehicles": [
                    "not a dict",
                    {"id": "A", "route": "2", "lat": "bad", "lng": 1},
                    {"id": "", "route": "2", "lat": 1, "lng": 1},
                    {"id": "B", "route": "", "lat": 1, "lng": 1},
                ]
            },
        },
        network,
    )
    assert result.value == ()
    assert result.complete is False
    assert "Ignored 4 malformed" in result.warnings[0]


def test_boolean_and_float_edges(network: NetworkIndex) -> None:
    result = parse(
        {
            "success": True,
            "data": {
                "vehicles": [
                    {
                        "id": "V1",
                        "route": "2",
                        "lat": 45,
                        "lng": 21,
                        "bearing": "not-a-number",
                        "speed": "",
                        "isAccessible": "yes",
                    },
                    {
                        "id": "V2",
                        "route": "2",
                        "lat": 45,
                        "lng": 21,
                        "isAccessible": "no",
                    },
                ]
            },
        },
        network,
    )
    first, second = result.value
    assert first.bearing is None
    assert first.speed_kmh is None
    assert first.accessible is True
    assert second.accessible is False


def test_vehicle_without_optional_fields(network: NetworkIndex) -> None:
    result = parse(
        {"success": True, "data": {"vehicles": [{"id": "V", "route": "2", "lat": 1, "lng": 2}]}},
        network,
    )
    vehicle = result.value[0]
    assert vehicle.headsign is None
    assert vehicle.current_stop is None
    assert vehicle.route_id is None
    assert vehicle.observed_at is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        (0, None),
        (-1, None),
        (1792578600, datetime(2026, 10, 21, 10, 30, tzinfo=UTC)),
        ("1792578600", datetime(2026, 10, 21, 10, 30, tzinfo=UTC)),
        (1792578600000, datetime(2026, 10, 21, 10, 30, tzinfo=UTC)),
        ("2026-10-21T10:30:00Z", datetime(2026, 10, 21, 10, 30, tzinfo=UTC)),
        ("2026-10-21T10:30:00+00:00", datetime(2026, 10, 21, 10, 30, tzinfo=UTC)),
        ("bad", None),
        ({}, None),
    ],
)
def test_parse_epoch(value: object, expected: datetime | None) -> None:
    assert parse_epoch(value) == expected
