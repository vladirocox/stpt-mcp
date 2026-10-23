"""Immutable runtime configuration loaded from the environment."""

from __future__ import annotations

import ipaddress
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mcp.server.transport_security import TransportSecuritySettings

from stpt_mcp import __version__

DEFAULT_LIVE_URL = "https://live.stpt.ro/proxy-smtt-cache.php"
DEFAULT_ALERTS_URL = "https://live.stpt.ro/proxy-alerts.php"
DEFAULT_VEHICLES_URL = "https://live.stpt.ro/gtfs-vehicles.php"
DEFAULT_USER_AGENT = f"stpt-mcp/{__version__} (+https://github.com/vladirocox/stpt-mcp)"
DEFAULT_OAUTH_REDIRECT_URIS = (
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
)
LOOPBACK_HOST_PATTERNS = ("127.0.0.1:*", "localhost:*", "[::1]:*")
LOOPBACK_ORIGIN_PATTERNS = (
    "http://127.0.0.1:*",
    "https://127.0.0.1:*",
    "http://localhost:*",
    "https://localhost:*",
    "http://[::1]:*",
    "https://[::1]:*",
)


class Transport(StrEnum):
    STDIO = "stdio"
    HTTP = "http"


class SettingsError(ValueError):
    """Invalid configuration supplied by the operator."""


def generate_auth_token() -> str:
    """Return a URL-safe bearer token with 256 bits of entropy."""
    return secrets.token_urlsafe(32)


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated server settings.

    The defaults are safe for local use and make no upstream requests until a
    tool is invoked. Network endpoints can be overridden for tests or mirrors.
    """

    transport: Transport = Transport.STDIO
    host: str = "127.0.0.1"
    port: int = 8000
    path: str = "/mcp"
    timezone: str = "Europe/Bucharest"
    log_level: str = "INFO"

    live_url: str = DEFAULT_LIVE_URL
    alerts_url: str = DEFAULT_ALERTS_URL
    vehicles_url: str = DEFAULT_VEHICLES_URL
    user_agent: str = DEFAULT_USER_AGENT

    request_timeout_seconds: float = 10.0
    connect_timeout_seconds: float = 5.0
    max_retries: int = 2
    backoff_base_seconds: float = 0.25
    backoff_max_seconds: float = 4.0
    max_retry_after_seconds: float = 10.0
    upstream_concurrency: int = 8
    max_response_bytes: int = 2_000_000

    arrival_ttl_seconds: float = 60.0
    arrival_stale_ttl_seconds: float = 900.0
    schedule_ttl_seconds: float = 3600.0
    schedule_stale_ttl_seconds: float = 21_600.0
    alerts_ttl_seconds: float = 45.0
    alerts_stale_ttl_seconds: float = 600.0
    vehicles_ttl_seconds: float = 60.0
    vehicles_stale_ttl_seconds: float = 600.0
    schedule_lookahead_minutes: int = 180
    max_cache_entries: int = 2048
    vehicle_stale_after_seconds: float = 120.0
    live_clock_skew_tolerance_seconds: float = 180.0

    auth_token: str | None = field(default=None, repr=False)
    public_url: str | None = field(default=None, repr=False)
    oauth_password: str | None = field(default=None, repr=False)
    oauth_allowed_redirect_uris: tuple[str, ...] = DEFAULT_OAUTH_REDIRECT_URIS
    oauth_access_token_ttl_seconds: float = 3600.0
    oauth_refresh_token_ttl_seconds: float = 2_592_000.0
    oauth_login_ttl_seconds: float = 600.0
    allowed_hosts: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ()
    max_request_body_size: int = 1_048_576
    max_sessions: int = 100
    session_idle_timeout_seconds: float = 900.0
    max_concurrent_http_requests: int = 100

    def __post_init__(self) -> None:
        if not 1 <= self.port <= 65_535:
            raise SettingsError("port must be between 1 and 65535")
        if not self.path.startswith("/") or (len(self.path) > 1 and self.path.endswith("/")):
            raise SettingsError("path must start with '/' and must not end with '/'")
        if self.log_level.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise SettingsError("log_level must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise SettingsError(f"unknown timezone: {self.timezone!r}") from exc

        for name, url in (
            ("live_url", self.live_url),
            ("alerts_url", self.alerts_url),
            ("vehicles_url", self.vehicles_url),
        ):
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise SettingsError(f"{name} must be an absolute http(s) URL")

        positive_numbers = {
            "request_timeout_seconds": self.request_timeout_seconds,
            "connect_timeout_seconds": self.connect_timeout_seconds,
            "backoff_base_seconds": self.backoff_base_seconds,
            "backoff_max_seconds": self.backoff_max_seconds,
            "max_retry_after_seconds": self.max_retry_after_seconds,
            "arrival_ttl_seconds": self.arrival_ttl_seconds,
            "arrival_stale_ttl_seconds": self.arrival_stale_ttl_seconds,
            "schedule_ttl_seconds": self.schedule_ttl_seconds,
            "schedule_stale_ttl_seconds": self.schedule_stale_ttl_seconds,
            "alerts_ttl_seconds": self.alerts_ttl_seconds,
            "alerts_stale_ttl_seconds": self.alerts_stale_ttl_seconds,
            "vehicles_ttl_seconds": self.vehicles_ttl_seconds,
            "vehicles_stale_ttl_seconds": self.vehicles_stale_ttl_seconds,
            "vehicle_stale_after_seconds": self.vehicle_stale_after_seconds,
            "live_clock_skew_tolerance_seconds": self.live_clock_skew_tolerance_seconds,
            "session_idle_timeout_seconds": self.session_idle_timeout_seconds,
        }
        for name, value in positive_numbers.items():
            if value <= 0:
                raise SettingsError(f"{name} must be positive")

        for name, ttl, stale_ttl in (
            ("arrival", self.arrival_ttl_seconds, self.arrival_stale_ttl_seconds),
            ("schedule", self.schedule_ttl_seconds, self.schedule_stale_ttl_seconds),
            ("alerts", self.alerts_ttl_seconds, self.alerts_stale_ttl_seconds),
            ("vehicles", self.vehicles_ttl_seconds, self.vehicles_stale_ttl_seconds),
        ):
            if stale_ttl < ttl:
                raise SettingsError(f"{name}_stale_ttl_seconds must be >= its TTL")

        bounded_ints = {
            "max_retries": (self.max_retries, 0, 10),
            "upstream_concurrency": (self.upstream_concurrency, 1, 64),
            "max_response_bytes": (self.max_response_bytes, 1024, 50_000_000),
            "schedule_lookahead_minutes": (self.schedule_lookahead_minutes, 1, 1440),
            "max_cache_entries": (self.max_cache_entries, 1, 100_000),
            "max_request_body_size": (self.max_request_body_size, 1024, 100_000_000),
            "max_sessions": (self.max_sessions, 1, 100_000),
            "max_concurrent_http_requests": (self.max_concurrent_http_requests, 1, 10_000),
        }
        for name, (value, minimum, maximum) in bounded_ints.items():
            if not minimum <= value <= maximum:
                raise SettingsError(f"{name} must be between {minimum} and {maximum}")

        if self.auth_token is not None:
            if len(self.auth_token) < 16:
                raise SettingsError("auth_token must be at least 16 characters")
            if any(char.isspace() for char in self.auth_token):
                raise SettingsError("auth_token must not contain whitespace")

        if self.oauth_password is not None:
            if len(self.oauth_password) < 12:
                raise SettingsError("oauth_password must be at least 12 characters")
            if any(char.isspace() for char in self.oauth_password):
                raise SettingsError("oauth_password must not contain whitespace")
            if self.public_url is None:
                raise SettingsError("oauth_password requires public_url")

        if self.public_url is not None:
            parsed_public = urlparse(self.public_url)
            if parsed_public.scheme not in {"http", "https"} or not parsed_public.netloc:
                raise SettingsError("public_url must be an absolute http(s) URL")
            if parsed_public.query or parsed_public.fragment:
                raise SettingsError("public_url must not contain a query or fragment")
            if parsed_public.path not in {"", "/"}:
                raise SettingsError("public_url must not contain a path")
            if parsed_public.scheme != "https" and parsed_public.hostname not in {
                "localhost",
                "127.0.0.1",
                "::1",
            }:
                raise SettingsError("public_url must use HTTPS")
            if self.oauth_password is None and self.auth_token is None:
                raise SettingsError(
                    "public_url requires oauth_password or auth_token for OAuth sign-in"
                )

        for redirect_uri in self.oauth_allowed_redirect_uris:
            parsed_redirect = urlparse(redirect_uri)
            if parsed_redirect.scheme not in {"http", "https"} or not parsed_redirect.netloc:
                raise SettingsError(
                    "oauth_allowed_redirect_uris entries must be absolute http(s) URLs"
                )
            if parsed_redirect.scheme != "https" and parsed_redirect.hostname not in {
                "localhost",
                "127.0.0.1",
                "::1",
            }:
                raise SettingsError(
                    "oauth_allowed_redirect_uris must use HTTPS unless the host is loopback"
                )

        for name, value in (
            ("oauth_access_token_ttl_seconds", self.oauth_access_token_ttl_seconds),
            ("oauth_refresh_token_ttl_seconds", self.oauth_refresh_token_ttl_seconds),
            ("oauth_login_ttl_seconds", self.oauth_login_ttl_seconds),
        ):
            if value <= 0:
                raise SettingsError(f"{name} must be positive")

        if not self.is_loopback_host() and not self.allowed_hosts:
            raise SettingsError(
                "binding to a non-loopback host requires STPT_MCP_ALLOWED_HOSTS "
                "(for example 'mcp.example.com')"
            )

    def is_loopback_host(self) -> bool:
        if self.host.lower() == "localhost":
            return True
        try:
            return ipaddress.ip_address(self.host).is_loopback
        except ValueError:
            return False

    def oauth_enabled(self) -> bool:
        """Return whether the built-in OAuth authorization server is configured."""
        return self.public_url is not None

    def resource_url(self) -> str | None:
        """Return the canonical public MCP resource URL, if configured."""
        if self.public_url is None:
            return None
        return self.public_url.rstrip("/") + self.path

    def oauth_login_password(self) -> str | None:
        """Return the password used by the browser login page."""
        return self.oauth_password or self.auth_token

    def transport_security(self) -> TransportSecuritySettings:
        """Return SDK host/origin validation with strict, explicit defaults."""
        hosts = list(self.allowed_hosts) if self.allowed_hosts else list(LOOPBACK_HOST_PATTERNS)
        origins = (
            list(self.allowed_origins) if self.allowed_origins else list(LOOPBACK_ORIGIN_PATTERNS)
        )
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=hosts,
            allowed_origins=origins,
        )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if environ is None else environ

        def text(name: str, default: str) -> str:
            value = env.get(name)
            return value.strip() if value is not None and value.strip() else default

        def integer(name: str, default: int) -> int:
            raw = env.get(name)
            if raw is None or not raw.strip():
                return default
            try:
                return int(raw)
            except ValueError as exc:
                raise SettingsError(f"{name} must be an integer") from exc

        def number(name: str, default: float) -> float:
            raw = env.get(name)
            if raw is None or not raw.strip():
                return default
            try:
                return float(raw)
            except ValueError as exc:
                raise SettingsError(f"{name} must be a number") from exc

        def csv(name: str) -> tuple[str, ...]:
            raw = env.get(name, "")
            return tuple(part.strip() for part in raw.split(",") if part.strip())

        transport_raw = text("STPT_MCP_TRANSPORT", Transport.STDIO.value).lower()
        try:
            transport = Transport(transport_raw)
        except ValueError as exc:
            raise SettingsError("STPT_MCP_TRANSPORT must be 'stdio' or 'http'") from exc

        auth_token = env.get("STPT_MCP_AUTH_TOKEN") or None
        if auth_token is not None:
            auth_token = auth_token.strip() or None

        settings = cls(
            transport=transport,
            host=text("STPT_MCP_HOST", "127.0.0.1"),
            port=integer("STPT_MCP_PORT", 8000),
            path=text("STPT_MCP_PATH", "/mcp"),
            timezone=text("STPT_MCP_TIMEZONE", "Europe/Bucharest"),
            log_level=text("STPT_MCP_LOG_LEVEL", "INFO").upper(),
            live_url=text("STPT_MCP_LIVE_URL", DEFAULT_LIVE_URL),
            alerts_url=text("STPT_MCP_ALERTS_URL", DEFAULT_ALERTS_URL),
            vehicles_url=text("STPT_MCP_VEHICLES_URL", DEFAULT_VEHICLES_URL),
            user_agent=text("STPT_MCP_USER_AGENT", DEFAULT_USER_AGENT),
            request_timeout_seconds=number("STPT_MCP_REQUEST_TIMEOUT_SECONDS", 10.0),
            connect_timeout_seconds=number("STPT_MCP_CONNECT_TIMEOUT_SECONDS", 5.0),
            max_retries=integer("STPT_MCP_MAX_RETRIES", 2),
            backoff_base_seconds=number("STPT_MCP_BACKOFF_BASE_SECONDS", 0.25),
            backoff_max_seconds=number("STPT_MCP_BACKOFF_MAX_SECONDS", 4.0),
            max_retry_after_seconds=number("STPT_MCP_MAX_RETRY_AFTER_SECONDS", 10.0),
            upstream_concurrency=integer("STPT_MCP_UPSTREAM_CONCURRENCY", 8),
            max_response_bytes=integer("STPT_MCP_MAX_RESPONSE_BYTES", 2_000_000),
            arrival_ttl_seconds=number("STPT_MCP_ARRIVAL_TTL_SECONDS", 60.0),
            arrival_stale_ttl_seconds=number("STPT_MCP_ARRIVAL_STALE_TTL_SECONDS", 900.0),
            schedule_ttl_seconds=number("STPT_MCP_SCHEDULE_TTL_SECONDS", 3600.0),
            schedule_stale_ttl_seconds=number("STPT_MCP_SCHEDULE_STALE_TTL_SECONDS", 21_600.0),
            alerts_ttl_seconds=number("STPT_MCP_ALERTS_TTL_SECONDS", 45.0),
            alerts_stale_ttl_seconds=number("STPT_MCP_ALERTS_STALE_TTL_SECONDS", 600.0),
            vehicles_ttl_seconds=number("STPT_MCP_VEHICLES_TTL_SECONDS", 60.0),
            vehicles_stale_ttl_seconds=number("STPT_MCP_VEHICLES_STALE_TTL_SECONDS", 600.0),
            schedule_lookahead_minutes=integer("STPT_MCP_SCHEDULE_LOOKAHEAD_MINUTES", 180),
            max_cache_entries=integer("STPT_MCP_MAX_CACHE_ENTRIES", 2048),
            vehicle_stale_after_seconds=number("STPT_MCP_VEHICLE_STALE_AFTER_SECONDS", 120.0),
            live_clock_skew_tolerance_seconds=number(
                "STPT_MCP_LIVE_CLOCK_SKEW_TOLERANCE_SECONDS", 180.0
            ),
            auth_token=auth_token,
            public_url=(env.get("STPT_MCP_PUBLIC_URL") or "").strip() or None,
            oauth_password=(env.get("STPT_MCP_OAUTH_PASSWORD") or "").strip() or None,
            oauth_allowed_redirect_uris=(
                csv("STPT_MCP_OAUTH_ALLOWED_REDIRECT_URIS") or DEFAULT_OAUTH_REDIRECT_URIS
            ),
            oauth_access_token_ttl_seconds=number(
                "STPT_MCP_OAUTH_ACCESS_TOKEN_TTL_SECONDS", 3600.0
            ),
            oauth_refresh_token_ttl_seconds=number(
                "STPT_MCP_OAUTH_REFRESH_TOKEN_TTL_SECONDS", 2_592_000.0
            ),
            oauth_login_ttl_seconds=number("STPT_MCP_OAUTH_LOGIN_TTL_SECONDS", 600.0),
            allowed_hosts=csv("STPT_MCP_ALLOWED_HOSTS"),
            allowed_origins=csv("STPT_MCP_ALLOWED_ORIGINS"),
            max_request_body_size=integer("STPT_MCP_MAX_REQUEST_BODY_SIZE", 1_048_576),
            max_sessions=integer("STPT_MCP_MAX_SESSIONS", 100),
            session_idle_timeout_seconds=number("STPT_MCP_SESSION_IDLE_TIMEOUT_SECONDS", 900.0),
            max_concurrent_http_requests=integer("STPT_MCP_MAX_CONCURRENT_HTTP_REQUESTS", 100),
        )
        return settings

    def network_snapshot_path(self) -> Path:
        """Path to the checked-in snapshot (mainly useful for diagnostics)."""
        return Path(__file__).parent / "data" / "network.json"
