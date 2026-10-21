# Agent guide

This repository is a standalone, publicly distributable MCP server for STPT
public transport in Timișoara. It is deliberately independent of Home Assistant.

## Non-negotiable constraints

- Do not add a Home Assistant or HACS dependency.
- Do not modify or depend on `stpt-ha-integration`.
- CI and the default test suite must never contact live STPT services.
- Do not remove provenance, stale-data signalling, or structured errors.
- Preserve these cache invariants:
  - concurrent requests for one stop share a single upstream live fetch;
  - a schedule refresh is single-flight per stop and service date;
  - a fresh cache hit performs no upstream requests;
  - cache keys include the stop so distinct stops never cross-contaminate;
  - caches and upstream concurrency are bounded.
- Keep stdio and Streamable HTTP working through the official MCP Python SDK.
- Secure HTTP defaults: localhost bind, DNS-rebinding and Origin validation,
  request body limit, bounded concurrency, and optional bearer authentication.
- Do not claim Docker, CI, or publication success unless it was actually run.

## Commands

```bash
uv sync --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
uv run pytest -m "not network" --cov --cov-report=term-missing
uv run bandit -c pyproject.toml -r src
uv run pip-audit
uv build
```

The opt-in live smoke test is the only test that contacts STPT:

```bash
STPT_MCP_RUN_NETWORK_TESTS=1 uv run pytest -m network
```

Run the server locally:

```bash
uv run stpt-mcp serve --transport stdio
uv run stpt-mcp serve --transport http
```

## Layout

- `src/stpt_mcp/config.py` — environment/CLI settings, no global mutable config.
- `src/stpt_mcp/cache.py` — bounded TTL cache, single-flight, stale-on-error.
- `src/stpt_mcp/upstream.py` — HTTP transport, retries, backoff, concurrency.
- `src/stpt_mcp/providers/` — parsers and endpoint adapters. Keep parsing pure.
- `src/stpt_mcp/service.py` — fallback orchestration and provenance.
- `src/stpt_mcp/server.py` — MCP tool definitions and lifespan.
- `src/stpt_mcp/http_app.py` — HTTP transport, security and health endpoint.
- `src/stpt_mcp/data/network.json` — offline GTFS-derived network snapshot.
- `docs/` — architecture, data sources, tools, configuration, testing,
  deployment, operations, security, decisions, plan, and execution log.
- `tests/` — unit, integration, protocol, end-to-end, and opt-in network tests.
- `Dockerfile`, `compose.yaml`, `.github/workflows/ci.yml` — deployment and CI.
- `scripts/generate_network_snapshot.py` — manual snapshot refresh; never in CI.

## Engineering conventions

- Python 3.11+, full type annotations, `from __future__ import annotations`.
- Async I/O only for network and cache paths.
- Provenance timestamps are timezone-aware and serialized as ISO 8601 UTC.
- Never invent an upstream observation timestamp. If a source has no timestamp,
  document that `observed_at` is the fetch time.
- Upstream `200 []` is a valid empty response, not a transport error.
- Parse schedule entries by stop code **and** shape; do not trust page order.
- Filter alerts by active period at the requested instant, not merely by text.
- Tests use deterministic fixtures and injected clocks/transports.
- Public tools return a typed envelope. A data-source problem is represented as
  `status: "partial"` or `"error"` with a structured error, not a traceback.
- Update `docs/IMPLEMENTATION_PLAN.md`, `docs/EXECUTION_LOG.md`, and this file
  when behavior, commands, or guarantees change.

## Before publishing

Follow `docs/RELEASE_CHECKLIST.md`. Verify a clean checkout in a fresh virtual
environment, run every check listed there, inspect the package contents for
secrets and TODOs, and confirm the GitHub remote after pushing.
