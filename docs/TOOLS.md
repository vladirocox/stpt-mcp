# Tools

All tools are read-only, idempotent, and return a typed JSON envelope. The server
also emits a short model-facing text summary for each result. Input schemas are
generated from the annotated function signatures and are visible through
`tools/list`.

## Common envelope

Every result includes:

| Field | Type | Meaning |
| --- | --- | --- |
| `status` | `"ok" \| "partial" \| "error"` | Overall result quality. |
| `error` | object or null | Structured failure, populated for `partial`/`error`. |
| `provenance` | object or null | Source, freshness, fallback, warnings. |

`error` contains:

```json
{
  "code": "upstream_http_error",
  "message": "Upstream returned HTTP 503.",
  "retryable": true,
  "upstream": "live arrivals"
}
```

`provenance` contains:

```json
{
  "source": "live",
  "fetched_at": "2026-10-21T10:30:00Z",
  "observed_at": "2026-10-21T10:30:00Z",
  "is_stale": false,
  "freshness": "fresh",
  "data_age_seconds": 0.0,
  "fallback_reason": null,
  "warnings": []
}
```

`freshness` is `fresh` only when an upstream observation time was checked
against the server's clock and passed; `stale` when the check failed or stale
cached data is served (`is_stale` is then true); `unverified` when the data has
no clock reference (schedules, network data, alerts). It defaults to
`unverified`, so nothing is called fresh without a check.

Times are ISO 8601 UTC. Display times such as `arrival_time` are local
`Europe/Bucharest` `HH:MM` strings and are not guaranteed timestamps.

## `search_stops`

Search the bundled network by stop ID, name fragment, or line.

| Argument | Type | Default | Constraints |
| --- | --- | --- | --- |
| `query` | string | required | 1–100 chars |
| `limit` | integer | `10` | 1–50 |

Matching is case- and diacritic-insensitive. Exact stop IDs and line matches rank
above name matches. The result includes `query`, `count`, and `stops`.

## `get_stop`

Return coordinates, serving lines, directions, destinations, terminal status, and
neighboring stops.

| Argument | Type | Default | Constraints |
| --- | --- | --- | --- |
| `stop_id` | string | required | numeric STPT stop ID |

Unknown IDs return `status: "error"` with `unknown_stop` and, when available, a
suggestion in the message. If the snapshot is outside its service range, the
result may be `partial` with `network_stale`.

## `list_routes`

List routes from the bundled snapshot.

| Argument | Type | Default | Constraints |
| --- | --- | --- | --- |
| `mode` | enum or null | null | `tram`, `trolleybus`, `bus`, `vaporetto`, `unknown` |

Each route includes line, short/long name, category, mode, GTFS type name, and
colors. Results are sorted naturally by line.

## `get_arrivals`

Return upcoming arrivals at a stop, preferring live data.

| Argument | Type | Default | Constraints |
| --- | --- | --- | --- |
| `stop_id` | string | required | numeric STPT stop ID |
| `line` | string or null | null | 1–10 chars, must be a known route |
| `limit` | integer | `10` | 1–50 |

Selection order:

1. fresh cached arrivals for the stop;
2. live predictions whose freshness was verified (`freshness: "fresh"`);
3. the bundled GTFS timetable for the local service date;
4. last-known-good data within the stale window;
5. structured error.

A live payload whose implied observation time is more than
`STPT_MCP_LIVE_CLOCK_SKEW_TOLERANCE_SECONDS` away from the fetch time, or whose
clock for that stop has stood still for longer than that tolerance, is stale:
`fallback_reason: "live_predictions_stale"`. Predictions with no display time,
or a stop seen for the first time while other stops recently looked stale, are
unverified: `fallback_reason: "live_predictions_unverified"`. Either way the
server uses the timetable. If the timetable is unavailable too, the predictions
are withheld: `arrivals` is empty and the error is `data_stale`. Unverified
minutes counted from now would look like real arrivals, so they are never
returned.

The arrivals cache is keyed by stop, so a line filter does not multiply upstream
requests. Filtering and limiting happen after retrieval.

An arrival contains line, destination, direction, mode, ETA minutes, local
display time, `live`, and optional vehicle/shape IDs. `live` is true only in a
`fresh` envelope; schedule arrivals and last-known-good cached predictions have
`live: false`.

`arrival_time` and `eta_minutes` are derived from one anchored instant on every
call, so the countdown keeps decreasing while a cache entry is served and the
two fields always agree within one minute. `data_age_seconds` measures the age
of the upstream observation, not the age of the cache entry; it is null when no
observation time can be derived.

`lookahead_minutes` reports the schedule lookahead used when fallback runs.

### Example: live result

```json
{
  "status": "ok",
  "error": null,
  "provenance": {
    "source": "live",
    "fetched_at": "2026-10-21T10:30:00Z",
    "observed_at": "2026-10-21T10:30:00Z",
    "is_stale": false,
    "data_age_seconds": 0.0,
    "fallback_reason": null,
    "warnings": ["Removed 1 duplicate live arrival record."]
  },
  "stop_id": "85",
  "stop_name": "Catedrala Mitropolitană",
  "line_filter": null,
  "lookahead_minutes": 180,
  "count": 4,
  "arrivals": [
    {
      "line": "7",
      "destination": "Calea Torontalului",
      "direction": "tur",
      "mode": "tram",
      "eta_minutes": 3,
      "arrival_time": "13:33",
      "live": true,
      "vehicle_id": "3512",
      "shape_id": "7_T"
    }
  ]
}
```

### Example: live empty, schedule fallback

```json
{
  "status": "ok",
  "error": null,
  "provenance": {
    "source": "schedule",
    "fetched_at": "2026-10-21T10:30:00Z",
    "observed_at": null,
    "is_stale": false,
    "freshness": "unverified",
    "data_age_seconds": 0.0,
    "fallback_reason": "live_returned_no_departures",
    "warnings": ["Live endpoint returned no predictions; trying the schedule."]
  },
  "stop_id": "59",
  "stop_name": "Piața Sfânta Maria",
  "line_filter": null,
  "lookahead_minutes": 180,
  "count": 10,
  "arrivals": []
}
```

`arrivals` is abbreviated. Fallback reasons are
`live_returned_no_departures`, `live_unavailable`, `live_predictions_stale`, and
`live_predictions_unverified`. `fetched_at` of a schedule result is the
timetable's generation time.

### Example: frozen live feed, schedule fallback

```json
{
  "status": "ok",
  "error": null,
  "provenance": {
    "source": "schedule",
    "fetched_at": "2026-10-23T10:30:00Z",
    "observed_at": null,
    "is_stale": false,
    "freshness": "unverified",
    "data_age_seconds": 0.0,
    "fallback_reason": "live_predictions_stale",
    "warnings": [
      "Live predictions are stale; trying the schedule fallback.",
      "Live predictions look stale: the feed implies 16:00 local while the current time is 12:55 (+185 min)."
    ]
  },
  "stop_id": "135",
  "line_filter": "14",
  "count": 10,
  "arrivals": [
    {"line": "14", "destination": "Ion Ionescu de la Brad", "direction": "tur",
     "mode": "trolleybus", "eta_minutes": 4, "arrival_time": "12:59",
     "live": false, "vehicle_id": null, "shape_id": "14.1_T"}
  ]
}
```

`arrivals` is abbreviated. If the timetable is unavailable too, the same call
returns `status: "partial"`, `source: "live"`, `is_stale: true`,
`freshness: "stale"`, `observed_at: null`, `data_age_seconds: null`, an empty
`arrivals` list, and an error with code `data_stale`.

## `get_alerts`

Return service alerts, optionally filtered.

| Argument | Type | Default | Constraints |
| --- | --- | --- | --- |
| `stop_id` | string or null | null | known stop ID |
| `line` | string or null | null | known route |
| `active_only` | boolean | `true` | |
| `limit` | integer | `50` | 1–100 |

Active periods are evaluated in `Europe/Bucharest` at the current instant. Alerts
are sorted with active ones first. If a filter has no matches, `status` is `ok`,
`count` is `0`, and a warning explains the empty result. An alert without a
parseable period is treated as active and produces a warning.

## `get_vehicles`

Return current vehicle positions.

| Argument | Type | Default | Constraints |
| --- | --- | --- | --- |
| `line` | string or null | null | known route |
| `limit` | integer | `100` | 1–200 |

Each vehicle includes coordinates, line, direction, headsign, bearing, speed,
current stop, accessibility, route/trip/shape IDs, upstream `observed_at`, age,
and `is_stale`. Vehicles older than the configured threshold, without a
timestamp, or with a timestamp ahead of the clock by more than the skew tolerance
are flagged. If some or all are stale, the envelope is `partial` with
`data_stale` and `freshness: "stale"`; otherwise it is `fresh`.

Alerts have no upstream timestamp, so their `freshness` is always `unverified`
(or `stale` when a cached copy is served after a refresh failure).

## `get_service_status`

Return readiness, version, timezone, current time, start time, network feed
version/generation/service range, cache statistics, and `live_feed`. It contains
no secrets.

`live_feed` is null until a stop has been fetched. Otherwise it reports the last
stop fetched (`stop_id`, `assessed_at`), its `freshness`, `skew_seconds` (the
feed's implied clock minus the real clock; positive means ahead),
`clock_unchanged_seconds` (how long that stop's feed clock has stood still), and
`recent_stops`, the freshness counts of every stop fetched in the last 15
minutes. Many `stale` stops mean the upstream feed is frozen.

| Argument | Type | Default |
| --- | --- | --- |
| none | | |

Cache statistics include entries, maximum entries, hits, misses, coalesced waits,
stale hits, evictions, loads, failures, and in-flight loads.

## Route information

The tool surface exposes route information through the existing tools rather
than through a dedicated route-detail tool:

- `list_routes` returns every route with its line, name, long name, category,
  mode, GTFS type name, and colors.
- `get_arrivals` returns the line, mode, direction, and destination for each
  arrival at a stop.
- `get_stop` returns the lines serving a stop, each with its direction and
  destination, plus the stop's neighboring stops.
- `get_vehicles` returns the route, trip, and shape identifiers of each
  vehicle.

A route's ordered stop list and its geometry are not exposed. The bundled
snapshot stores them for internal lookups, but no tool returns them.

## Error codes

| Code | Typical meaning |
| --- | --- |
| `invalid_argument` | Limit or other argument outside its allowed range. |
| `unknown_stop` | Stop ID is not in the bundled network snapshot. |
| `unknown_route` | Line is not in the bundled network snapshot. |
| `not_ready` | Tool called before the service lifespan started. |
| `upstream_timeout` | Upstream request timed out after retries. |
| `upstream_unavailable` | Transport failure or both live and schedule unavailable. |
| `upstream_http_error` | Non-retryable or exhausted HTTP failure. |
| `upstream_rate_limited` | HTTP 429 after retry policy. |
| `upstream_invalid_response` | Malformed JSON/HTML or unusable records. |
| `upstream_response_too_large` | Response exceeded the configured byte cap. |
| `schedule_unavailable` | The stop has no scheduled shapes, or the date is outside the bundled timetable. |
| `network_stale` | Bundled snapshot is outside its service range. |
| `data_stale` | Data failed the freshness check (old, skewed, frozen, or unverifiable with no fallback). |
| `internal_error` | Unexpected server fault; message is non-sensitive. |

`retryable` indicates whether a later call may succeed. Clients should inspect
`status` and `provenance` before treating data as current.

## Protocol behavior

Unknown tool names produce a protocol-level tool error. Known tools with invalid
arguments produce either a validation error from the SDK or a structured
`invalid_argument` envelope for range checks performed by the service. Neither
case returns a traceback.
