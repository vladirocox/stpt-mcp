"""Bundled network snapshot lookup and search behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stpt_mcp.errors import ErrorCode, StptError
from stpt_mcp.models import Direction, VehicleMode
from stpt_mcp.network import NetworkIndex, _load_snapshot, normalize_text


def test_snapshot_metadata(network: NetworkIndex) -> None:
    snapshot = network.snapshot
    assert snapshot.feed_version
    assert snapshot.counts["stops"] == len(snapshot.stops)
    assert snapshot.counts["routes"] == len(snapshot.routes)
    assert snapshot.counts["shapes"] == len(snapshot.shapes)
    assert snapshot.service_range["start"]
    assert snapshot.service_range["end"]
    assert snapshot.attribution


def test_stop_lookup(network: NetworkIndex) -> None:
    stop = network.require_stop(" 85 ")
    assert stop.stop_id == "85"
    assert stop.name == "Catedrala Mitropolitană"
    assert stop.routes == ("2", "4", "7")
    assert stop.shapes == ("2.1_T", "4.1_R", "7.1_T")
    assert stop.neighbors == ("59", "60", "87")


def test_unknown_stop_is_structured(network: NetworkIndex) -> None:
    with pytest.raises(StptError) as caught:
        network.require_stop("does-not-exist")
    assert caught.value.code is ErrorCode.UNKNOWN_STOP
    assert "Unknown STPT stop" in caught.value.message


def test_unknown_stop_suggests_a_nearby_name(network: NetworkIndex) -> None:
    with pytest.raises(StptError) as caught:
        network.require_stop("Catedrala Mitropolitana")
    assert "Did you mean" in caught.value.message
    assert "Catedrala Mitropolitană" in caught.value.message


def test_route_lookup_is_case_insensitive(network: NetworkIndex) -> None:
    route = network.require_route(" e3 ")
    assert route.line == "E3"
    assert route.mode is VehicleMode.BUS
    assert route.category == "express"
    with pytest.raises(StptError) as caught:
        network.require_route("ZZZ")
    assert caught.value.code is ErrorCode.UNKNOWN_ROUTE


def test_search_is_diacritic_insensitive_and_ranked(network: NetworkIndex) -> None:
    results = network.search_stops("catedrala mitropolitana", limit=5)
    assert results
    assert results[0].stop_id in {"85", "86"}
    assert all("catedrala" in normalize_text(stop.name) for stop in results)


def test_search_by_stop_id_and_route(network: NetworkIndex) -> None:
    by_id = network.search_stops("85", limit=1)
    assert by_id[0].stop_id == "85"
    by_route = network.search_stops("7", limit=50)
    assert any("7" in stop.routes for stop in by_route)


def test_search_empty_and_limit(network: NetworkIndex) -> None:
    assert network.search_stops("   ") == []
    results = network.search_stops("piata", limit=3)
    assert len(results) == 3


def test_search_returns_no_match_for_nonsense(network: NetworkIndex) -> None:
    assert network.search_stops("zzzzzzzzzzzz") == []


def test_directions_for_stop(network: NetworkIndex) -> None:
    stop = network.require_stop("85")
    directions = network.directions_for_stop(stop)
    assert [(entry.line, entry.direction) for entry in directions] == [
        ("2", Direction.OUTBOUND),
        ("4", Direction.INBOUND),
        ("7", Direction.OUTBOUND),
    ]
    assert all(entry.destination for entry in directions)
    assert directions[0].stop_sequence >= 0


def test_shape_helpers(network: NetworkIndex) -> None:
    assert network.route_for_shape("7.1_T") is not None
    assert network.route_for_shape("7.1_T").line == "7"  # type: ignore[union-attr]
    assert network.direction_for_shape("2.1_T") is Direction.OUTBOUND
    assert network.direction_for_shape("4.1_R") is Direction.INBOUND
    assert network.direction_for_shape("2.1_X") is None
    assert network.destination_for_shape("7.1_T") == "Calea Torontalului (Ciocanul)"
    assert network.destination_for_shape("missing") is None
    assert network.target_shapes_for_stop(network.require_stop("85")) == {
        "T": ["2.1_T", "7.1_T"],
        "R": ["4.1_R"],
    }


def test_stop_info_and_summary(network: NetworkIndex) -> None:
    stop = network.require_stop("85")
    info = network.stop_info(stop)
    assert info.stop_id == "85"
    assert info.is_terminal is False
    assert len(info.directions) == 3
    assert info.neighbors == ["59", "60", "87"]
    summary = network.stop_summary(stop)
    assert summary.routes == ["2", "4", "7"]


def test_list_routes_filters_and_sorts(network: NetworkIndex) -> None:
    trams = network.list_routes(mode=VehicleMode.TRAM)
    assert [route.line for route in trams] == ["1", "2", "4", "7", "8", "9"]
    all_routes = network.list_routes()
    assert len(all_routes) == 59
    assert [route.line for route in all_routes[:6]] == ["1", "2", "4", "5b", "5c", "7"]


def test_normalize_text_handles_romanian_diacritics() -> None:
    assert normalize_text("Piața Șagului") == "piata sagului"
    assert normalize_text("  CATEDRALA  ") == "catedrala"
    assert normalize_text("") == ""


def test_snapshot_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "network.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="missing or invalid"):
        _load_snapshot(path)


def test_snapshot_rejects_unknown_schema(tmp_path: Path) -> None:
    path = tmp_path / "network.json"
    path.write_text(json.dumps({"schema_version": 999}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="unsupported"):
        _load_snapshot(path)


def test_snapshot_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="missing or invalid"):
        _load_snapshot(tmp_path / "absent.json")
