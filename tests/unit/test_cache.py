"""Bounded TTL cache: freshness, coalescing, eviction, and stale fallback."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from stpt_mcp.cache import BoundedTTLCache, CacheState
from tests.support import MutableClock


def make_cache(
    clock: MutableClock,
    *,
    ttl: float = 10.0,
    stale_ttl: float = 100.0,
    max_entries: int = 4,
) -> BoundedTTLCache[str, str]:
    return BoundedTTLCache(
        "test",
        ttl_seconds=ttl,
        stale_ttl_seconds=stale_ttl,
        max_entries=max_entries,
        clock=clock.monotonic,
        wall_clock=clock.wall,
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"ttl_seconds": 0, "stale_ttl_seconds": 100, "max_entries": 4}, "ttl_seconds"),
        ({"ttl_seconds": 10, "stale_ttl_seconds": 5, "max_entries": 4}, "stale_ttl_seconds"),
        ({"ttl_seconds": 10, "stale_ttl_seconds": 10, "max_entries": 0}, "max_entries"),
    ],
)
def test_invalid_configuration(kwargs: dict[str, float], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        BoundedTTLCache("invalid", **kwargs)  # type: ignore[arg-type]


async def test_fresh_hit_avoids_loader(clock: MutableClock) -> None:
    cache = make_cache(clock)
    calls = 0

    async def load() -> str:
        nonlocal calls
        calls += 1
        return f"value-{calls}"

    first = await cache.get_or_load("a", load)
    second = await cache.get_or_load("a", load)
    assert first.value == "value-1"
    assert second.value == "value-1"
    assert first.state is CacheState.FRESH
    assert second.state is CacheState.FRESH
    assert calls == 1
    stats = cache.stats()
    assert stats.hits == 1
    assert stats.misses == 1
    assert stats.loads == 1


async def test_ttl_expiry_reloads(clock: MutableClock) -> None:
    cache = make_cache(clock, ttl=10)
    calls = 0

    async def load() -> str:
        nonlocal calls
        calls += 1
        return f"value-{calls}"

    await cache.get_or_load("a", load)
    clock.advance(9.9)
    assert (await cache.get_or_load("a", load)).value == "value-1"
    clock.advance(0.2)
    assert (await cache.get_or_load("a", load)).value == "value-2"
    assert calls == 2


async def test_concurrent_loads_are_coalesced(clock: MutableClock) -> None:
    cache = make_cache(clock)
    calls = 0
    release = asyncio.Event()

    async def load() -> str:
        nonlocal calls
        calls += 1
        await release.wait()
        return "shared"

    tasks = [asyncio.create_task(cache.get_or_load("key", load)) for _ in range(50)]
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(*tasks)
    assert calls == 1
    assert {result.value for result in results} == {"shared"}
    assert cache.stats().coalesced == 49
    assert cache.stats().inflight == 0


async def test_distinct_keys_do_not_share_loads(clock: MutableClock) -> None:
    cache = make_cache(clock)
    loads: list[str] = []

    def loader_for(key: str) -> Callable[[], Awaitable[str]]:
        async def load() -> str:
            loads.append(key)
            return key.upper()

        return load

    results = await asyncio.gather(
        *(cache.get_or_load(key, loader_for(key)) for key in ("a", "b", "a", "b"))
    )
    assert sorted(loads) == ["a", "b"]
    assert [result.value for result in results] == ["A", "B", "A", "B"]


async def test_stale_value_is_served_on_error(clock: MutableClock) -> None:
    cache = make_cache(clock, ttl=10, stale_ttl=100)

    async def good() -> str:
        return "last-known-good"

    async def broken() -> str:
        raise RuntimeError("upstream down")

    await cache.get_or_load("a", good)
    clock.advance(20)
    result = await cache.get_or_load("a", broken)
    assert result.value == "last-known-good"
    assert result.state is CacheState.STALE
    assert isinstance(result.error, RuntimeError)
    assert result.age_seconds == pytest.approx(20)
    assert cache.stats().stale_hits == 1


async def test_stale_value_is_not_served_when_disabled(clock: MutableClock) -> None:
    cache = make_cache(clock, ttl=10, stale_ttl=100)

    async def good() -> str:
        return "good"

    async def broken() -> str:
        raise RuntimeError("down")

    await cache.get_or_load("a", good)
    clock.advance(20)
    with pytest.raises(RuntimeError, match="down"):
        await cache.get_or_load("a", broken, allow_stale=False)


async def test_stale_value_is_not_served_after_stale_window(clock: MutableClock) -> None:
    cache = make_cache(clock, ttl=10, stale_ttl=30)

    async def good() -> str:
        return "good"

    async def broken() -> str:
        raise RuntimeError("down")

    await cache.get_or_load("a", good)
    clock.advance(31)
    with pytest.raises(RuntimeError, match="down"):
        await cache.get_or_load("a", broken)
    assert cache.stats().stale_hits == 0


async def test_lru_eviction_is_bounded(clock: MutableClock) -> None:
    cache = make_cache(clock, max_entries=2)

    async def value() -> str:
        return "v"

    await cache.get_or_load("a", value)
    await cache.get_or_load("b", value)
    await cache.get_or_load("a", value)
    await cache.get_or_load("c", value)
    assert len(cache) == 2
    assert cache.stats().evictions == 1
    # "b" was least recently used and should have been evicted.
    calls = 0

    async def replacement() -> str:
        nonlocal calls
        calls += 1
        return "replacement"

    assert (await cache.get_or_load("b", replacement)).value == "replacement"
    assert calls == 1


async def test_invalidate_and_clear(clock: MutableClock) -> None:
    cache = make_cache(clock)

    async def value() -> str:
        return "v"

    await cache.get_or_load("a", value)
    await cache.get_or_load("b", value)
    cache.invalidate("a")
    assert len(cache) == 1
    cache.clear()
    assert len(cache) == 0


async def test_close_rejects_new_loads_and_cancels_inflight(clock: MutableClock) -> None:
    cache = make_cache(clock)
    started = asyncio.Event()

    async def forever() -> str:
        started.set()
        await asyncio.Event().wait()
        return "never"

    task = asyncio.create_task(cache.get_or_load("a", forever))
    await started.wait()
    await cache.close()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(RuntimeError, match="closed"):
        await cache.get_or_load("b", forever)


async def test_cancelled_waiter_does_not_cancel_shared_load(clock: MutableClock) -> None:
    cache = make_cache(clock)
    release = asyncio.Event()
    calls = 0

    async def load() -> str:
        nonlocal calls
        calls += 1
        await release.wait()
        return "shared"

    cancelled = asyncio.create_task(cache.get_or_load("a", load))
    await asyncio.sleep(0)
    survivor = asyncio.create_task(cache.get_or_load("a", load))
    await asyncio.sleep(0)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    release.set()
    assert (await survivor).value == "shared"
    assert calls == 1


async def test_stats_snapshot(clock: MutableClock) -> None:
    cache = make_cache(clock)

    async def value() -> str:
        return "v"

    await cache.get_or_load("a", value)
    stats = cache.stats()
    assert stats.name == "test"
    assert stats.entries == 1
    assert stats.max_entries == 4
    assert stats.failures == 0
    assert stats.inflight == 0
    assert cache.ttl_seconds == 10
    assert cache.stale_ttl_seconds == 100
