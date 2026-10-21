"""Date- and shape-aware schedule fallback provider."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from datetime import time as time_type
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from stpt_mcp.cache import BoundedTTLCache, CacheState
from stpt_mcp.config import Settings
from stpt_mcp.errors import ErrorCode, StptError
from stpt_mcp.models import Direction
from stpt_mcp.network import NetworkIndex, StopRecord
from stpt_mcp.providers.base import ProviderResult, ScheduleDeparture
from stpt_mcp.upstream import UpstreamClient

_ATTR_TIME_RE = re.compile(r"^(?P<hour>[01]?\d|2[0-3])\|(?P<rest>.*)$")


@dataclass(frozen=True, slots=True)
class ScheduleTime:
    hour: int
    minute: int
    shape_id: str


@dataclass(frozen=True, slots=True)
class SchedulePage:
    times: tuple[ScheduleTime, ...]
    fetched_at: datetime
    warnings: tuple[str, ...] = ()
    complete: bool = True


@dataclass(frozen=True, slots=True)
class _PageTarget:
    label: str
    url: str
    shapes: frozenset[str]
    stop_id: str


class ScheduleProvider:
    def __init__(
        self,
        client: UpstreamClient,
        settings: Settings,
        network: NetworkIndex,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._settings = settings
        self._network = network
        self._zone = ZoneInfo(settings.timezone)
        self._pages: BoundedTTLCache[tuple[str, str], SchedulePage] = BoundedTTLCache(
            "schedule_pages",
            ttl_seconds=settings.schedule_ttl_seconds,
            stale_ttl_seconds=settings.schedule_stale_ttl_seconds,
            max_entries=settings.schedule_page_cache_entries,
            clock=clock,
            wall_clock=wall_clock or (lambda: datetime.now(UTC)),
        )

    async def fetch_stop_schedule(
        self,
        stop: StopRecord,
        service_date: date,
    ) -> ProviderResult[tuple[ScheduleDeparture, ...]]:
        targets, truncated = self._targets_for_stop(stop)
        if not targets:
            raise StptError(
                ErrorCode.SCHEDULE_UNAVAILABLE,
                f"No schedule shapes are known for stop {stop.stop_id}.",
                retryable=False,
                upstream="schedule",
            )

        warnings: list[str] = []
        if truncated:
            warnings.append(
                f"Schedule lookup was limited to {self._settings.schedule_max_pages} pages."
            )

        results = await asyncio.gather(
            *(self._load_page(target, service_date) for target in targets),
            return_exceptions=True,
        )

        departures: list[ScheduleDeparture] = []
        seen: set[tuple[str, Direction, datetime]] = set()
        fetched_times: list[datetime] = []
        failures = 0
        incomplete = truncated
        used_stale_page = False
        for target, result in zip(targets, results, strict=True):
            if isinstance(result, BaseException):
                if isinstance(result, asyncio.CancelledError):
                    raise result
                failures += 1
                incomplete = True
                warnings.append(f"{target.label} schedule page was unavailable.")
                continue
            page, stale, page_warnings = result
            fetched_times.append(page.fetched_at)
            warnings.extend(f"{target.label}: {warning}" for warning in page_warnings)
            if stale:
                warnings.append(f"{target.label} schedule page came from the stale cache.")
                used_stale_page = True
            if not page.complete:
                incomplete = True
            for schedule_time in page.times:
                route = self._network.route_for_shape(schedule_time.shape_id)
                direction = self._network.direction_for_shape(schedule_time.shape_id)
                destination = self._network.destination_for_shape(schedule_time.shape_id)
                if route is None or direction is None or destination is None:
                    continue
                if schedule_time.shape_id not in target.shapes:
                    continue
                departure_at = datetime.combine(
                    service_date,
                    time_type(schedule_time.hour, schedule_time.minute),
                    tzinfo=self._zone,
                )
                key = (route.line, direction, departure_at)
                if key in seen:
                    continue
                seen.add(key)
                departures.append(
                    ScheduleDeparture(
                        line=route.line,
                        destination=destination,
                        direction=direction,
                        mode=route.mode,
                        shape_id=schedule_time.shape_id,
                        departure_at=departure_at,
                    )
                )

        if failures == len(targets):
            raise StptError(
                ErrorCode.SCHEDULE_UNAVAILABLE,
                "No schedule page could be loaded; schedule fallback is unavailable.",
                retryable=True,
                upstream="schedule",
            )

        departures.sort(key=lambda departure: (departure.departure_at, departure.line))
        fetched_at = min(fetched_times) if fetched_times else datetime.now(self._zone)
        return ProviderResult(
            value=tuple(departures),
            fetched_at=fetched_at,
            warnings=tuple(warnings[:30]),
            complete=not incomplete,
            stale=used_stale_page,
        )

    async def _load_page(
        self, target: _PageTarget, service_date: date
    ) -> tuple[SchedulePage, bool, tuple[str, ...]]:
        key = (target.url, service_date.isoformat())
        result = await self._pages.get_or_load(
            key,
            lambda: self._fetch_page(target, service_date),
        )
        warnings = result.value.warnings
        if result.error is not None:
            warnings = (*warnings, "The refresh failed; cached page content was used.")
        return result.value, result.state is CacheState.STALE, warnings

    async def _fetch_page(self, target: _PageTarget, service_date: date) -> SchedulePage:
        html, fetched_at = await self._client.get_text(
            target.url,
            source=f"schedule {target.label}",
            params={"custom_value": service_date.strftime("%d.%m.%Y")},
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "Referer": f"{self._settings.schedule_base_url.rstrip('/')}/",
            },
        )
        times, warnings = parse_schedule_page(
            html,
            stop_id=target.stop_id,
            target_shapes=target.shapes,
        )
        return SchedulePage(
            times=times,
            fetched_at=fetched_at,
            warnings=warnings,
            complete=not any("not present" in warning for warning in warnings),
        )

    def _targets_for_stop(self, stop: StopRecord) -> tuple[list[_PageTarget], bool]:
        grouped: dict[tuple[str, str], set[str]] = {}
        for shape_id in stop.shapes:
            suffix = shape_id.rsplit("_", 1)[-1]
            if suffix not in {"T", "R"}:
                continue
            line = shape_id.split(".", 1)[0]
            grouped.setdefault((line, suffix), set()).add(shape_id)

        targets: list[_PageTarget] = []
        for (line, suffix), shapes in sorted(grouped.items()):
            slug = f"linie-transport-public-{line.lower()}"
            if suffix == "R":
                slug += "-r"
            label = f"Line {line} {'retur' if suffix == 'R' else 'tur'}"
            url = f"{self._settings.schedule_base_url.rstrip('/')}/{slug}/"
            targets.append(
                _PageTarget(
                    label=label,
                    url=url,
                    shapes=frozenset(shapes),
                    stop_id=stop.stop_id,
                )
            )
        truncated = len(targets) > self._settings.schedule_max_pages
        return targets[: self._settings.schedule_max_pages], truncated

    @property
    def page_cache(self) -> BoundedTTLCache[tuple[str, str], SchedulePage]:
        return self._pages

    async def close(self) -> None:
        await self._pages.close()


def parse_schedule_page(
    html: str,
    *,
    stop_id: str,
    target_shapes: frozenset[str] | set[str],
) -> tuple[tuple[ScheduleTime, ...], tuple[str, ...]]:
    """Parse schedule entries for one stop and its known shape IDs.

    Only tokens whose shape suffix matches ``target_shapes`` are accepted. This
    prevents times from another direction or line variant being attributed to
    the requested stop.
    """
    if "data-times" not in html:
        raise StptError(
            ErrorCode.UPSTREAM_INVALID_RESPONSE,
            "schedule page does not contain a timetable",
            retryable=True,
            upstream="schedule",
        )
    soup = BeautifulSoup(html, "html.parser")
    wanted_stop = str(stop_id)
    target_set = set(target_shapes)
    times: list[ScheduleTime] = []
    warnings: list[str] = []
    seen: set[tuple[int, int, str]] = set()
    rows = [
        element
        for element in soup.find_all(attrs={"data-code": True})
        if str(element.get("data-code")) == wanted_stop
    ]
    if not rows:
        return (), (f"Stop {wanted_stop} was not present on the schedule page.",)

    malformed = 0
    for row in rows:
        raw_times = row.get("data-times")
        if not isinstance(raw_times, str):
            continue
        for chunk in raw_times.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            match = _ATTR_TIME_RE.match(chunk)
            if not match:
                malformed += 1
                continue
            hour = int(match.group("hour"))
            for token in match.group("rest").split():
                minute_text, separator, shape_id = token.partition("-")
                if not separator:
                    malformed += 1
                    continue
                try:
                    minute = int(minute_text)
                except ValueError:
                    malformed += 1
                    continue
                if not 0 <= minute <= 59 or shape_id not in target_set:
                    continue
                key = (hour, minute, shape_id)
                if key in seen:
                    continue
                seen.add(key)
                times.append(ScheduleTime(hour=hour, minute=minute, shape_id=shape_id))
    if malformed:
        warnings.append(f"Ignored {malformed} malformed schedule time token(s).")
    times.sort(key=lambda entry: (entry.hour, entry.minute, entry.shape_id))
    return tuple(times), tuple(warnings)
