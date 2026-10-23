"""Transit service orchestration, fallback policy, and provenance."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from stpt_mcp import __version__
from stpt_mcp.cache import BoundedTTLCache, CacheResult, CacheState
from stpt_mcp.config import Settings
from stpt_mcp.errors import ErrorCode, StptError
from stpt_mcp.models import (
    AlertPeriod,
    AlertsResult,
    Arrival,
    ArrivalsResult,
    CacheStats,
    DataSource,
    Direction,
    ErrorInfo,
    Freshness,
    Provenance,
    RoutesResult,
    ServiceStatus,
    Status,
    StopResult,
    StopSearchResult,
    TransitAlert,
    Vehicle,
    VehicleMode,
    VehiclesResult,
)
from stpt_mcp.network import NetworkIndex, StopRecord
from stpt_mcp.providers.alerts import AlertsProvider
from stpt_mcp.providers.base import (
    AlertRecord,
    LiveArrival,
    ProviderResult,
    ScheduleDeparture,
    VehicleRecord,
)
from stpt_mcp.providers.live import LiveArrivalsProvider
from stpt_mcp.providers.schedule import ScheduleProvider, Timetable
from stpt_mcp.providers.vehicles import VehiclesProvider
from stpt_mcp.upstream import UpstreamClient


@dataclass(frozen=True, slots=True)
class ArrivalRecord:
    line: str
    destination: str
    direction: Direction | None
    mode: VehicleMode
    live: bool
    vehicle_id: str | None = None
    shape_id: str | None = None
    departure_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ArrivalsSnapshot:
    records: tuple[ArrivalRecord, ...]
    source: DataSource
    fetched_at: datetime
    observed_at: datetime | None = None
    fallback_reason: str | None = None
    warnings: tuple[str, ...] = ()
    partial: bool = False
    stale: bool = False
    error: StptError | None = None


@dataclass(frozen=True, slots=True)
class ScheduleSnapshot:
    departures: tuple[ScheduleDeparture, ...]
    fetched_at: datetime
    warnings: tuple[str, ...] = ()
    partial: bool = False
    stale: bool = False
    error: StptError | None = None


@dataclass(frozen=True, slots=True)
class AlertsSnapshot:
    alerts: tuple[AlertRecord, ...]
    fetched_at: datetime
    warnings: tuple[str, ...] = ()
    partial: bool = False


@dataclass(frozen=True, slots=True)
class VehiclesSnapshot:
    vehicles: tuple[VehicleRecord, ...]
    fetched_at: datetime
    warnings: tuple[str, ...] = ()
    partial: bool = False


class TransitService:
    def __init__(
        self,
        settings: Settings,
        *,
        network: NetworkIndex | None = None,
        timetable: Timetable | None = None,
        upstream: UpstreamClient | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self.network = network or NetworkIndex()
        self._zone = ZoneInfo(settings.timezone)
        self._clock = clock
        self._wall_clock = wall_clock or (lambda: datetime.now(UTC))
        self._upstream = upstream
        self._timetable = timetable
        self._live: LiveArrivalsProvider | None = None
        self._schedule: ScheduleProvider | None = None
        self._alerts: AlertsProvider | None = None
        self._vehicles: VehiclesProvider | None = None
        self._ready = False
        self._closed = False
        self._started_at: datetime | None = None

        self._arrivals_cache: BoundedTTLCache[str, ArrivalsSnapshot] = BoundedTTLCache(
            "arrivals",
            ttl_seconds=settings.arrival_ttl_seconds,
            stale_ttl_seconds=settings.arrival_stale_ttl_seconds,
            max_entries=settings.max_cache_entries,
            clock=clock,
            wall_clock=self._utc_now,
        )
        self._schedule_cache: BoundedTTLCache[tuple[str, str], ScheduleSnapshot] = BoundedTTLCache(
            "schedules",
            ttl_seconds=settings.schedule_ttl_seconds,
            stale_ttl_seconds=settings.schedule_stale_ttl_seconds,
            max_entries=settings.max_cache_entries,
            clock=clock,
            wall_clock=self._utc_now,
        )
        self._alerts_cache: BoundedTTLCache[str, AlertsSnapshot] = BoundedTTLCache(
            "alerts",
            ttl_seconds=settings.alerts_ttl_seconds,
            stale_ttl_seconds=settings.alerts_stale_ttl_seconds,
            max_entries=1,
            clock=clock,
            wall_clock=self._utc_now,
        )
        self._vehicles_cache: BoundedTTLCache[str, VehiclesSnapshot] = BoundedTTLCache(
            "vehicles",
            ttl_seconds=settings.vehicles_ttl_seconds,
            stale_ttl_seconds=settings.vehicles_stale_ttl_seconds,
            max_entries=1,
            clock=clock,
            wall_clock=self._utc_now,
        )

    async def start(self) -> None:
        if self._ready:
            return
        if self._closed:
            raise RuntimeError("service cannot be restarted after close")
        if self._upstream is None:
            self._upstream = UpstreamClient(self.settings, wall_clock=self._utc_now)
        self._live = LiveArrivalsProvider(self._upstream, self.settings, self.network)
        self._schedule = ScheduleProvider(self.settings, self.network, self._timetable)
        self._alerts = AlertsProvider(self._upstream, self.settings, self.network)
        self._vehicles = VehiclesProvider(self._upstream, self.settings, self.network)
        self._started_at = self._utc_now()
        self._ready = True

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._ready = False
        for cache in (
            self._arrivals_cache,
            self._schedule_cache,
            self._alerts_cache,
            self._vehicles_cache,
        ):
            await cache.close()
        if self._upstream is not None:
            await self._upstream.close()

    def _utc_now(self) -> datetime:
        value = self._wall_clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _require_ready(self) -> None:
        if not self._ready:
            raise StptError(
                ErrorCode.NOT_READY,
                "Transit service is not ready.",
                retryable=True,
            )

    @property
    def ready(self) -> bool:
        return self._ready

    # Discovery ---------------------------------------------------------

    def search_stops(self, query: str, limit: int = 10) -> StopSearchResult:
        if not query.strip():
            return StopSearchResult(
                status=Status.ERROR,
                error=_error_info(StptError(ErrorCode.INVALID_ARGUMENT, "query must not be empty")),
                query=query,
                count=0,
            )
        if not 1 <= limit <= 50:
            return StopSearchResult(
                status=Status.ERROR,
                error=_error_info(
                    StptError(ErrorCode.INVALID_ARGUMENT, "limit must be between 1 and 50")
                ),
                query=query,
                count=0,
            )
        stops = self.network.search_stops(query, limit=limit)
        provenance, status, error = self._network_context()
        return StopSearchResult(
            status=status,
            error=error,
            provenance=provenance,
            query=query,
            count=len(stops),
            stops=stops,
        )

    def get_stop(self, stop_id: str) -> StopResult:
        try:
            stop = self.network.require_stop(stop_id)
        except StptError as exc:
            return StopResult(status=Status.ERROR, error=_error_info(exc))
        provenance, status, error = self._network_context()
        return StopResult(
            status=status,
            error=error,
            provenance=provenance,
            stop=self.network.stop_info(stop),
        )

    def list_routes(self, mode: VehicleMode | None = None) -> RoutesResult:
        routes = self.network.list_routes(mode=mode)
        provenance, status, error = self._network_context()
        return RoutesResult(
            status=status,
            error=error,
            provenance=provenance,
            count=len(routes),
            routes=routes,
        )

    def _network_context(self) -> tuple[Provenance, Status, ErrorInfo | None]:
        now = self._utc_now()
        generated = self.network.snapshot.generated_at
        age = max(0.0, (now - generated).total_seconds())
        warnings: list[str] = []
        error: ErrorInfo | None = None
        is_stale = False
        service_start = self.network.snapshot.service_range.get("start", "")
        service_end = self.network.snapshot.service_range.get("end", "")
        today = now.astimezone(self._zone).strftime("%Y%m%d")
        if service_end and today > service_end:
            is_stale = True
            message = (
                f"Bundled network snapshot expired after {service_end}; "
                "stop/routes may have changed."
            )
            warnings.append(message)
            error = _error_info(
                StptError(
                    ErrorCode.NETWORK_STALE,
                    message,
                    retryable=False,
                    upstream="network",
                )
            )
        elif service_start and today < service_start:
            warnings.append(f"Bundled network snapshot starts on {service_start}.")
        provenance = Provenance(
            source=DataSource.NETWORK,
            fetched_at=generated,
            observed_at=None,
            is_stale=is_stale,
            data_age_seconds=age,
            warnings=warnings,
        )
        return provenance, Status.PARTIAL if is_stale else Status.OK, error

    # Arrivals ----------------------------------------------------------

    async def get_arrivals(
        self,
        stop_id: str,
        *,
        line: str | None = None,
        limit: int = 10,
    ) -> ArrivalsResult:
        if not 1 <= limit <= 50:
            return _arrival_error(
                stop_id,
                stop_id,
                StptError(ErrorCode.INVALID_ARGUMENT, "limit must be between 1 and 50"),
                line,
            )
        try:
            stop = self.network.require_stop(stop_id)
            canonical_line = self.network.require_route(line).line if line else None
        except StptError as exc:
            return _arrival_error(stop_id, stop_id, exc, line)
        try:
            self._require_ready()
            result = await self._arrivals_cache.get_or_load(
                stop.stop_id,
                lambda: self._load_arrivals(stop),
            )
        except StptError as exc:
            return _arrival_error(stop.stop_id, stop.name, exc, line)
        snapshot = result.value
        now = self._utc_now()
        is_stale = snapshot.stale or result.state is CacheState.STALE
        arrivals, filtered = self._materialize_arrivals(
            snapshot,
            now=now,
            line=canonical_line,
            limit=limit,
            stale=is_stale,
        )
        warnings = list(snapshot.warnings)
        if result.state is CacheState.STALE:
            warnings.append("Served last-known-good arrivals because a refresh failed.")
        status = Status.OK
        error: StptError | None = None
        if result.state is CacheState.STALE:
            error = _coerce_stpt_error(result.error, "arrivals refresh failed")
            status = Status.PARTIAL if arrivals else Status.ERROR
        elif snapshot.partial:
            status = Status.PARTIAL
            error = snapshot.error
        if not arrivals and result.state is CacheState.STALE:
            status = Status.ERROR
        if not arrivals and not warnings:
            warnings.append("No upcoming departures were found for this stop and filter.")
        warnings.extend(_arrival_consistency_warnings(arrivals, now=now, zone=self._zone))
        observed_at = snapshot.observed_at
        if snapshot.source is DataSource.LIVE:
            data_age = (
                max(0.0, (now - observed_at).total_seconds()) if observed_at is not None else None
            )
        else:
            data_age = max(0.0, (now - snapshot.fetched_at).total_seconds())
        provenance = Provenance(
            source=snapshot.source,
            fetched_at=snapshot.fetched_at,
            observed_at=observed_at,
            is_stale=is_stale,
            freshness=(
                Freshness.FRESH
                if snapshot.source is DataSource.LIVE and observed_at is not None
                else Freshness.UNVERIFIED
            ),
            data_age_seconds=data_age,
            fallback_reason=snapshot.fallback_reason,
            warnings=_unique(warnings),
        )
        return ArrivalsResult(
            status=status,
            error=_error_info(error) if error is not None else None,
            provenance=provenance,
            stop_id=stop.stop_id,
            stop_name=stop.name,
            line_filter=filtered,
            lookahead_minutes=self.settings.schedule_lookahead_minutes,
            count=len(arrivals),
            arrivals=arrivals,
        )

    async def _load_arrivals(self, stop: StopRecord) -> ArrivalsSnapshot:
        live_provider = self._live
        if live_provider is None:
            raise StptError(ErrorCode.NOT_READY, "Live provider is not ready.", retryable=True)
        live_error: StptError | None = None
        live_result: ProviderResult[tuple[LiveArrival, ...]] | None = None
        try:
            live_result = await live_provider.fetch(stop.stop_id)
        except StptError as exc:
            live_error = exc

        has_predictions = live_result is not None and bool(live_result.value)
        # Live is used only when its freshness was verified against the real
        # clock: not stale, and with an observation time derived from the feed.
        live_is_fresh = (
            live_result is not None
            and has_predictions
            and not live_result.stale
            and live_result.observed_at is not None
        )
        if live_result is not None and live_is_fresh:
            live_partial_error = None
            partial = not live_result.complete
            if partial:
                live_partial_error = StptError(
                    ErrorCode.UPSTREAM_INVALID_RESPONSE,
                    "Some live arrivals were malformed and were ignored.",
                    retryable=True,
                    upstream="live arrivals",
                )
            return ArrivalsSnapshot(
                records=tuple(_record_from_live(arrival) for arrival in live_result.value),
                source=DataSource.LIVE,
                fetched_at=live_result.fetched_at,
                observed_at=live_result.observed_at,
                warnings=live_result.warnings,
                partial=partial,
                error=live_partial_error,
            )

        warnings: list[str] = []
        if live_result is not None and has_predictions and live_result.stale:
            fallback_reason = "live_predictions_stale"
            warnings.append("Live predictions are stale; trying the schedule fallback.")
            warnings.extend(live_result.warnings)
        elif live_result is not None and has_predictions:
            fallback_reason = "live_predictions_unverified"
            warnings.append(
                "Live predictions could not be verified as current; trying the schedule fallback."
            )
            warnings.extend(live_result.warnings)
        elif live_result is not None:
            fallback_reason = "live_returned_no_departures"
            warnings.append("Live endpoint returned no predictions; trying the schedule.")
            warnings.extend(live_result.warnings)
        else:
            fallback_reason = "live_unavailable"
            warnings.append("Live arrivals were unavailable; trying the schedule.")
            if live_error is not None:
                warnings.append(live_error.message)

        service_date = self._utc_now().astimezone(self._zone).date()
        try:
            schedule = await self._get_schedule_cached(stop, service_date)
        except StptError as schedule_error:
            if live_result is not None and has_predictions:
                # The feed answered, but its predictions failed the freshness
                # check. Their minutes may be frozen for hours, so they are
                # withheld: anchored to now they would look like real arrivals.
                stale = live_result.stale
                return ArrivalsSnapshot(
                    records=(),
                    source=DataSource.LIVE,
                    fetched_at=live_result.fetched_at,
                    observed_at=None,
                    fallback_reason=fallback_reason,
                    warnings=tuple(
                        _unique(
                            (
                                *warnings,
                                schedule_error.message,
                                "The schedule fallback is unavailable; the unverified live "
                                "predictions were withheld.",
                            )
                        )
                    ),
                    partial=True,
                    stale=stale,
                    error=StptError(
                        ErrorCode.DATA_STALE,
                        (
                            "Live predictions are stale and the schedule fallback is unavailable."
                            if stale
                            else "Live predictions could not be verified as current and the "
                            "schedule fallback is unavailable."
                        ),
                        retryable=True,
                        upstream="live+sched",
                    ),
                )
            if live_error is not None:
                raise StptError(
                    ErrorCode.UPSTREAM_UNAVAILABLE,
                    "Live arrivals and schedule fallback are both unavailable.",
                    retryable=live_error.retryable or schedule_error.retryable,
                    upstream="live+sched",
                ) from schedule_error
            raise

        warnings.extend(schedule.warnings)
        partial = schedule.partial or live_error is not None
        if live_result is not None and not live_result.complete:
            partial = True
        # A live outage is retryable and more useful to report than a timetable gap.
        error: StptError | None = live_error or schedule.error
        if live_result is not None and not live_result.complete and error is None:
            error = StptError(
                ErrorCode.UPSTREAM_INVALID_RESPONSE,
                "Some live arrival records were malformed.",
                retryable=True,
                upstream="live arrivals",
            )
        records = tuple(_record_from_schedule(departure) for departure in schedule.departures)
        return ArrivalsSnapshot(
            records=records,
            source=DataSource.SCHEDULE,
            fetched_at=schedule.fetched_at,
            fallback_reason=fallback_reason,
            warnings=tuple(warnings),
            partial=partial,
            stale=schedule.stale,
            error=error,
        )

    async def _get_schedule_cached(self, stop: StopRecord, service_date: date) -> ScheduleSnapshot:
        schedule_provider = self._schedule
        if schedule_provider is None:
            raise StptError(ErrorCode.NOT_READY, "Schedule provider is not ready.", retryable=True)
        result: CacheResult[ScheduleSnapshot] = await self._schedule_cache.get_or_load(
            (stop.stop_id, service_date.isoformat()),
            lambda: self._load_schedule(stop, service_date),
        )
        snapshot = result.value
        if result.state is CacheState.STALE:
            error = _coerce_stpt_error(result.error, "schedule refresh failed")
            snapshot = replace(
                snapshot,
                stale=True,
                partial=True,
                error=snapshot.error or error,
                warnings=tuple(
                    _unique(
                        (
                            *snapshot.warnings,
                            "Served a stale cached schedule after a refresh failure.",
                        )
                    )
                ),
            )
        return snapshot

    async def _load_schedule(self, stop: StopRecord, service_date: date) -> ScheduleSnapshot:
        schedule_provider = self._schedule
        if schedule_provider is None:
            raise StptError(ErrorCode.NOT_READY, "Schedule provider is not ready.", retryable=True)
        provider = await schedule_provider.fetch_stop_schedule(stop, service_date)
        return ScheduleSnapshot(
            departures=provider.value,
            fetched_at=provider.fetched_at,
            warnings=provider.warnings,
            partial=not provider.complete,
            error=(
                None
                if provider.complete
                else StptError(
                    ErrorCode.SCHEDULE_UNAVAILABLE,
                    "The bundled timetable has ended; the schedule fallback is incomplete.",
                    retryable=False,
                    upstream="schedule",
                )
            ),
        )

    def _materialize_arrivals(
        self,
        snapshot: ArrivalsSnapshot,
        *,
        now: datetime,
        line: str | None,
        limit: int,
        stale: bool,
    ) -> tuple[list[Arrival], str | None]:
        arrivals: list[Arrival] = []
        wanted = line.casefold() if line else None
        for record in snapshot.records:
            if wanted is not None and record.line.casefold() != wanted:
                continue
            if record.departure_at is None:
                continue
            anchor = record.departure_at
            delta_minutes = (anchor - now).total_seconds() / 60
            if not record.live:
                # Schedule departures are absolute instants across adjacent
                # service days; keep only those inside the lookahead window.
                if not 0 <= delta_minutes <= self.settings.schedule_lookahead_minutes:
                    continue
            elif delta_minutes < -1:
                # A cached prediction more than a minute past is gone, not "due now".
                continue
            remaining_minutes = (anchor - now).total_seconds() / 60
            eta = max(0, math.ceil(remaining_minutes - 1e-9))
            arrivals.append(
                Arrival(
                    line=record.line,
                    destination=record.destination,
                    direction=record.direction,
                    mode=record.mode,
                    eta_minutes=eta,
                    arrival_time=anchor.astimezone(self._zone).strftime("%H:%M"),
                    live=record.live and not stale,
                    vehicle_id=record.vehicle_id,
                    shape_id=record.shape_id,
                )
            )
        arrivals.sort(
            key=lambda arrival: (
                arrival.eta_minutes,
                arrival.line.casefold(),
                arrival.destination,
            )
        )
        return arrivals[:limit], line

    # Alerts ------------------------------------------------------------

    async def get_alerts(
        self,
        *,
        stop_id: str | None = None,
        line: str | None = None,
        active_only: bool = True,
        limit: int = 50,
    ) -> AlertsResult:
        if not 1 <= limit <= 100:
            return AlertsResult(
                status=Status.ERROR,
                error=_error_info(
                    StptError(ErrorCode.INVALID_ARGUMENT, "limit must be between 1 and 100")
                ),
                count=0,
                active_only=active_only,
                stop_id=stop_id,
                line=line,
            )
        stop: StopRecord | None = None
        canonical_line: str | None = None
        try:
            if stop_id is not None:
                stop = self.network.require_stop(stop_id)
            if line is not None:
                canonical_line = self.network.require_route(line).line
            self._require_ready()
            result = await self._alerts_cache.get_or_load("all", self._load_alerts)
        except StptError as exc:
            return AlertsResult(
                status=Status.ERROR,
                error=_error_info(exc),
                count=0,
                active_only=active_only,
                stop_id=stop_id,
                line=canonical_line or line,
            )

        snapshot = result.value
        now = self._utc_now()
        warnings = list(snapshot.warnings)
        if result.state is CacheState.STALE:
            warnings.append("Served last-known-good alerts because a refresh failed.")
        matches: list[TransitAlert] = []
        for record in snapshot.alerts:
            active = _alert_is_active(record, now)
            if active_only and not active:
                continue
            if not _alert_matches(record, stop=stop, line=canonical_line):
                continue
            matches.append(
                TransitAlert(
                    id=record.alert_id,
                    title=record.title,
                    description=record.description,
                    cause=record.cause,
                    effect=record.effect,
                    active=active,
                    active_periods=[
                        AlertPeriod(start=start, end=end) for start, end in record.periods
                    ],
                    affected_routes=list(record.routes),
                    affected_stop_ids=list(record.stop_ids),
                )
            )
        matches.sort(key=lambda alert: (not alert.active, alert.id))
        matches = matches[:limit]
        stale = result.state is CacheState.STALE
        error = None
        status = Status.OK
        if stale:
            error = _coerce_stpt_error(result.error, "alerts refresh failed")
            status = Status.PARTIAL
        elif snapshot.partial:
            status = Status.PARTIAL
            error = StptError(
                ErrorCode.UPSTREAM_INVALID_RESPONSE,
                "Some alert records were malformed and were ignored.",
                retryable=True,
                upstream="alerts",
            )
        if not matches and not warnings:
            warnings.append("No matching alerts.")
        provenance = Provenance(
            source=DataSource.LIVE,
            fetched_at=snapshot.fetched_at,
            observed_at=None,
            is_stale=stale,
            data_age_seconds=max(0.0, (now - snapshot.fetched_at).total_seconds()),
            warnings=_unique(warnings),
        )
        return AlertsResult(
            status=status,
            error=_error_info(error) if error else None,
            provenance=provenance,
            count=len(matches),
            active_only=active_only,
            stop_id=stop.stop_id if stop else None,
            line=canonical_line,
            alerts=matches,
        )

    async def _load_alerts(self) -> AlertsSnapshot:
        alerts_provider = self._alerts
        if alerts_provider is None:
            raise StptError(ErrorCode.NOT_READY, "Alerts provider is not ready.", retryable=True)
        provider = await alerts_provider.fetch()
        return AlertsSnapshot(
            alerts=provider.value,
            fetched_at=provider.fetched_at,
            warnings=provider.warnings,
            partial=not provider.complete,
        )

    # Vehicles ----------------------------------------------------------

    async def get_vehicles(self, *, line: str | None = None, limit: int = 100) -> VehiclesResult:
        if not 1 <= limit <= 200:
            return VehiclesResult(
                status=Status.ERROR,
                error=_error_info(
                    StptError(ErrorCode.INVALID_ARGUMENT, "limit must be between 1 and 200")
                ),
                count=0,
                line_filter=line,
            )
        canonical_line: str | None = None
        try:
            if line is not None:
                canonical_line = self.network.require_route(line).line
            self._require_ready()
            result = await self._vehicles_cache.get_or_load("all", self._load_vehicles)
        except StptError as exc:
            return VehiclesResult(
                status=Status.ERROR,
                error=_error_info(exc),
                count=0,
                line_filter=canonical_line or line,
            )

        snapshot = result.value
        now = self._utc_now()
        warnings = list(snapshot.warnings)
        if result.state is CacheState.STALE:
            warnings.append("Served last-known-good vehicle positions because a refresh failed.")
        vehicles: list[Vehicle] = []
        stale_count = 0
        future_count = 0
        wanted = canonical_line.casefold() if canonical_line else None
        for record in snapshot.vehicles:
            if wanted is not None and record.line.casefold() != wanted:
                continue
            raw_age = (
                (now - record.observed_at).total_seconds()
                if record.observed_at is not None
                else None
            )
            age = max(0.0, raw_age) if raw_age is not None else None
            # A timestamp ahead of the real clock is a skewed upstream clock,
            # not a fresh fix, so it cannot count as verified.
            in_future = (
                raw_age is not None and -raw_age > self.settings.live_clock_skew_tolerance_seconds
            )
            if in_future:
                future_count += 1
            is_stale = age is None or age > self.settings.vehicle_stale_after_seconds or in_future
            if is_stale:
                stale_count += 1
            vehicles.append(
                Vehicle(
                    vehicle_id=record.vehicle_id,
                    line=record.line,
                    direction_id=record.direction_id,
                    headsign=record.headsign,
                    latitude=record.latitude,
                    longitude=record.longitude,
                    bearing=record.bearing,
                    speed_kmh=record.speed_kmh,
                    current_stop=record.current_stop,
                    accessible=record.accessible,
                    route_id=record.route_id,
                    trip_id=record.trip_id,
                    shape_id=record.shape_id,
                    observed_at=record.observed_at,
                    age_seconds=age,
                    is_stale=is_stale,
                )
            )
        if future_count:
            warnings.append(
                f"{future_count} vehicle position(s) carry a timestamp ahead of the real clock."
            )
        if stale_count - future_count:
            warnings.append(
                f"{stale_count - future_count} vehicle position(s) are older than "
                f"{self.settings.vehicle_stale_after_seconds:.0f}s or have no timestamp."
            )
        vehicles.sort(key=lambda vehicle: (_natural_line_key(vehicle.line), vehicle.vehicle_id))
        vehicles = vehicles[:limit]
        cache_stale = result.state is CacheState.STALE
        status = Status.OK
        error: StptError | None = None
        if cache_stale:
            status = Status.PARTIAL
            error = _coerce_stpt_error(result.error, "vehicles refresh failed")
        elif snapshot.partial:
            status = Status.PARTIAL
            error = StptError(
                ErrorCode.UPSTREAM_INVALID_RESPONSE,
                "Some vehicle records were malformed and were ignored.",
                retryable=True,
                upstream="vehicles",
            )
        elif stale_count:
            status = Status.PARTIAL
            error = StptError(
                ErrorCode.DATA_STALE,
                "Some vehicle positions failed the freshness check against the real clock.",
                retryable=True,
                upstream="vehicles",
            )
        if not vehicles and not warnings:
            warnings.append("No matching vehicles.")
        observed_at = max(
            (vehicle.observed_at for vehicle in vehicles if vehicle.observed_at is not None),
            default=None,
        )
        data_age = max(0.0, (now - observed_at).total_seconds()) if observed_at else None
        envelope_stale = cache_stale or stale_count > 0
        provenance = Provenance(
            source=DataSource.LIVE,
            fetched_at=snapshot.fetched_at,
            observed_at=observed_at,
            is_stale=envelope_stale,
            freshness=Freshness.FRESH if vehicles else Freshness.UNVERIFIED,
            data_age_seconds=data_age,
            warnings=_unique(warnings),
        )
        return VehiclesResult(
            status=status,
            error=_error_info(error) if error else None,
            provenance=provenance,
            count=len(vehicles),
            line_filter=canonical_line,
            vehicles=vehicles,
        )

    async def _load_vehicles(self) -> VehiclesSnapshot:
        vehicles_provider = self._vehicles
        if vehicles_provider is None:
            raise StptError(ErrorCode.NOT_READY, "Vehicles provider is not ready.", retryable=True)
        provider = await vehicles_provider.fetch()
        return VehiclesSnapshot(
            vehicles=provider.value,
            fetched_at=provider.fetched_at,
            warnings=provider.warnings,
            partial=not provider.complete,
        )

    # Status ------------------------------------------------------------

    async def get_status(self) -> ServiceStatus:
        now = self._utc_now()
        provenance, network_status, network_error = self._network_context()
        caches: list[CacheStats] = [
            self._arrivals_cache.stats(),
            self._schedule_cache.stats(),
            self._alerts_cache.stats(),
            self._vehicles_cache.stats(),
        ]
        caches.sort(key=lambda stats: stats.name)
        return ServiceStatus(
            status=network_status,
            error=network_error,
            provenance=provenance,
            ready=self._ready,
            version=__version__,
            current_time=now,
            timezone=self.settings.timezone,
            started_at=self._started_at,
            network_feed_version=self.network.snapshot.feed_version,
            network_generated_at=self.network.snapshot.generated_at,
            network_service_range=self.network.snapshot.service_range,
            caches=caches,
            live_feed=self._live.feed_status(now) if self._live is not None else None,
        )


def _arrival_consistency_warnings(
    arrivals: list[Arrival],
    *,
    now: datetime,
    zone: ZoneInfo,
) -> list[str]:
    """Guard the public contract: ``arrival_time`` must agree with ``eta_minutes``.

    Both fields are derived from one anchored instant, so a mismatch means a
    bug. Reporting it as a provenance warning is better than silently returning
    a time-of-day that contradicts the countdown.
    """

    warnings: list[str] = []
    now_local = now.astimezone(zone)
    for arrival in arrivals:
        if arrival.arrival_time is None:
            continue
        try:
            hour_text, minute_text = arrival.arrival_time.split(":", 1)
            hour = int(hour_text)
            minute = int(minute_text)
        except ValueError:
            warnings.append(
                f"{arrival.line} returned an unparseable arrival_time {arrival.arrival_time!r}."
            )
            continue
        candidate = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        nearest = min(
            (candidate - timedelta(days=1), candidate, candidate + timedelta(days=1)),
            key=lambda value: abs((value - now).total_seconds()),
        )
        implied_minutes = (nearest - now).total_seconds() / 60
        if abs(implied_minutes - arrival.eta_minutes) > 1.5:
            warnings.append(
                f"{arrival.line} shows {arrival.arrival_time} "
                f"({implied_minutes:+.1f} min away) but eta_minutes is "
                f"{arrival.eta_minutes}; the arrival time and countdown disagree."
            )
    return warnings[:5]


def _record_from_live(arrival: LiveArrival) -> ArrivalRecord:
    return ArrivalRecord(
        line=arrival.line,
        destination=arrival.destination,
        direction=None,
        mode=arrival.mode,
        live=True,
        vehicle_id=arrival.vehicle_id,
        departure_at=arrival.arrival_at,
    )


def _record_from_schedule(departure: ScheduleDeparture) -> ArrivalRecord:
    return ArrivalRecord(
        line=departure.line,
        destination=departure.destination,
        direction=departure.direction,
        mode=departure.mode,
        live=False,
        shape_id=departure.shape_id,
        departure_at=departure.departure_at,
    )


def _arrival_error(
    stop_id: str,
    stop_name: str,
    error: StptError,
    line_filter: str | None,
) -> ArrivalsResult:
    return ArrivalsResult(
        status=Status.ERROR,
        error=_error_info(error),
        stop_id=stop_id,
        stop_name=stop_name,
        line_filter=line_filter,
        count=0,
    )


def _error_info(error: StptError) -> ErrorInfo:
    return ErrorInfo(
        code=error.code,
        message=error.message,
        retryable=error.retryable,
        upstream=error.upstream,
    )


def _coerce_stpt_error(error: Exception | None, fallback: str) -> StptError:
    if isinstance(error, StptError):
        return error
    return StptError(ErrorCode.INTERNAL_ERROR, fallback, retryable=True)


def _alert_is_active(record: AlertRecord, now: datetime) -> bool:
    if not record.periods:
        return True
    for start, end in record.periods:
        if start is not None and now < start:
            continue
        if end is not None and now > end:
            continue
        return True
    return False


def _alert_matches(
    record: AlertRecord,
    *,
    stop: StopRecord | None,
    line: str | None,
) -> bool:
    has_constraints = bool(record.routes or record.stop_ids)
    if not has_constraints:
        return True
    route_match = False
    if line is not None:
        if record.routes:
            route_match = any(route.casefold() == line.casefold() for route in record.routes)
            if not route_match:
                return False
        if stop is not None and record.stop_ids:
            return stop.stop_id in record.stop_ids
        return True
    if stop is None:
        return True
    if stop.stop_id in record.stop_ids:
        return True
    if record.routes and not record.stop_ids:
        stop_routes = {route.casefold() for route in stop.routes}
        return any(route.casefold() in stop_routes for route in record.routes)
    return False


def _unique(values: list[str] | tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _natural_line_key(line: str) -> tuple[int, str]:
    digits = ""
    for char in line:
        if char.isdigit():
            digits += char
        else:
            break
    return (int(digits) if digits else 10**6, line.casefold())
