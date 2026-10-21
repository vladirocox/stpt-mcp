# Deployment

## Choose a transport

| Scenario | Transport | Notes |
| --- | --- | --- |
| Desktop MCP client launching a child process | stdio | Default; no network listener. |
| Local browser/app client, or one server shared by local clients | Streamable HTTP on loopback | Safe default; no auth required locally. |
| Container, LAN, or public service | Streamable HTTP behind a proxy | Set allowed hosts, auth, and TLS. |

## stdio

```bash
stpt-mcp serve --transport stdio
```

Client configuration:

```json
{
  "mcpServers": {
    "stpt": {
      "command": "stpt-mcp",
      "args": ["serve", "--transport", "stdio"]
    }
  }
}
```

Logs go to stderr. Do not add stdout logging; stdout is the protocol stream.

## Local HTTP

```bash
stpt-mcp serve --transport http
```

Defaults:

- bind `127.0.0.1:8000`;
- MCP endpoint `/mcp`;
- health endpoint `/healthz`;
- no authentication, safe only because the listener is loopback;
- Host/Origin validation limited to loopback patterns.

Test:

```bash
curl -fsS http://127.0.0.1:8000/healthz
```

Expected:

```json
{"status":"ok","service":"stpt-mcp","version":"0.2.0"}
```

## Docker

The repository includes a multi-stage `Dockerfile` and a `compose.yaml` example.
The image runs as a non-root user and includes a health check.

Build and run:

```bash
docker build -t stpt-mcp:0.2.0 .
docker run --rm -p 127.0.0.1:8000:8000 \
  -e STPT_MCP_ALLOWED_HOSTS=localhost:8000,127.0.0.1:8000 \
  stpt-mcp:0.2.0
```

The image sets:

```text
STPT_MCP_TRANSPORT=http
STPT_MCP_HOST=0.0.0.0
STPT_MCP_PORT=8000
STPT_MCP_ALLOWED_HOSTS=localhost:8000,127.0.0.1:8000
```

Add authentication before exposing it beyond your machine:

```bash
docker run --rm -p 127.0.0.1:8000:8000 \
  -e STPT_MCP_ALLOWED_HOSTS=localhost:8000,127.0.0.1:8000 \
  -e STPT_MCP_AUTH_TOKEN="$(stpt-mcp token)" \
  stpt-mcp:0.2.0
```

The `stpt-mcp token` command prints a new 256-bit URL-safe token and exits.

### Compose

```bash
export STPT_MCP_AUTH_TOKEN="$(stpt-mcp token)"
docker compose up --build
```

The example binds the published port to `127.0.0.1`, drops capabilities, uses a
read-only root filesystem, and sets `no-new-privileges`. It is a starting point,
not a complete production platform.

> **Verification status:** Docker/Podman was unavailable in the development
> environment used for v0.1.0, so no local build was run. CI builds the image and
> checks `/healthz` successfully. The Compose file is syntax-validated, but has
> not been launched locally.

## Reverse proxy and TLS

Put TLS and any additional authentication in front of the server. Example Caddy
configuration:

```caddyfile
mcp.example.com {
    reverse_proxy 127.0.0.1:8000 {
        # Streamable HTTP can keep responses open; do not buffer them.
        flush_interval -1
    }
}
```

For a non-standard HTTPS port, use the same certificate and include the port in
the allowlists:

```caddyfile
https://mcp.example.com:9000 {
    tls /etc/letsencrypt/live/mcp.example.com/fullchain.pem /etc/letsencrypt/live/mcp.example.com/privkey.pem
    reverse_proxy 127.0.0.1:8000 {
        flush_interval -1
    }
}
```

The public client URL is then `https://mcp.example.com:9000/mcp`, and the
server needs `STPT_MCP_ALLOWED_HOSTS=mcp.example.com:9000` because the proxy
forwards the port in the `Host` header.

Example nginx location:

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;
    proxy_read_timeout 3600s;
}
```

Configure the server for the public host:

```bash
STPT_MCP_TRANSPORT=http \
STPT_MCP_HOST=127.0.0.1 \
STPT_MCP_ALLOWED_HOSTS=mcp.example.com \
STPT_MCP_ALLOWED_ORIGINS=https://mcp.example.com \
STPT_MCP_AUTH_TOKEN="$(stpt-mcp token)" \
stpt-mcp serve
```

Notes:

- The Host allowlist must match what the proxy forwards. Include a port only if
  the forwarded Host header includes one.
- Streamable HTTP uses long-lived responses. Disable proxy buffering and allow
  long read timeouts.
- If a browser calls the endpoint, configure CORS at the proxy. The server does
  not add permissive CORS.
- Do not put the token in query strings, logs, or source control.

## systemd

A minimal unit for a local HTTP service:

```ini
[Unit]
Description=stpt-mcp
After=network-online.target

[Service]
Type=simple
User=stpt
Group=stpt
Environment=STPT_MCP_TRANSPORT=http
Environment=STPT_MCP_HOST=127.0.0.1
Environment=STPT_MCP_ALLOWED_HOSTS=localhost:8000,127.0.0.1:8000
ExecStart=/opt/stpt-mcp/.venv/bin/stpt-mcp serve
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Put secrets in a root-readable environment file (`EnvironmentFile=`), not in the
unit file.

## Scaling

- One process owns one set of in-memory caches.
- Multiple workers or replicas are supported but duplicate cache entries and
  upstream requests.
- For a single host, prefer one process and let its caches coalesce clients.
- There is no Redis or database by design. Add an external cache only if
  cross-process sharing becomes a measured requirement.

## Upgrades

1. Stop the service.
2. Install the new wheel or image.
3. Start the service and check `/healthz`.
4. Call `get_service_status` and inspect the version, feed metadata, and cache
   counters.
5. Roll back by reinstalling the previous artifact. Caches are in memory and
   need no migration.

## Related

- [Configuration](CONFIGURATION.md)
- [Operations](OPERATIONS.md)
- [Security](SECURITY.md)
