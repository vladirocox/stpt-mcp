# Implementation plan

Status: **v0.2.0 deployed on `debian-hp`**

This plan is the delivery contract for `v0.1.0`. Checkboxes are updated only
after the corresponding check has actually run. A checked item with a command
means the command passed; environmental limitations are recorded separately.

Legend: `[x]` complete and verified · `[ ]` not complete · **BLOCKED** external
limitation · **N/A** not applicable.

## 1. Discovery and decisions

- [x] Inspect the existing `stpt-ha-integration` source without modifying it.
- [x] Verify live endpoints and payload shapes on 2026-10-21.
- [x] Identify defects in the original coordinator: weak empty/failure
  distinction, no single-flight, unbounded caches, ambiguous fallback
  provenance, schedule direction/date bugs, broad exception handling.
- [x] Select a standalone Python 3.11+ architecture using the official `mcp`
  Python SDK and Streamable HTTP.
- [x] Record architecture decisions in `docs/adr/`.

## 2. Repository and documentation foundation

- [x] Initialize an independent Git repository.
- [x] Add license, attribution, agent guide, and implementation plan.
- [x] Add architecture, data-source, tool, testing, deployment, operations, and
  security documentation.
- [x] Add contribution guide, code of conduct, changelog, release checklist,
  issue templates, and pull-request template.

## 3. Network snapshot

- [x] Generate the compact offline snapshot from the verified GTFS-derived route
  manifest, stops index, and stop sequences.
- [x] Record feed version, service range, generation time, checksum, and
  attribution in the snapshot.
- [x] Add a reproducible generation script and validate the bundled snapshot in
  tests.

## 4. Core domain and upstream layer

- [x] Configuration and settings validation, including loopback-safe defaults.
- [x] Structured error model and typed result envelopes.
- [x] Pydantic models for stops, routes, arrivals, alerts, vehicles, provenance,
  and cache statistics.
- [x] Offline network index with accent-insensitive stop search and route lookup.
- [x] Bounded TTL cache with LRU eviction, stale-on-error policy, and
  concurrency-safe single-flight.
- [x] Async HTTP client with bounded concurrency, timeouts, retries with
  exponential backoff and jitter, `Retry-After` support, and response-size caps.
- [x] Live arrivals parser preserving upstream minutes/times and vehicle type.
- [x] Alerts parser with timezone-aware active-period and informed-entity
  filtering.
- [x] Vehicles parser preserving the upstream observation timestamp.
- [x] Schedule parser that selects the target stop code and shape, identifies
  line and direction, uses the correct terminal destination, and supports
  date-specific service pages.
- [x] Arrival orchestration with live-first fallback, schedule fallback,
  last-known-good stale data, explicit provenance, and partial/error statuses.

## 5. MCP interface

- [x] `search_stops`, `get_stop`, and `list_routes` discovery tools.
- [x] `get_arrivals` tool with line filtering and typed output.
- [x] `get_alerts` tool with active and entity filters.
- [x] `get_vehicles` tool with line filtering.
- [x] `get_service_status` tool exposing cache/upstream health without secrets.
- [x] Typed output schemas and useful model-facing text for every tool.
- [x] Lifespan wiring that opens and closes the HTTP client and reports readiness.
- [x] stdio entry point that never writes protocol data to stdout accidentally.
- [x] Streamable HTTP app with configurable path, secure transport settings,
  request limits, optional bearer auth, and unauthenticated `/healthz`.

## 6. Tests

- [x] Unit tests for configuration, errors, parsers, cache, HTTP retry/backoff,
  network search, and schedule date handling.
- [x] Cache test with 50 concurrent requests for one stop: exactly one live
  fetch and at most one schedule refresh.
- [x] TTL-hit test: after warm-up, no additional upstream calls.
- [x] Distinct-stop isolation test.
- [x] Stale/last-known-good, malformed upstream, timeout, and partial-failure
  tests.
- [x] Integration tests for live→schedule fallback and provenance.
- [x] In-process real MCP client protocol tests for tool listing, structured
  output, error envelopes, and validation.
- [x] stdio end-to-end test launching the actual console entry point.
- [x] Streamable HTTP end-to-end test with a real client, host/origin checks,
  auth, request-size limit, and health endpoint.
- [x] Verify the suite makes no live network requests by default.

## 7. Packaging and operations

- [x] Build wheel and sdist and inspect their contents.
- [x] Add Dockerfile and Compose example with non-root user, health check, and
  secure environment defaults.
- [ ] Build/run the container locally. **BLOCKED:** Docker/Podman is absent, so
  no local container build/run claim is permitted. The CI container job passed.
- [x] Add GitHub Actions CI for Python 3.11–3.13, lint, format, type check,
  tests/coverage, security audit, package build, and container build.
  Initial and dependency-update runs passed, including the container job.
- [x] Add release checklist and `v0.1.0` release notes.

## 8. Final verification and publication

- [x] Run `uv sync --all-groups` from a clean checkout.
- [x] Run Ruff format check and lint.
- [x] Run strict MyPy.
- [x] Run full pytest suite with coverage and record actual counts/percentage.
- [x] Run Bandit and pip-audit and record results.
- [x] Build distributions and install the wheel in a fresh environment; run a
  stdio smoke test from the installed package.
- [x] Inspect repository for secrets, generated junk, and unresolved TODOs.
- [x] Commit the complete deliverable.
- [x] Create public GitHub repository `vladirocox/stpt-mcp` if authenticated.
- [x] Push, verify the remote branch/commit, and inspect GitHub Actions results.
- [x] Write the final acceptance report with verified evidence and remaining
  limitations.

## 9. Remote deployment (v0.2.0)

- [x] Add `stpt-mcp token` and verify generated-token authentication.
- [x] Install v0.2.0 in `/opt/stpt-mcp` on `debian-hp` under a dedicated
  virtual environment.
- [x] Create and enable `stpt-mcp.service`; bind `0.0.0.0:9000` with the public
  host and local hostnames in the allowlist.
- [x] Store the bearer token in root-only `/etc/stpt-mcp.env`.
- [x] Allow TCP 9000 through `ufw`; verify the existing router forwarding.
- [x] Verify `/healthz`, unauthenticated `401`, authenticated MCP initialize,
  the seven tools, a stop lookup, live arrivals, and service status over
  `http://vladiro.go.ro:9000`.
- [x] Verify service restart and boot enablement.
- [x] Leave `/home/vladirocox/STPT_MCP_REMOVAL.md` on the host with removal and
  firewall/router rollback commands.

## Explicit non-goals for v0.1.0

- Real-time push subscriptions or websocket updates.
- GTFS-Realtime prediction beyond what STPT's existing public live endpoint
  exposes.
- Scraping account-only or private STPT systems.
- Running a database, Redis, or other external state store.
- Home Assistant integration.
