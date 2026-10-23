"""Schedule page parsing and the date/shape-aware schedule provider."""

from __future__ import annotations

from datetime import date
from urllib.parse import urlsplit

import pytest

from stpt_mcp.errors import ErrorCode, StptError
from stpt_mcp.models import Direction, VehicleMode
from stpt_mcp.network import NetworkIndex, StopRecord
from stpt_mcp.providers.schedule import ScheduleProvider, parse_schedule_page
from tests.support import (
    SCHEDULE_BASE_URL,
    FakeSTPT,
    MutableClock,
    load_text,
    make_test_settings,
    make_upstream,
    seed_fake_stpt,
)

SERVICE_DATE = date(2026, 10, 21)


def test_parse_filters_by_stop_and_shape() -> None:
    times, warnings = parse_schedule_page(
        load_text("schedule/mixed.html"),
        stop_id="85",
        target_shapes=frozenset({"2.1_T", "4.1_R"}),
    )
    assert [(entry.hour, entry.minute, entry.shape_id) for entry in times] == [
        (12, 5, "2.1_T"),
        (12, 20, "4.1_R"),
        (13, 0, "2.1_T"),
    ]
    assert warnings == ("Ignored 2 malformed schedule time token(s).",)


def test_parse_missing_timetable_is_an_error() -> None:
    with pytest.raises(StptError) as caught:
        parse_schedule_page("<html><body>nope</body></html>", stop_id="85", target_shapes={"2.1_T"})
    assert caught.value.code is ErrorCode.UPSTREAM_INVALID_RESPONSE


def test_parse_missing_stop_is_reported() -> None:
    times, warnings = parse_schedule_page(
        load_text("schedule/line-2.html"),
        stop_id="999",
        target_shapes={"2.1_T"},
    )
    assert times == ()
    assert warnings == ("Stop 999 was not present on the schedule page.",)


def test_parse_rows_without_times_are_ignored() -> None:
    html = (
        '<html data-times="1|2"><table>'
        '<tr data-code="85"></tr>'
        '<tr data-code="85" data-times="12|05-2.1_T"></tr>'
        "</table></html>"
    )
    times, warnings = parse_schedule_page(html, stop_id="85", target_shapes={"2.1_T"})
    assert [(entry.hour, entry.minute) for entry in times] == [(12, 5)]
    assert warnings == ()


def test_parse_rejects_invalid_minutes_and_foreign_shapes() -> None:
    html = (
        "<html><table>"
        '<tr data-code="85" data-times="12|60-2.1_T 05-9.9_T 05-2.1_T"></tr>'
        "</table></html>"
    )
    times, warnings = parse_schedule_page(html, stop_id="85", target_shapes={"2.1_T"})
    assert [(entry.hour, entry.minute) for entry in times] == [(12, 5)]
    assert warnings == ()


def test_parse_accepts_after_midnight_hours() -> None:
    html = (
        '<html><table><tr data-code="85" data-times="24|10-2.1_T, 25|05-2.1_T"></tr></table></html>'
    )
    times, warnings = parse_schedule_page(html, stop_id="85", target_shapes={"2.1_T"})
    assert [(entry.hour, entry.minute) for entry in times] == [(24, 10), (25, 5)]
    assert warnings == ()


async def test_provider_fetches_all_direction_pages(
    network: NetworkIndex, clock: MutableClock
) -> None:
    fake = seed_fake_stpt(FakeSTPT())
    settings = make_test_settings()
    upstream = make_upstream(settings, fake, clock)
    provider = ScheduleProvider(
        upstream, settings, network, clock=clock.monotonic, wall_clock=clock.wall
    )
    try:
        result = await provider.fetch_stop_schedule(network.require_stop("85"), SERVICE_DATE)
        assert result.complete is True
        assert result.stale is False
        assert result.fetched_at == clock.wall()
        assert result.warnings == ()
        assert [
            (departure.line, departure.departure_at.strftime("%H:%M"), departure.direction)
            for departure in result.value
        ] == [
            ("2", "13:35", Direction.OUTBOUND),
            ("4", "13:40", Direction.INBOUND),
            ("7", "13:45", Direction.OUTBOUND),
            ("2", "13:50", Direction.OUTBOUND),
            ("4", "13:58", Direction.INBOUND),
            ("2", "14:05", Direction.OUTBOUND),
            ("7", "14:15", Direction.OUTBOUND),
            ("4", "14:16", Direction.INBOUND),
            ("7", "15:15", Direction.OUTBOUND),
        ]
        assert all(departure.mode is VehicleMode.TRAM for departure in result.value)
        paths = {urlsplit(str(request.url)).path for request in fake.requests}
        assert paths == {
            "/linie-transport-public-2/",
            "/linie-transport-public-4-r/",
            "/linie-transport-public-7/",
        }
        assert all(
            request.url.params.get("custom_value") == "21.10.2026" for request in fake.requests
        )
    finally:
        await provider.close()
        await upstream.close()


async def test_provider_rolls_after_midnight_times_to_the_next_day(
    network: NetworkIndex, clock: MutableClock
) -> None:
    fake = seed_fake_stpt(FakeSTPT())
    fake.set_text(
        f"{SCHEDULE_BASE_URL}/linie-transport-public-2/",
        "<html><table>"
        '<tr data-code="85" data-times="24|10-2.1_T, 25|05-2.1_T"></tr>'
        "</table></html>",
    )
    settings = make_test_settings()
    upstream = make_upstream(settings, fake, clock)
    provider = ScheduleProvider(
        upstream, settings, network, clock=clock.monotonic, wall_clock=clock.wall
    )
    try:
        result = await provider.fetch_stop_schedule(network.require_stop("85"), SERVICE_DATE)
        departures = [
            departure.departure_at.strftime("%Y-%m-%d %H:%M")
            for departure in result.value
            if departure.line == "2"
        ]
        assert departures == ["2026-10-22 00:10", "2026-10-22 01:05"]
    finally:
        await provider.close()
        await upstream.close()


async def test_provider_uses_date_specific_pages(
    network: NetworkIndex, clock: MutableClock
) -> None:
    fake = seed_fake_stpt(FakeSTPT())
    settings = make_test_settings()
    upstream = make_upstream(settings, fake, clock)
    provider = ScheduleProvider(
        upstream, settings, network, clock=clock.monotonic, wall_clock=clock.wall
    )
    try:
        await provider.fetch_stop_schedule(network.require_stop("85"), date(2026, 10, 22))
        assert all(
            request.url.params.get("custom_value") == "22.10.2026" for request in fake.requests
        )
    finally:
        await provider.close()
        await upstream.close()


async def test_provider_marks_partial_when_one_page_fails(
    network: NetworkIndex, clock: MutableClock
) -> None:
    fake = seed_fake_stpt(FakeSTPT())
    settings = make_test_settings()
    upstream = make_upstream(settings, fake, clock)
    provider = ScheduleProvider(
        upstream, settings, network, clock=clock.monotonic, wall_clock=clock.wall
    )
    fake.fail_next(f"{SCHEDULE_BASE_URL}/linie-transport-public-4-r/", times=3)
    try:
        result = await provider.fetch_stop_schedule(network.require_stop("85"), SERVICE_DATE)
        assert result.complete is False
        assert result.stale is False
        assert [departure.line for departure in result.value] == ["2", "7", "2", "2", "7", "7"]
        assert any(
            "Line 4 retur schedule page was unavailable" in warning for warning in result.warnings
        )
    finally:
        await provider.close()
        await upstream.close()


async def test_provider_fails_when_every_page_fails(
    network: NetworkIndex, clock: MutableClock
) -> None:
    fake = seed_fake_stpt(FakeSTPT())
    settings = make_test_settings()
    upstream = make_upstream(settings, fake, clock)
    provider = ScheduleProvider(
        upstream, settings, network, clock=clock.monotonic, wall_clock=clock.wall
    )
    for path in ("2", "4-r", "7"):
        fake.fail_next(f"{SCHEDULE_BASE_URL}/linie-transport-public-{path}/", times=3)
    try:
        with pytest.raises(StptError) as caught:
            await provider.fetch_stop_schedule(network.require_stop("85"), SERVICE_DATE)
        assert caught.value.code is ErrorCode.SCHEDULE_UNAVAILABLE
        assert caught.value.retryable is True
    finally:
        await provider.close()
        await upstream.close()


async def test_provider_rejects_stop_without_shapes(
    network: NetworkIndex, clock: MutableClock
) -> None:
    fake = FakeSTPT()
    settings = make_test_settings()
    upstream = make_upstream(settings, fake, clock)
    provider = ScheduleProvider(
        upstream, settings, network, clock=clock.monotonic, wall_clock=clock.wall
    )
    stop = StopRecord(
        stop_id="0",
        name="No shapes",
        latitude=0.0,
        longitude=0.0,
        routes=(),
        shapes=(),
        is_terminal=False,
        terminal_for=(),
        neighbors=(),
    )
    try:
        with pytest.raises(StptError) as caught:
            await provider.fetch_stop_schedule(stop, SERVICE_DATE)
        assert caught.value.code is ErrorCode.SCHEDULE_UNAVAILABLE
        assert fake.requests == []
    finally:
        await provider.close()
        await upstream.close()


async def test_provider_truncates_page_list(network: NetworkIndex, clock: MutableClock) -> None:
    fake = seed_fake_stpt(FakeSTPT())
    settings = make_test_settings(schedule_max_pages=1)
    upstream = make_upstream(settings, fake, clock)
    provider = ScheduleProvider(
        upstream, settings, network, clock=clock.monotonic, wall_clock=clock.wall
    )
    try:
        result = await provider.fetch_stop_schedule(network.require_stop("85"), SERVICE_DATE)
        assert result.complete is False
        assert [departure.line for departure in result.value] == ["2", "2", "2"]
        assert result.warnings == ("Schedule lookup was limited to 1 pages.",)
        assert len(fake.requests) == 1
    finally:
        await provider.close()
        await upstream.close()


async def test_provider_serves_stale_page_after_refresh_failure(
    network: NetworkIndex, clock: MutableClock
) -> None:
    fake = seed_fake_stpt(FakeSTPT())
    settings = make_test_settings(schedule_ttl_seconds=10, schedule_stale_ttl_seconds=100)
    upstream = make_upstream(settings, fake, clock)
    provider = ScheduleProvider(
        upstream, settings, network, clock=clock.monotonic, wall_clock=clock.wall
    )
    try:
        await provider.fetch_stop_schedule(network.require_stop("85"), SERVICE_DATE)
        clock.advance(11)
        fake.fail_next(f"{SCHEDULE_BASE_URL}/linie-transport-public-2/", times=3)
        result = await provider.fetch_stop_schedule(network.require_stop("85"), SERVICE_DATE)
        assert result.complete is True
        assert result.stale is True
        assert [departure.line for departure in result.value] == [
            "2",
            "4",
            "7",
            "2",
            "4",
            "2",
            "7",
            "4",
            "7",
        ]
        assert any("came from the stale cache" in warning for warning in result.warnings)
        assert any("refresh failed" in warning for warning in result.warnings)
    finally:
        await provider.close()
        await upstream.close()


async def test_provider_page_cache_avoids_duplicate_fetches(
    network: NetworkIndex, clock: MutableClock
) -> None:
    fake = seed_fake_stpt(FakeSTPT())
    settings = make_test_settings()
    upstream = make_upstream(settings, fake, clock)
    provider = ScheduleProvider(
        upstream, settings, network, clock=clock.monotonic, wall_clock=clock.wall
    )
    try:
        await provider.fetch_stop_schedule(network.require_stop("85"), SERVICE_DATE)
        await provider.fetch_stop_schedule(network.require_stop("85"), SERVICE_DATE)
        assert len(fake.requests) == 3
        assert provider.page_cache.stats().hits == 3
    finally:
        await provider.close()
        await upstream.close()
