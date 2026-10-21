"""Structured error types shared by providers and tools."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    """Stable machine-readable error codes exposed by the MCP tools."""

    INVALID_ARGUMENT = "invalid_argument"
    UNKNOWN_STOP = "unknown_stop"
    UNKNOWN_ROUTE = "unknown_route"
    NOT_READY = "not_ready"
    UPSTREAM_TIMEOUT = "upstream_timeout"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    UPSTREAM_HTTP_ERROR = "upstream_http_error"
    UPSTREAM_RATE_LIMITED = "upstream_rate_limited"
    UPSTREAM_INVALID_RESPONSE = "upstream_invalid_response"
    UPSTREAM_RESPONSE_TOO_LARGE = "upstream_response_too_large"
    SCHEDULE_UNAVAILABLE = "schedule_unavailable"
    NETWORK_STALE = "network_stale"
    DATA_STALE = "data_stale"
    INTERNAL_ERROR = "internal_error"


class StptError(Exception):
    """An expected failure with a stable code and safe public message."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool = False,
        upstream: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.upstream = upstream
        self.details = details

    def __str__(self) -> str:
        return self.message
