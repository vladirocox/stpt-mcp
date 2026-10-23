# Implementation plan

Status: **v0.3.5 published on PyPI; v0.3.6 live-arrival freshness fix
prepared, not yet released**

This plan is the delivery contract for `v0.1.0`, `v0.2.0`, `v0.3.0`,
`v0.3.1`, `v0.3.2`, the `v0.3.3` documentation release, the `v0.3.4` PyPI
publication release, the `v0.3.5` documentation cleanup, and the `v0.3.6`
live-arrival freshness fix.
Checkboxes are updated only
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
- [x] Add architecture, data-source, tool, testing, deployment, public HTTPS,
  operations, and security documentation, plus copy-paste deployment examples.
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
- [x] Install v0.2.0 on a self-hosted deployment under a dedicated virtual
  environment and a systemd service.
- [x] Keep the bearer token out of the repository, in a protected environment
  file on the host.
- [x] Put the endpoint behind an HTTPS reverse proxy with response buffering
  disabled, as described in `docs/PUBLIC_HTTPS.md`.
- [x] Verify HTTPS `/healthz`, unauthenticated `401`, authenticated MCP
  initialize, the seven tools, a stop lookup, live arrivals, and service status
  over the public HTTPS endpoint.
- [x] Verify service restart and boot enablement.
- [x] Keep removal and rollback instructions with the deployment, outside the
  repository.

## 10. OAuth for hosted clients (v0.3.0)

- [x] Implement OAuth 2.1 discovery metadata, dynamic client registration,
  authorization-code + PKCE, refresh tokens, and revocation on the Streamable
  HTTP app.
- [x] Add a single-owner browser login page with expiring, one-time login
  requests and a redirect-URI allowlist.
- [x] Keep static bearer authentication available alongside OAuth.
- [x] Add configuration for public origin, OAuth password, redirect allowlist, and
  token lifetimes.
- [x] Add discovery, registration, login, token, refresh, static-token, and
  rejection tests.
- [x] Document the Caddy OAuth routes, Claude custom-connector steps, and
  troubleshooting in `docs/OAUTH.md` and `docs/PUBLIC_HTTPS.md`.
- [x] Deploy v0.3.0 and verify the public OAuth flow end to end.
- [x] Publish the v0.3.0 release and verify installation from the release asset.

## 11. Maintenance (v0.3.1)

- [x] Merge the Dependabot update for `mypy` 2.4.0.
- [x] Re-run Ruff, strict MyPy, the test suite with coverage, Bandit,
  pip-audit, and the package build.
- [x] Keep the OAuth per-process token-state limitation in the public docs.
- [x] Publish v0.3.1 and verify the release asset installation.
- [x] Deploy v0.3.1 and verify public OAuth metadata, token exchange, and the
  seven MCP tools over HTTPS.

## 12. Tool surface scope (v0.3.2)

- [x] Keep the public tool surface at the seven tools listed in
  `docs/TOOLS.md`.
- [x] Document the route information those tools already expose, so clients do
  not need a separate route lookup.
- [x] Decide against a route-detail tool that returns an ordered stop list or
  route geometry. The decision is recorded here and in `docs/TOOLS.md`.

## 13. Project cross-references (v0.3.3)

- [x] Add a chat screenshot to the README showing the tools answering an
  arrival question, and include `assets/` in the source distribution.
- [x] Add a "Related projects" section to the README pointing to the Home
  Assistant integration and the Inky pHAT dashboard.
- [x] Add the matching cross-references to the Home Assistant integration
  README, in English and Romanian, and to its HACS `info.md`.
- [x] Keep the cross-references documentation-only. The two projects remain
  independently deployable, with no runtime, build, or test dependency.

## 14. PyPI publication (v0.3.4)

- [x] Name the author as Vlad Ohai in `LICENSE`, `pyproject.toml`, and the
  README.
- [x] Add `.github/workflows/publish.yml`: on a published GitHub release, check
  the tag against the package version, build, upload to PyPI through trusted
  publishing, and attach the wheel and sdist to the release.
- [x] Document `pip install stpt-mcp`, `uvx stpt-mcp`, and a `uvx` client
  configuration in the README; make README links absolute for the PyPI page.
- [x] Build locally and pass `twine check --strict` on the wheel and sdist.
- [x] Configure the trusted publisher for `stpt-mcp` on PyPI (owner action).
- [x] Publish the `v0.3.4` GitHub release and confirm the `Publish` workflow
  uploaded to PyPI.
- [x] Install `stpt-mcp==0.3.4` from PyPI in a fresh environment and run
  `stpt-mcp --version`.

## 15. Documentation cleanup (v0.3.5)

- [x] Remove real deployment details (hostname, ports, network and firewall
  setup, server paths, accepted credentials, certificate details) from
  `docs/EXECUTION_LOG.md` and this plan, keeping the verification evidence in
  generic terms.
- [x] Add an `AGENTS.md` rule and a release-checklist item so deployment details
  are not recorded again.
- [x] Confirm the author is named as Vlad Ohai in `LICENSE`, the package
  metadata, and the README.
- [x] Publish the `v0.3.5` GitHub release and confirm the `Publish` workflow
  uploaded to PyPI.

## 16. Live arrival freshness (v0.3.6)

- [x] Derive an upstream observation clock from each live prediction's display
  time and remaining minutes; use the median across the response.
- [x] Reject a feed whose implied clock diverges from the real clock by more than
  `STPT_MCP_LIVE_CLOCK_SKEW_TOLERANCE_SECONDS`, fall back to the schedule, and
  report `fallback_reason: "live_predictions_stale"` with a warning naming both
  clocks.
- [x] Keep `observed_at` null when the feed has no clock reference or is stale;
  never copy the fetch time into it.
- [x] Anchor each live arrival at `fetched_at + ETA` and derive both
  `arrival_time` and `eta_minutes` from that anchor on every call.
- [x] Measure `data_age_seconds` from the upstream observation, not the fetch
  time, and return null when unknown.
- [x] Return stale live predictions as reference only (`is_stale: true`,
  `live: false`, `data_stale` error) when the schedule fallback also fails.
  Superseded in v0.3.7: they are withheld.
- [x] Add a runtime guard that warns when `arrival_time` and `eta_minutes`
  disagree by more than 1.5 minutes.
- [x] Align `get_vehicles` `provenance.is_stale` with its per-vehicle stale flags
  and `data_stale` error.
- [x] Parse GTFS schedule times at or after 24:00 and map them to the next
  service day; accept live hour labels up to `29:xx`.
- [x] Add deterministic regression tests for frozen feeds, DST, after-midnight
  times, midnight crossing, ETA decrease, the arrival-time/ETA invariant, and
  observation-age accounting.
- [x] Publish the `v0.3.6` GitHub release and confirm the `Publish` workflow
  uploaded to PyPI.

## v0.3.7 — offline timetable and verified freshness

- [x] Replace the `smtt.ro` scraping fallback with a bundled `timetable.json`
  generated from the official GTFS zip by `scripts/generate_timetable.py`;
  validate it against `network.json`; drop BeautifulSoup and the scraper
  settings ([ADR 0005](adr/0005-offline-timetable-and-verified-freshness.md)).
- [x] Detect frozen feeds whose stale clock matches real time by remembering
  each stop's implied clock; treat a first sight during a broken feed, and
  predictions without a clock, as unverified.
- [x] Withhold stale or unverified predictions when no fallback exists.
- [x] Add `Provenance.freshness` (`fresh`/`stale`/`unverified`) to every tool;
  label cached last-known-good predictions `live: false`; treat vehicle
  timestamps ahead of the clock as stale.
- [x] Expose `live_feed` in `get_service_status`.
- [x] Regression tests that fail with each fix reverted, plus a running-feed test
  proving the countdown and the `arrival_time`/`eta_minutes` invariant.
- [ ] Publish `v0.3.7` through the release workflow and redeploy.
- [ ] Re-run the live countdown check when the upstream feed recovers (see
  `LESSONS.md`).

## Explicit non-goals for v0.1.0

- Real-time push subscriptions or websocket updates.
- GTFS-Realtime prediction beyond what STPT's existing public live endpoint
  exposes.
- Scraping account-only or private STPT systems.
- Running a database, Redis, or other external state store.
- Home Assistant integration.
- A route-detail tool that returns a line's ordered stop list or
  geometry. Route information stays available through `list_routes`,
  `get_arrivals`, `get_stop`, and `get_vehicles`.
