# Operations

## Health and readiness

`GET /healthz` is unauthenticated and returns:

- `200` with `{"status":"ok","service":"stpt-mcp","version":"..."}` when the
  service lifespan has started;
- `503` with `{"status":"starting",...}` before readiness.

It is a liveness/readiness signal, not a data-quality check. It deliberately
exposes no cache details or secrets. Use `get_service_status` through MCP for
operational data.

## Startup logs

The CLI logs to stderr. A normal local HTTP start looks like:

```text
... INFO stpt_mcp.cli: starting stpt-mcp 0.3.6 over streamable HTTP on 127.0.0.1:8000/mcp
```

If HTTP binds to a non-loopback address without a static token or OAuth, the
process logs a warning. Put authentication and TLS in front of that listener.

Startup fails for invalid settings, including:

- malformed URLs or timezone;
- a stale window shorter than its TTL;
- a non-loopback bind without `STPT_MCP_ALLOWED_HOSTS`;
- an auth token shorter than 16 characters or containing whitespace;
- an OAuth password shorter than 12 characters or containing whitespace, or an
  OAuth password without `STPT_MCP_PUBLIC_URL`;
- a public URL that is not an origin, or does not use HTTPS on a non-loopback
  host.

## Inspecting status

Call `get_service_status` and check:

- `ready` and `started_at`;
- `version`;
- `network_feed_version`, `network_generated_at`, and service range;
- per-cache `entries`, `hits`, `misses`, `coalesced`, `stale_hits`, `evictions`,
  `loads`, `failures`, and `inflight`.

Useful patterns:

| Observation | Interpretation |
| --- | --- |
| `coalesced` rising with `loads` flat | Single-flight is working under concurrency. |
| `failures` rising | Upstream or parser failures; inspect tool errors. |
| `stale_hits` rising | Refresh failures are being masked by last-known-good data. |
| `entries` at `max_entries`, `evictions` rising | Working set exceeds cache bounds; raise bounds or reduce unique stops. |
| One cache `inflight` stuck | A slow upstream request; check timeouts and upstream latency. |

Cache counters are cumulative since process start and reset on restart.

## Interpreting tool results

- `status: ok` — usable, complete data. Still check `is_stale` for vehicle-level
  staleness if relevant.
- `status: partial` — usable data with a problem. Read `error.code`,
  `provenance.fallback_reason`, and `warnings`.
- `status: error` — no usable data. `error.retryable` indicates whether an
  immediate retry may help.

Common cases:

| Symptom | Cause | Action |
| --- | --- | --- |
| Arrivals source `schedule`, reason `live_returned_no_departures` | No live predictions at that instant; schedule is valid. | None. |
| Arrivals source `schedule`, reason `live_unavailable` | Live endpoint failed, schedule worked. | Check upstream status; data is usable but not live. |
| Arrivals `partial`, `is_stale: true` | Refresh failed; last-known-good data served. | Check upstream and cache failure counters. |
| Arrivals `error`, `upstream_unavailable` | Live and schedule both failed. | Retry later; inspect network/proxy. |
| `data_stale` on vehicles | Positions older than threshold. | Treat positions as historical. |
| `network_stale` | Bundled snapshot outside service range. | Update the snapshot and release. |

## Upstream degradation

The service degrades in this order:

1. fresh cache;
2. live;
3. schedule;
4. stale cache;
5. structured error.

It does not retry forever, queue unbounded requests, or fabricate data. During a
prolonged upstream outage, expect `partial`/`error` results and rising
`stale_hits`. Consider reducing client polling rather than disabling caches.

## HTTP overload

When `max_concurrent_http_requests` is reached, the ASGI limiter returns `503`
with `Retry-After: 1`. This protects the process from unbounded concurrency.
Tune the limit only with measured capacity; upstream concurrency is separately
bounded by `STPT_MCP_UPSTREAM_CONCURRENCY`.

## Memory

Caches are bounded by entry count, but values vary in size (especially schedule
snapshots). Watch process RSS under real traffic. If memory is a concern:

- lower `STPT_MCP_MAX_CACHE_ENTRIES`;
- lower `STPT_MCP_SCHEDULE_PAGE_CACHE_ENTRIES`;
- lower `STPT_MCP_SCHEDULE_LOOKAHEAD_MINUTES`;
- run one process per container rather than many workers.

## Time and timezone

The server stores UTC-aware timestamps and serializes ISO 8601 UTC. Service dates
and alert active periods are evaluated in `Europe/Bucharest` by default. Keep the
host clock synchronized (NTP). A wrong clock shifts ETA calculations and alert
activity.

## Shutdown

Send SIGTERM/SIGINT. The lifespan closes the HTTP client and cancels in-flight
cache loads. In-flight tool calls may fail during shutdown; clients should retry
after restart. No persistent state needs flushing.

## Logs and privacy

- Authorization headers and tokens are never logged by this project.
- Tool results may contain public transit data only.
- If you add request logging at a proxy, exclude the `Authorization` header.
- Do not enable debug logging of full upstream bodies in production.

## Incident checklist

1. Check `/healthz`.
2. Call `get_service_status`; confirm `ready` and inspect failures.
3. Call `get_arrivals` for a known stop and read `status`, `provenance`, and
   `error`.
4. Check upstream reachability from the host.
5. Check proxy Host/Origin configuration if requests are rejected with 403/421.
6. Check auth configuration if requests return 401.
7. If only schedule fallback fails, inspect SMTT HTML changes.
8. If only the network snapshot is stale, schedule a snapshot update release.
