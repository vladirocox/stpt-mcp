# Architecture

## Goals

`stpt-mcp` is a small, read-only MCP server that is safe to poll and simple to
deploy. It prioritizes:

1. correct source attribution and freshness signalling;
2. bounded work against shared public infrastructure;
3. one implementation for stdio and Streamable HTTP;
4. deterministic tests with no live dependency in CI.

It has no Home Assistant, database, Redis, or other external state dependency.

## Component map

```text
MCP client
   │  stdio or Streamable HTTP
   ▼
server.py ── MCP tools, lifespan, /healthz
   │
   ▼
service.py ── orchestration, filters, provenance, fallback
   ├── cache.py ── bounded TTL + LRU + single-flight + stale-on-error
   ├── network.py ── bundled GTFS-derived snapshot
   ├── upstream.py ── HTTPX client, retries, backoff, concurrency cap
   └── providers/
        ├── live.py       live arrivals JSON
        ├── schedule.py   bundled GTFS timetable (offline)
        ├── alerts.py     service alerts JSON
        └── vehicles.py   vehicle positions JSON
```

`cli.py` selects the transport. `http_app.py` adds authentication and HTTP
concurrency limiting around the SDK's Streamable HTTP ASGI app.

## Request path

### Arrivals

1. Validate `limit`, stop, and optional line against the bundled network.
2. Look up the arrivals cache by **stop ID only**. Line filtering happens after
   retrieval so all clients at a stop share one snapshot.
3. A fresh entry returns immediately. Concurrent misses for the same key await a
   single shared loader task.
4. The loader fetches live arrivals. They win only if their freshness is
   verified: the feed's implied clock is within tolerance of the real clock and
   is not standing still across fetches of that stop (`providers/live.py`).
5. If live is empty, fails, or is stale or unverified, the schedule provider
   expands the bundled GTFS timetable for the stop's shapes on the previous,
   current, and next local service days. No network request is made. If the
   timetable is unavailable too, stale or unverified predictions are withheld.
6. Schedule entries are filtered to the lookahead window and converted to ETA
   minutes at response time; cached live predictions more than a minute past
   are dropped.
7. If a refresh fails but a last-known-good snapshot is inside its stale window,
   that snapshot is returned with `is_stale: true`, `live: false` on every
   arrival, and the refresh error.
8. If no data is usable, a structured error envelope is returned.

The schedule bundle is cached per `(stop_id, service_date)`.

### Alerts, vehicles, and status

Alerts and vehicles are cached globally, then filtered by stop/line at response
time. Alert active periods are evaluated at the current instant in the configured
timezone. Vehicle staleness is derived from the upstream observation timestamp,
not from cache age; a timestamp ahead of the clock is stale, not fresh.
`get_service_status` reads cache counters, feed metadata, and the live feed's
freshness assessment;
it never exposes tokens or request headers.

## Caches

| Cache | Key | TTL | Stale window |
| --- | --- | --- | --- |
| `arrivals` | stop ID | 60 s | 15 min |
| `schedules` | stop ID + service date | 1 h | 6 h |
| `alerts` | singleton | 45 s | 10 min |
| `vehicles` | singleton | 60 s | 10 min |

All caches are bounded (`max_cache_entries`) and
evict least-recently-used entries. The cache clears expired-stale entries lazily.
`get_service_status` reports hits, misses, coalesced waits, stale hits, evictions,
loads, failures, and in-flight loads per cache.

Single-flight properties:

- one `asyncio.Task` per key while a load is in flight;
- all waiters share the result or exception;
- caller cancellation is shielded from the shared task;
- the in-flight map is cleaned up in a done callback;
- `close()` cancels outstanding loads.

These properties are exercised by the mandatory 50-concurrent-call tests.

## Upstream client

`upstream.py` wraps HTTPX with:

- connect and total timeouts;
- an `asyncio.Semaphore` cap on concurrent requests;
- retries for timeouts, transport errors, HTTP 429, and 5xx;
- exponential backoff with jitter and a cap;
- `Retry-After` support with an upper bound;
- a maximum response size;
- a user agent that identifies this project.

A `200 []` live response is a valid empty response, not an error. Malformed JSON
or malformed records produce a structured invalid-response error or a partial
result.

## Provenance and status

Every data-bearing result carries a `Provenance` object:

- `source`: `live`, `schedule`, or `network`;
- `fetched_at`: when this server retrieved the data (UTC);
- `observed_at`: upstream observation time where one exists;
- `is_stale`: stale-cache policy was used;
- `data_age_seconds`: age of the data or cache entry;
- `fallback_reason`: why the primary source was not used;
- `warnings`: data-quality notes.

`status` is:

- `ok`: primary data is complete and fresh;
- `partial`: usable data exists but something failed or is stale;
- `error`: no usable data; `error` is populated.

Live arrivals have no upstream timestamp, so `observed_at` equals `fetched_at`
for live arrivals and is `null` for schedule results. Vehicle results use the
upstream timestamp. Alerts and schedules describe state/future service and leave
`observed_at` null.

## Transport and lifecycle

`build_server()` creates an `MCPServer` with a lifespan that starts and closes the
`TransitService`. Tool functions are synchronous where no I/O is needed and async
where they touch caches or upstreams.

The HTTP app uses the SDK's Streamable HTTP transport in stateless mode, with:

- configurable path (default `/mcp`);
- DNS-rebinding protection and Host/Origin validation;
- a request body size limit;
- bounded sessions and an ASGI concurrency limiter;
- optional static bearer authentication;
- optional built-in OAuth 2.1 authorization-server routes for hosted clients,
  with DCR, PKCE, refresh tokens, and a single-owner browser login page;
- an unauthenticated `/healthz` liveness endpoint.

The CLI defaults to stdio. HTTP requires `--transport http` or
`STPT_MCP_TRANSPORT=http`.

## Error handling

Expected failures use `StptError` with a stable `ErrorCode`. Tools convert them to
`ErrorInfo` in the result envelope. Unexpected exceptions are contained at the
tool/service boundary and reported as `internal_error` with a non-sensitive
message. The server does not return tracebacks to clients.

## Scaling model

Each process owns its caches. Running multiple HTTP workers or replicas is
supported, but each one fetches independently. For public read-only data this is
an acceptable trade-off that avoids an external cache and its operational
burden. Use one worker per container by default.

## Extension points

- Add fields to Pydantic models in `models.py`; MCP output schemas update.
- Add provider logic under `providers/` and keep parsing pure.
- Add cache policies in `Settings` and wire them in `TransitService`.
- Update the network snapshot with `scripts/generate_network_snapshot.py` and
  re-run tests. The script contacts upstream and is not part of CI.
