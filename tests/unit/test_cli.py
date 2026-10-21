"""CLI parsing and environment override behavior."""

from __future__ import annotations

import argparse

import pytest

from stpt_mcp.cli import _parser, _settings_from_args
from stpt_mcp.config import SettingsError, Transport


def test_parser_requires_serve_or_defaults() -> None:
    parser = _parser()
    args = parser.parse_args([])
    assert args.command is None
    assert parser.parse_args(["serve"]).command == "serve"


def test_parser_collects_http_options() -> None:
    args = _parser().parse_args(
        [
            "serve",
            "--transport",
            "http",
            "--host",
            "0.0.0.0",
            "--port",
            "9000",
            "--path",
            "/transit",
            "--log-level",
            "debug",
            "--allowed-host",
            "mcp.example.com",
            "--allowed-host",
            "localhost:*",
            "--allowed-origin",
            "https://app.example.com",
        ]
    )
    settings = _settings_from_args(args)
    assert settings.transport is Transport.HTTP
    assert settings.host == "0.0.0.0"
    assert settings.port == 9000
    assert settings.path == "/transit"
    assert settings.log_level == "DEBUG"
    assert settings.allowed_hosts == ("mcp.example.com", "localhost:*")
    assert settings.allowed_origins == ("https://app.example.com",)


def test_environment_is_the_base_for_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STPT_MCP_PORT", "8001")
    monkeypatch.setenv("STPT_MCP_LOG_LEVEL", "warning")
    args = argparse.Namespace(
        transport=None,
        host=None,
        port=8002,
        path=None,
        log_level=None,
        allowed_host=None,
        allowed_origin=None,
    )
    settings = _settings_from_args(args)
    assert settings.port == 8002
    assert settings.log_level == "WARNING"


def test_invalid_cli_transport_is_rejected_by_argparse() -> None:
    with pytest.raises(SystemExit):
        _parser().parse_args(["serve", "--transport", "smtp"])


def test_cli_error_is_a_settings_error() -> None:
    args = argparse.Namespace(
        transport=None,
        host=None,
        port=0,
        path=None,
        log_level=None,
        allowed_host=None,
        allowed_origin=None,
    )
    with pytest.raises(SettingsError):
        _settings_from_args(args)
