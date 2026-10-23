# Testing

## Principles

- The default suite never contacts live STPT services.
- Time, randomness, and HTTP are injected or replaced with deterministic doubles.
- Concurrency guarantees are tested directly, not inferred from unit tests.
- Protocol behavior is tested with the official MCP `Client`, not a hand-rolled
  JSON-RPC shim.
- Live checks are opt-in and never run in CI.

## Layers

| Layer | Location | What it proves |
| --- | --- | --- |
| Unit | `tests/unit/` | Config validation, cache policy, retries, parsers, network search, HTTP middleware. |
| Integration | `tests/integration/` | Live-first fallback, stale data, error envelopes, filters, concurrency. |
| Protocol | `tests/protocol/` | Tool listing, schemas, structured content, error results via the SDK client. |
| End-to-end | `tests/e2e/` | Real stdio subprocess, real Streamable HTTP server, auth/host/origin/body limits, CLI HTTP process. |
| Network | `tests/network/` | Opt-in live smoke test; skipped unless explicitly enabled. |

## Deterministic harness

`tests/support.py` provides:

- `MutableClock`: monotonic and wall clocks advance together.
- `FakeSTPT`: URL-keyed fake with request accounting, delays, and scripted faults.
- `httpx.MockTransport` wiring for most tests.
- `FakeUpstreamHTTPServer`: a real loopback HTTP server for subprocess e2e tests.
- `run_uvicorn`: a real loopback ASGI listener on a random port.
- `asgi_request`: a direct ASGI caller for middleware tests.
- `make_service` / `make_test_settings`: service factory with injected clocks.

The fixed clock is `2026-10-21T10:30:00Z` (13:30 in `Europe/Bucharest`). Fixtures
under `tests/fixtures/` contain captured live arrivals for stops 85 and 60,
alerts, vehicles, and five schedule pages.

The alert fixture intentionally includes an alert with no parseable active period.
It must be treated as active with a warning; dropping it silently would be a
regression.

## Mandatory concurrency proofs

`tests/integration/test_concurrency.py` covers:

1. **50 concurrent calls, same stop, live available** — exactly one upstream live
   request; no schedule refresh; 49 coalesced cache waits.
2. **50 concurrent calls, same stop, live empty** — exactly one schedule refresh,
   one request per required schedule page, 49 coalesced waits.
3. **50 concurrent calls split across two stops** — one live request per stop and
   no cross-contamination of lines, stop IDs, or results.
4. **Immediate TTL hit** — zero upstream requests after warm-up.
5. **Upstream concurrency slot** — the 50-call burst uses one upload slot.

These tests fail if single-flight, key composition, or cache-hit behavior
regresses.

## Running

```bash
uv sync --all-groups
uv run pytest
uv run pytest --cov --cov-report=term-missing
```

The suite enforces `--strict-markers` and `--strict-config`. Warnings are errors
except for known third-party deprecations.

### Selective runs

```bash
uv run pytest tests/unit
uv run pytest -m "not e2e"
uv run pytest tests/integration/test_concurrency.py -vv
```

### Opt-in live test

```bash
STPT_MCP_RUN_NETWORK_TESTS=1 uv run pytest -m network -vv
```

This contacts public STPT endpoints and is excluded from CI. It passed once
during the v0.1.0 publication checks. It is a smoke test, not a guarantee of
upstream correctness.

## Coverage

Coverage is branch-aware, measures `stpt_mcp`, and fails below **85%**. At the
v0.3.5 verification run the suite had **211 tests passing, 1 deselected** and
**91.30%** total coverage. The exact numbers are recorded in the execution log.

Coverage is not a substitute for the concurrency and real-client tests, which
assert behavior rather than line execution.

## CI

GitHub Actions runs on Python 3.11, 3.12, and 3.13:

- `ruff format --check`
- `ruff check`
- `mypy`
- `pytest -m "not network"` with coverage
- Bandit and pip-audit in a separate job
- `uv build`
- a container build and `/healthz` smoke check

Docker/Podman was unavailable in the development environment, so the container
job has not been run locally. CI builds the image and checks `/healthz`; that job
passed on the v0.1.0 publication run.

## Adding tests

- Add parser cases to `tests/unit/` with small, focused fixtures.
- Add orchestration cases to `tests/integration/` using `service_factory`.
- Add MCP-visible behavior to `tests/protocol/` or `tests/e2e/`.
- Do not assert on live data or wall-clock timing.
- If you add an upstream field, update the fixture and assert the mapping.
- If you change cache semantics, extend the concurrency tests first.
