"""Configuration validation and secure defaults."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from stpt_mcp.config import Settings, SettingsError, Transport


def test_defaults_are_local_and_safe() -> None:
    settings = Settings()
    assert settings.transport is Transport.STDIO
    assert settings.host == "127.0.0.1"
    assert settings.is_loopback_host()
    assert settings.auth_token is None
    assert settings.oauth_enabled() is False
    assert settings.resource_url() is None
    security = settings.transport_security()
    assert security.enable_dns_rebinding_protection is True
    assert "127.0.0.1:*" in security.allowed_hosts
    assert "http://localhost:*" in security.allowed_origins
    assert settings.network_snapshot_path().is_file()


def test_from_env_parses_values() -> None:
    settings = Settings.from_env(
        {
            "STPT_MCP_TRANSPORT": "http",
            "STPT_MCP_HOST": "0.0.0.0",
            "STPT_MCP_PORT": "9123",
            "STPT_MCP_PATH": "/transit",
            "STPT_MCP_TIMEZONE": "UTC",
            "STPT_MCP_LOG_LEVEL": "debug",
            "STPT_MCP_LIVE_URL": "http://127.0.0.1:9000/live",
            "STPT_MCP_MAX_RETRIES": "4",
            "STPT_MCP_REQUEST_TIMEOUT_SECONDS": "2.5",
            "STPT_MCP_ARRIVAL_TTL_SECONDS": "5",
            "STPT_MCP_ARRIVAL_STALE_TTL_SECONDS": "50",
            "STPT_MCP_LIVE_CLOCK_SKEW_TOLERANCE_SECONDS": "240",
            "STPT_MCP_AUTH_TOKEN": "a" * 16,
            "STPT_MCP_PUBLIC_URL": "https://mcp.example.com",
            "STPT_MCP_OAUTH_PASSWORD": "oauth-password-123",
            "STPT_MCP_OAUTH_ALLOWED_REDIRECT_URIS": (
                "https://claude.ai/api/mcp/auth_callback,http://localhost:33418/callback"
            ),
            "STPT_MCP_OAUTH_ACCESS_TOKEN_TTL_SECONDS": "120",
            "STPT_MCP_ALLOWED_HOSTS": "mcp.example.com, localhost:*",
            "STPT_MCP_ALLOWED_ORIGINS": "https://app.example.com",
        }
    )
    assert settings.transport is Transport.HTTP
    assert settings.port == 9123
    assert settings.log_level == "DEBUG"
    assert settings.max_retries == 4
    assert settings.request_timeout_seconds == 2.5
    assert settings.live_clock_skew_tolerance_seconds == 240
    assert settings.allowed_hosts == ("mcp.example.com", "localhost:*")
    assert settings.allowed_origins == ("https://app.example.com",)
    assert settings.auth_token == "a" * 16
    assert settings.oauth_enabled() is True
    assert settings.resource_url() == "https://mcp.example.com/transit"
    assert settings.oauth_login_password() == "oauth-password-123"
    assert settings.oauth_allowed_redirect_uris == (
        "https://claude.ai/api/mcp/auth_callback",
        "http://localhost:33418/callback",
    )
    assert settings.oauth_access_token_ttl_seconds == 120
    assert "mcp.example.com" in settings.transport_security().allowed_hosts


def test_from_env_ignores_blank_values() -> None:
    settings = Settings.from_env({"STPT_MCP_HOST": "   ", "STPT_MCP_AUTH_TOKEN": "   "})
    assert settings.host == "127.0.0.1"
    assert settings.auth_token is None


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"transport": "carrier-pigeon"}, "STPT_MCP_TRANSPORT"),
        ({"port": "0"}, "port must be"),
        ({"port": "70000"}, "port must be"),
        ({"path": "mcp"}, "path must"),
        ({"path": "/mcp/"}, "path must"),
        ({"timezone": "Mars/Olympus"}, "unknown timezone"),
        ({"log_level": "TRACE"}, "log_level"),
        ({"live_url": "ftp://example.com/live"}, "absolute http"),
        ({"max_retries": "eleven"}, "must be an integer"),
        ({"request_timeout_seconds": "soon"}, "must be a number"),
        ({"arrival_ttl_seconds": "0"}, "must be positive"),
        ({"arrival_ttl_seconds": "10", "arrival_stale_ttl_seconds": "5"}, "stale_ttl"),
        ({"max_cache_entries": "0"}, "between"),
        ({"upstream_concurrency": "1000"}, "between"),
        ({"auth_token": "short"}, "at least 16"),
        ({"auth_token": "has whitespace here"}, "whitespace"),
        ({"oauth_password": "short"}, "at least 12"),
        ({"oauth_password": "valid-password-123"}, "requires public_url"),
        (
            {
                "public_url": "https://mcp.example.com",
                "oauth_password": "has whitespace here",
            },
            "whitespace",
        ),
        ({"public_url": "http://mcp.example.com", "auth_token": "a" * 16}, "HTTPS"),
        (
            {"public_url": "https://mcp.example.com/path", "auth_token": "a" * 16},
            "must not contain a path",
        ),
        ({"public_url": "https://mcp.example.com"}, "requires oauth_password"),
        (
            {"oauth_allowed_redirect_uris": "http://evil.example/callback"},
            "HTTPS",
        ),
    ],
)
def test_invalid_environment_is_rejected(
    overrides: dict[str, str], message: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in list(overrides):
        monkeypatch.setenv(f"STPT_MCP_{key.upper()}", overrides[key])
    with pytest.raises(SettingsError, match=message):
        Settings.from_env()


def test_invalid_direct_settings() -> None:
    with pytest.raises(SettingsError, match="port must be"):
        Settings(port=0)
    with pytest.raises(SettingsError, match="path must"):
        Settings(path="relative")
    with pytest.raises(SettingsError, match="unknown timezone"):
        Settings(timezone="Nowhere/Nothing")
    with pytest.raises(SettingsError, match="live_url"):
        Settings(live_url="file:///etc/passwd")


def test_oauth_login_password_falls_back_to_static_token() -> None:
    settings = Settings(
        public_url="https://mcp.example.com",
        auth_token="test-token-1234567890",  # noqa: S106 - test fixture
        allowed_hosts=("mcp.example.com",),
    )
    assert settings.oauth_enabled() is True
    assert settings.oauth_login_password() == settings.auth_token


def test_non_loopback_bind_requires_allowed_hosts() -> None:
    with pytest.raises(SettingsError, match="ALLOWED_HOSTS"):
        Settings(host="0.0.0.0")
    settings = Settings(host="0.0.0.0", allowed_hosts=("mcp.example.com",))
    assert not settings.is_loopback_host()
    assert settings.transport_security().allowed_hosts == ["mcp.example.com"]


@pytest.mark.parametrize(
    ("host", "loopback"),
    [
        ("127.0.0.1", True),
        ("127.0.0.2", True),
        ("::1", True),
        ("localhost", True),
        ("LOCALHOST", True),
        ("0.0.0.0", False),
        ("192.168.1.10", False),
        ("mcp.example.com", False),
        ("not-an-ip", False),
    ],
)
def test_is_loopback_host(host: str, loopback: bool) -> None:
    settings = Settings(host=host, allowed_hosts=() if loopback else ("*",))
    assert settings.is_loopback_host() is loopback


def test_network_snapshot_path_points_at_bundled_data() -> None:
    path = Settings().network_snapshot_path()
    assert path.name == "network.json"
    assert path.read_text(encoding="utf-8").startswith("{")


def test_settings_are_frozen() -> None:
    settings = Settings()
    with pytest.raises(AttributeError):
        settings.port = 9000  # type: ignore[misc]


def test_custom_paths_are_preserved(tmp_path: Path) -> None:
    settings = Settings()
    assert isinstance(settings.live_url, str)
    assert tmp_path.exists()
    assert settings.allowed_hosts == ()
    assert settings.allowed_origins == ()


def test_all_overrides_are_validated() -> None:
    overrides: dict[str, Any] = {
        "max_response_bytes": 1024,
        "schedule_lookahead_minutes": 1,
        "max_sessions": 1,
        "max_concurrent_http_requests": 1,
        "session_idle_timeout_seconds": 1.0,
        "vehicle_stale_after_seconds": 1.0,
    }
    settings = Settings(**overrides)
    assert settings.max_response_bytes == 1024
