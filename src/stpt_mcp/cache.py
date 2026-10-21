"""A bounded async TTL cache with single-flight and stale-on-error semantics."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Generic, TypeVar

from stpt_mcp.models import CacheStats

K = TypeVar("K", bound=Hashable)
T = TypeVar("T")


class CacheState(StrEnum):
    FRESH = "fresh"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class CacheResult(Generic[T]):
    """A cached value and the freshness decision made when serving it."""

    value: T
    state: CacheState
    age_seconds: float
    error: Exception | None = None


@dataclass(frozen=True, slots=True)
class _Entry(Generic[T]):
    value: T
    stored_at: float
    fetched_at: datetime


class BoundedTTLCache(Generic[K, T]):
    """TTL cache with LRU eviction and a single in-flight load per key.

    The cache is intended for one asyncio event loop. Caller cancellation is
    shielded from the shared load so one disconnected client cannot cancel the
    fetch observed by every other waiter.
    """

    def __init__(
        self,
        name: str,
        *,
        ttl_seconds: float,
        stale_ttl_seconds: float,
        max_entries: int,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if stale_ttl_seconds < ttl_seconds:
            raise ValueError("stale_ttl_seconds must be >= ttl_seconds")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self.name = name
        self._ttl = ttl_seconds
        self._stale_ttl = stale_ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._wall_clock = wall_clock
        self._entries: OrderedDict[K, _Entry[T]] = OrderedDict()
        self._inflight: dict[K, asyncio.Task[_Entry[T]]] = {}
        self._closed = False
        self._hits = 0
        self._misses = 0
        self._coalesced = 0
        self._stale_hits = 0
        self._evictions = 0
        self._loads = 0
        self._failures = 0

    @property
    def ttl_seconds(self) -> float:
        return self._ttl

    @property
    def stale_ttl_seconds(self) -> float:
        return self._stale_ttl

    def __len__(self) -> int:
        return len(self._entries)

    async def get_or_load(
        self,
        key: K,
        loader: Callable[[], Awaitable[T]],
        *,
        allow_stale: bool = True,
    ) -> CacheResult[T]:
        """Return a fresh value or coalesce a load; serve stale data on failure."""
        if self._closed:
            raise RuntimeError(f"cache {self.name!r} is closed")
        now = self._clock()
        entry = self._entries.get(key)
        if entry is not None and now - entry.stored_at < self._ttl:
            self._entries.move_to_end(key)
            self._hits += 1
            return CacheResult(
                value=entry.value,
                state=CacheState.FRESH,
                age_seconds=max(0.0, now - entry.stored_at),
            )
        if entry is not None and now - entry.stored_at > self._stale_ttl:
            self._entries.pop(key, None)

        task = self._inflight.get(key)
        if task is not None:
            self._coalesced += 1
            return await self._wait_for(key, task, allow_stale=allow_stale)

        self._misses += 1
        task = asyncio.create_task(self._load_and_store(key, loader))
        self._inflight[key] = task
        task.add_done_callback(lambda finished: self._finish(key, finished))
        return await self._wait_for(key, task, allow_stale=allow_stale)

    async def _load_and_store(self, key: K, loader: Callable[[], Awaitable[T]]) -> _Entry[T]:
        try:
            value = await loader()
        except Exception:
            self._failures += 1
            raise
        entry = _Entry(value=value, stored_at=self._clock(), fetched_at=self._wall_clock())
        self._entries[key] = entry
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
            self._evictions += 1
        self._loads += 1
        return entry

    async def _wait_for(
        self,
        key: K,
        task: asyncio.Task[_Entry[T]],
        *,
        allow_stale: bool,
    ) -> CacheResult[T]:
        try:
            fresh_entry = await asyncio.shield(task)
        except Exception as exc:
            stale_entry = self._entries.get(key)
            if stale_entry is not None and allow_stale:
                age = max(0.0, self._clock() - stale_entry.stored_at)
                if age <= self._stale_ttl:
                    self._entries.move_to_end(key)
                    self._stale_hits += 1
                    return CacheResult(
                        value=stale_entry.value,
                        state=CacheState.STALE,
                        age_seconds=age,
                        error=exc,
                    )
            raise
        age = max(0.0, self._clock() - fresh_entry.stored_at)
        return CacheResult(value=fresh_entry.value, state=CacheState.FRESH, age_seconds=age)

    def _finish(self, key: K, task: asyncio.Task[_Entry[T]]) -> None:
        if self._inflight.get(key) is task:
            self._inflight.pop(key, None)
        # Retrieve an exception even if every waiter was cancelled, so asyncio
        # does not emit "Task exception was never retrieved" on shutdown.
        if not task.cancelled():
            task.exception()

    def invalidate(self, key: K) -> None:
        self._entries.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()

    def stats(self) -> CacheStats:
        return CacheStats(
            name=self.name,
            entries=len(self._entries),
            max_entries=self._max_entries,
            hits=self._hits,
            misses=self._misses,
            coalesced=self._coalesced,
            stale_hits=self._stale_hits,
            evictions=self._evictions,
            loads=self._loads,
            failures=self._failures,
            inflight=len(self._inflight),
        )

    async def close(self) -> None:
        """Cancel outstanding loads and mark the cache closed."""
        self._closed = True
        tasks = list(self._inflight.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._inflight.clear()
        self._entries.clear()

    def debug_state(self) -> dict[str, Any]:  # pragma: no cover - diagnostics only
        """Return non-sensitive internal counts for local debugging."""
        return {
            "name": self.name,
            "entries": len(self._entries),
            "inflight": len(self._inflight),
            "ttl_seconds": self._ttl,
            "stale_ttl_seconds": self._stale_ttl,
        }
