# Data sources

This page documents what the server reads, how it interprets it, and what it does
not promise. Upstream services are unofficial public interfaces and can change
without notice.

## Summary

| Data | Endpoint | Format | Cached | Observation time |
| --- | --- | --- | --- | --- |
| Live arrivals | `https://live.stpt.ro/proxy-smtt-cache.php?stopid=...` | JSON array | 60 s | none; fetch time is used |
| Service alerts | `https://live.stpt.ro/proxy-alerts.php` | JSON object | 45 s | none |
| Vehicles | `https://live.stpt.ro/gtfs-vehicles.php` | JSON object | 60 s | epoch milliseconds per vehicle |
| Schedule | bundled `timetable.json` (from the official GTFS zip) | JSON | 1 h per stop/date | none; future service |
| Network | bundled `network.json` | JSON | process lifetime | feed generation time |

Every envelope carries `provenance.freshness`:

- `fresh` — an upstream observation time was compared with this server's clock
  and is within tolerance (live arrivals that passed every check, vehicles whose
  timestamps all passed);
- `stale` — the comparison failed (old, skewed, or frozen data) or a cache entry
  past its TTL is served; `is_stale` is then always true;
- `unverified` — the data has no upstream clock reference, so it cannot be
  confirmed as current (schedules, the network snapshot, alerts, predictions
  without a display time).

Only `fresh` data is labelled live: `Arrival.live` is true only in a `fresh`
envelope.

URLs are configurable for mirrors and tests. The defaults are compiled into
`config.py`.

## Live arrivals

Request:

```text
GET /proxy-smtt-cache.php?stopid=85
```

The response is a JSON array. A valid empty array means "no live predictions
right now", not a transport failure. The parser tolerates minor shape variations
and records warnings for records it cannot use. It preserves the upstream ETA
minutes and display time, line, destination, direction, mode, vehicle ID, and
shape ID where available.

No upstream observation timestamp is present. The parser therefore derives one
from the feed itself: for each prediction, `display time - remaining minutes` is
the instant the feed believed it was. The median across the response is
`provenance.observed_at`. If it diverges from the real fetch time by more than
`STPT_MCP_LIVE_CLOCK_SKEW_TOLERANCE_SECONDS` (default 180 s), the feed is frozen
or otherwise incoherent: the live predictions are **not** labelled live, the
server tries the schedule fallback, and the response carries
`fallback_reason: "live_predictions_stale"` plus a warning naming both clocks.
If the feed has no clock reference at all, `observed_at` is null, a warning
says freshness could not be verified, and the predictions are treated as
unverified: the server uses the schedule with
`fallback_reason: "live_predictions_unverified"`. `observed_at` is never set to
the fetch time.

A single response can only be compared with the clock once. A frozen feed whose
stale clock happens to equal the current time would pass that check, and STPT's
frozen snapshots carry a different stale clock per stop, so at almost any moment
some stop matches. The provider therefore remembers each stop's implied clock
(bounded by `STPT_MCP_MAX_CACHE_ENTRIES`). If it has not advanced for longer than
the skew tolerance while real time has, the predictions are stale ("look
frozen"). Readings are compared as times of day, so a frozen reading stays the
same when it resolves to another date. If it moved backwards by more than a
minute, by a different amount than real time passed, or away from a reading
already judged stale (a swapped snapshot can match the clock by chance), the
stop becomes suspect. A stop fetched again after 12 hours or more, whose last
observation was not fresh, is suspect too, unless another stop has since been seen running. A suspect stop
stays unverified until its clock is seen to advance with real time, and turns
stale if it keeps standing still. A response without display times does not
reset this tracking. Readings are compared as displayed wall times, so a frozen
reading stays frozen when a DST fold resolves it to two instants an hour
apart. A stop seen for the
first time, while another stop was found stale in the last 10 minutes, is
unverified until its clock is seen to move. Display times are resolved on
adjacent local dates and both sides of a DST fold. After a
restart the memory is empty, so a frozen stop fetched for the first time inside
its coincidence window is accepted until the next fetch; this is a known limit of
a feed with no timestamps.

`get_service_status` reports `live_feed`: the last stop fetched, its freshness,
signed skew, how long its clock has stood still, and the freshness counts of all
stops fetched in the last 15 minutes.

Arrival instants are anchored locally at fetch time (`fetched_at + ETA`), and
both `arrival_time` and `eta_minutes` are derived from that anchor on every
call. The countdown therefore decreases with the wall clock even while a cache
entry is served, and the two fields cannot disagree.

If the live feed is stale or unverified and the schedule fallback also fails,
the predictions are withheld: `arrivals` is empty, `source` is `live`,
`observed_at` and `data_age_seconds` are null, and the error is `data_stale`.
Frozen minutes anchored to the current time would look like real arrivals, so
they are never returned, not even marked as stale.

When a refresh fails and last-known-good predictions are served from the cache,
they keep counting down from their original fetch time, but `is_stale` is true,
`freshness` is `stale`, and every arrival has `live: false`.

If the array is non-empty but some records are malformed, the usable records are
returned as `status: "partial"` with an `upstream_invalid_response` error.

## Service alerts

Request:

```text
GET /proxy-alerts.php
```

The response contains an `alerts` array. Each alert may include:

- an ID, title, description, cause, and effect;
- active periods with start/end times;
- informed entities that reference routes and stops.

The parser normalizes timestamps to timezone-aware values. Active periods are
evaluated at the requested instant in `Europe/Bucharest`; `active_only=true`
filters out inactive alerts. An alert with no parseable active period is treated
as active with a warning rather than silently dropped. Filters by line and stop
use informed entities where available.

## Vehicles

Request:

```text
GET /gtfs-vehicles.php
```

The response contains vehicle/trip records with coordinates, line, headsign,
bearing, speed, accessibility, and an epoch-millisecond observation timestamp.
The parser converts timestamps to UTC-aware datetimes and computes age at
response time. Vehicles older than `STPT_MCP_VEHICLE_STALE_AFTER_SECONDS`
(default 120 s), without a timestamp, or with a timestamp more than the clock
skew tolerance ahead of the real clock are flagged individually. If any returned vehicle is stale,
`provenance.is_stale` is true and the envelope is `partial` with `data_stale`.

Vehicle positions are a snapshot, not a route history.

## Schedule

The fallback reads `src/stpt_mcp/data/timetable.json`, generated by
`scripts/generate_timetable.py` from STPT's official GTFS zip
(`https://smtt.ro/transit/gtfs/maps/smtt-timisoara-ro.zip`; the Mobility
Database mirrors it as feed `mdb-2868`). It contains `calendar.txt`,
`calendar_dates.txt`, and every trip's departure minutes, stored per shape as a
start minute plus a shared pattern of stop-to-stop deltas. The generator refuses
a feed whose version differs from `network.json` or whose trips do not follow the
snapshot's shapes, so the two files always describe the same network.

There is no network I/O: the fallback works when every STPT web service is down.
For a stop and local service date the provider:

1. takes the stop's shapes from the network snapshot, and the stop's positions
   on each shape, excluding a trip's final stop (an arrival, not a departure);
2. expands trips of the previous, current, and next service days whose service
   runs that day (weekday flags and date range, then `calendar_dates`
   exceptions), so after-midnight times (`24:xx`+) and long lookahead windows are
   covered;
3. labels line, direction, destination, and mode from the shape;
4. computes departure datetimes in `Europe/Bucharest`; the arrivals layer keeps
   those between now and the lookahead window.

On the timetable's last service day a warning says later days are missing. On
the day after it only that day's after-midnight trips are returned, as
`partial` with `schedule_unavailable` (a live failure, being retryable, is
reported in preference). Any
later date is a `schedule_unavailable` error that is not retryable: the bundled
data must be regenerated. Results are cached per stop
and service date with single-flight.

Schedule times are timetable data, never real-time predictions. Every schedule
arrival has `live: false`, provenance source `schedule`, and freshness
`unverified`.

## Bundled network snapshot

`src/stpt_mcp/data/network.json` is generated by
`scripts/generate_network_snapshot.py` from STPT's public GTFS-derived JSON:

- `route-manifest.json`
- `stops-index.json`
- `stop-sequences.json`

The snapshot contains 892 stops, 59 routes, and 186 shapes for feed version
`4.28`, with a service range of `20261019`–`20261231` at the time of generation.
It records generation time, source URLs, SHA-256 checksums of the inputs, counts,
and attribution.

The snapshot is **not** refreshed at runtime. If it is outside its service range,
`get_stop` and `search_stops` still work but report a `network_stale` warning or
partial status. The timetable shares that service range. A stop absent from
the snapshot cannot use schedule fallback,
although live arrivals may still be returned if the caller supplies a valid live
stop ID that the network index does not know. In practice `get_arrivals` requires
the stop to exist in the snapshot.

## What is not used

- No account-only or private STPT systems.
- No GTFS-Realtime feed beyond the public live endpoint. As of 2026-10-23 the
  Mobility Database lists only the static GTFS feed for Timișoara, and
  `live.stpt.ro` serves no GTFS-Realtime endpoint that this project found.
- No web scraping: the schedule comes from the bundled GTFS timetable.
- No browser automation, cookies, or login flows.

## Etiquette and load

The server applies caching, single-flight, bounded upstream concurrency,
timeouts, and conservative retries. Operators should still avoid aggressive
polling. Do not disable caching to "make it fresher"; upstream freshness is
already limited by the source.

## Attribution

See [`NOTICE`](../NOTICE). STPT data remains the property of its respective
owners. OpenStreetMap-derived coordinates, if present, are © OpenStreetMap
contributors under the ODbL.
