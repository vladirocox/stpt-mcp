# Execution log

Chronological evidence for the implementation. Times are local
(`Europe/Bucharest`) unless stated otherwise. Commands are recorded as run.

## 2026-10-21 — Phase 0: discovery

- The project working directory was confirmed empty and outside the existing
  Home Assistant integration repository.
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

- Initialized an independent Git repository.
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

Commands run from the project working directory with Python 3.11.17 and
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

## 2026-10-21 — v0.2.0: auth token generation

- Added `stpt-mcp token`, which prints a new 256-bit URL-safe bearer token and
  exits. `STPT_MCP_AUTH_TOKEN` remains the runtime setting.
- Added unit tests for token validity, uniqueness, settings validation, and CLI
  output.
- Updated README, configuration, deployment, security, AGENTS, and changelog.
- Verification for v0.2.0: Ruff format/lint passed, strict MyPy passed, pytest
  reported **196 passed, 1 deselected** at **93.02%** coverage, Bandit reported
  0 issues, pip-audit found no known vulnerabilities, and `uv build` produced
  `stpt_mcp-0.2.0` wheel and sdist.

## 2026-10-21 — deployment to a private host (v0.2.0)

- CI for commit `4f99792` passed all jobs: Python 3.11, 3.12, 3.13, security,
  build, and container (run `39590112771`).
- Installed the `main` checkout in `/opt/stpt-mcp` on the deployment host under
  Python 3.13.5 with a dedicated `.venv`.
- Created `stpt-mcp.service` running as a dedicated service account, enabled at
  boot. It bound `0.0.0.0:9000`, used an explicit `STPT_MCP_ALLOWED_HOSTS`
  list, and read the bearer token from a root-only environment file.
- Opened TCP 9000 in `ufw`; the existing router forwarding for port 9000 was
  confirmed by reaching the service through the public hostname.
- Verification:
  - The public `/healthz` endpoint returned HTTP 200
    `{"status":"ok","service":"stpt-mcp","version":"0.2.0"}`; the same request
    also succeeded through an external reader.
  - MCP `initialize` without a token returned 401; a wrong token returned 401;
    the configured token returned 200.
  - The official MCP client negotiated protocol `2026-07-28`, listed all seven
    tools, resolved stop 85 (`Catedrala Mitropolitană`), returned three live
    arrivals with `source=live`, `stale=false`, and reported the service ready
    with feed `4.28`.
  - A service restart preserved the token and authentication behavior; the
    unit is `enabled` and `active`.
- Added a removal note in the service account's home directory with
  stop/disable, unit removal, token removal, installation deletion, `ufw`
  rollback, router-forwarding rollback, and verification commands.
- The endpoint used plain HTTP on port 9000. The bearer token should be treated
  as sensitive; TLS via a reverse proxy is recommended if the endpoint is kept
  public long-term. **Superseded by the HTTPS deployment below.**

## 2026-10-21 — HTTPS for the public MCP endpoint

- The deployment host already runs Caddy on ports 80/443 with a Let's Encrypt
  certificate for the public hostname.
- Backed up the Caddy config, Caddy Compose file, and systemd unit before
  changing them.
- Moved the MCP process to an internal private port so Caddy could own the
  public port 9000. Updated `STPT_MCP_ALLOWED_HOSTS` and
  `STPT_MCP_ALLOWED_ORIGINS` for the public hostname and local checks.
- Added a Caddy site block for the public HTTPS endpoint using the existing
  certificate, reverse-proxying to the internal port with `flush_interval -1`
  for Streamable HTTP streaming.
- Added the public port to the Caddy Compose port mappings and recreated the
  container after `caddy validate` passed.
- Verification:
  - The public HTTPS `/healthz` endpoint returned HTTP 200 with
    `{"status":"ok","service":"stpt-mcp","version":"0.2.0"}` through the public
    hostname and through an external reader.
  - The certificate is the existing Let's Encrypt certificate, valid through
    2026-12-21. Caddy loads it explicitly and does not renew it, so the existing
    renewal process must be confirmed before that date.
  - Plain HTTP to the public port returns 400; the endpoint is HTTPS-only.
  - MCP `initialize` without a token returned 401 and with the configured token
    returned 200 over HTTPS.
  - The official MCP client negotiated protocol `2026-07-28`, listed seven
    tools, resolved stop 85, returned three live arrivals with `source=live`,
    and reported the service ready with feed `4.28`.
  - The existing root site still returns its 301 redirect; the Caddy change did
    not alter the unrelated site blocks.
- Updated the deployment guide with Caddy `flush_interval -1` and non-standard
  HTTPS port guidance, and updated the remote removal note with Caddy rollback
  instructions.

## 2026-10-21 — repository hardening and public HTTPS guide

- Enabled `main` branch protection through the GitHub API:
  force pushes and branch deletion are blocked, administrators are subject to
  the rules, and pull requests require the Python 3.11/3.12/3.13 test jobs,
  `security`, and `build` checks. Direct pushes to `main` are rejected; changes
  go through a pull request.
- Audited the current tree and all commits for bearer tokens, passwords, private
  keys, `.env` files, and obvious cloud/API credentials; none were found.
- Removed unnecessary internal deployment metadata (private hostname, LAN
  addresses, internal Docker gateway, service-account paths, and backup names)
  from the public execution log and implementation plan. The public hostname
  and public IP were also removed from the repository docs.
- Added [Public HTTPS](PUBLIC_HTTPS.md): an end-to-end systemd + Caddy guide
  covering DNS, certificates, firewall, authentication, client configuration,
  renewal, troubleshooting, and removal.
- Added copy-paste examples under `deploy/` and an authenticated
  `examples/https-client.json`; linked them from the README and deployment
  documentation. Added `deploy/` to the source distribution.
- Exposed `/mcp` on standard HTTPS port 443 through the reverse proxy for hosted
  clients that do not connect to non-standard ports. The existing port 9000
  endpoint remains available.

## Environment limitations

- Docker/Podman is unavailable locally. Local `docker build`, `docker run`, and
  `docker compose config` were not executed. The CI container job is the
  verification path and passed.
- The opt-in live network smoke test passed once after publication. It is not part
  of CI; the default suite is deterministic and makes no live STPT requests.

## 2026-10-21 — v0.3.0: OAuth 2.1 for hosted clients

- Added `src/stpt_mcp/oauth.py` using the MCP SDK authorization-server
  interfaces: RFC 8414 discovery metadata, RFC 9728 protected-resource metadata,
  dynamic client registration, authorization-code + PKCE (`S256`), refresh
  tokens, revocation, and a single-owner browser sign-in page.
- Added bounded in-memory stores for clients, authorization codes, login
  requests, access tokens, and refresh tokens. Tokens are opaque random values
  and are lost on restart.
- Added `STPT_MCP_PUBLIC_URL`, `STPT_MCP_OAUTH_PASSWORD`, redirect allowlist, and
  token-lifetime settings. Static bearer tokens remain valid when OAuth is
  enabled.
- Restricted dynamic registration to the configured redirect allowlist plus
  loopback HTTP callbacks, and advertised the public-client `none` token method
  in discovery metadata.
- Updated the MCP server and HTTP app to use the SDK bearer middleware when OAuth
  is enabled, with OAuth discovery, login, token, and revocation routes.
- Added OAuth tests for discovery, registration, login, code exchange, refresh,
  static-token compatibility, wrong passwords, untrusted redirect URIs, and
  resource mismatch.
- Verification for v0.3.0: Ruff format/lint passed, strict MyPy passed, pytest
  reported **211 passed, 1 deselected** at **91.30%** coverage, Bandit reported
  no issues, pip-audit found no known vulnerabilities, and `uv build` produced
  the `stpt_mcp-0.3.0` wheel and sdist.
- Deployed v0.3.0 on the private host, added
  `STPT_MCP_PUBLIC_URL=https://vladiro.go.ro` to the root-only environment file,
  and added the OAuth path routes to the existing Caddy site on port 443.
- Verified through the public HTTPS endpoint:
  - discovery metadata returns `200` with issuer `https://vladiro.go.ro`, DCR,
    PKCE, refresh tokens, and public-client auth methods;
  - protected-resource metadata returns `200` and identifies
    `https://vladiro.go.ro/mcp`;
  - unauthenticated MCP returns `401` with a `resource_metadata` challenge;
  - DCR, browser login, authorization-code exchange, refresh-token issuance, and
    an authenticated MCP `initialize` all succeeded;
  - the static bearer token still returns `200`, and a wrong token returns `401`;
  - the existing root redirect still returns `301` to its configured
    destination.

## 2026-10-21 — v0.3.0 release and installation check

- Published `v0.3.0` with the wheel and source distribution assets at
  <https://github.com/vladirocox/stpt-mcp/releases/tag/v0.3.0>.
- Verified the release asset installs in a fresh Python 3.11 virtual
  environment and reports `stpt-mcp 0.3.0`.
- Confirmed the deployed public endpoint still accepts the legacy static bearer
  token on both `https://vladiro.go.ro/mcp` and
  `https://vladiro.go.ro:9000/mcp` while OAuth is enabled.

## 2026-10-21 — v0.3.1 maintenance release

- Merged Dependabot PR [#4](https://github.com/vladirocox/stpt-mcp/pull/4),
  bumping `mypy` from 1.20.2 to 2.4.0, and opened and merged PR
  [#11](https://github.com/vladirocox/stpt-mcp/pull/11) for the release state.
- Verification for v0.3.1: Ruff format/lint passed; strict MyPy passed with
  mypy 2.4.0; pytest reported **211 passed, 1 deselected** at **91.30%**
  coverage; Bandit reported no issues; pip-audit found no known vulnerabilities
  in the auditable dependencies; and `uv build` produced the
  `stpt_mcp-0.3.1` wheel and sdist. pip-audit skipped the local `stpt-mcp`
  package because it is not published on PyPI.
- Inspected both artifacts for secrets, caches, and unresolved TODO/FIXME
  markers; none were present. Installed the wheel in a fresh Python 3.11
  virtual environment and confirmed `stpt-mcp 0.3.1`.
- Ran stdio and authenticated HTTP smoke tests from the installed wheel; each
  returned the seven tools and a successful structured `search_stops` or
  `get_service_status` result.
- CI passed the Python 3.11, 3.12, and 3.13 test jobs plus the security, build,
  and container jobs.
- Published
  [v0.3.1](https://github.com/vladirocox/stpt-mcp/releases/tag/v0.3.1) with the
  wheel and sdist assets, then installed the published wheel in another fresh
  Python 3.11 environment; it reports `stpt-mcp 0.3.1`.
- Updated `/opt/stpt-mcp` to commit `2323672`, installed the 0.3.1 wheel in the
  service virtual environment, and restarted `stpt-mcp`. The service is active
  and enabled; `/healthz` returns `0.3.1`.
- Verified through the public HTTPS endpoints:
  - OAuth discovery 200, protected-resource metadata 200, dynamic registration
    201, login page 200, authorization redirect 302, code exchange 200, refresh
    exchange 200, and an OAuth-authenticated MCP client listed seven tools and
    returned `get_service_status` with `status: ok` and `ready: true`;
  - the static bearer token listed seven tools and returned successful
    `get_service_status` and `search_stops` results on both
    `https://vladiro.go.ro/mcp` and `https://vladiro.go.ro:9000/mcp`;
  - the legacy health endpoint reports `0.3.1`, and the root site still returns
    `301` to its configured destination.
- Re-ran the opt-in live smoke test with
  `STPT_MCP_RUN_NETWORK_TESTS=1 uv run pytest -m network`; it passed
  (**1 passed, 211 deselected**) against the live STPT live, alerts, and
  vehicles endpoints.
- The restart invalidated the in-memory OAuth tokens as documented; the static
  bearer token remains valid. Existing OAuth clients must sign in again.
- The deployment host could not reach its own public hostname because of NAT
  hairpin behaviour. Public endpoint checks were executed from the external
  workstation that manages the host.
