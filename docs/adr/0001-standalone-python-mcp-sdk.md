# ADR 0001: Standalone Python server on the official MCP SDK

- Status: Accepted
- Date: 2026-10-21

## Context

The existing Home Assistant custom integration contains useful endpoint and
parsing knowledge, but it is coupled to Home Assistant's event loop, coordinator,
config entries, and entities. The new deliverable must be an independent public
MCP server that works from desktop hosts and containers. The MCP Python SDK has a
new high-level `MCPServer` API and serves both stdio and Streamable HTTP.

## Decision

Build a Python 3.11+ package using:

- the official `mcp` Python SDK (`MCPServer`, current protocol);
- Pydantic 2 models for structured tool output;
- HTTPX for upstream I/O;
- BeautifulSoup for the schedule HTML;
- Uvicorn for the ASGI deployment path;
- an in-process asyncio cache and no database or Redis.

The server works with no Home Assistant installation. It uses a small,
checked-in, GTFS-derived network snapshot for offline stop/route discovery. Live
and schedule data are fetched at request time.

## Consequences

- One codebase runs under stdio and HTTP with identical tools.
- Pydantic output annotations become MCP output schemas automatically.
- The `mcp` dependency is relatively new; dependency bounds and lockfile
  reproducibility matter.
- Multi-process HTTP deployment is supported for protocol requests, but each
  process has its own cache. This is acceptable for public read-only data and
  avoids external state.
- The network snapshot can become stale between feed releases. Responses expose
  feed metadata, and maintainers have a reproducible update script.
