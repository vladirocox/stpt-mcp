"""Arrivals orchestration: live-first, fallback, stale, and isolation."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from stpt_mcp.models import DataSource, Status
from stpt_mcp.network import NetworkIndex
from tests.support import FakeSTPT, MutableClock, ServiceFactory, make_test_settings

BUCHAREST = ZoneInfo("Europe/Bucharest")
LIVE_URL = "https://live.test/proxy-smtt-cache.php"
FROZEN_LIVE = [
    {
        "route": "7",
        "type": "tv",
        "destination": "Calea Torontalului (Ciocanul)",
        "vehicleId": None,
        "times": ["16:10|10", "16:26|26"],
    }
]


def minutes_until(arrival_time: str, now: datetime) -> float:
    """Resolve a public ``HH:MM`` against ``now`` and return the signed minutes."""

    local = now.astimezone(BUCHAREST)
    hour, minute = (int(part) for part in arrival_time.split(":"))
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    nearest = min(
        (candidate - timedelta(days=1), candidate, candidate + timedelta(days=1)),
        key=lambda value: abs((value - local).total_seconds()),
    )
    return (nearest - local).total_seconds() / 60


async def test_live_arrivals_are_preferred(
    service_factory: ServiceFactory, fake: FakeSTPT, clock: MutableClock
) -> None:
    service, _ = await service_factory(fake=fake)
    result = await service.get_arrivals("85")
    assert result.status is Status.OK
    assert result.stop_id == "85"
    assert result.stop_name == "Catedrala Mitropolitană"
    assert result.count == 4
    assert [(arrival.line, arrival.eta_minutes, arrival.live) for arrival in result.arrivals] == [
        ("7", 3, True),
        ("2", 5, True),
        ("4", 8, True),
        ("7", 10, True),
    ]
    assert result.provenance is not None
    assert result.provenance.source is DataSource.LIVE
    assert result.provenance.is_stale is False
    assert result.provenance.observed_at == result.provenance.fetched_at
    assert result.provenance.data_age_seconds == 0
    assert result.provenance.fallback_reason is None
    assert any("duplicate" in warning for warning in result.provenance.warnings)
    assert result.provenance.fetched_at.tzinfo is not None


async def test_live_arrivals_can_be_filtered_and_limited(
    service_factory: ServiceFactory, fake: FakeSTPT
) -> None:
    service, _ = await service_factory(fake=fake)
    filtered = await service.get_arrivals("85", line="7")
    assert filtered.status is Status.OK
    assert filtered.line_filter == "7"
    assert [arrival.line for arrival in filtered.arrivals] == ["7", "7"]

    limited = await service.get_arrivals("85", limit=2)
    assert limited.count == 2
    assert [arrival.eta_minutes for arrival in limited.arrivals] == [3, 5]


async def test_fresh_cache_hit_makes_no_upstream_request(
    service_factory: ServiceFactory, fake: FakeSTPT, clock: MutableClock
) -> None:
    service, _ = await service_factory(fake=fake)
    await service.get_arrivals("85")
    fake.clear_requests()
    await service.get_arrivals("85")
    await service.get_arrivals("85")
    assert fake.requests == []


async def test_live_eta_decreases_as_time_passes(
    service_factory: ServiceFactory, fake: FakeSTPT, clock: MutableClock
) -> None:
    service, _ = await service_factory(
        fake=fake, settings=make_test_settings(arrival_ttl_seconds=120)
    )
    first = await service.get_arrivals("85")
    clock.advance(90)
    second = await service.get_arrivals("85")
    assert [arrival.eta_minutes for arrival in first.arrivals] == [3, 5, 8, 10]
    assert [arrival.eta_minutes for arrival in second.arrivals] == [2, 4, 7, 9]
    assert first.provenance is not None
    assert second.provenance is not None
    assert second.provenance.source is DataSource.LIVE
    assert second.provenance.fetched_at == first.provenance.fetched_at
    assert second.provenance.observed_at == first.provenance.observed_at
    assert second.provenance.data_age_seconds == 90


async def test_arrival_time_and_eta_always_agree(
    service_factory: ServiceFactory, fake: FakeSTPT, clock: MutableClock
) -> None:
    service, _ = await service_factory(fake=fake)
    for stop_id in ("85", "59"):
        result = await service.get_arrivals(stop_id)
        assert result.arrivals
        for arrival in result.arrivals:
            assert arrival.arrival_time is not None
            delta = minutes_until(arrival.arrival_time, clock.wall())
            assert abs(delta - arrival.eta_minutes) <= 1.5, (arrival, delta)


async def test_frozen_live_feed_falls_back_to_schedule(
    service_factory: ServiceFactory, fake: FakeSTPT
) -> None:
    fake.set_json(LIVE_URL, FROZEN_LIVE)
    service, _ = await service_factory(fake=fake)
    result = await service.get_arrivals("85")
    assert result.status is Status.OK
    assert result.provenance is not None
    assert result.provenance.source is DataSource.SCHEDULE
    assert result.provenance.fallback_reason == "live_predictions_stale"
    assert result.provenance.is_stale is False
    assert result.provenance.observed_at is None
    assert all(arrival.live is False for arrival in result.arrivals)
    assert any("look stale" in warning for warning in result.provenance.warnings)
    assert any("schedule fallback" in warning.lower() for warning in result.provenance.warnings)


async def test_stale_live_is_reference_only_when_schedule_fails(
    service_factory: ServiceFactory, fake: FakeSTPT
) -> None:
    fake.set_json(LIVE_URL, FROZEN_LIVE)
    for path in ("2", "4-r", "7"):
        fake.fail_next(f"https://smtt.test/linie-transport-public-{path}/", times=3)
    service, _ = await service_factory(fake=fake)
    result = await service.get_arrivals("85")
    assert result.status is Status.PARTIAL
    assert result.count == 2
    assert result.provenance is not None
    assert result.provenance.source is DataSource.LIVE
    assert result.provenance.is_stale is True
    assert result.provenance.observed_at is None
    assert result.provenance.data_age_seconds is None
    assert result.provenance.fallback_reason == "live_predictions_stale"
    assert result.error is not None
    assert result.error.code.value == "data_stale"
    assert all(arrival.live is False for arrival in result.arrivals)
    assert any("reference only" in warning for warning in result.provenance.warnings)


async def test_data_age_tracks_upstream_observation_time(
    service_factory: ServiceFactory, fake: FakeSTPT, clock: MutableClock
) -> None:
    # The feed claims it is 13:28 while the wall clock is 13:30. That is inside
    # the skew tolerance, so the data is fresh but two minutes old.
    fake.set_json(
        LIVE_URL,
        [
            {
                "route": "7",
                "type": "tv",
                "destination": "Calea Torontalului (Ciocanul)",
                "times": ["13:33|5"],
            }
        ],
    )
    service, _ = await service_factory(fake=fake)
    result = await service.get_arrivals("85")
    assert result.provenance is not None
    assert result.provenance.source is DataSource.LIVE
    assert result.provenance.is_stale is False
    assert result.provenance.fetched_at == clock.wall()
    assert result.provenance.observed_at == clock.wall() - timedelta(minutes=2)
    assert result.provenance.data_age_seconds == 120


async def test_stale_last_known_good_after_refresh_failure(
    service_factory: ServiceFactory, fake: FakeSTPT, clock: MutableClock
) -> None:
    service, _ = await service_factory(fake=fake)
    await service.get_arrivals("85")
    clock.advance(61)
    fake.fail_next("https://live.test/proxy-smtt-cache.php", times=3)
    for path in ("2", "4-r", "7"):
        fake.fail_next(f"https://smtt.test/linie-transport-public-{path}/", times=3)
    result = await service.get_arrivals("85")
    assert result.status is Status.PARTIAL
    assert result.count == 4
    assert result.provenance is not None
    assert result.provenance.source is DataSource.LIVE
    assert result.provenance.is_stale is True
    assert result.provenance.data_age_seconds is not None
    assert result.provenance.data_age_seconds >= 61
    assert result.error is not None
    assert result.error.retryable is True
    assert any("last-known-good" in warning for warning in result.provenance.warnings)


async def test_live_and_schedule_failure_returns_structured_error(
    service_factory: ServiceFactory, fake: FakeSTPT, clock: MutableClock
) -> None:
    service, _ = await service_factory(fake=fake)
    await service.get_arrivals("85")
    clock.advance(901)
    fake.fail_next(
        "https://live.test/proxy-smtt-cache.php",
        times=3,
    )
    for path in ("2", "4-r", "7"):
        fake.fail_next(f"https://smtt.test/linie-transport-public-{path}/", times=3)
    result = await service.get_arrivals("85")
    assert result.status is Status.ERROR
    assert result.count == 0
    assert result.error is not None
    assert result.error.code.value == "upstream_unavailable"


async def test_schedule_fallback_when_live_is_empty(
    service_factory: ServiceFactory, fake: FakeSTPT
) -> None:
    service, _ = await service_factory(fake=fake)
    result = await service.get_arrivals("59")
    assert result.status is Status.OK
    assert result.stop_name.startswith("Piața Sfânta Maria")
    assert result.provenance is not None
    assert result.provenance.source is DataSource.SCHEDULE
    assert result.provenance.fallback_reason == "live_returned_no_departures"
    assert result.provenance.observed_at is None
    assert result.provenance.is_stale is False
    assert result.count == 9
    assert [arrival.line for arrival in result.arrivals] == [
        "1",
        "2",
        "1",
        "2",
        "8",
        "2",
        "1",
        "8",
        "8",
    ]
    assert all(arrival.live is False for arrival in result.arrivals)
    assert [arrival.eta_minutes for arrival in result.arrivals] == [
        6,
        7,
        24,
        25,
        25,
        31,
        39,
        55,
        115,
    ]


async def test_schedule_fallback_when_live_fails(
    service_factory: ServiceFactory, fake: FakeSTPT
) -> None:
    service, _ = await service_factory(fake=fake)
    fake.fail_next("https://live.test/proxy-smtt-cache.php", times=3)
    result = await service.get_arrivals("59")
    assert result.status is Status.PARTIAL
    assert result.provenance is not None
    assert result.provenance.source is DataSource.SCHEDULE
    assert result.provenance.fallback_reason == "live_unavailable"
    assert result.error is not None
    assert result.error.code.value == "upstream_http_error"
    assert result.count == 9


async def test_schedule_fallback_partial_when_a_page_fails(
    service_factory: ServiceFactory, fake: FakeSTPT
) -> None:
    service, _ = await service_factory(fake=fake)
    fake.fail_next("https://smtt.test/linie-transport-public-8/", times=3)
    result = await service.get_arrivals("59")
    assert result.status is Status.PARTIAL
    assert result.provenance is not None
    assert result.provenance.source is DataSource.SCHEDULE
    assert result.error is not None
    assert result.error.code.value == "schedule_unavailable"
    assert result.count == 6
    assert any("Line 8 tur" in warning for warning in result.provenance.warnings)


async def test_both_sources_down_is_a_structured_error(
    service_factory: ServiceFactory, fake: FakeSTPT
) -> None:
    service, _ = await service_factory(fake=fake)
    fake.fail_next("https://live.test/proxy-smtt-cache.php", times=3)
    for path in ("1", "2", "8"):
        fake.fail_next(f"https://smtt.test/linie-transport-public-{path}/", times=3)
    result = await service.get_arrivals("59")
    assert result.status is Status.ERROR
    assert result.error is not None
    assert result.error.code.value == "upstream_unavailable"
    assert result.count == 0


async def test_malformed_live_data_falls_back_with_partial_status(
    service_factory: ServiceFactory, fake: FakeSTPT
) -> None:
    fake.set_json("https://live.test/proxy-smtt-cache.php", [{"route": "", "times": []}])
    service, _ = await service_factory(fake=fake)
    result = await service.get_arrivals("59")
    assert result.status is Status.PARTIAL
    assert result.provenance is not None
    assert result.provenance.source is DataSource.SCHEDULE
    assert result.error is not None
    assert result.error.code.value == "upstream_invalid_response"
    assert any("malformed" in warning for warning in result.provenance.warnings)


async def test_distinct_stops_are_isolated(service_factory: ServiceFactory, fake: FakeSTPT) -> None:
    service, _ = await service_factory(fake=fake)
    first = await service.get_arrivals("85")
    second = await service.get_arrivals("60")
    assert {arrival.line for arrival in first.arrivals} == {"2", "4", "7"}
    assert {arrival.line for arrival in second.arrivals} == {"4"}
    assert first.stop_id == "85"
    assert second.stop_id == "60"
    assert fake.query_counts("https://live.test/proxy-smtt-cache.php", "stopid") == {
        "85": 1,
        "60": 1,
    }
    fake.clear_requests()
    await service.get_arrivals("85")
    await service.get_arrivals("60")
    assert fake.requests == []


async def test_schedule_pages_are_shared_but_stop_rows_are_not(
    service_factory: ServiceFactory, fake: FakeSTPT
) -> None:
    fake.set_json("https://live.test/proxy-smtt-cache.php", [])
    service, _ = await service_factory(fake=fake)
    first = await service.get_arrivals("85")
    second = await service.get_arrivals("59")
    assert {arrival.line for arrival in first.arrivals} == {"2", "4", "7"}
    assert {arrival.line for arrival in second.arrivals} == {"1", "2", "8"}
    paths = [request.url.path for request in fake.requests]
    assert paths.count("/linie-transport-public-2/") == 1
    assert paths.count("/linie-transport-public-4-r/") == 1
    assert paths.count("/linie-transport-public-7/") == 1
    assert paths.count("/linie-transport-public-1/") == 1
    assert paths.count("/linie-transport-public-8/") == 1


async def test_invalid_arguments_are_structured(
    service_factory: ServiceFactory, fake: FakeSTPT
) -> None:
    service, _ = await service_factory(fake=fake)
    unknown_stop = await service.get_arrivals("nope")
    assert unknown_stop.status is Status.ERROR
    assert unknown_stop.error is not None
    assert unknown_stop.error.code.value == "unknown_stop"

    unknown_line = await service.get_arrivals("85", line="ZZZ")
    assert unknown_line.status is Status.ERROR
    assert unknown_line.error is not None
    assert unknown_line.error.code.value == "unknown_route"

    bad_limit = await service.get_arrivals("85", limit=0)
    assert bad_limit.status is Status.ERROR
    assert bad_limit.error is not None
    assert bad_limit.error.code.value == "invalid_argument"


async def test_service_must_be_started(
    network: NetworkIndex, clock: MutableClock, fake: FakeSTPT
) -> None:
    from stpt_mcp.service import TransitService

    service = TransitService(
        make_test_settings(), network=network, clock=clock.monotonic, wall_clock=clock.wall
    )
    try:
        result = await service.get_arrivals("85")
        assert result.status is Status.ERROR
        assert result.error is not None
        assert result.error.code.value == "not_ready"
    finally:
        await service.close()
