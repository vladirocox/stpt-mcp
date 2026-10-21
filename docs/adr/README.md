# Architecture decision records

ADRs capture decisions that are expensive to reverse. They are immutable once
accepted; supersede one with a new ADR rather than rewriting history.

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](0001-standalone-python-mcp-sdk.md) | Standalone Python server on the official MCP SDK | Accepted |
| [0002](0002-caching-and-fallback.md) | Bounded caches, single-flight, and last-known-good fallback | Accepted |
| [0003](0003-schedule-scraping.md) | Date- and shape-aware schedule scraping | Accepted |
| [0004](0004-http-security.md) | Secure HTTP defaults and optional bearer authentication | Accepted |

## Adding an ADR

1. Copy the structure: title, status, date, context, decision, consequences.
2. Number it sequentially.
3. Add it to the table above.
4. Link it from relevant docs.
5. Keep it focused on one decision.
