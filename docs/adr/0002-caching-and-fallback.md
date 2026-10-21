# ADR 0002: Bounded caches, single-flight, and last-known-good fallback

- Status: Accepted
- Date: 2026-10-21

## Context

A transit MCP server is commonly polled by multiple clients. Without
coalescing, N clients polling the same stop create N identical upstream requests.
STPT's public endpoints are shared infrastructure and must not be used as a
per-client fan-out bus. A naive cache also has unbounded growth and no policy for
serving useful data during a transient upstream failure.

## Decision

Use a generic in-process cache with:

- TTL freshness and a separate, longer stale-retention window;
- LRU eviction at a configurable maximum entry count;
- one in-flight `asyncio.Task` per key, shared by all callers;
- caller cancellation shielded from the shared fetch;
- on refresh failure, serve the last-known-good value while the stale window is
  valid, with the refresh error surfaced;
- cache keys that include every input that changes the upstream representation
  (at minimum stop ID and, for schedule data, service date).

Arrival fallback order:

1. Fresh cached arrivals for the stop.
2. Upstream live arrivals.
3. Date-specific scraped schedule when live is empty or fails.
4. Last-known-good arrivals during the stale window.
5. A structured error envelope.

The arrival cache is keyed by stop, not by optional line filter, so all clients
at a stop share the same fetch. Filtering happens after retrieval. Schedule
pages are cached by URL and date in addition to the stop/date schedule bundle.

## Consequences

- 50 concurrent calls for one stop generate one live fetch and at most one
  schedule refresh. A subsequent hit inside the TTL generates none.
- Distinct stops have independent keys and cannot contaminate one another.
- Empty live data is a valid response, not automatically an error; it triggers
  the schedule fallback because it usually means no real-time prediction at that
  moment.
- Stale data is never silently presented as live: provenance carries source,
  fetch/observation times, age, stale flag, fallback reason, and warnings.
- Multi-worker deployments have independent caches and stale windows. This
  trades cache duplication for operational simplicity.
