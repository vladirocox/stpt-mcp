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
  "data_age_seconds": 0.0,
  "fallback_reason": null,
  "warnings": []
}
```

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
2. live predictions;
3. date-specific schedule fallback;
4. last-known-good data within the stale window;
5. structured error.

The arrivals cache is keyed by stop, so a line filter does not multiply upstream
requests. Filtering and limiting happen after retrieval.

An arrival contains line, destination, direction, mode, ETA minutes, local
display time, `live`, and optional vehicle/shape IDs. Schedule arrivals have
`live: false`.

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
    "data_age_seconds": 0.0,
    "fallback_reason": "live_returned_no_departures",
    "warnings": ["Live endpoint returned no predictions; trying the schedule."]
  },
  "stop_id": "59",
  "stop_name": "Piața Sfânta Maria",
  "line_filter": null,
  "lookahead_minutes": 180,
  "count": 9,
  "arrivals": []
}
```

`arrivals` is abbreviated. Fallback reasons are
`live_returned_no_departures` and `live_unavailable`.

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
and `is_stale`. Vehicles older than the configured threshold are flagged. If some
or all are stale, the envelope is `partial` with `data_stale`.

## `get_service_status`

Return readiness, version, timezone, current time, start time, network feed
version/generation/service range, and cache statistics. It contains no secrets.

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
| `schedule_unavailable` | No schedule targets/pages or all pages failed. |
| `network_stale` | Bundled snapshot is outside its service range. |
| `data_stale` | Data is older than the freshness threshold. |
| `internal_error` | Unexpected server fault; message is non-sensitive. |

`retryable` indicates whether a later call may succeed. Clients should inspect
`status` and `provenance` before treating data as current.

## Protocol behavior

Unknown tool names produce a protocol-level tool error. Known tools with invalid
arguments produce either a validation error from the SDK or a structured
`invalid_argument` envelope for range checks performed by the service. Neither
case returns a traceback.
