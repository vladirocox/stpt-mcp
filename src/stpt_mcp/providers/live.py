"""Live real-time arrivals provider."""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from datetime import time as time_type
from typing import Any
from zoneinfo import ZoneInfo

from stpt_mcp.config import Settings
from stpt_mcp.errors import ErrorCode, StptError
from stpt_mcp.models import Freshness, LiveFeedStatus, VehicleMode
from stpt_mcp.network import NetworkIndex
from stpt_mcp.providers.base import LiveArrival, ProviderResult
from stpt_mcp.upstream import UpstreamClient

# The feed uses a 24-hour clock, but after-midnight service may be encoded as
# 24:xx through 29:xx. Accept those instead of silently dropping the record.
_TIME_RE = re.compile(r"^(?P<hour>[01]?\d|2[0-9]):(?P<minute>[0-5]\d)$")
DEFAULT_CLOCK_SKEW_TOLERANCE_SECONDS = 180.0
# A stop seen for the first time cannot prove its clock advances. While other
# stops showed a broken feed this recently, such a stop stays unverified.
_FEED_SUSPICION_SECONDS = 600.0
_RECENT_STOPS_SECONDS = 900.0
# Display times are resolved to the reading nearest the fetch, so two readings
# more than half a day apart cannot tell a running clock from a frozen one.
_COMPARABLE_SECONDS = 12 * 3600.0
_TYPE_MODES = {
    "tv": VehicleMode.TRAM,
    "tram": VehicleMode.TRAM,
    "tramvai": VehicleMode.TRAM,
    "tb": VehicleMode.TROLLEYBUS,
    "trolley": VehicleMode.TROLLEYBUS,
    "trolleybus": VehicleMode.TROLLEYBUS,
    "troleibuz": VehicleMode.TROLLEYBUS,
    "bu": VehicleMode.BUS,
    "bus": VehicleMode.BUS,
    "autobuz": VehicleMode.BUS,
    "va": VehicleMode.VAPORETTO,
    "vaporetto": VehicleMode.VAPORETTO,
}


@dataclass(frozen=True, slots=True)
class _ClockObservation:
    implied_now: datetime | None
    since: datetime
    assessed_at: datetime
    freshness: Freshness
    skew_seconds: float | None
    advancing: bool  # this stop's clock has been seen to move forward
    suspect: bool  # it jumped or was stale, and has not advanced since


class LiveArrivalsProvider:
    """Fetch live arrivals and decide whether the feed's clock is really running.

    A single response can only be compared with the real clock. A frozen feed
    whose stale clock happens to match the current time passes that check, so
    the provider also remembers each stop's implied clock: if it stands still
    while real time advances past the tolerance, the feed is frozen.
    """

    def __init__(
        self,
        client: UpstreamClient,
        settings: Settings,
        network: NetworkIndex,
    ) -> None:
        self._client = client
        self._settings = settings
        self._network = network
        self._clocks: OrderedDict[str, _ClockObservation] = OrderedDict()
        self._last_stop: str | None = None
        self._last_problem_at: datetime | None = None
        self._last_advance_at: datetime | None = None
        self._zone = ZoneInfo(settings.timezone)

    async def fetch(self, stop_id: str) -> ProviderResult[tuple[LiveArrival, ...]]:
        raw, fetched_at = await self._client.get_json(
            self._settings.live_url,
            source="live arrivals",
            params={"stopid": stop_id},
            headers={"Referer": "https://live.stpt.ro/"},
        )
        result = parse_live_arrivals(
            raw,
            fetched_at=fetched_at,
            network=self._network,
            timezone=self._settings.timezone,
            clock_skew_tolerance_seconds=self._settings.live_clock_skew_tolerance_seconds,
        )
        return self._track_clock(stop_id, result)

    def _track_clock(
        self, stop_id: str, result: ProviderResult[tuple[LiveArrival, ...]]
    ) -> ProviderResult[tuple[LiveArrival, ...]]:
        if not result.value:
            return result
        fetched_at = result.fetched_at
        tolerance = self._settings.live_clock_skew_tolerance_seconds
        reading = (
            fetched_at + timedelta(seconds=result.skew_seconds)
            if result.skew_seconds is not None
            else None
        )
        previous = self._clocks.pop(stop_id, None)

        # ``anchor`` is the stop's feed clock reading and ``since`` when it was
        # first seen; both carry over while the clock stands still.
        anchor, since = reading, fetched_at
        advancing = jumped = suspect = False
        if previous is not None and (
            (fetched_at - previous.assessed_at).total_seconds() >= _COMPARABLE_SECONDS - tolerance
        ):
            # After half a day without a fetch, a time of day cannot tell a running
            # clock from a frozen one. A stop that was not fresh then stays suspect
            # unless other stops have shown the feed running since.
            suspect = (
                previous.suspect or previous.freshness is not Freshness.FRESH
            ) and not self._feed_recovered()
            previous = None
        if previous is not None:
            suspect = previous.suspect
            if previous.implied_now is None:
                pass  # nothing to compare with yet
            elif reading is None:
                # A response without a clock proves nothing; keep the old reading.
                anchor, since = previous.implied_now, previous.since
            else:
                moved = (reading - previous.implied_now).total_seconds()
                elapsed = (fetched_at - previous.since).total_seconds()
                wall_moved = (
                    self._wall(reading) - self._wall(previous.implied_now)
                ).total_seconds()
                wall_moved = (wall_moved + 43_200) % 86_400 - 43_200
                # Readings have minute resolution, and the median may step back a
                # minute as vehicles change. Comparing displayed times of day keeps
                # a frozen reading frozen when it resolves to another day or, across
                # a DST fold, to an instant an hour apart.
                if -60 <= wall_moved < 60:
                    anchor, since = previous.implied_now, previous.since
                    advancing = previous.advancing
                elif (
                    moved > 0
                    and abs(moved - elapsed) <= tolerance
                    and previous.freshness is not Freshness.STALE
                ):
                    advancing = True
                else:
                    # Backwards, by a different amount than real time passed, or away
                    # from a reading already judged stale (a swapped snapshot can
                    # match the clock by chance): no proof until it advances again.
                    jumped = True
        suspect = (suspect or jumped) and not advancing
        unchanged = (fetched_at - since).total_seconds()

        if result.stale:
            freshness = Freshness.STALE
        elif result.observed_at is None:
            freshness = Freshness.UNVERIFIED
        elif unchanged > tolerance:
            freshness = Freshness.STALE
            result = replace(
                result,
                stale=True,
                observed_at=None,
                warnings=(
                    *result.warnings,
                    f"Live predictions look frozen: the feed clock for this stop has not "
                    f"advanced for {unchanged / 60:.0f} min.",
                ),
            )
        elif suspect or (
            not advancing
            and self._last_problem_at is not None
            and (fetched_at - self._last_problem_at).total_seconds() < _FEED_SUSPICION_SECONDS
        ):
            freshness = Freshness.UNVERIFIED
            result = replace(
                result,
                observed_at=None,
                warnings=(
                    *result.warnings,
                    "Live predictions match the current time, but this stop's feed clock "
                    "jumped by a different amount than real time passed, so their freshness "
                    "cannot be verified."
                    if jumped
                    else "Live predictions match the current time, but this stop's feed clock has "
                    "not yet been seen to advance with real time while the feed recently "
                    "looked frozen or skewed, so their freshness cannot be verified.",
                ),
            )
        else:
            freshness = Freshness.FRESH
        if freshness is Freshness.STALE:
            self._last_problem_at = fetched_at
        elif freshness is Freshness.FRESH and advancing:
            self._last_advance_at = fetched_at

        self._clocks[stop_id] = _ClockObservation(
            implied_now=anchor,
            since=since,
            assessed_at=fetched_at,
            freshness=freshness,
            skew_seconds=result.skew_seconds,
            advancing=advancing and freshness is Freshness.FRESH,
            suspect=suspect,
        )
        while len(self._clocks) > self._settings.max_cache_entries:
            self._clocks.popitem(last=False)
        self._last_stop = stop_id
        return result

    def _wall(self, instant: datetime) -> datetime:
        return instant.astimezone(self._zone).replace(tzinfo=None)

    def _feed_recovered(self) -> bool:
        """Whether some stop's clock was seen advancing after the last problem."""
        return self._last_advance_at is not None and (
            self._last_problem_at is None or self._last_advance_at > self._last_problem_at
        )

    def feed_status(self, now: datetime) -> LiveFeedStatus | None:
        if self._last_stop is None or self._last_stop not in self._clocks:
            return None
        last = self._clocks[self._last_stop]
        recent: dict[Freshness, int] = {}
        for observation in self._clocks.values():
            if (now - observation.assessed_at).total_seconds() <= _RECENT_STOPS_SECONDS:
                recent[observation.freshness] = recent.get(observation.freshness, 0) + 1
        return LiveFeedStatus(
            stop_id=self._last_stop,
            assessed_at=last.assessed_at,
            freshness=last.freshness,
            skew_seconds=last.skew_seconds,
            clock_unchanged_seconds=max(0.0, (last.assessed_at - last.since).total_seconds()),
            recent_stops=recent,
        )


def parse_live_arrivals(
    raw: Any,
    *,
    fetched_at: datetime,
    network: NetworkIndex,
    timezone: str = "Europe/Bucharest",
    clock_skew_tolerance_seconds: float = DEFAULT_CLOCK_SKEW_TOLERANCE_SECONDS,
) -> ProviderResult[tuple[LiveArrival, ...]]:
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=UTC)
    if isinstance(raw, dict):
        raw = raw.get("arrivals", raw.get("data", raw.get("times")))
    if not isinstance(raw, list):
        raise StptError(
            ErrorCode.UPSTREAM_INVALID_RESPONSE,
            "live arrivals response is not a JSON array",
            retryable=True,
            upstream="live arrivals",
        )

    arrivals: list[LiveArrival] = []
    warnings: list[str] = []
    observations: list[tuple[str, int]] = []
    skipped = 0
    duplicates = 0
    seen: set[tuple[str, str, str | None, int, str | None]] = set()

    for item in raw:
        if not isinstance(item, dict):
            skipped += 1
            continue
        line = _text(item.get("line") or item.get("route"))
        if not line:
            skipped += 1
            continue
        destination = _text(item.get("headsign") or item.get("destination"))
        mode = _mode_for_item(item, line, network)
        vehicle_id = _optional_text(item.get("vehicleId") or item.get("vehicle_id"))
        entries = _arrival_entries(item)
        if not entries:
            skipped += 1
            continue
        for feed_time, eta in entries:
            key = (line, destination, feed_time, eta, vehicle_id)
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            eta_minutes = max(0, eta)
            arrivals.append(
                LiveArrival(
                    line=line,
                    destination=destination,
                    mode=mode,
                    eta_minutes=eta_minutes,
                    arrival_at=fetched_at + timedelta(minutes=eta_minutes),
                    vehicle_id=vehicle_id,
                    feed_time=feed_time,
                )
            )
            if feed_time is not None:
                observations.append((feed_time, eta_minutes))

    if skipped:
        warnings.append(f"Ignored {skipped} malformed live arrival record(s).")
    if duplicates:
        warnings.append(f"Removed {duplicates} duplicate live arrival record(s).")

    observed_at, stale, freshness_warnings, skew_seconds = _assess_freshness(
        observations,
        has_predictions=bool(arrivals),
        fetched_at=fetched_at,
        zone=ZoneInfo(timezone),
        tolerance_seconds=clock_skew_tolerance_seconds,
    )
    warnings.extend(freshness_warnings)

    arrivals.sort(key=lambda item: (item.eta_minutes, item.line.casefold(), item.destination))
    return ProviderResult(
        value=tuple(arrivals),
        fetched_at=fetched_at,
        warnings=tuple(warnings),
        complete=not skipped,
        stale=stale,
        observed_at=observed_at,
        skew_seconds=skew_seconds,
    )


def _assess_freshness(
    observations: list[tuple[str, int]],
    *,
    has_predictions: bool,
    fetched_at: datetime,
    zone: ZoneInfo,
    tolerance_seconds: float,
) -> tuple[datetime | None, bool, tuple[str, ...], float | None]:
    """Compare the feed's own clock with the fetch time.

    The arrivals payload has no observation timestamp, but each prediction
    carries both a display time and the minutes remaining. ``display_time -
    minutes`` is therefore the instant the feed believed it was, derived from
    upstream fields rather than invented. A frozen snapshot cannot track the
    wall clock, so its implied instant diverges from ``fetched_at`` and the
    whole response is rejected instead of being labelled live.
    """

    implied: list[datetime] = []
    for feed_time, eta in observations:
        value = _implied_now(feed_time, eta, fetched_at=fetched_at, zone=zone)
        if value is not None:
            implied.append(value)
    skew_seconds: float | None = None
    if not observations:
        if not has_predictions:
            return None, False, (), None
        return (
            None,
            False,
            ("Live predictions carry no clock reference, so their freshness cannot be verified.",),
            None,
        )
    if not implied:
        return (
            None,
            False,
            ("Live predictions carry no clock reference, so their freshness cannot be verified.",),
            None,
        )

    implied.sort()
    anchor = implied[len(implied) // 2]
    skew_seconds = (anchor - fetched_at).total_seconds()
    if abs(skew_seconds) <= tolerance_seconds:
        return anchor, False, (), skew_seconds

    local_feed = anchor.astimezone(zone).strftime("%H:%M")
    local_now = fetched_at.astimezone(zone).strftime("%H:%M")
    minutes = round(skew_seconds / 60)
    return (
        None,
        True,
        (
            "Live predictions look stale: the feed implies "
            f"{local_feed} local while the current time is {local_now} "
            f"({minutes:+d} min).",
        ),
        skew_seconds,
    )


def _implied_now(
    feed_time: str,
    eta: int,
    *,
    fetched_at: datetime,
    zone: ZoneInfo,
) -> datetime | None:
    match = _TIME_RE.match(feed_time.strip())
    if not match:
        return None
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    day_offset, hour_of_day = divmod(hour, 24)
    service_date: date = fetched_at.astimezone(zone).date()
    # Resolve the wall time on adjacent local dates and both sides of a DST
    # fold, so midnight wraps and the repeated autumn hour get the right offset.
    candidates = [
        datetime.combine(
            service_date + timedelta(days=day_offset + shift),
            time_type(hour_of_day, minute, fold=fold),
            tzinfo=zone,
        ).astimezone(UTC)
        - timedelta(minutes=max(0, eta))
        for shift in (-1, 0, 1)
        for fold in (0, 1)
    ]
    return min(candidates, key=lambda candidate: abs((candidate - fetched_at).total_seconds()))


def _arrival_entries(item: dict[str, Any]) -> list[tuple[str | None, int]]:
    entries: list[tuple[str | None, int]] = []
    raw_times = item.get("times")
    if isinstance(raw_times, str):
        raw_times = [raw_times]
    if isinstance(raw_times, list):
        for value in raw_times:
            parsed = _parse_time_entry(value)
            if parsed is not None:
                entries.append(parsed)
    if entries:
        return entries

    raw_minutes = item.get("minutes")
    if isinstance(raw_minutes, (int, float)):
        return [(None, max(0, int(raw_minutes)))]
    if isinstance(raw_minutes, list):
        for value in raw_minutes:
            if isinstance(value, (int, float)):
                entries.append((None, max(0, int(value))))
    return entries


def _parse_time_entry(value: Any) -> tuple[str | None, int] | None:
    if isinstance(value, dict):
        raw_time = value.get("time")
        raw_minutes = value.get("minutes")
        arrival_time = _valid_time(raw_time)
        if isinstance(raw_minutes, (int, float)):
            return arrival_time, max(0, int(raw_minutes))
        if arrival_time is not None:
            return arrival_time, 0
        return None
    if not isinstance(value, str):
        return None
    parts = value.split("|", 1)
    arrival_time = _valid_time(parts[0])
    if len(parts) == 2:
        try:
            return arrival_time, max(0, int(parts[1].strip()))
        except ValueError:
            return None
    return None


def _valid_time(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    match = _TIME_RE.match(value.strip())
    if not match:
        return None
    return f"{int(match.group('hour')):02d}:{match.group('minute')}"


def _mode_for_item(item: dict[str, Any], line: str, network: NetworkIndex) -> VehicleMode:
    raw_type = _text(item.get("type") or item.get("vehicle_type")).casefold()
    if raw_type in _TYPE_MODES:
        return _TYPE_MODES[raw_type]
    route = network.get_route(line)
    return route.mode if route is not None else VehicleMode.UNKNOWN


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _optional_text(value: object) -> str | None:
    text = _text(value)
    return text or None
