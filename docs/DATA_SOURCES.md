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
| Schedule | `https://smtt.ro/linie-transport-public-*` | HTML | 1 h per page/date | none; future service |
| Network | bundled `network.json` | JSON | process lifetime | feed generation time |

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
If the feed has no clock reference at all, `observed_at` is null and a warning
says freshness could not be verified. `observed_at` is never set to the fetch
time.

Arrival instants are anchored locally at fetch time (`fetched_at + ETA`), and
both `arrival_time` and `eta_minutes` are derived from that anchor on every
call. The countdown therefore decreases with the wall clock even while a cache
entry is served, and the two fields cannot disagree.

If the live feed is stale and the schedule fallback also fails, the stale
predictions are returned for reference only: `source: "live"`, `is_stale: true`,
`live: false` on every arrival, `observed_at` and `data_age_seconds` null, and a
`data_stale` error. Stale predictions are never presented as live.

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
(default 120 s) are flagged individually. If any returned vehicle is stale,
`provenance.is_stale` is true and the envelope is `partial` with `data_stale`.

Vehicle positions are a snapshot, not a route history.

## Schedule pages

SMTT route pages embed timetable data in HTML attributes. The server:

1. derives target shapes for a stop from the bundled GTFS snapshot;
2. groups shapes into unique line/direction pages (`_T` → base page, `_R` →
   `-r` page);
3. requests the page for the requested local service date with
   `custom_value=DD.MM.YYYY`;
4. selects `[data-code]` entries matching the exact stop ID;
5. accepts only time tokens whose shape suffix matches one of the stop's target
   shapes, including GTFS after-midnight hours (`24:xx`–`29:xx`), which are
   mapped to the next service day;
6. labels direction and destination from the shape sequence;
7. derives mode from GTFS route metadata;
8. computes departure datetimes in `Europe/Bucharest` and filters to the
   lookahead window at response time.

Pages are fetched with bounded concurrency and cached per URL and service date. A
single page failure makes the fallback partial and adds a warning. If every page
fails, the schedule load fails and the arrivals layer may serve last-known-good
data or return an error.

Schedule times are timetable data, never real-time predictions. Every schedule
arrival has `live: false` and provenance source `schedule`.

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
partial status. A stop absent from the snapshot cannot use schedule fallback,
although live arrivals may still be returned if the caller supplies a valid live
stop ID that the network index does not know. In practice `get_arrivals` requires
the stop to exist in the snapshot.

## What is not used

- No account-only or private STPT systems.
- No GTFS-Realtime feed beyond the public live endpoint.
- No scraping of pages other than the public schedule pages needed for fallback.
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
