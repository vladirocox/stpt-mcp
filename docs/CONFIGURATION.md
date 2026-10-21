# Configuration

Settings are immutable at runtime and validated at startup. Environment variables
are read by `Settings.from_env()`. CLI flags override the corresponding
environment value for `serve`.

## Precedence

1. Built-in defaults.
2. Environment variables (`.env` is not loaded automatically).
3. CLI flags: `--transport`, `--host`, `--port`, `--path`, `--log-level`,
   `--allowed-host`, `--allowed-origin`.

Invalid values fail fast with a clear message. A non-loopback bind without
`STPT_MCP_ALLOWED_HOSTS` is rejected.

## Transport and logging

| Variable | Default | Notes |
| --- | --- | --- |
| `STPT_MCP_TRANSPORT` | `stdio` | `stdio` or `http`. |
| `STPT_MCP_HOST` | `127.0.0.1` | HTTP bind address. |
| `STPT_MCP_PORT` | `8000` | 1–65535. |
| `STPT_MCP_PATH` | `/mcp` | Must start with `/` and not end with `/`. |
| `STPT_MCP_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. |
| `STPT_MCP_TIMEZONE` | `Europe/Bucharest` | IANA timezone for service dates and alerts. |
| `STPT_MCP_USER_AGENT` | project default | Sent to upstreams. |

## Upstream endpoints

| Variable | Default |
| --- | --- |
| `STPT_MCP_LIVE_URL` | `https://live.stpt.ro/proxy-smtt-cache.php` |
| `STPT_MCP_ALERTS_URL` | `https://live.stpt.ro/proxy-alerts.php` |
| `STPT_MCP_VEHICLES_URL` | `https://live.stpt.ro/gtfs-vehicles.php` |
| `STPT_MCP_SCHEDULE_BASE_URL` | `https://smtt.ro` |

All must be absolute `http(s)` URLs. Tests point these at loopback servers.

## HTTP security

| Variable | Default | Notes |
| --- | --- | --- |
| `STPT_MCP_AUTH_TOKEN` | unset | Static bearer token for `/mcp`; 16+ chars, no whitespace. Also the OAuth login password fallback. Generate one with `stpt-mcp token`. |
| `STPT_MCP_ALLOWED_HOSTS` | loopback patterns | Comma-separated Host allowlist. Required for non-loopback binds. |
| `STPT_MCP_ALLOWED_ORIGINS` | loopback origins | Comma-separated browser Origin allowlist. |
| `STPT_MCP_MAX_REQUEST_BODY_SIZE` | `1048576` | Bytes; 1 KiB–100 MB. |
| `STPT_MCP_MAX_SESSIONS` | `100` | Bounded MCP sessions. |
| `STPT_MCP_SESSION_IDLE_TIMEOUT_SECONDS` | `900` | Session idle timeout. |
| `STPT_MCP_MAX_CONCURRENT_HTTP_REQUESTS` | `100` | ASGI concurrency cap; excess returns 503. |

Host and origin patterns follow the MCP SDK syntax, for example
`mcp.example.com`, `mcp.example.com:443`, or `*.example.com:443`. Include the port
that clients use. For a browser, also configure CORS at the deployment edge; this
server does not add permissive CORS.

## OAuth sign-in

Set `STPT_MCP_PUBLIC_URL` to enable the built-in OAuth 2.1 authorization server.
It must be an origin such as `https://mcp.example.com`, with no path, query, or
fragment. The protected resource URL is derived from this origin and
`STPT_MCP_PATH`.

| Variable | Default | Notes |
| --- | --- | --- |
| `STPT_MCP_PUBLIC_URL` | unset | Public HTTPS origin. Enables discovery, DCR, PKCE, and token endpoints. |
| `STPT_MCP_OAUTH_PASSWORD` | unset | Browser sign-in password; 12+ chars, no whitespace. If unset, `STPT_MCP_AUTH_TOKEN` is used. |
| `STPT_MCP_OAUTH_ALLOWED_REDIRECT_URIS` | Claude web callbacks | Comma-separated exact redirect URIs. Loopback HTTP redirects are always allowed. |
| `STPT_MCP_OAUTH_ACCESS_TOKEN_TTL_SECONDS` | `3600` | Access-token lifetime. |
| `STPT_MCP_OAUTH_REFRESH_TOKEN_TTL_SECONDS` | `2592000` | Refresh-token lifetime. |
| `STPT_MCP_OAUTH_LOGIN_TTL_SECONDS` | `600` | Browser sign-in-request lifetime. |

The default redirect allowlist is
`https://claude.ai/api/mcp/auth_callback` and
`https://claude.com/api/mcp/auth_callback`. Dynamic registration rejects other
non-loopback redirect URIs.

The OAuth paths must reach the MCP process:
`/.well-known/oauth-authorization-server`,
`/.well-known/oauth-protected-resource/mcp`, `/authorize`, `/token`, `/register`,
`/revoke`, and `/oauth/login`. See [OAuth sign-in](OAUTH.md) for the Caddy
configuration and Claude connector steps.

The static bearer token remains valid when OAuth is enabled. This allows existing
header-based clients to keep working while hosted clients use sign-in.

## Upstream behavior

| Variable | Default | Notes |
| --- | --- | --- |
| `STPT_MCP_REQUEST_TIMEOUT_SECONDS` | `10` | Total request timeout. |
| `STPT_MCP_CONNECT_TIMEOUT_SECONDS` | `5` | Connection timeout. |
| `STPT_MCP_MAX_RETRIES` | `2` | 0–10 retries after the first attempt. |
| `STPT_MCP_BACKOFF_BASE_SECONDS` | `0.25` | Exponential backoff base. |
| `STPT_MCP_BACKOFF_MAX_SECONDS` | `4` | Backoff cap. |
| `STPT_MCP_MAX_RETRY_AFTER_SECONDS` | `10` | Cap for upstream `Retry-After`. |
| `STPT_MCP_UPSTREAM_CONCURRENCY` | `8` | 1–64 concurrent upstream requests. |
| `STPT_MCP_MAX_RESPONSE_BYTES` | `2000000` | 1 KiB–50 MB. |

## Cache freshness

| Variable | Default | Notes |
| --- | --- | --- |
| `STPT_MCP_ARRIVAL_TTL_SECONDS` | `60` | Fresh arrival window. |
| `STPT_MCP_ARRIVAL_STALE_TTL_SECONDS` | `900` | Last-known-good window; must be ≥ TTL. |
| `STPT_MCP_SCHEDULE_TTL_SECONDS` | `3600` | Schedule bundle freshness. |
| `STPT_MCP_SCHEDULE_STALE_TTL_SECONDS` | `21600` | Schedule stale window. |
| `STPT_MCP_ALERTS_TTL_SECONDS` | `45` | Alerts freshness. |
| `STPT_MCP_ALERTS_STALE_TTL_SECONDS` | `600` | Alerts stale window. |
| `STPT_MCP_VEHICLES_TTL_SECONDS` | `60` | Vehicles freshness. |
| `STPT_MCP_VEHICLES_STALE_TTL_SECONDS` | `600` | Vehicles stale window. |
| `STPT_MCP_VEHICLE_STALE_AFTER_SECONDS` | `120` | Per-vehicle observation age threshold. |
| `STPT_MCP_SCHEDULE_LOOKAHEAD_MINUTES` | `180` | Schedule departure window; 1–1440. |
| `STPT_MCP_MAX_CACHE_ENTRIES` | `2048` | Arrival/schedule/alert/vehicle cache bound. |
| `STPT_MCP_SCHEDULE_PAGE_CACHE_ENTRIES` | `1024` | HTML page cache bound. |
| `STPT_MCP_SCHEDULE_MAX_PAGES` | `20` | Maximum pages per schedule refresh. |

Stale windows must be greater than or equal to their TTLs. Lowering TTLs increases
upstream load; raising them increases staleness. The defaults are chosen for a
multi-client local deployment.

## Example

```bash
STPT_MCP_TRANSPORT=http \
STPT_MCP_HOST=0.0.0.0 \
STPT_MCP_ALLOWED_HOSTS=mcp.example.com \
STPT_MCP_PUBLIC_URL=https://mcp.example.com \
STPT_MCP_OAUTH_PASSWORD='replace-with-a-long-random-password' \
STPT_MCP_LOG_LEVEL=INFO \
stpt-mcp serve
```

Terminate TLS at a reverse proxy. See [Public HTTPS](PUBLIC_HTTPS.md),
[OAuth sign-in](OAUTH.md), [Deployment](DEPLOYMENT.md), and
[Security](SECURITY.md).
