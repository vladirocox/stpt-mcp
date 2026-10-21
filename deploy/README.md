# Deployment examples

These files are starting points for a small public deployment. Replace
`mcp.example.com` and the credential placeholders before using them.

- `Caddyfile.example` — TLS termination and reverse proxy.
- `stpt-mcp.service.example` — systemd unit for a loopback HTTP listener.
- `stpt-mcp.env.example` — root-only credential environment file.

Read [Public HTTPS](../docs/PUBLIC_HTTPS.md) for the full walkthrough, including
DNS, firewall, certificate renewal, verification, and removal. For hosted-client
sign-in, read [OAuth sign-in](../docs/OAUTH.md) and add the OAuth paths to your
reverse proxy.

Never commit a real token, OAuth password, private key, or certificate private
key.
