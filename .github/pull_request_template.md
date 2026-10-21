## Summary

Describe the change and why it is needed.

## Behavior

- [ ] No behavior change
- [ ] Tool schema or output changed
- [ ] Configuration changed
- [ ] Cache, fallback, or provenance behavior changed
- [ ] Documentation only

## Verification

Paste the commands that actually ran and their results. Do not check a box for a
check you did not run.

- [ ] `uv run ruff format --check .`
- [ ] `uv run ruff check .`
- [ ] `uv run mypy`
- [ ] `uv run pytest -m "not network"`
- [ ] `uv run pytest --cov --cov-report=term-missing`
- [ ] `uv run bandit -c pyproject.toml -r src`
- [ ] `uv run pip-audit`
- [ ] `uv build`
- [ ] Docker/Compose (only if available)

## Checklist

- [ ] Tests are deterministic and do not contact live STPT services.
- [ ] Provenance, stale-data signalling, and structured errors are preserved.
- [ ] Cache keys and bounds remain correct.
- [ ] No Home Assistant or `stpt-ha-integration` dependency was added.
- [ ] Documentation and changelog were updated if needed.
- [ ] No secrets, tokens, or private data are included.

## Limitations

List anything not verified or intentionally out of scope.
