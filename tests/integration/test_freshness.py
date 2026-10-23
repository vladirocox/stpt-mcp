"""Live arrivals are labelled live only after a freshness check against the real clock.

The clock here is the user-reported reference instant: 2026-10-23 12:55 in
Timișoara, when Google Maps showed line 14 at Piața Mărăști at 12:57 and the
upstream feed was frozen at an implied 16:00.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from stpt_mcp.models import DataSource, Freshness, Status
from stpt_mcp.providers.schedule import Timetable
from tests.support import (
    LIVE_URL,
    FakeSTPT,
    MutableClock,
    ServiceFactory,
    load_json,
    make_test_settings,
)

BUCHAREST = ZoneInfo("Europe/Bucharest")
REFERENCE_NOW = datetime(2026, 10, 23, 9, 55, tzinfo=UTC)  # 12:55 local


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock(REFERENCE_NOW)


@pytest.fixture
def expired_timetable(timetable: Timetable) -> Timetable:
    return replace(timetable, end=date(2026, 10, 21))


def line_14(minutes: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "route": "14",
            "type": "tb",
            "destination": "I. Ionescu de la Brad",
            "vehicleId": None,
            "times": minutes,
        }
    ]


def running_feed(clock: MutableClock, instants: list[datetime]) -> Any:
    """A healthy feed: fixed arrival instants whose countdown follows the clock."""

    def payload(_request: httpx.Request) -> Any:
        now = clock.wall()
        times = [
            f"{instant.astimezone(BUCHAREST):%H:%M}|"
            f"{max(0, math.ceil((instant - now).total_seconds() / 60))}"
            for instant in instants
        ]
        return line_14(times)

    return payload


def minutes_until(arrival_time: str, now: datetime) -> float:
    local = now.astimezone(BUCHAREST)
    hour, minute = (int(part) for part in arrival_time.split(":"))
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return (candidate - local).total_seconds() / 60


async def test_reference_stop_135_uses_the_timetable_not_the_frozen_feed(
    service_factory: ServiceFactory, fake: FakeSTPT
) -> None:
    fake.set_json(LIVE_URL, load_json("live_135_frozen.json"))
    service, _ = await service_factory(fake=fake)
    result = await service.get_arrivals("135", line="14")
    assert result.status is Status.OK
    assert result.provenance is not None
    assert result.provenance.source is DataSource.SCHEDULE
    assert result.provenance.fallback_reason == "live_predictions_stale"
    assert result.provenance.freshness is Freshness.UNVERIFIED
    assert all(arrival.live is False for arrival in result.arrivals)
    # The frozen 10/26/32/33-minute predictions would read 13:05, 13:21,
    # 13:27 and 13:28. The official timetable says 12:59, then every 15 min.
    assert [(arrival.arrival_time, arrival.eta_minutes) for arrival in result.arrivals[:4]] == [
        ("12:59", 4),
        ("13:14", 19),
        ("13:29", 34),
        ("13:44", 49),
    ]
    assert any(
        "16:00" in warning and "+185 min" in warning for warning in result.provenance.warnings
    )


async def test_running_feed_counts_down_and_stays_consistent(
    service_factory: ServiceFactory, fake: FakeSTPT, clock: MutableClock
) -> None:
    first = REFERENCE_NOW + timedelta(minutes=14)
    fake.set_json_callback(LIVE_URL, running_feed(clock, [first, first + timedelta(minutes=15)]))
    service, _ = await service_factory(fake=fake)
    etas: list[int] = []
    # Twelve minutes in two-minute steps: far beyond the skew tolerance, so
    # the frozen-clock detector must not mistake a running feed for a frozen one.
    for _ in range(7):
        result = await service.get_arrivals("135", line="14")
        assert result.status is Status.OK
        assert result.provenance is not None
        assert result.provenance.source is DataSource.LIVE
        assert result.provenance.freshness is Freshness.FRESH
        assert result.provenance.observed_at == clock.wall()
        assert all(arrival.live for arrival in result.arrivals)
        for arrival in result.arrivals:
            assert arrival.arrival_time is not None
            assert abs(minutes_until(arrival.arrival_time, clock.wall()) - arrival.eta_minutes) <= 1
        etas.append(result.arrivals[0].eta_minutes)
        clock.advance(120)
    assert etas == [14, 12, 10, 8, 6, 4, 2]
    status = await service.get_status()
    assert status.live_feed is not None
    assert status.live_feed.freshness is Freshness.FRESH
    assert status.live_feed.clock_unchanged_seconds == 0


async def test_frozen_feed_is_caught_when_its_clock_matches_real_time(
    service_factory: ServiceFactory, fake: FakeSTPT, clock: MutableClock
) -> None:
    # Frozen at an implied 13:05. At 12:55 the skew check rejects it; at 13:05
    # the skew is zero, so only the "clock never advances" check can catch it.
    fake.set_json(LIVE_URL, line_14(["13:15|10", "13:31|26"]))
    service, _ = await service_factory(fake=fake)
    early = await service.get_arrivals("135", line="14")
    assert early.provenance is not None
    assert early.provenance.fallback_reason == "live_predictions_stale"

    clock.advance(600)
    result = await service.get_arrivals("135", line="14")
    assert result.provenance is not None
    assert result.provenance.source is DataSource.SCHEDULE
    assert result.provenance.fallback_reason == "live_predictions_stale"
    assert all(arrival.live is False for arrival in result.arrivals)
    assert result.arrivals[0].arrival_time == "13:14"
    assert any("look frozen" in warning for warning in result.provenance.warnings)
    status = await service.get_status()
    assert status.live_feed is not None
    assert status.live_feed.freshness is Freshness.STALE
    assert status.live_feed.skew_seconds == 0
    assert status.live_feed.clock_unchanged_seconds == 600


async def test_first_sight_of_a_stop_is_unverified_while_the_feed_is_broken(
    service_factory: ServiceFactory, fake: FakeSTPT, clock: MutableClock
) -> None:
    frozen = load_json("live_135_frozen.json")
    stop_135: dict[str, Any] = {"payload": line_14(["13:05|10"])}  # implies 12:55: matches

    def payload(request: httpx.Request) -> Any:
        return stop_135["payload"] if request.url.params.get("stopid") == "135" else frozen

    fake.set_json_callback(LIVE_URL, payload)
    service, _ = await service_factory(fake=fake)
    await service.get_arrivals("85")  # frozen and skewed: the feed is broken

    first = await service.get_arrivals("135", line="14")
    assert first.provenance is not None
    assert first.provenance.source is DataSource.SCHEDULE
    assert first.provenance.fallback_reason == "live_predictions_unverified"
    assert first.provenance.freshness is Freshness.UNVERIFIED
    assert any("not yet been seen to advance" in w for w in first.provenance.warnings)

    clock.advance(120)
    stop_135["payload"] = line_14(["13:05|8"])  # the clock moved: 12:57
    second = await service.get_arrivals("135", line="14")
    assert second.provenance is not None
    assert second.provenance.source is DataSource.LIVE
    assert second.provenance.freshness is Freshness.FRESH
    assert [(arrival.eta_minutes, arrival.live) for arrival in second.arrivals] == [(8, True)]


async def test_a_frozen_clock_read_a_day_later_is_not_progress(
    service_factory: ServiceFactory, fake: FakeSTPT, clock: MutableClock
) -> None:
    # Frozen at an implied 16:00 for days. On the evening of day 1 it reads as
    # day-1 16:00; just before 16:00 on day 2 the nearest reading is day-2 16:00,
    # a 24 h jump that is not real progress of the feed clock.
    fake.set_json(LIVE_URL, load_json("live_135_frozen.json"))
    service, _ = await service_factory(fake=fake)
    clock.advance(timedelta(hours=7, minutes=5).total_seconds())  # day 1, 20:00
    evening = await service.get_arrivals("135", line="14")
    assert evening.provenance is not None
    assert evening.provenance.fallback_reason == "live_predictions_stale"

    clock.advance(timedelta(hours=19, minutes=59).total_seconds())  # day 2, 15:59
    result = await service.get_arrivals("135", line="14")
    assert result.provenance is not None
    assert result.provenance.source is DataSource.SCHEDULE
    assert result.provenance.fallback_reason == "live_predictions_unverified"
    assert all(arrival.live is False for arrival in result.arrivals)
    assert any("not yet been seen to advance" in w for w in result.provenance.warnings)

    # The suspicion persists while the clock stands still, then turns stale.
    for seconds, reason in ((60, "live_predictions_unverified"), (150, "live_predictions_stale")):
        clock.advance(seconds)
        later = await service.get_arrivals("135", line="14")
        assert later.provenance is not None
        assert later.provenance.source is DataSource.SCHEDULE
        assert later.provenance.fallback_reason == reason


async def test_a_clock_jump_stays_suspect_until_it_advances(
    service_factory: ServiceFactory, fake: FakeSTPT, clock: MutableClock
) -> None:
    # Every reading stays inside the skew tolerance; only the clock's movement
    # between fetches gives the jump away.
    stop_135: dict[str, Any] = {"payload": line_14(["13:02|10"])}  # implies 12:52
    fake.set_json_callback(LIVE_URL, lambda _request: stop_135["payload"])
    service, _ = await service_factory(fake=fake)
    first = await service.get_arrivals("135")
    assert first.provenance is not None
    assert first.provenance.source is DataSource.LIVE

    clock.advance(60)  # 12:56; the feed clock moves six minutes to 12:58
    stop_135["payload"] = line_14(["13:08|10"])
    jumped = await service.get_arrivals("135")
    assert jumped.provenance is not None
    assert jumped.provenance.fallback_reason == "live_predictions_unverified"
    assert any("jumped" in warning for warning in jumped.provenance.warnings)

    clock.advance(60)  # 12:57; it stands still: still suspect, not fresh
    frozen = await service.get_arrivals("135")
    assert frozen.provenance is not None
    assert frozen.provenance.fallback_reason == "live_predictions_unverified"

    clock.advance(120)  # 12:59; it advances with real time again: verified
    stop_135["payload"] = line_14(["13:10|10"])  # implies 13:00
    recovered = await service.get_arrivals("135")
    assert recovered.provenance is not None
    assert recovered.provenance.source is DataSource.LIVE
    assert recovered.provenance.freshness is Freshness.FRESH


async def test_a_whole_day_between_readings_is_not_progress(
    service_factory: ServiceFactory, fake: FakeSTPT, clock: MutableClock
) -> None:
    frozen = load_json("live_135_frozen.json")  # implied 16:00 at every stop
    skewed = line_14(["23:10|10"])  # implied 23:00, far from the clock

    def payload(request: httpx.Request) -> Any:
        return frozen if request.url.params.get("stopid") == "135" else skewed

    fake.set_json_callback(LIVE_URL, payload)
    service, _ = await service_factory(fake=fake)
    clock.advance(timedelta(hours=3, minutes=5).total_seconds())  # day 1, 16:00
    first = await service.get_arrivals("135", line="14")
    assert first.provenance is not None
    # A first sight that matches the clock cannot be told apart from live data.
    assert first.provenance.source is DataSource.LIVE

    clock.advance(timedelta(hours=23, minutes=55).total_seconds())  # day 2, 15:55
    await service.get_arrivals("85")  # another stop looks broken
    clock.advance(180)  # 15:58: the same frozen 16:00 is 24 h "later"
    result = await service.get_arrivals("135", line="14")
    assert result.provenance is not None
    assert result.provenance.source is DataSource.SCHEDULE
    assert result.provenance.fallback_reason == "live_predictions_unverified"


async def test_a_one_minute_step_back_is_not_a_jump(
    service_factory: ServiceFactory, fake: FakeSTPT, clock: MutableClock
) -> None:
    stop_135: dict[str, Any] = {"payload": line_14(["13:06|10", "13:07|10", "13:08|10"])}
    fake.set_json_callback(LIVE_URL, lambda _request: stop_135["payload"])
    service, _ = await service_factory(
        fake=fake,
        settings=make_test_settings(arrival_ttl_seconds=20, arrival_stale_ttl_seconds=900),
    )
    first = await service.get_arrivals("135")
    assert first.provenance is not None
    assert first.provenance.source is DataSource.LIVE  # median implies 12:57
    clock.advance(30)
    # A different vehicle mix: the median now implies 12:56, one minute back.
    stop_135["payload"] = line_14(["13:05|10", "13:06|10", "13:07|10"])
    second = await service.get_arrivals("135")
    assert second.provenance is not None
    assert second.provenance.source is DataSource.LIVE
    assert second.provenance.freshness is Freshness.FRESH


async def test_a_frozen_reading_stays_frozen_across_the_dst_fold(
    service_factory: ServiceFactory, fake: FakeSTPT, clock: MutableClock
) -> None:
    # 2026-10-25: local 03:30 occurs twice, at 00:30Z (EEST) and 01:30Z (EET).
    fake.set_json(LIVE_URL, line_14(["03:35|5"]))  # frozen at 03:30 local
    service, _ = await service_factory(fake=fake)
    clock.advance((datetime(2026, 10, 25, 0, 31, tzinfo=UTC) - REFERENCE_NOW).total_seconds())
    first = await service.get_arrivals("135", line="14")
    assert first.provenance is not None
    assert first.provenance.source is DataSource.LIVE  # first sight, matches the clock

    clock.advance(3540)  # 01:30Z: the same bytes now also match the clock
    result = await service.get_arrivals("135", line="14")
    assert result.provenance is not None
    assert result.provenance.source is DataSource.SCHEDULE
    assert result.provenance.fallback_reason == "live_predictions_stale"
    assert any("look frozen" in warning for warning in result.provenance.warnings)


async def test_a_response_without_a_clock_keeps_the_stop_suspect(
    service_factory: ServiceFactory, fake: FakeSTPT, clock: MutableClock
) -> None:
    stop_135: dict[str, Any] = {"payload": line_14(["11:56|1"])}  # 11:55: an hour behind
    fake.set_json_callback(LIVE_URL, lambda _request: stop_135["payload"])
    service, _ = await service_factory(fake=fake)
    stale = await service.get_arrivals("135")
    assert stale.provenance is not None
    assert stale.provenance.fallback_reason == "live_predictions_stale"

    clock.advance(700)  # 13:06:40; the clock jumps to 13:06
    stop_135["payload"] = line_14(["13:07|1"])
    jumped = await service.get_arrivals("135")
    assert jumped.provenance is not None
    assert jumped.provenance.fallback_reason == "live_predictions_unverified"

    clock.advance(60)  # no display time at all
    stop_135["payload"] = [{"route": "14", "destination": "I. Ionescu de la Brad", "minutes": 5}]
    await service.get_arrivals("135")

    clock.advance(60)  # the jumped reading again, never seen advancing
    stop_135["payload"] = line_14(["13:07|1"])
    result = await service.get_arrivals("135")
    assert result.provenance is not None
    assert result.provenance.source is DataSource.SCHEDULE
    assert result.provenance.fallback_reason == "live_predictions_unverified"


async def test_an_old_suspicion_clears_once_the_feed_is_seen_running(
    service_factory: ServiceFactory, fake: FakeSTPT, clock: MutableClock
) -> None:
    frozen = load_json("live_135_frozen.json")
    feeds: dict[str, Any] = {"135": frozen}
    fake.set_json_callback(
        LIVE_URL, lambda request: feeds.get(request.url.params.get("stopid", ""), [])
    )
    service, _ = await service_factory(fake=fake)
    stale = await service.get_arrivals("135")
    assert stale.provenance is not None
    assert stale.provenance.fallback_reason == "live_predictions_stale"

    # The feed recovers; another stop is seen advancing with real time.
    clock.advance(timedelta(hours=1).total_seconds())
    other = clock.wall() + timedelta(minutes=20)
    feeds["85"] = running_feed(clock, [other])(None)
    await service.get_arrivals("85")
    clock.advance(120)
    feeds["85"] = running_feed(clock, [other])(None)
    running = await service.get_arrivals("85")
    assert running.provenance is not None
    assert running.provenance.freshness is Freshness.FRESH

    # Stop 135 is next asked about half a day later, now healthy.
    clock.advance(timedelta(hours=12).total_seconds())
    feeds["135"] = running_feed(clock, [clock.wall() + timedelta(minutes=9)])(None)
    result = await service.get_arrivals("135")
    assert result.provenance is not None
    assert result.provenance.source is DataSource.LIVE
    assert result.provenance.freshness is Freshness.FRESH


async def test_a_swapped_snapshot_after_a_freeze_must_advance_first(
    service_factory: ServiceFactory, fake: FakeSTPT, clock: MutableClock
) -> None:
    first = REFERENCE_NOW + timedelta(minutes=10)
    stop_135: dict[str, Any] = {"payload": running_feed(clock, [first])(None)}
    fake.set_json_callback(LIVE_URL, lambda _request: stop_135["payload"])
    service, _ = await service_factory(fake=fake)
    for _ in range(2):  # running
        stop_135["payload"] = running_feed(clock, [first])(None)
        assert (await service.get_arrivals("135")).provenance.source is DataSource.LIVE  # type: ignore[union-attr]
        clock.advance(60)
    for _ in range(4):  # frozen at the last reading, then stale
        frozen = await service.get_arrivals("135")
        clock.advance(60)
    assert frozen.provenance is not None
    assert frozen.provenance.fallback_reason == "live_predictions_stale"

    # Another frozen snapshot whose clock happens to match now.
    stop_135["payload"] = line_14(
        [f"{(clock.wall() + timedelta(minutes=3)).astimezone(BUCHAREST):%H:%M}|3"]
    )
    swapped = await service.get_arrivals("135")
    assert swapped.provenance is not None
    assert swapped.provenance.source is DataSource.SCHEDULE
    assert swapped.provenance.fallback_reason == "live_predictions_unverified"

    clock.advance(60)  # it really runs: the next reading advanced one minute
    stop_135["payload"] = line_14(
        [f"{(clock.wall() + timedelta(minutes=3)).astimezone(BUCHAREST):%H:%M}|3"]
    )
    recovered = await service.get_arrivals("135")
    assert recovered.provenance is not None
    assert recovered.provenance.source is DataSource.LIVE


async def test_a_frozen_stop_polled_all_day_is_never_live(
    service_factory: ServiceFactory, fake: FakeSTPT, clock: MutableClock
) -> None:
    # Stop 135 is frozen at an implied 12:38; stop 85 runs. Polled every 20
    # minutes for 26 hours, the frozen clock matches real time once a day.
    feeds: dict[str, Any] = {"135": line_14(["12:48|10"])}
    fake.set_json_callback(LIVE_URL, lambda request: feeds[request.url.params.get("stopid", "")])
    service, _ = await service_factory(fake=fake)
    # Fetch on :00/:20/:40, so one fetch lands just after the frozen time of day
    # flips to the next date (00:40) and another twelve hours later, at 12:40.
    clock.advance(300)
    for _ in range(26 * 3):
        feeds["85"] = running_feed(clock, [clock.wall() + timedelta(minutes=7)])(None)
        await service.get_arrivals("85")
        result = await service.get_arrivals("135", line="14")
        assert result.provenance is not None
        assert result.provenance.source is DataSource.SCHEDULE, clock.wall()
        clock.advance(1200)


async def test_predictions_without_a_clock_are_never_live(
    service_factory: ServiceFactory, fake: FakeSTPT
) -> None:
    fake.set_json(LIVE_URL, [{"route": "14", "destination": "I. Ionescu de la Brad", "minutes": 4}])
    service, _ = await service_factory(fake=fake)
    result = await service.get_arrivals("135", line="14")
    assert result.provenance is not None
    assert result.provenance.source is DataSource.SCHEDULE
    assert result.provenance.fallback_reason == "live_predictions_unverified"
    assert result.provenance.freshness is Freshness.UNVERIFIED
    assert all(arrival.live is False for arrival in result.arrivals)
    assert any("no clock reference" in warning for warning in result.provenance.warnings)


async def test_unverified_predictions_are_withheld_without_a_fallback(
    service_factory: ServiceFactory, fake: FakeSTPT, expired_timetable: Timetable
) -> None:
    fake.set_json(LIVE_URL, [{"route": "14", "destination": "I. Ionescu de la Brad", "minutes": 4}])
    service, _ = await service_factory(fake=fake, timetable_override=expired_timetable)
    result = await service.get_arrivals("135", line="14")
    assert result.status is Status.PARTIAL
    assert result.count == 0
    assert result.arrivals == []
    assert result.error is not None
    assert result.error.code.value == "data_stale"
    assert "could not be verified" in result.error.message
    assert result.provenance is not None
    assert result.provenance.is_stale is False
    assert result.provenance.freshness is Freshness.UNVERIFIED
    assert any("withheld" in warning for warning in result.provenance.warnings)


async def test_frozen_reference_feed_is_withheld_without_a_fallback(
    service_factory: ServiceFactory, fake: FakeSTPT, expired_timetable: Timetable
) -> None:
    fake.set_json(LIVE_URL, load_json("live_135_frozen.json"))
    service, _ = await service_factory(fake=fake, timetable_override=expired_timetable)
    result = await service.get_arrivals("135", line="14")
    assert result.status is Status.PARTIAL
    assert result.count == 0
    assert result.arrivals == []
    assert result.error is not None
    assert result.error.code.value == "data_stale"
    assert result.provenance is not None
    assert result.provenance.is_stale is True
    assert result.provenance.freshness is Freshness.STALE
