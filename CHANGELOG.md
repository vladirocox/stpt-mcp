# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-10-21

### Added

- `stpt-mcp token` command for generating a 256-bit URL-safe bearer token.
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

[0.2.0]: https://github.com/vladirocox/stpt-mcp/releases/tag/v0.2.0
[0.1.0]: https://github.com/vladirocox/stpt-mcp/releases/tag/v0.1.0
