"""Alert parsing, period handling, and entity normalization."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from stpt_mcp.errors import ErrorCode, StptError
from stpt_mcp.network import NetworkIndex
from stpt_mcp.providers.alerts import parse_alerts
from stpt_mcp.providers.base import AlertRecord, ProviderResult
from tests.support import load_json

FETCHED_AT = datetime(2026, 10, 21, 10, 30, tzinfo=UTC)


def parse(raw: object, network: NetworkIndex) -> ProviderResult[tuple[AlertRecord, ...]]:
    return parse_alerts(raw, fetched_at=FETCHED_AT, network=network)


def test_parses_fixture(network: NetworkIndex) -> None:
    result = parse(load_json("alerts.json"), network)
    assert result.fetched_at == FETCHED_AT
    assert result.complete is True
    assert [alert.alert_id for alert in result.value] == ["A1", "A2", "A3", "A4"]
    assert result.value[0].routes == ("7",)
    assert result.value[0].stop_ids == ("85",)
    assert result.value[0].periods
    assert result.value[3].title == "Network-wide information without a period"
    assert "A4 has no parseable active period" in result.warnings[0]


def test_non_list_payload_is_an_error(network: NetworkIndex) -> None:
    with pytest.raises(StptError) as caught:
        parse({"unexpected": {}}, network)
    assert caught.value.code is ErrorCode.UPSTREAM_INVALID_RESPONSE


def test_nested_alert_shape(network: NetworkIndex) -> None:
    result = parse(
        {
            "alerts": [
                {
                    "alert": {
                        "id": "N1",
                        "headerText": "Nested",
                        "activePeriod": [{"start": "1792578000"}],
                        "informedEntity": [{"routeId": "R_TV_2"}],
                    }
                }
            ]
        },
        network,
    )
    assert result.value[0].alert_id == "N1"
    assert result.value[0].routes == ("2",)


def test_flat_entity_lists_and_route_normalization(network: NetworkIndex) -> None:
    result = parse(
        [
            {
                "id": "F1",
                "headerText": "Flat",
                "informedEntity": [
                    [{"routeId": "R_AB_E3"}, {"stopId": "1037"}],
                    [{"routeId": "unknown_route"}],
                    {"agencyId": "STPT"},
                    {"routeId": "R_TV_7", "stopId": "85"},
                    {"routeId": "R_TV_7", "stopId": "85"},
                ],
            }
        ],
        network,
    )
    alert = result.value[0]
    assert alert.routes == ("E3", "unknown_route", "7")
    assert alert.stop_ids == ("1037", "85")


def test_translation_objects_and_missing_title(network: NetworkIndex) -> None:
    result = parse(
        [
            {
                "id": "T1",
                "headerText": {"translation": [{"text": "Translated"}]},
                "descriptionText": {"translation": [{"text": "Body"}]},
            },
            {"id": "T2", "descriptionText": "No title"},
            "not an object",
        ],
        network,
    )
    assert [alert.alert_id for alert in result.value] == ["T1"]
    assert result.value[0].title == "Translated"
    assert result.value[0].description == "Body"
    assert result.complete is False
    assert any("Ignored 2 malformed" in warning for warning in result.warnings)


def test_period_normalization_and_invalid_periods(network: NetworkIndex) -> None:
    result = parse(
        [
            {
                "id": "P1",
                "headerText": "Periods",
                "activePeriod": [
                    {"start": "1792578600", "end": "1792656000"},
                    {"start": "bad", "end": None},
                    {"start": "1792578600000"},
                    {"start": "2026-10-21T10:30:00Z"},
                    "not a dict",
                ],
            }
        ],
        network,
    )
    alert = result.value[0]
    assert len(alert.periods) == 3
    assert alert.periods[0][0] == datetime(2026, 10, 21, 10, 30, tzinfo=UTC)
    assert alert.periods[1][0] == datetime(2026, 10, 21, 10, 30, tzinfo=UTC)
    assert any("Ignored a malformed alert active period" in warning for warning in result.warnings)
    assert any(
        "Ignored an alert active period with no valid timestamps" in warning
        for warning in result.warnings
    )


def test_missing_ids_get_stable_fallback(network: NetworkIndex) -> None:
    result = parse([{"headerText": "No id"}], network)
    assert result.value[0].alert_id == "alert-1"


def test_optional_cause_and_effect_are_upper(network: NetworkIndex) -> None:
    result = parse(
        [{"id": "C1", "headerText": "Cause", "cause": " construction ", "effect": "detour"}],
        network,
    )
    assert result.value[0].cause == "CONSTRUCTION"
    assert result.value[0].effect == "DETOUR"
