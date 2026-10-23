"""Alerts, vehicles, service status, and network staleness."""

from __future__ import annotations

from datetime import timedelta

from stpt_mcp.models import DataSource, Status
from stpt_mcp.network import NetworkIndex
from tests.support import ALERTS_URL, VEHICLES_URL, FakeSTPT, MutableClock, ServiceFactory


async def test_alerts_active_filtering(
    service_factory: ServiceFactory, fake: FakeSTPT, clock: MutableClock
) -> None:
    service, _ = await service_factory(fake=fake)
    result = await service.get_alerts()
    assert result.status is Status.OK
    assert result.active_only is True
    assert result.count == 2
    assert [alert.id for alert in result.alerts] == ["A1", "A4"]
    assert all(alert.active for alert in result.alerts)
    assert result.provenance is not None
    assert result.provenance.source is DataSource.LIVE
    assert any(
        "A4 has no parseable active period" in warning for warning in result.provenance.warnings
    )


async def test_alerts_can_include_inactive(service_factory: ServiceFactory, fake: FakeSTPT) -> None:
    service, _ = await service_factory(fake=fake)
    result = await service.get_alerts(active_only=False)
    assert result.count == 4
    assert [alert.id for alert in result.alerts] == ["A1", "A4", "A2", "A3"]
    assert [alert.active for alert in result.alerts] == [True, True, False, False]


async def test_alerts_filter_by_line_and_stop(
    service_factory: ServiceFactory, fake: FakeSTPT
) -> None:
    service, _ = await service_factory(fake=fake)
    by_line = await service.get_alerts(line="7")
    assert [alert.id for alert in by_line.alerts] == ["A1", "A4"]
    by_stop = await service.get_alerts(stop_id="85")
    assert [alert.id for alert in by_stop.alerts] == ["A1", "A4"]
    assert by_stop.stop_id == "85"


async def test_alerts_limit_and_invalid_arguments(
    service_factory: ServiceFactory, fake: FakeSTPT
) -> None:
    service, _ = await service_factory(fake=fake)
    limited = await service.get_alerts(active_only=False, limit=1)
    assert limited.count == 1

    bad_limit = await service.get_alerts(limit=0)
    assert bad_limit.status is Status.ERROR
    assert bad_limit.error is not None
    assert bad_limit.error.code.value == "invalid_argument"

    unknown_stop = await service.get_alerts(stop_id="nope")
    assert unknown_stop.status is Status.ERROR
    assert unknown_stop.error is not None
    assert unknown_stop.error.code.value == "unknown_stop"

    unknown_line = await service.get_alerts(line="ZZZ")
    assert unknown_line.status is Status.ERROR
    assert unknown_line.error is not None
    assert unknown_line.error.code.value == "unknown_route"


async def test_alerts_stale_on_refresh_failure(
    service_factory: ServiceFactory, fake: FakeSTPT, clock: MutableClock
) -> None:
    service, _ = await service_factory(fake=fake)
    await service.get_alerts()
    clock.advance(46)
    fake.fail_next(ALERTS_URL, times=3)
    result = await service.get_alerts()
    assert result.status is Status.PARTIAL
    assert result.provenance is not None
    assert result.provenance.is_stale is True
    assert result.error is not None
    assert result.error.retryable is True
    assert any("last-known-good" in warning for warning in result.provenance.warnings)


async def test_alerts_malformed_records_make_partial(
    service_factory: ServiceFactory, fake: FakeSTPT
) -> None:
    fake.set_json(ALERTS_URL, [{"id": "broken"}])
    service, _ = await service_factory(fake=fake)
    result = await service.get_alerts()
    assert result.status is Status.PARTIAL
    assert result.error is not None
    assert result.error.code.value == "upstream_invalid_response"


async def test_vehicles_include_staleness_per_vehicle(
    service_factory: ServiceFactory, fake: FakeSTPT, clock: MutableClock
) -> None:
    service, _ = await service_factory(fake=fake)
    result = await service.get_vehicles()
    assert result.status is Status.PARTIAL
    assert result.count == 4
    assert result.error is not None
    assert result.error.code.value == "data_stale"
    assert [vehicle.vehicle_id for vehicle in result.vehicles] == ["3512", "GLU", "3515", "GLR"]
    assert [vehicle.is_stale for vehicle in result.vehicles] == [True, False, False, True]
    assert result.provenance is not None
    assert result.provenance.is_stale is True
    assert result.provenance.observed_at == result.vehicles[1].observed_at
    assert result.provenance.data_age_seconds == 5
    assert any("older than 120s" in warning for warning in result.provenance.warnings)


async def test_vehicles_filter_and_limit(service_factory: ServiceFactory, fake: FakeSTPT) -> None:
    service, _ = await service_factory(fake=fake)
    line = await service.get_vehicles(line="7")
    assert line.status is Status.OK
    assert line.line_filter == "7"
    assert [vehicle.vehicle_id for vehicle in line.vehicles] == ["GLU"]

    limited = await service.get_vehicles(limit=2)
    assert limited.count == 2

    unknown = await service.get_vehicles(line="ZZZ")
    assert unknown.status is Status.ERROR
    assert unknown.error is not None
    assert unknown.error.code.value == "unknown_route"

    bad_limit = await service.get_vehicles(limit=0)
    assert bad_limit.status is Status.ERROR
    assert bad_limit.error is not None
    assert bad_limit.error.code.value == "invalid_argument"


async def test_vehicles_stale_cache_on_refresh_failure(
    service_factory: ServiceFactory, fake: FakeSTPT, clock: MutableClock
) -> None:
    service, _ = await service_factory(fake=fake)
    await service.get_vehicles()
    clock.advance(61)
    fake.fail_next(VEHICLES_URL, times=3)
    result = await service.get_vehicles()
    assert result.status is Status.PARTIAL
    assert result.provenance is not None
    assert result.provenance.is_stale is True
    assert result.error is not None
    assert result.error.code.value == "upstream_http_error"
    assert any("last-known-good" in warning for warning in result.provenance.warnings)


async def test_vehicles_all_stale_filter_is_flagged(
    service_factory: ServiceFactory, fake: FakeSTPT
) -> None:
    service, _ = await service_factory(fake=fake)
    result = await service.get_vehicles(line="2")
    assert result.count == 1
    assert result.status is Status.PARTIAL
    assert result.provenance is not None
    assert result.provenance.is_stale is True


async def test_service_status_reports_caches_and_feed(
    service_factory: ServiceFactory, fake: FakeSTPT, clock: MutableClock
) -> None:
    service, _ = await service_factory(fake=fake)
    await service.get_arrivals("85")
    await service.get_alerts()
    await service.get_vehicles()
    status = await service.get_status()
    assert status.status is Status.OK
    assert status.ready is True
    assert status.version
    assert status.current_time == clock.wall()
    assert status.timezone == "Europe/Bucharest"
    assert status.started_at is not None
    assert status.network_feed_version
    assert status.network_service_range["start"]
    names = [cache.name for cache in status.caches]
    assert names == ["alerts", "arrivals", "schedule_pages", "schedules", "vehicles"]
    arrivals = next(cache for cache in status.caches if cache.name == "arrivals")
    assert arrivals.entries == 1
    assert arrivals.loads == 1


async def test_service_status_before_start(network: NetworkIndex, clock: MutableClock) -> None:
    from stpt_mcp.service import TransitService
    from tests.support import make_test_settings

    service = TransitService(
        make_test_settings(), network=network, clock=clock.monotonic, wall_clock=clock.wall
    )
    try:
        status = await service.get_status()
        assert status.ready is False
        assert status.started_at is None
        assert [cache.name for cache in status.caches] == [
            "alerts",
            "arrivals",
            "schedules",
            "vehicles",
        ]
    finally:
        await service.close()


async def test_network_snapshot_expiry_is_reported(
    service_factory: ServiceFactory, fake: FakeSTPT, clock: MutableClock
) -> None:
    service, _ = await service_factory(fake=fake)
    clock.advance(timedelta(days=200).total_seconds())
    result = service.get_stop("85")
    assert result.status is Status.PARTIAL
    assert result.error is not None
    assert result.error.code.value == "network_stale"
    assert result.provenance is not None
    assert result.provenance.is_stale is True
    assert result.provenance.source is DataSource.NETWORK


async def test_network_before_service_start_is_a_warning(
    service_factory: ServiceFactory, fake: FakeSTPT, clock: MutableClock
) -> None:
    service, _ = await service_factory(fake=fake)
    clock.now = clock.now - timedelta(days=365)
    result = service.search_stops("catedrala", limit=1)
    assert result.status is Status.OK
    assert result.provenance is not None
    assert any("starts on" in warning for warning in result.provenance.warnings)
