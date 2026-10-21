# Execution log

Chronological evidence for the implementation. Times are local
(`Europe/Bucharest`) unless stated otherwise. Commands are recorded as run.

## 2026-10-21 — Phase 0: discovery

- Working directory `/home/vlad/Documents/stpt-mcp` confirmed empty and outside
  the existing Home Assistant integration repository.
- Source cloned read-only to `/tmp/opencode/stpt-ha-integration`; its working
  tree was not modified.
- Verified `uv 0.13.4`, Python `3.13.5`, Node `22`, Git `2.47.3`, GitHub CLI
  `2.46.0`, Make `4.4.1`. Docker and Podman are not installed.
- `gh auth status` shows authenticated account `vladirocox` with `repo`,
  `workflow`, `gist`, and `read:org` scopes.
- Verified endpoints:
  - live arrivals for stop 85 return HTTP 200 with a JSON array;
  - unknown stops return an empty array with HTTP 200;
  - alerts return a JSON `alerts` array with active periods and informed
    entities;
  - vehicles return trip data with epoch-millisecond timestamps;
  - SMTT schedule pages expose `data-code`, `data-shape`, and `data-times`;
  - a `custom_value=DD.MM.YYYY` query changes the returned service date;
  - GTFS-derived data exposes 892 stops, 59 routes, and 186 shape sequences.
- Captured deterministic HTML fixtures for the line 2 schedule, including
  regular, Saturday/Sunday, and holiday/vacation dates.

## 2026-10-21 — Phase 1: foundation

- Initialized independent Git repository in `/home/vlad/Documents/stpt-mcp`.
- Added `AGENTS.md`, `LICENSE`, `NOTICE`, `pyproject.toml`, and this plan/log.
- Chose Python 3.11+, `mcp==2.3.x`, Pydantic 2, HTTPX, BeautifulSoup, and
  Uvicorn. Decisions recorded in ADRs.
- Added an initial test/tooling configuration with Ruff, strict MyPy, pytest,
  coverage, Bandit, and pip-audit.

## 2026-10-21 — Phase 2: implementation

- Added the standalone package under `src/stpt_mcp/`: configuration, errors,
  Pydantic models, bounded TTL/single-flight cache, retrying upstream client,
  offline network index, live/schedule/alerts/vehicles providers, orchestration,
  MCP tools, HTTP app, and CLI.
- Generated `src/stpt_mcp/data/network.json` from the public GTFS-derived route
  manifest, stops index, and stop sequences: 892 stops, 59 routes, 186 shapes,
  feed `4.28`, service range `20261019`–`20261231`, with source checksums and
  attribution.
- Added deterministic fixtures for live arrivals (stops 85 and 60), alerts,
  vehicles, and five schedule pages.
- Added unit, integration, protocol, and end-to-end tests, including real MCP
  stdio/HTTP clients and the mandatory 50-concurrent-call single-flight tests.
- Fixed a CLI defect found by tests: `--transport http` was initially left as a
  string and compared incorrectly against the `Transport` enum; log level is now
  normalized too.
- Fixed a test determinism defect: the stdio e2e test asserted an exact schedule
  fallback count using the real wall clock. Exact counts remain covered by
  fixed-clock integration tests; the e2e test now checks envelope consistency
  and source.

## 2026-10-21 — Phase 3: verification

Commands run from `/home/vlad/Documents/stpt-mcp` with Python 3.11.17 and
`uv 0.13.4`:

- `uv run ruff format --check .` — 67 files already formatted.
- `uv run ruff check .` — all checks passed.
- `uv run mypy` — success, no issues in 43 source files.
- `uv run pytest -m "not network" --cov --cov-report=term-missing` — **194
  passed, 1 deselected**, total coverage **92.96%** (threshold 85%). The
  deselected test is the opt-in live smoke test.
- `uv run bandit -c pyproject.toml -r src` — 0 issues by severity/confidence.
- `uv run pip-audit` — no known vulnerabilities; the local `stpt-mcp` package is
  skipped because it is not on PyPI.
- `uv build` — wheel and sdist built successfully.
- Wheel inspection: `stpt_mcp/data/network.json` and
  `dist-info/entry_points.txt` are present.
- Fresh `/tmp/opencode/stpt-wheel-venv` installed the wheel; `stpt-mcp --version`
  returned `0.1.0`.
- Installed-wheel stdio smoke through the official MCP client: seven tools,
  `get_stop` returned stop 85, `get_service_status` reported feed `4.28`.
- Installed-wheel HTTP smoke through the official MCP client on a loopback
  listener: `/healthz` and `get_stop` succeeded.
- Repository scan found no tokens or secrets. The only `Bearer` string is a
  synthetic test token. No unresolved TODO/FIXME exists in shipped code.

## 2026-10-21 — Phase 4: documentation and automation

- Added README, architecture, data-source, tool, configuration, testing,
  deployment, operations, security, release-checklist, contribution, conduct,
  changelog, and ADR index documentation.
- Added Dockerfile, Compose example, GitHub Actions CI, Dependabot, issue forms,
  and pull-request template.
- Updated `AGENTS.md` with the opt-in network test and repository layout.
- CI adds Python 3.11–3.13 formatting, lint, typing, tests/coverage, Bandit,
  pip-audit, package build, and a container health check.

## 2026-10-21 — Clean-checkout verification

- Committed the deliverable as `36cea50`.
- Cloned the repository to `/tmp/opencode/stpt-clean`; `git log` confirmed the
  same commit and `uv sync --all-groups --locked` created a fresh environment.
- In that clone: Ruff format/lint passed, strict MyPy passed (43 files), pytest
  passed (**194 passed, 1 deselected**) at **92.96%** coverage, Bandit reported
  0 issues, pip-audit reported no known vulnerabilities, and `uv build`
  succeeded.
- Parsed the CI workflow, Dependabot config, issue forms, and Compose file with
  PyYAML; all are syntactically valid. This is not a GitHub Actions run.

## 2026-10-21 — Phase 5: publication

- Created public repository <https://github.com/vladirocox/stpt-mcp>.
- Pushed `main`; the remote branch points at the release history. Description and
  topics were set through the GitHub CLI.
- Initial CI run for `a69c8aa` passed all jobs:
  <https://github.com/vladirocox/stpt-mcp/actions/runs/39587827016>
  - test Python 3.11, 3.12, 3.13
  - security (Bandit and pip-audit)
  - build (wheel/sdist and wheel-content inspection)
  - container (image build and `/healthz` check)
- Dependabot opened three action-version PRs. All checks passed; they were
  squash-merged, producing code/dependency commit `8665661`.
- Final CI run for `8665661` passed all jobs, including the container job:
  <https://github.com/vladirocox/stpt-mcp/actions/runs/39588040771>
- The published CI container build and health check succeeded. This validates the
  Dockerfile in CI; it does not constitute a local Docker run in this
  environment.
- Ran the opt-in live smoke test after publication:
  `STPT_MCP_RUN_NETWORK_TESTS=1 uv run pytest -m network -vv` — **1 passed,
  194 deselected**. It contacted public STPT endpoints for arrivals, alerts,
  vehicles, and status. It remains excluded from CI.

## Environment limitations

- Docker/Podman is unavailable locally. Local `docker build`, `docker run`, and
  `docker compose config` were not executed. The CI container job is the
  verification path and passed.
- The opt-in live network smoke test passed once after publication. It is not part
  of CI; the default suite is deterministic and makes no live STPT requests.
