"""Concurrency guarantees: single-flight live and schedule refreshes."""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

from stpt_mcp.models import DataSource
from stpt_mcp.network import StopRecord
from stpt_mcp.providers.base import ProviderResult, ScheduleDeparture
from stpt_mcp.service import TransitService
from tests.support import LIVE_URL, FakeSTPT, MutableClock, ServiceFactory


def wrap_schedule_fetch(
    service: TransitService, monkeypatch: pytest.MonkeyPatch
) -> list[tuple[str, str]]:
    provider = service._schedule
    assert provider is not None
    original = provider.fetch_stop_schedule
    calls: list[tuple[str, str]] = []

    async def counting(
        stop: StopRecord, service_date: date
    ) -> ProviderResult[tuple[ScheduleDeparture, ...]]:
        calls.append((stop.stop_id, service_date.isoformat()))
        return await original(stop, service_date)

    monkeypatch.setattr(provider, "fetch_stop_schedule", counting)
    return calls


async def test_fifty_concurrent_same_stop_calls_share_one_live_fetch(
    service_factory: ServiceFactory, fake: FakeSTPT, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake.set_delay(LIVE_URL, 0.05)
    service, _ = await service_factory(fake=fake)
    schedule_calls = wrap_schedule_fetch(service, monkeypatch)

    results = await asyncio.gather(*(service.get_arrivals("85") for _ in range(50)))
    assert all(result.status.value == "ok" for result in results)
    assert all(result.count == 4 for result in results)
    assert len(fake.requests_for(LIVE_URL)) == 1
    assert fake.query_counts(LIVE_URL, "stopid") == {"85": 1}
    assert schedule_calls == []
    stats = service._arrivals_cache.stats()
    assert stats.loads == 1
    assert stats.coalesced == 49
    assert stats.inflight == 0


async def test_fifty_concurrent_fallback_calls_share_one_schedule_refresh(
    service_factory: ServiceFactory, fake: FakeSTPT, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake.set_json(LIVE_URL, [])
    fake.set_delay(LIVE_URL, 0.02)
    service, _ = await service_factory(fake=fake)
    schedule_calls = wrap_schedule_fetch(service, monkeypatch)

    results = await asyncio.gather(*(service.get_arrivals("59") for _ in range(50)))
    assert all(result.status.value == "ok" for result in results)
    assert all(result.count == 10 for result in results)
    assert all(
        result.provenance is not None and result.provenance.source is DataSource.SCHEDULE
        for result in results
    )
    assert len(fake.requests_for(LIVE_URL)) == 1
    assert schedule_calls == [("59", "2026-10-21")]
    assert {request.url.path for request in fake.requests} == {"/proxy-smtt-cache.php"}
    stats = service._arrivals_cache.stats()
    assert stats.loads == 1
    assert stats.coalesced == 49


async def test_fifty_concurrent_calls_to_two_stops_do_not_cross_contaminate(
    service_factory: ServiceFactory, fake: FakeSTPT, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake.set_delay(LIVE_URL, 0.03)
    service, _ = await service_factory(fake=fake)
    schedule_calls = wrap_schedule_fetch(service, monkeypatch)

    calls = [service.get_arrivals("85") for _ in range(25)]
    calls.extend(service.get_arrivals("60") for _ in range(25))
    results = await asyncio.gather(*calls)
    first, second = results[:25], results[25:]
    assert all({arrival.line for arrival in result.arrivals} == {"2", "4", "7"} for result in first)
    assert all({arrival.line for arrival in result.arrivals} == {"4"} for result in second)
    assert all(result.stop_id == "85" for result in first)
    assert all(result.stop_id == "60" for result in second)
    assert fake.query_counts(LIVE_URL, "stopid") == {"85": 1, "60": 1}
    assert schedule_calls == []


async def test_immediate_cache_hit_after_concurrency_makes_no_requests(
    service_factory: ServiceFactory, fake: FakeSTPT, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake.set_json(LIVE_URL, [])
    service, _ = await service_factory(fake=fake)
    schedule_calls = wrap_schedule_fetch(service, monkeypatch)
    await asyncio.gather(*(service.get_arrivals("59") for _ in range(50)))
    assert schedule_calls == [("59", "2026-10-21")]

    fake.clear_requests()
    hit = await service.get_arrivals("59")
    assert hit.count == 10
    assert fake.requests == []
    assert schedule_calls == [("59", "2026-10-21")]


async def test_fifty_concurrent_live_requests_use_one_upstream_slot(
    service_factory: ServiceFactory, fake: FakeSTPT, clock: MutableClock
) -> None:
    fake.set_delay(LIVE_URL, 0.05)
    service, _ = await service_factory(fake=fake, settings=None)
    results = await asyncio.gather(*(service.get_arrivals("85") for _ in range(50)))
    assert all(result.count == 4 for result in results)
    assert wait_count(service) == 1
    assert fake.count(LIVE_URL) == 1


def wait_count(service: TransitService) -> int:
    return service._arrivals_cache.stats().loads
