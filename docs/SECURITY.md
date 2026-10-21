# Security

## Threat model

### Assets

- Availability of the server and the shared STPT upstream services.
- Integrity of the data returned to MCP clients.
- Any bearer token, OAuth password, access token, or refresh token configured
  for HTTP access.
- The host running the server.

### Trust boundaries

```text
untrusted MCP client
   │  stdio (local process) or HTTP (network)
   ▼
server + middleware
   │  trusted process boundary
   ▼
public STPT HTTP endpoints (untrusted input)
```

Upstream responses are untrusted input. They are parsed with size limits and
bounded retries, and malformed data becomes a structured result, not code
execution or an unbounded allocation.

### Primary threats and controls

| Threat | Control |
| --- | --- |
| DNS rebinding against a localhost listener | SDK DNS-rebinding protection; loopback-only Host/Origin defaults; explicit allowlist for other binds. |
| Cross-origin browser calls | Origin validation; no permissive CORS. Add CORS deliberately at the edge. |
| Unauthorized network access | Optional static bearer token or built-in OAuth 2.1 with PKCE; constant-time comparisons, 401 with `WWW-Authenticate`; TLS via reverse proxy. |
| Oversized request bodies | 1 MiB default limit; 413 response. |
| Unbounded sessions/concurrency | Bounded sessions, ASGI concurrency limiter, 503 with `Retry-After`. |
| Upstream fan-out/DoS | Bounded caches, single-flight, upstream semaphore, timeouts, retries with caps. |
| Oversized upstream response | `max_response_bytes`; structured `upstream_response_too_large`. |
| Secret leakage | Token excluded from repr, no auth-header logging, no token in errors or status. |
| Dependency compromise | Lockfile, pip-audit in CI, Dependabot-friendly pin ranges. |
| Container breakout | Non-root user, read-only root filesystem in the example, dropped capabilities, `no-new-privileges`. |

## HTTP controls in detail

- **Bind default:** `127.0.0.1`. A non-loopback bind requires
  `STPT_MCP_ALLOWED_HOSTS`; startup fails otherwise.
- **Host validation:** exact allowlist match or `host:*` wildcard-port patterns.
  This is the SDK's DNS-rebinding defense.
- **Origin validation:** loopback origins by default. Browser deployments must
  explicitly list origins and configure CORS at the proxy.
- **Authentication:** `STPT_MCP_AUTH_TOKEN` is accepted as a static bearer token
  on `/mcp`. When `STPT_MCP_PUBLIC_URL` is set, the server also exposes an OAuth
  2.1 authorization server with discovery metadata, dynamic client registration,
  authorization-code + PKCE, access tokens, refresh tokens, and revocation. Both
  methods can be enabled together. Comparisons use `hmac.compare_digest`.
- **OAuth login:** A browser sign-in page accepts `STPT_MCP_OAUTH_PASSWORD`, or
  `STPT_MCP_AUTH_TOKEN` when no separate OAuth password is set. Login requests
  are one-time, expire after ten minutes, and failed passwords incur a short
  delay. Registered redirect URIs are restricted to the configured allowlist
  plus loopback HTTP callbacks.
- **Body limit:** applied by the Streamable HTTP app before tool execution.
- **Concurrency limit:** excess requests receive 503 instead of queueing without
  bound.
- **Health endpoint:** unauthenticated, liveness-only, no cache or config data.

## Deployment checklist

- [ ] Keep the default loopback bind unless remote access is required.
- [ ] For remote access, set `STPT_MCP_ALLOWED_HOSTS` to the exact public host.
- [ ] Set `STPT_MCP_AUTH_TOKEN` to a long random value, or enable OAuth with a
      strong `STPT_MCP_OAUTH_PASSWORD`; store credentials in a secret manager or
      root-readable environment file.
- [ ] Terminate TLS at a trusted reverse proxy; do not expose plain HTTP to the
      internet.
- [ ] Set `STPT_MCP_ALLOWED_ORIGINS` only if browser clients are intended, and
      configure CORS at the proxy.
- [ ] Block `/healthz` from the public internet if your platform requires it, or
      leave it as a minimal liveness signal.
- [ ] Run as a non-root user with a read-only filesystem where possible.
- [ ] Do not log `Authorization` headers, OAuth codes, or token responses at the
      proxy.
- [ ] Keep the package and container base image updated.
- [ ] Monitor 401/403/421/413/503 rates for misconfiguration or abuse.
- [ ] Apply a network egress policy if the server should reach only STPT hosts.

## Token handling

- Generate tokens with `stpt-mcp token` (256 bits of entropy) or
  `openssl rand -base64 32`.
- Minimum length is 16 characters; prefer at least 32 random bytes.
- Static tokens and OAuth passwords are not written to disk by this project.
- `Settings` marks authentication secrets `repr=False`; `get_service_status`
  never includes them.
- Rotate a static token by restarting with the new value and updating clients.
- OAuth access and refresh tokens are opaque random values in a bounded in-memory
  store. A restart revokes them and clients sign in again.
- A static token or OAuth access token grants full tool access. There are no
  per-user scopes or limits beyond the global concurrency cap.

## Dependency and supply chain

- Runtime dependencies are bounded in `pyproject.toml` and locked in `uv.lock`.
- CI runs `pip-audit` and Bandit.
- The container installs a built wheel, not a mutable source tree.
- The bundled network snapshot is generated from documented public URLs with
  recorded SHA-256 checksums.

## Limitations

- No built-in rate limiting per client or token beyond the OAuth login delay and
  global concurrency cap. Use a proxy or gateway if you need quotas.
- OAuth is single-owner. There are no per-user identities, scopes, or multi-instance
  token revocation.
- No mTLS, audit log of tool calls, or persistent OAuth token store.
- Bearer auth and OAuth are only as strong as credential handling and TLS.
- The server is read-only and does not accept file paths or shell commands from
  clients; keep it that way.

## Reporting

See the repository [security policy](../SECURITY.md). Do not open public issues
for vulnerabilities.
