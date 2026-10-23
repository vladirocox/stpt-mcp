# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [0.3.6] - 2026-10-23

### Fixed

- `get_arrivals` no longer returns a frozen ETA as live. The arrivals feed has no
  observation timestamp, so the parser derives one from each prediction's
  display time and remaining minutes. When that implied clock differs from the
  real clock by more than `STPT_MCP_LIVE_CLOCK_SKEW_TOLERANCE_SECONDS`
  (default 180 s), the feed is rejected, the schedule fallback is tried, and the
  response reports `fallback_reason: "live_predictions_stale"` with a warning
  naming both clocks.
- `arrival_time` and `eta_minutes` are now derived from one instant anchored at
  fetch time (`fetched_at + ETA`). They stay coherent on every call and the
  countdown decreases while a cache entry is served instead of freezing.
- `provenance.observed_at` is the derived upstream observation time for live
  arrivals, and null when it cannot be derived or the feed is stale. It is never
  set to the fetch time.
- `provenance.data_age_seconds` measures the age of the upstream observation,
  not the age of the fetch, and is null when unknown.
- If the live feed is stale and the schedule fallback also fails, stale
  predictions are returned as reference only: `is_stale: true`, `live: false` on
  every arrival, and a `data_stale` error.
- `get_vehicles` now sets `provenance.is_stale: true` when any returned vehicle
  position is stale, matching the existing `data_stale` error and per-vehicle
  flags.
- Schedule tables with GTFS times at or after 24:00 (after-midnight service) are
  now parsed and mapped to the next service day instead of being dropped.
- The live parser also accepts after-midnight hour labels up to `29:xx`.

### Added

- A runtime consistency guard: if `arrival_time` and `eta_minutes` ever disagree
  by more than 1.5 minutes, a warning is added to `provenance.warnings` instead
  of silently returning contradictory data.
- Regression tests with a controlled clock for frozen feeds, DST offsets
  (summer, winter, and the spring-forward boundary), GTFS times after 24:00,
  midnight crossings, ETA decreasing between calls, the arrival-time/ETA
  invariant, and `data_age_seconds` tracking the observation rather than the
  fetch.

## [0.3.5] - 2026-10-23

### Documentation

- Removed details of the maintainer's own deployment from the execution log and
  implementation plan: the hostname, ports, network and firewall setup, server
  paths, which credentials the server accepts, and certificate details. The
  verification evidence is kept in generic terms.
- Added an `AGENTS.md` rule and a release-checklist item so real deployment
  details are not recorded in the repository again.
- Recorded the v0.3.4 PyPI publication in the execution log.

### Changed

- Bumped the package version to 0.3.5 and refreshed the documented release
  artifact references.

## [0.3.4] - 2026-10-23

### Added

- A `Publish` workflow (`.github/workflows/publish.yml`). Publishing a GitHub
  release checks that the tag matches the package version, builds the wheel
  and sdist, uploads them to PyPI through trusted publishing, and attaches
  them to the release. No PyPI token is stored in the repository.

### Changed

- Bumped the package version to 0.3.4 and refreshed the documented release
  artifact references.

### Documentation

- The author is now named as Vlad Ohai in `LICENSE`, the package metadata in
  `pyproject.toml`, and a new "Author" section in the README.
- The README documents installing from PyPI with `pip install stpt-mcp` or
  `uvx stpt-mcp`, and an MCP client configuration that uses `uvx`.
- README links and the screenshot now use absolute GitHub URLs, so they also
  work on the PyPI project page.

## [0.3.3] - 2026-10-21

### Added

- Added a chat screenshot showing the MCP tools answering an arrival question,
  and a "Related projects" section that points to the Home Assistant
  integration and the Inky pHAT dashboard.

### Changed

- Bumped the package version to 0.3.3 and refreshed the documented release
  artifact references.
- Included the `assets/` directory in the source distribution so the README
  image resolves from the published sdist.

## [0.3.2] - 2026-10-21

### Changed

- Bumped the package version to 0.3.2 and refreshed the documented release
  artifact references.

### Documentation

- Documented how route information is exposed by the current tools.
- Recorded that the public tool surface stays at seven tools. No route-detail
  tool with an ordered stop list or route geometry is exposed.

## [0.3.1] - 2026-10-21

### Changed

- Bumped the development dependency `mypy` from 1.20.2 to 2.4.0.

### Documentation

- Noted that OAuth token state is per process and that multi-worker
  deployments require one worker or sticky sessions.

## [0.3.0] - 2026-10-21

### Added

- Built-in OAuth 2.1 authorization server for the Streamable HTTP endpoint,
  including RFC 8414/RFC 9728 discovery metadata, dynamic client registration,
  authorization-code + PKCE, refresh tokens, revocation, and a browser sign-in
  page.
- `STPT_MCP_PUBLIC_URL`, `STPT_MCP_OAUTH_PASSWORD`, redirect allowlist, and token
  lifetime settings.
- Claude custom-connector instructions and OAuth reverse-proxy routes.
- Static bearer tokens remain accepted when OAuth is enabled.

## [0.2.0] - 2026-10-21

### Added

- `stpt-mcp token` command for generating a 256-bit URL-safe bearer token.
- Public HTTPS deployment guide with systemd, Caddy, DNS, firewall, client, and
  renewal examples.
- Copy-paste Caddy, systemd, and environment examples under `deploy/`.
- Documentation for token generation in HTTP, Docker, reverse-proxy, and
  security guidance.

## [0.1.0] - 2026-10-21

### Added

- Standalone MCP server for STPT Timișoara with stdio and Streamable HTTP
  transports through the official Python SDK.
- Tools: `search_stops`, `get_stop`, `list_routes`, `get_arrivals`, `get_alerts`,
  `get_vehicles`, and `get_service_status`.
- Live arrivals with date- and shape-aware schedule fallback, typed provenance,
  structured errors, and last-known-good stale data.
- Bounded TTL/LRU caches with single-flight refresh, stale-on-error policy, and
  cache statistics.
- Upstream HTTPX client with timeouts, bounded concurrency, retries, jitter,
  `Retry-After` support, and response-size limits.
- Bundled GTFS-derived network snapshot (892 stops, 59 routes, 186 shapes) and a
  reproducible generation script.
- HTTP security controls: loopback default, DNS-rebinding and Host/Origin
  validation, request body limit, bounded sessions/concurrency, optional bearer
  authentication, and unauthenticated `/healthz`.
- Deterministic unit, integration, protocol, and end-to-end tests, including
  50-concurrent-call single-flight proofs and real stdio/HTTP MCP clients.
- Documentation for architecture, data sources, tools, configuration, testing,
  deployment, operations, security, and release verification.
- Dockerfile, Compose example, GitHub Actions CI, issue/PR templates, and ADRs.

### Known limitations

- Upstream endpoints and schedule HTML are unofficial and may change.
- Each process has an independent in-memory cache.
- Live arrivals expose no upstream observation timestamp.
- Docker/Podman was unavailable in the development environment; the container
  build and health check are verified by CI, not by a local run.

[0.3.5]: https://github.com/vladirocox/stpt-mcp/releases/tag/v0.3.5
[0.3.4]: https://github.com/vladirocox/stpt-mcp/releases/tag/v0.3.4
[0.3.3]: https://github.com/vladirocox/stpt-mcp/releases/tag/v0.3.3
[0.3.2]: https://github.com/vladirocox/stpt-mcp/releases/tag/v0.3.2
[0.3.1]: https://github.com/vladirocox/stpt-mcp/releases/tag/v0.3.1
[0.3.0]: https://github.com/vladirocox/stpt-mcp/releases/tag/v0.3.0
[0.2.0]: https://github.com/vladirocox/stpt-mcp/releases/tag/v0.2.0
[0.1.0]: https://github.com/vladirocox/stpt-mcp/releases/tag/v0.1.0
