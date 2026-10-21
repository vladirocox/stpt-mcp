# Deployment examples

These files are starting points for a small public deployment. Replace
`mcp.example.com` and the token placeholder before using them.

- `Caddyfile.example` — TLS termination and reverse proxy.
- `stpt-mcp.service.example` — systemd unit for a loopback HTTP listener.
- `stpt-mcp.env.example` — root-only bearer-token environment file.

Read [Public HTTPS](../docs/PUBLIC_HTTPS.md) for the full walkthrough, including
DNS, firewall, certificate renewal, verification, and removal.

Never commit a real token, private key, or certificate private key.
