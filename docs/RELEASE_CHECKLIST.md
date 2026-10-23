# Release checklist

Use this for every tagged release. A checked item means the command or inspection
actually ran and passed. Environmental limitations are recorded in
`docs/EXECUTION_LOG.md`, not silently skipped.

## 1. Freeze and inspect

- [ ] `git status` is clean and the branch is up to date.
- [ ] `CHANGELOG.md` has a dated section for the release.
- [ ] Version in `pyproject.toml` and `src/stpt_mcp/__init__.py` agree.
- [ ] No secrets, tokens, `.env` files, caches, build outputs, or editor files
      are tracked.
- [ ] No real deployment details in docs or commit messages: hostnames,
      domains, IP addresses, ports, server paths, or network setup (see
      `AGENTS.md`).
- [ ] `AGENTS.md`, README, plan, and execution log reflect the current behavior.
- [ ] No unresolved `TODO`/`FIXME` in shipped code.

## 2. Clean environment

- [ ] Clone into a fresh directory or remove `.venv`.
- [ ] `uv sync --all-groups` succeeds from the lockfile.
- [ ] Record Python and `uv` versions.

## 3. Required checks

- [ ] `uv run ruff format --check .`
- [ ] `uv run ruff check .`
- [ ] `uv run mypy`
- [ ] `uv run pytest -m "not network" --cov --cov-report=term-missing`
- [ ] Coverage is at least 85%.
- [ ] `uv run bandit -c pyproject.toml -r src`
- [ ] `uv run pip-audit`
- [ ] `uv build`

Record actual test counts, coverage, and tool output summaries.

## 4. Package inspection

- [ ] Wheel contains `stpt_mcp`, `network.json`, `timetable.json`, metadata, and
      entry point.
- [ ] The bundled network snapshot and timetable cover the release date and have
      the same feed version (`get_service_status`, `scripts/generate_timetable.py`).
- [ ] sdist contains source, tests, docs, examples, license, notice, and
      changelog.
- [ ] No cache directories, coverage files, or secrets in either artifact.
- [ ] Install the wheel into a fresh virtual environment.
- [ ] `stpt-mcp --version` works.
- [ ] stdio smoke test returns a tool list and a successful tool result using a
      deterministic or live source as appropriate.
- [ ] HTTP smoke test starts the server, `/healthz` returns 200, and an MCP client
      completes a tool call.

## 5. Container (if Docker/Podman is available)

- [ ] `docker build -t stpt-mcp:<version> .`
- [ ] Run with a loopback port mapping and explicit allowed hosts.
- [ ] `/healthz` returns 200.
- [ ] `docker compose config` validates the example.
- [ ] Non-root user and health check are present.
- [ ] If Docker is unavailable, state that explicitly; do not claim validation.

## 6. Live smoke (optional, manual)

- [ ] `STPT_MCP_RUN_NETWORK_TESTS=1 uv run pytest -m network`
- [ ] Manually check `get_arrivals` for a stop with live data.
- [ ] Manually check a schedule fallback stop.
- [ ] Manually check alerts and vehicles.
- [ ] Confirm no unexpected request volume.

## 7. Publish

- [ ] Commit the release state.
- [ ] Create an annotated tag `v<version>`.
- [ ] Push the branch and tag.
- [ ] Create a GitHub release with the changelog section. Publishing it runs
      `.github/workflows/publish.yml`, which checks that the tag matches the
      package version, uploads the wheel and sdist to PyPI through trusted
      publishing, and attaches them to the release.
- [ ] Verify the remote commit, tag, CI result, and `Publish` workflow result.
- [ ] Announce with the unofficial/disclaimer wording.

PyPI trusted publishing is configured once on PyPI for project `stpt-mcp`,
owner `vladirocox`, repository `stpt-mcp`, workflow `publish.yml`, and
environment `pypi`. A version can be uploaded to PyPI only once; to fix a bad
release, publish a new version.

## 8. Post-release

- [ ] Verify installation from PyPI in a fresh virtual environment:
      `pip install stpt-mcp==<version>` and `stpt-mcp --version`.
- [ ] Verify installation from the release assets.
- [ ] Watch CI and issues for regressions.
- [ ] Open follow-up issues for known limitations.
- [ ] Update the execution log with publication evidence.
