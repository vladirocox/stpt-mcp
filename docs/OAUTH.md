# OAuth sign-in for hosted clients

The server can act as a small OAuth 2.1 authorization server for its own MCP
endpoint. This lets clients such as Claude's custom connector use **Sign in now**
instead of asking you to paste a static bearer token into a request header.

The implementation is deliberately single-owner:

- one password protects the browser sign-in page;
- dynamic client registration (DCR) is enabled for MCP clients;
- authorization-code + PKCE (`S256`) is required;
- access and refresh tokens live in memory and are bounded;
- only the MCP endpoint is protected; discovery and sign-in routes are public;
- the existing static bearer token still works when configured.

It is not a multi-user identity provider. Use an external OIDC provider if you
need per-user accounts, audit trails, or centrally managed sessions.

## Requirements

- HTTPS on the standard port 443 for hosted clients such as Claude.
- `STPT_MCP_PUBLIC_URL` set to the public origin, for example
  `https://mcp.example.com`.
- A sign-in password, either `STPT_MCP_OAUTH_PASSWORD` (recommended) or the
  existing `STPT_MCP_AUTH_TOKEN`.
- A reverse proxy that forwards both the MCP path and the OAuth paths to the
  server.

`STPT_MCP_PUBLIC_URL` must be an origin only, with no path, query, or fragment.
The resource URL is derived from it and the configured `STPT_MCP_PATH` (default
`/mcp`).

## Configuration

```bash
STPT_MCP_PUBLIC_URL=https://mcp.example.com
STPT_MCP_OAUTH_PASSWORD=replace-with-a-long-random-password
```

`STPT_MCP_OAUTH_PASSWORD` must be at least 12 characters and contain no
whitespace. If it is omitted, `STPT_MCP_AUTH_TOKEN` is used as the browser
sign-in password. That fallback works, but a shorter, unique OAuth password is
usually more convenient.

Optional settings:

| Variable | Default | Notes |
| --- | --- | --- |
| `STPT_MCP_OAUTH_ALLOWED_REDIRECT_URIS` | Claude web callbacks | Comma-separated exact redirect URIs. Loopback HTTP redirects are always allowed for desktop clients. |
| `STPT_MCP_OAUTH_ACCESS_TOKEN_TTL_SECONDS` | `3600` | Access-token lifetime. |
| `STPT_MCP_OAUTH_REFRESH_TOKEN_TTL_SECONDS` | `2592000` | Refresh-token lifetime (30 days). |
| `STPT_MCP_OAUTH_LOGIN_TTL_SECONDS` | `600` | How long a browser sign-in request may sit open. |

The default redirect allowlist is:

```text
https://claude.ai/api/mcp/auth_callback
https://claude.com/api/mcp/auth_callback
```

Dynamic registration rejects every redirect URI outside this allowlist, except
loopback HTTP URIs used by local MCP clients.

## Reverse proxy

The OAuth authorization server uses these public paths:

```text
/.well-known/oauth-authorization-server
/.well-known/oauth-protected-resource/mcp
/authorize
/token
/register
/revoke
/oauth/login
```

They must reach the same Python process as `/mcp`. With Caddy, add this before a
catch-all redirect:

```caddyfile
example.com {
    @oauth path /.well-known/oauth-authorization-server /.well-known/oauth-protected-resource/mcp /authorize /token /register /revoke /oauth/*

    handle @oauth {
        reverse_proxy 127.0.0.1:8000 {
            flush_interval -1
        }
    }

    handle /mcp* {
        reverse_proxy 127.0.0.1:8000 {
            flush_interval -1
        }
    }

    handle {
        redir https://example.net permanent
    }
}
```

If the MCP server owns the whole hostname, a single `reverse_proxy` block is
enough; every path is forwarded.

Do not proxy the OAuth paths over plain HTTP. On an HTTP-only site, redirect them
to HTTPS instead.

## Claude custom connector

1. In Claude, add a custom connector.
2. Name it, for example, `stpt`.
3. Set the URL to `https://mcp.example.com/mcp` (no port unless 443 is not used).
4. Choose **Sign in now**.
5. For the OAuth client, choose **Register automatically**. This uses dynamic
   client registration.
   - Do not choose **Use Claude's published identity** unless the server
     advertises and implements Client ID Metadata Documents. This server uses
     DCR instead.
6. Connect. Claude opens the server's sign-in page in your browser.
7. Enter `STPT_MCP_OAUTH_PASSWORD`, or the static bearer token if you did not set
   a separate OAuth password.
8. After the browser redirects back, Claude receives an access token and refresh
   token.

The access token lasts one hour and the refresh token lasts 30 days by default.
A server restart clears the in-memory token store, so clients sign in again after
a restart or deployment.

If you prefer the static-header flow, choose **No sign-in** and configure:

```text
Authorization: Bearer <token>
```

as a required request header. Both flows can be enabled at the same time.

## Verify

Discovery metadata:

```bash
curl -fsS https://mcp.example.com/.well-known/oauth-authorization-server | jq
curl -fsS https://mcp.example.com/.well-known/oauth-protected-resource/mcp | jq
```

The first response includes `authorization_endpoint`, `token_endpoint`,
`registration_endpoint`, and `code_challenge_methods_supported: ["S256"]`. The
second identifies `https://mcp.example.com/mcp` as the protected resource.

An unauthenticated MCP request should return `401` with a `WWW-Authenticate`
header that points at the protected-resource metadata:

```bash
curl -sS -D - -o /dev/null -X POST https://mcp.example.com/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
```

The sign-in page is available only through a valid authorization request. A
direct visit to `/oauth/login` without a `login_id` returns an invalid-request
page.

## Security notes

- Use HTTPS; the password and authorization codes travel through the browser.
- Use a long, unique OAuth password and keep it in a root-readable environment
  file, not in the unit file, shell history, or git.
- The browser login form uses a one-time, expiring `login_id`. Failed passwords
  are rate-limited by a short delay, not by a distributed quota.
- Dynamic registration is public, but redirect URIs are allowlisted. A client
  cannot redirect an authorization code to an arbitrary host.
- Access tokens are opaque random values stored in a bounded in-memory map; they
  are not written to disk.
- The static bearer token remains a full-access credential. Rotate it by
  restarting with a new value.
- The OAuth layer authenticates one owner. It does not provide per-user scopes,
  audit logs, or revocation across multiple server instances.
- Token state is per process. Run one HTTP worker or use sticky sessions; a token
  issued by one worker is not valid on another worker.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Claude says it cannot reach the server | Use `https://mcp.example.com/mcp` on port 443. Check that Caddy forwards `/mcp` and the OAuth paths. |
| Discovery returns `404` | The OAuth paths are not routed to the Python process, or `STPT_MCP_PUBLIC_URL` is not set. |
| Sign-in page says the request expired | The login page was open longer than `STPT_MCP_OAUTH_LOGIN_TTL_SECONDS`; start again from Claude. |
| Token request returns `invalid_grant` | The authorization code was already used or expired, or the PKCE verifier does not match. Restart the sign-in flow. |
| Claude still asks for a header | Choose **Sign in now** and **Register automatically**, not **No sign-in**. |
| "Use Claude's published identity" fails | Choose **Register automatically**; CIMD is not implemented. |
| Tokens stop working after a restart | Expected: token state is in memory. Sign in again. |

## Related

- [Public HTTPS](PUBLIC_HTTPS.md)
- [Configuration](CONFIGURATION.md)
- [Deployment](DEPLOYMENT.md)
- [Security](SECURITY.md)
