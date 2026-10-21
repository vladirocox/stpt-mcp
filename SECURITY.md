# Security policy

## Supported versions

Security fixes are provided for the latest `0.1.x` release. Older releases may
not receive fixes.

## Reporting a vulnerability

Please **do not open a public issue** for a security problem.

Use GitHub's private vulnerability reporting:

<https://github.com/vladirocox/stpt-mcp/security/advisories/new>

If private reporting is unavailable, contact the maintainer through their GitHub
profile and ask for a private channel. Include:

- affected version/commit;
- transport (stdio or HTTP) and deployment shape;
- a minimal reproduction;
- impact and any suggested fix;
- whether you are willing to be credited.

Do not include real access tokens, personal data, or third-party secrets. The
maintainer will acknowledge and investigate as availability permits; no response
SLA is promised for this volunteer project.

## Scope

In scope:

- the `stpt-mcp` package, CLI, HTTP middleware, and container examples;
- authentication/authorization bypasses;
- DNS-rebinding or Host/Origin validation bypasses;
- request-size or concurrency-limit bypasses that cause a practical denial of
  service;
- secret leakage in logs, errors, or packaged artifacts;
- dependency vulnerabilities with a demonstrated impact on this server.

Out of scope:

- STPT's own services and data accuracy;
- transit information being unavailable, delayed, or incorrect;
- denial of service by a caller who already possesses a valid token;
- issues that require a misconfigured reverse proxy or disabled security settings;
- social engineering and physical access.

## Safe harbor

Good-faith research that avoids privacy violations, data destruction, and
disruption of public services is welcome. Do not test against STPT infrastructure
in ways that violate their terms or degrade service for others. Use local fakes
and your own deployment for testing.

## More

See [docs/SECURITY.md](docs/SECURITY.md) for the threat model, controls, and
deployment checklist.
