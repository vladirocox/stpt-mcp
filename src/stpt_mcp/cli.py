"""Command-line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import replace

import uvicorn

from stpt_mcp import __version__
from stpt_mcp.config import Settings, SettingsError, Transport, generate_auth_token
from stpt_mcp.http_app import create_http_app
from stpt_mcp.server import build_server

logger = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stpt-mcp",
        description="Unofficial STPT Timișoara transit MCP server.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("token", help="generate a bearer token for HTTP auth and exit")
    serve = subparsers.add_parser("serve", help="run the MCP server")
    serve.add_argument(
        "--transport",
        choices=[Transport.STDIO.value, Transport.HTTP.value],
        help="transport (default: STPT_MCP_TRANSPORT or stdio)",
    )
    serve.add_argument("--host", help="HTTP bind address (default: 127.0.0.1)")
    serve.add_argument("--port", type=int, help="HTTP port (default: 8000)")
    serve.add_argument("--path", help="Streamable HTTP path (default: /mcp)")
    serve.add_argument("--log-level", help="DEBUG, INFO, WARNING, ERROR, or CRITICAL")
    serve.add_argument(
        "--allowed-host",
        action="append",
        help="allowed HTTP Host header (repeatable; use STPT_MCP_ALLOWED_HOSTS in production)",
    )
    serve.add_argument(
        "--allowed-origin",
        action="append",
        help="allowed browser Origin (repeatable; use STPT_MCP_ALLOWED_ORIGINS)",
    )
    return parser


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _settings_from_args(args: argparse.Namespace) -> Settings:
    settings = Settings.from_env()
    overrides: dict[str, object] = {}
    for attribute in ("transport", "host", "port", "path", "log_level"):
        value = getattr(args, attribute, None)
        if value is not None:
            overrides[attribute] = value
    if "transport" in overrides:
        overrides["transport"] = Transport(str(overrides["transport"]))
    if "log_level" in overrides:
        overrides["log_level"] = str(overrides["log_level"]).upper()
    if getattr(args, "allowed_host", None):
        overrides["allowed_hosts"] = tuple(args.allowed_host)
    if getattr(args, "allowed_origin", None):
        overrides["allowed_origins"] = tuple(args.allowed_origin)
    return replace(settings, **overrides)  # type: ignore[arg-type]


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    if args.command == "token":
        print(generate_auth_token())
        return
    if args.command is None:
        # Running `stpt-mcp` directly is the shortest stdio configuration.
        args = parser.parse_args(["serve"])
    try:
        settings = _settings_from_args(args)
    except SettingsError as exc:
        parser.error(str(exc))
    _configure_logging(settings.log_level)

    if settings.transport is Transport.STDIO:
        logger.info("starting stpt-mcp %s over stdio", __version__)
        build_server(settings).run("stdio")
        return

    if (
        not settings.is_loopback_host()
        and settings.auth_token is None
        and not settings.oauth_enabled()
    ):
        logger.warning(
            "HTTP is bound to %s without STPT_MCP_AUTH_TOKEN or OAuth; put "
            "authentication in front of this service",
            settings.host,
        )
    logger.info(
        "starting stpt-mcp %s over streamable HTTP on %s:%s%s",
        __version__,
        settings.host,
        settings.port,
        settings.path,
    )
    app = create_http_app(settings)
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        access_log=False,
        server_header=False,
        limit_concurrency=settings.max_concurrent_http_requests,
        timeout_keep_alive=30,
    )


if __name__ == "__main__":
    main()
