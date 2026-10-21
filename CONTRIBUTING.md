# Contributing

Thanks for helping improve `stpt-mcp`. This is an unofficial, read-only transit
server; correctness of provenance and restraint toward upstream services matter
more than adding endpoints.

## Before you start

- Read [AGENTS.md](AGENTS.md) and the non-negotiable constraints.
- For security issues, follow [SECURITY.md](SECURITY.md), not a public issue.
- Keep the project independent of Home Assistant and `stpt-ha-integration`.
- Do not make tests or CI depend on live STPT services.

## Development setup

```bash
git clone https://github.com/vladirocox/stpt-mcp.git
cd stpt-mcp
uv sync --all-groups
```

Run the server:

```bash
uv run stpt-mcp serve --transport stdio
uv run stpt-mcp serve --transport http
```

## Required checks

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
uv run pytest --cov --cov-report=term-missing
uv run bandit -c pyproject.toml -r src
uv run pip-audit
uv build
```

CI runs these on Python 3.11–3.13. The coverage floor is 85%.

## Code conventions

- Python 3.11+, full annotations, `from __future__ import annotations`.
- Async I/O only for network and cache paths.
- Keep parsers pure and testable; no I/O in parsing functions.
- Use `StptError` with a stable `ErrorCode` for expected failures.
- Return typed envelopes; never leak a traceback to an MCP client.
- Preserve provenance: source, fetch/observation times, staleness, fallback
  reason, and warnings.
- Never invent an upstream observation timestamp.
- Treat upstream `200 []` as valid empty data, not a transport error.
- Cache keys must include every input that changes upstream results.
- Keep caches and upstream concurrency bounded.

## Tests

- Add deterministic fixtures, not live calls.
- Inject clocks, sleeps, and transports.
- If you change caching or fallback, extend
  `tests/integration/test_concurrency.py` and the arrival orchestration tests.
- If you change MCP schemas, update protocol/e2e assertions.
- Run the opt-in live smoke test only when needed:
  `STPT_MCP_RUN_NETWORK_TESTS=1 uv run pytest -m network`.

## Documentation

Update the relevant files when behavior changes:

- `README.md` for user-facing setup and guarantees;
- `docs/TOOLS.md` for schemas and semantics;
- `docs/CONFIGURATION.md` for new settings;
- `docs/ARCHITECTURE.md` and ADRs for structural decisions;
- `docs/IMPLEMENTATION_PLAN.md` and `docs/EXECUTION_LOG.md` for delivery status.

## Network snapshot updates

`scripts/generate_network_snapshot.py` contacts upstream and rewrites
`src/stpt_mcp/data/network.json`. Run it intentionally, inspect the diff, verify
the feed version and service range, and run the full suite. CI never runs it.

## Commits and pull requests

- Keep commits focused and messages descriptive.
- Describe the behavior change, tests run, and any limitation.
- Do not commit tokens, credentials, `.env` files, caches, or build artifacts.
- Fill out the pull-request template.
- By contributing, you agree that your contribution is licensed under the MIT
  License and that you will follow the code of conduct.

## Data and attribution

Do not remove attribution or imply STPT endorsement. See [NOTICE](NOTICE).
