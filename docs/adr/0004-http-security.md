# ADR 0004: Secure HTTP defaults and optional bearer authentication

- Status: Accepted
- Date: 2026-10-21

## Context

Streamable HTTP exposes tools to network callers. MCP servers can be attacked via
DNS rebinding against a localhost listener, cross-origin browser requests,
oversized request bodies, unbounded concurrent calls, or accidental public
exposure. Deployments also vary: local desktop, Docker bridge, reverse proxy with
TLS, and public hosted service.

## Decision

- Bind to `127.0.0.1` by default. stdio is the default transport for local hosts;
  HTTP must be selected explicitly.
- Enable the MCP SDK's Host/Origin validation. Loopback hostnames are the only
  defaults. A non-loopback bind requires an explicit `STPT_MCP_ALLOWED_HOSTS`
  allowlist; a wildcard bind fails fast without one.
- Set a request body limit (1 MiB) and bounded MCP sessions; set Uvicorn
  connection/concurrency limits for the HTTP listener.
- Support a static bearer token via `STPT_MCP_AUTH_TOKEN` on `/mcp`; compare with
  `hmac.compare_digest`. `/healthz` stays unauthenticated and exposes no secrets
  or detailed cache data.
- Recommend TLS termination and authentication for every non-local deployment.
  Do not log authorization headers or tokens.
- Do not add permissive CORS. Browser clients must intentionally configure both
  allowed origins and CORS at the deployment edge.

## Consequences

- A user who runs `--transport http` locally gets a safe localhost-only server
  with no setup.
- A container on `0.0.0.0` must supply an allowlist, which forces the operator to
  state the intended public hostname. Example Compose configuration does this.
- Static bearer tokens are intentionally simple; OAuth or a reverse-proxy auth
  layer can be placed in front of the server by operators who need it.
- Health checks are liveness-only by design; operational data remains behind the
  MCP boundary.
