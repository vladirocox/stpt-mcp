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
- The GitHub CLI was authenticated as the repository owner.
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

## 2026-10-21 — self-hosted deployment (v0.2.0)

Deployment specifics (hostname, addresses, ports, paths, and network setup) are
intentionally not recorded in this public log.

- CI for commit `4f99792` passed all jobs: Python 3.11, 3.12, 3.13, security,
  build, and container (run `39590112771`).
- Installed the `main` checkout on a self-hosted deployment under Python 3.13.5
  with a dedicated virtual environment and a systemd service enabled at boot,
  using an explicit `STPT_MCP_ALLOWED_HOSTS` list. The bearer token is kept in a
  protected environment file on the host, not in the repository.
- Verification:
  - `/healthz` returned HTTP 200
    `{"status":"ok","service":"stpt-mcp","version":"0.2.0"}`.
  - MCP `initialize` without a token returned 401; a wrong token returned 401;
    the configured token returned 200.
  - The official MCP client negotiated protocol `2026-07-28`, listed all seven
    tools, resolved stop 85 (`Catedrala Mitropolitană`), returned three live
    arrivals with `source=live`, `stale=false`, and reported the service ready
    with feed `4.28`.
  - A service restart preserved the token and authentication behavior.
- Removal and rollback instructions are kept with the deployment, outside the
  repository.

## 2026-10-21 — HTTPS for the public MCP endpoint

- Put the endpoint behind a Caddy reverse proxy with TLS, reverse-proxying to an
  internal port with `flush_interval -1` for Streamable HTTP streaming, after
  `caddy validate` passed. Existing proxy configuration was backed up first.
- Verification:
  - The HTTPS `/healthz` endpoint returned HTTP 200 with
    `{"status":"ok","service":"stpt-mcp","version":"0.2.0"}`.
  - Plain HTTP to the MCP port is rejected; the endpoint is HTTPS-only.
  - MCP `initialize` without a token returned 401 and with the configured token
    returned 200 over HTTPS.
  - The official MCP client negotiated protocol `2026-07-28`, listed seven
    tools, resolved stop 85, returned three live arrivals with `source=live`,
    and reported the service ready with feed `4.28`.
  - Unrelated sites on the same proxy were unaffected.
- Updated the deployment guide with Caddy `flush_interval -1` and non-standard
  HTTPS port guidance.

## 2026-10-21 — repository hardening and public HTTPS guide

- Enabled `main` branch protection through the GitHub API:
  force pushes and branch deletion are blocked, administrators are subject to
  the rules, and pull requests require the Python 3.11/3.12/3.13 test jobs,
  `security`, and `build` checks. Direct pushes to `main` are rejected; changes
  go through a pull request.
- Audited the current tree and all commits for bearer tokens, passwords, private
  keys, `.env` files, and obvious cloud/API credentials; none were found.
- Removed internal deployment metadata from the public execution log and
  implementation plan.
- Added [Public HTTPS](PUBLIC_HTTPS.md): an end-to-end systemd + Caddy guide
  covering DNS, certificates, firewall, authentication, client configuration,
  renewal, troubleshooting, and removal.
- Added copy-paste examples under `deploy/` and an authenticated
  `examples/https-client.json`; linked them from the README and deployment
  documentation. Added `deploy/` to the source distribution.
- Exposed `/mcp` on standard HTTPS port 443 through the reverse proxy for hosted
  clients that do not connect to non-standard ports.

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
- Deployed v0.3.0 on the self-hosted deployment with `STPT_MCP_PUBLIC_URL` set
  to its public HTTPS origin, and added the OAuth path routes to the reverse
  proxy.
- Verified through the public HTTPS endpoint:
  - discovery metadata returns `200` with the configured issuer, DCR, PKCE,
    refresh tokens, and public-client auth methods;
  - protected-resource metadata returns `200` and identifies the `/mcp`
    resource;
  - unauthenticated MCP returns `401` with a `resource_metadata` challenge;
  - DCR, browser login, authorization-code exchange, refresh-token issuance, and
    an authenticated MCP `initialize` all succeeded;
  - a wrong token returns `401`.

## 2026-10-21 — v0.3.0 release and installation check

- Published `v0.3.0` with the wheel and source distribution assets at
  <https://github.com/vladirocox/stpt-mcp/releases/tag/v0.3.0>.
- Verified the release asset installs in a fresh Python 3.11 virtual
  environment and reports `stpt-mcp 0.3.0`.

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
- Updated the self-hosted deployment to commit `2323672`, installed the 0.3.1
  wheel in the service virtual environment, and restarted the service. It is
  active and enabled; `/healthz` returns `0.3.1`.
- Verified through the public HTTPS endpoints:
  - OAuth discovery 200, protected-resource metadata 200, dynamic registration
    201, login page 200, authorization redirect 302, code exchange 200, refresh
    exchange 200, and an OAuth-authenticated MCP client listed seven tools and
    returned `get_service_status` with `status: ok` and `ready: true`;
  - bearer-token authentication listed seven tools and returned successful
    `get_service_status` and `search_stops` results;
  - the health endpoint reports `0.3.1`.
- Re-ran the opt-in live smoke test with
  `STPT_MCP_RUN_NETWORK_TESTS=1 uv run pytest -m network`; it passed
  (**1 passed, 211 deselected**) against the live STPT live, alerts, and
  vehicles endpoints.
- The restart invalidated the in-memory OAuth tokens as documented. Existing
  OAuth clients must sign in again.
- Public endpoint checks were executed from outside the deployment host.

## 2026-10-21 — v0.3.2 tool surface scope

- Kept the public tool surface at the seven existing tools. A route-detail
  tool that returns a line's ordered stop list or geometry was considered and
  rejected, so no `get_route` tool was added.
- Documented the route information the current tools already expose in
  `docs/TOOLS.md`: `list_routes` returns lines, names, modes, categories, and
  colors; `get_arrivals` returns line, mode, direction, and destination;
  `get_stop` returns the serving lines with their directions and destinations
  plus neighboring stops; `get_vehicles` returns route, trip, and shape
  identifiers. The bundled snapshot keeps the ordered stop lists and shapes for
  internal lookups, but no tool returns them.
- Recorded the decision in `docs/IMPLEMENTATION_PLAN.md` as a scope section and
  as an explicit non-goal.
- Bumped the package version to 0.3.2 and refreshed the documented release
  artifact references.
- Verification for v0.3.2: Ruff format/lint passed; strict MyPy passed;
  pytest reported **211 passed, 1 deselected** at **91.30%** coverage;
  Bandit reported no issues; pip-audit found no known vulnerabilities in the
  auditable dependencies; `uv build` produced the `stpt_mcp-0.3.2` wheel and
  sdist. pip-audit skipped the local `stpt-mcp` package because it is not
  published on PyPI.
- Inspected both artifacts for secrets, caches, and unresolved TODO/FIXME
  markers; none were present. Installed the wheel in a fresh Python 3.11
  virtual environment and confirmed `stpt-mcp 0.3.2`.
- Ran the stdio and authenticated HTTP smoke tests from the installed wheel;
  each returned the seven tools and a successful structured result.
- Re-ran the opt-in live smoke test with
  `STPT_MCP_RUN_NETWORK_TESTS=1 uv run pytest -m network`; it passed
  (**1 passed, 211 deselected**) against the live STPT live, alerts, and
  vehicles endpoints.

## 2026-10-21 — v0.3.3 documentation release

- Added `assets/chat-arrivals.png`, a chat screenshot of the MCP tools
  answering an arrival question, and a "Related projects" section in the
  README that points to the Home Assistant integration and the Inky pHAT
  dashboard.
- Checked the screenshot for personal data before publishing it. A high-pass
  filter over the full frame confirmed the frame contains only the chat
  content and the messenger wallpaper, with no phone number, watermark, or PNG
  text metadata.
- Added the matching cross-references to the Home Assistant integration
  repository: the English README, the Romanian README, and the HACS `info.md`,
  each with the same screenshot and a link back to this repository.
- Included `assets/` in the source distribution so the README image resolves
  from the published sdist.
- Relaxed the `AGENTS.md` rule that forbade touching `stpt-ha-integration` so
  it now forbids a dependency rather than a documentation cross-reference. The
  two projects stay independently deployable.
- Bumped the package version to 0.3.3 and refreshed the documented release
  artifact references.
- Verification for v0.3.3: Ruff format/lint passed; strict MyPy passed; pytest
  reported **211 passed, 1 deselected** at **91.30%** coverage; Bandit reported
  no issues; pip-audit found no known vulnerabilities in the auditable
  dependencies; and `uv build` produced the `stpt_mcp-0.3.3` wheel and sdist
  with the asset present.

## 2026-10-23 — v0.3.4 PyPI publication release (prepared)

- Named the author as Vlad Ohai in `LICENSE`, `pyproject.toml`, and a new
  README "Author" section.
- Added `.github/workflows/publish.yml`. On a published GitHub release it
  checks that the tag matches both `pyproject.toml` and `__init__.py`, runs
  `uv build` and `twine check --strict`, uploads to PyPI through trusted
  publishing (environment `pypi`, `id-token: write`), and attaches the wheel and
  sdist to the release.
- Documented `pip install stpt-mcp`, `uvx stpt-mcp`, and a `uvx` stdio client
  configuration in the README. Made README links and the screenshot absolute
  GitHub URLs so the PyPI project page renders them.
- Bumped the package version to 0.3.4 and refreshed the documented release
  artifact references; `uv lock` updated the project entry.
- Verification for v0.3.4 (local): Ruff format/lint passed; strict MyPy passed;
  pytest reported **211 passed, 1 deselected** at **91.30%** coverage; Bandit
  reported no issues; pip-audit found no known vulnerabilities in the auditable
  dependencies; `uv build` produced the `stpt_mcp-0.3.4` wheel and sdist;
  `twine check --strict` passed for both; the wheel metadata names
  `Vlad Ohai` and the long description has no relative links.
- Installed the wheel in a fresh Python 3.11 virtual environment:
  `stpt-mcp --version` printed `stpt-mcp 0.3.4`, and a stdio
  `initialize` + `tools/list` smoke test returned version 0.3.4 and the seven
  tools.
- Not yet done: PyPI trusted-publisher setup (owner action), the `v0.3.4`
  GitHub release, the `Publish` workflow run, and installation from PyPI. None
  of these is claimed here.

## 2026-10-23 — v0.3.4 published on PyPI

- The repository owner registered the PyPI trusted publisher for `stpt-mcp`
  (workflow `publish.yml`, environment `pypi`), merged PR
  [#16](https://github.com/vladirocox/stpt-mcp/pull/16), and published the
  [v0.3.4](https://github.com/vladirocox/stpt-mcp/releases/tag/v0.3.4) release.
- The `Publish` workflow succeeded in all three jobs (build, PyPI upload, and
  release assets). The release carries the wheel and sdist.
- PyPI serves <https://pypi.org/project/stpt-mcp/0.3.4/> with author
  `Vlad Ohai`. `pip install stpt-mcp==0.3.4` in a fresh Python 3.11
  environment reports `stpt-mcp 0.3.4`.
- Started the server with `uvx stpt-mcp serve --transport stdio`, as an MCP
  client configuration would, and connected with the official MCP client: it
  reported version 0.3.4 and seven read-only tools; `search_stops`,
  `list_routes`, and `get_service_status` succeeded. The live-data tools
  returned a structured `upstream_unavailable` error because that test
  environment could not reach STPT; this is the documented behavior, not a
  defect.

## 2026-10-23 — v0.3.5 documentation cleanup

- Removed real deployment details from this log and the implementation plan
  and kept the verification evidence in generic terms. Earlier entries
  describing the self-hosted deployment were rewritten accordingly.
- Added an `AGENTS.md` rule and a release-checklist item against recording
  deployment details.
- Verification for v0.3.5 (local): Ruff format/lint passed; strict MyPy passed;
  pytest reported **211 passed, 1 deselected** at **91.30%** coverage; Bandit
  reported no issues; pip-audit found no known vulnerabilities in the auditable
  dependencies; `uv build` produced the `stpt_mcp-0.3.5` wheel and sdist;
  `twine check --strict` passed for both; a scan of both artifacts found no
  deployment hostnames, addresses, or paths; the metadata and `LICENSE` name
  `Vlad Ohai`.

## 2026-10-23 — v0.3.6 release check

- Verified from a fresh session: tag `v0.3.6` points at the PR #19 merge commit;
  the GitHub release carries the wheel and sdist; the `Publish` workflow run
  succeeded; PyPI serves 0.3.6 and `pip install stpt-mcp==0.3.6` in a fresh
  virtual environment reports 0.3.6. The deployed wheel's SHA-256 equals the
  PyPI wheel's.

## 2026-10-23 — v0.3.7 investigation

- The live arrivals endpoint was frozen on every sampled stop: 57 of 64 stops
  returned byte-identical payloads 3.5 minutes apart (7 were empty), each with
  its own stale implied clock between 12:48 and 23:33 local. Stop 135 showed line
  14 at `16:10|10, 16:26|26, 16:32|32, 16:33|33` from at least 12:55 to 13:29,
  an implied clock of 16:00.
- Vehicle positions were current: 131 vehicles, median age 28 s, and the feed's
  own `is_stale` was false.
- `smtt.ro` refused TCP connections from two independent networks, so every
  schedule page load failed. The official GTFS zip was obtained from the
  Mobility Database mirror (`mdb-2868`, SHA-256 `3408e0c3…`); it is feed 4.28,
  identical in version, service range, and trip count to `network.json`, and
  all 13 530 trips follow the snapshot's shapes exactly.
- Reference check: on 2026-10-23 around 12:55 a map service showed line 14 at
  Piața Mărăști at 12:57. The GTFS timetable lists 12:59 (trip
  `14.1_WDSC_T_04_08`), a 2-minute difference; the frozen feed implied 13:05.
- No GTFS-Realtime feed for Timișoara was found (Mobility Database catalogue,
  `live.stpt.ro` assets).
- An adversarial multi-agent review of the change confirmed eight defects, all
  fixed with tests: a frozen clock read one day later counted as progress (false
  fresh); DST-night display times resolved with the wrong offset (false stale);
  cached predictions already past showed as due now; the timetable's last-day
  after-midnight trips were lost; the stdio e2e test would fail after the
  timetable expires; three documentation inaccuracies.
- A second review round over those fixes confirmed five more, all fixed with
  tests: a jump's suspicion lasted only one fetch, so a frozen stop came back as
  fresh; a frozen clock read exactly one day later matched elapsed time; a
  one-minute step back of the median counted as a jump; a non-retryable
  timetable gap hid a retryable live outage; the timetable's last day gave no
  warning that later days are missing.
- A third round, focused on the clock tracking, confirmed three more, all fixed
  with tests: an identical frozen reading resolved an hour apart across the DST
  fold looked like progress; a response without display times erased a stop's
  suspicion; a stop once not fresh and queried only every 12 hours stayed
  unverified forever on a healthy feed.
- A fourth round over the committed branch confirmed two more, fixed with tests:
  a stop that froze after running accepted a swapped snapshot matching the
  clock as progress; a frozen stop polled all day lost its history at a 12-hour
  comparability reset and was labelled fresh once a day. Comparability now uses
  the gap between fetches and readings compare as times of day.
- Local verification: Ruff format/lint passed; strict MyPy passed; pytest
  reported **246 passed, 1 skipped** (247 collected, up from 226 in 0.3.6);
  coverage **91.7%**; Bandit reported no issues; pip-audit found no known
  vulnerabilities; `uv build` and `twine check --strict` passed. Reverting each fix individually made its
  targeted tests fail (frozen-clock detector, clock jump, DST resolution,
  unverified-as-live, withholding, future vehicle timestamps, stale-cache live
  flag, past live and schedule departures, timetable grace day).

## 2026-10-23 — v0.3.7 published and deployed

- PR [#21](https://github.com/vladirocox/stpt-mcp/pull/21) merged after the
  required checks passed (tests on Python 3.11–3.13, security, build,
  container); no protection bypass was used. Tag `v0.3.7` points at the merge
  commit `01db5c1`.
- The [v0.3.7 release](https://github.com/vladirocox/stpt-mcp/releases/tag/v0.3.7)
  ran the `Publish` workflow (run 39981963172): build, PyPI upload through
  trusted publishing, and release assets all succeeded. PyPI serves
  <https://pypi.org/project/stpt-mcp/0.3.7/> (wheel SHA-256 `f3452000…`);
  `pip install stpt-mcp==0.3.7` in a fresh Python 3.12 environment reports
  0.3.7, includes the timetable, and no longer installs BeautifulSoup.
- The self-hosted deployment was upgraded from 0.3.6 after backing up its
  virtual environment: the PyPI wheel was downloaded, its SHA-256 compared with
  PyPI's, and installed; the service restarted cleanly with no errors logged.
  Health and MCP calls succeeded on the host and through the public HTTPS
  endpoint.
- Live check after the upstream feed recovered (it alternated between working
  and HTTP 503): `get_arrivals(stop_id="135", line="14")` returned
  `source: live`, `freshness: fresh` at 19:00:14 (skew −14 s: 19:02/2,
  19:20/20, 19:35/35 min) and again at 19:17:46 (skew +14 s: 19:18/1,
  19:35/18, 19:51/34), so the 19:35 trip counted down from 35 to 18 minutes and
  `arrival_time - now` matched `eta_minutes` both times. During the 503 periods
  the same call returned the timetable (`partial`, `upstream_http_error`,
  `source: schedule`, `live: false`; 19:07, 19:21, 19:36, … for line 14,
  identical to the GTFS `stop_times`), counting down minute by minute.

