"""Minimal OAuth 2.1 authorization server for the remote MCP endpoint.

The MCP SDK provides the protocol handlers, discovery metadata, dynamic client
registration, PKCE verification, and bearer-token middleware. This module owns
the small amount of state needed by a single-owner server: registered clients,
authorization codes, access tokens, refresh tokens, and short-lived browser
login requests.
"""

from __future__ import annotations

import asyncio
import hmac
import html
import json
import secrets
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, TypeVar, cast
from urllib.parse import urlencode, urlparse

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    construct_redirect_uri,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from stpt_mcp.config import Settings

MCP_SCOPE = "mcp"
OAUTH_TOKEN_TYPE: Literal["Bearer"] = "Bearer"  # noqa: S105 - token type, not a credential
MAX_CLIENTS = 100
MAX_AUTHORIZATION_CODES = 1_000
MAX_LOGIN_REQUESTS = 1_000
MAX_ACCESS_TOKENS = 5_000
MAX_REFRESH_TOKENS = 1_000

_StoreT = TypeVar("_StoreT")


@dataclass(frozen=True, slots=True)
class LoginRequest:
    """A pending browser login for one authorization request."""

    client_id: str
    params: AuthorizationParams
    expires_at: float


class StptOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """Single-owner OAuth provider with in-memory, bounded token state."""

    def __init__(
        self,
        *,
        issuer_url: str,
        resource_url: str,
        password: str,
        static_token: str | None = None,
        allowed_redirect_uris: tuple[str, ...] = (),
        access_token_ttl_seconds: float = 3600.0,
        refresh_token_ttl_seconds: float = 2_592_000.0,
        login_ttl_seconds: float = 600.0,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.issuer_url = issuer_url.rstrip("/")
        self.resource_url = resource_url.rstrip("/")
        self.password = password
        self.static_token = static_token
        self.allowed_redirect_uris = frozenset(allowed_redirect_uris)
        self.access_token_ttl_seconds = access_token_ttl_seconds
        self.refresh_token_ttl_seconds = refresh_token_ttl_seconds
        self.login_ttl_seconds = login_ttl_seconds
        self._clock = clock
        self._sleep = sleep
        self._clients: OrderedDict[str, OAuthClientInformationFull] = OrderedDict()
        self._codes: OrderedDict[str, AuthorizationCode] = OrderedDict()
        self._logins: OrderedDict[str, LoginRequest] = OrderedDict()
        self._access_tokens: OrderedDict[str, AccessToken] = OrderedDict()
        self._refresh_tokens: OrderedDict[str, RefreshToken] = OrderedDict()
        self._access_to_refresh: dict[str, str] = {}
        self._refresh_to_access: dict[str, str] = {}

    def _redirect_allowed(self, redirect_uri: str) -> bool:
        normalized = redirect_uri.rstrip("/")
        if normalized in {uri.rstrip("/") for uri in self.allowed_redirect_uris}:
            return True
        parsed = urlparse(redirect_uri)
        return parsed.scheme == "http" and parsed.hostname in {
            "localhost",
            "127.0.0.1",
            "::1",
        }

    @staticmethod
    def _put(
        store: OrderedDict[str, _StoreT],
        key: str,
        value: _StoreT,
        max_size: int,
    ) -> None:
        store[key] = value
        store.move_to_end(key)
        while len(store) > max_size:
            store.popitem(last=False)

    def _prune(self) -> None:
        now = self._clock()
        for key, code in list(self._codes.items()):
            if code.expires_at < now:
                self._codes.pop(key, None)
        for key, access in list(self._access_tokens.items()):
            if access.expires_at is not None and access.expires_at < now:
                self._access_tokens.pop(key, None)
        for key, refresh in list(self._refresh_tokens.items()):
            if refresh.expires_at is not None and refresh.expires_at < now:
                self._refresh_tokens.pop(key, None)
        for key, request in list(self._logins.items()):
            if request.expires_at < now:
                self._logins.pop(key, None)

        for access_token in list(self._access_to_refresh):
            refresh_token = self._access_to_refresh[access_token]
            if access_token not in self._access_tokens or refresh_token not in self._refresh_tokens:
                self._access_to_refresh.pop(access_token, None)
        for refresh_token in list(self._refresh_to_access):
            access_token = self._refresh_to_access[refresh_token]
            if refresh_token not in self._refresh_tokens or access_token not in self._access_tokens:
                self._refresh_to_access.pop(refresh_token, None)

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        self._prune()
        return self._clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        redirect_uris = client_info.redirect_uris or []
        if not redirect_uris:
            raise RegistrationError(
                "invalid_redirect_uri", "At least one redirect URI is required."
            )
        for redirect_uri in redirect_uris:
            if not self._redirect_allowed(str(redirect_uri)):
                raise RegistrationError(
                    "invalid_redirect_uri",
                    f"Redirect URI is not allowed: {redirect_uri}",
                )
        client_info.issuer = self.issuer_url
        self._put(self._clients, client_info.client_id, client_info, MAX_CLIENTS)

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        if params.resource is not None and params.resource.rstrip("/") != self.resource_url:
            raise AuthorizeError("invalid_target", "The requested resource is not this server.")
        scopes = params.scopes or [MCP_SCOPE]
        if any(scope != MCP_SCOPE for scope in scopes):
            raise AuthorizeError("invalid_scope", "Only the 'mcp' scope is supported.")

        login_id = secrets.token_urlsafe(32)
        self._put(
            self._logins,
            login_id,
            LoginRequest(
                client_id=client.client_id,
                params=params,
                expires_at=self._clock() + self.login_ttl_seconds,
            ),
            MAX_LOGIN_REQUESTS,
        )
        return f"{self.issuer_url}/oauth/login?{urlencode({'login_id': login_id})}"

    async def handle_login(self, request: Request) -> Response:
        if request.method == "GET":
            return self._login_response(request.query_params.get("login_id"))

        form = await request.form()
        login_id = form.get("login_id")
        password = form.get("password")
        if not isinstance(login_id, str) or not isinstance(password, str):
            return self._login_response(None, error="The sign-in form is incomplete.", status=400)

        login = self._logins.get(login_id)
        if login is None or login.expires_at < self._clock():
            self._logins.pop(login_id, None)
            return self._login_response(
                None,
                error="This sign-in request expired. Start again from Claude.",
                status=400,
            )

        expected = self.password
        valid = len(password) <= 512 and hmac.compare_digest(
            password.encode("utf-8"), expected.encode("utf-8")
        )
        if not valid:
            await self._sleep(0.25)
            return self._login_response(login_id, error="Incorrect password.", status=401)

        code = secrets.token_urlsafe(32)
        self._put(
            self._codes,
            code,
            AuthorizationCode(
                code=code,
                scopes=login.params.scopes or [MCP_SCOPE],
                expires_at=self._clock() + 300.0,
                client_id=login.client_id,
                code_challenge=login.params.code_challenge,
                redirect_uri=login.params.redirect_uri,
                redirect_uri_provided_explicitly=login.params.redirect_uri_provided_explicitly,
                resource=login.params.resource or self.resource_url,
                subject="owner",
            ),
            MAX_AUTHORIZATION_CODES,
        )
        self._logins.pop(login_id, None)
        redirect = construct_redirect_uri(
            str(login.params.redirect_uri),
            code=code,
            state=login.params.state,
        )
        return RedirectResponse(
            redirect,
            status_code=302,
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    def _login_response(
        self,
        login_id: str | None,
        *,
        error: str | None = None,
        status: int = 200,
    ) -> Response:
        login = self._logins.get(login_id) if login_id else None
        if login is None or login.expires_at < self._clock():
            if login_id:
                self._logins.pop(login_id, None)
            return HTMLResponse(
                self._login_html(
                    login_id="",
                    client_name="this client",
                    error="This sign-in request is invalid or expired.",
                ),
                status_code=400,
                headers=self._html_headers(),
            )
        login_id = login_id or ""
        client = self._clients.get(login.client_id)
        client_name = client.client_name if client and client.client_name else "this client"
        return HTMLResponse(
            self._login_html(login_id=login_id, client_name=client_name, error=error),
            status_code=status,
            headers=self._html_headers(),
        )

    @staticmethod
    def _html_headers() -> dict[str, str]:
        return {
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; "
                "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
            ),
        }

    @staticmethod
    def _login_html(*, login_id: str, client_name: str, error: str | None) -> str:
        error_html = f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ""
        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sign in · STPT MCP</title>
  <style>
    :root {{ color-scheme: light dark; }}
    body {{ font-family: system-ui, sans-serif; margin: 0; min-height: 100vh;
           display: grid; place-items: center; background: #111; color: #eee; }}
    main {{ width: min(24rem, calc(100vw - 2rem)); padding: 2rem;
            border: 1px solid #444; border-radius: 1rem; background: #1b1b1b; }}
    h1 {{ font-size: 1.35rem; margin: 0 0 .5rem; }}
    p {{ line-height: 1.45; }}
    label, input, button {{ display: block; width: 100%; box-sizing: border-box; }}
    label {{ margin: 1.25rem 0 .4rem; font-weight: 600; }}
    input {{ padding: .7rem; border: 1px solid #666; border-radius: .5rem;
             background: #0d0d0d; color: inherit; }}
    button {{ margin-top: 1rem; padding: .75rem; border: 0; border-radius: .5rem;
              background: #e6e6e6; color: #111; font-weight: 700; cursor: pointer; }}
    .error {{ color: #ffb4b4; }}
    .muted {{ color: #aaa; font-size: .9rem; }}
  </style>
</head>
<body>
  <main>
    <h1>STPT MCP</h1>
    <p><strong>{html.escape(client_name)}</strong> wants to use the STPT transit tools.</p>
    {error_html}
    <form method="post" action="/oauth/login">
      <input type="hidden" name="login_id" value="{html.escape(login_id)}">
      <label for="password">Password</label>
      <input id="password" name="password" type="password"
             autocomplete="current-password" required autofocus>
      <button type="submit">Sign in</button>
    </form>
    <p class="muted">Use the sign-in password configured on this server.</p>
  </main>
</body>
</html>
"""

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        self._prune()
        code = self._codes.get(authorization_code)
        if code is None or code.client_id != client.client_id:
            return None
        return code

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        self._codes.pop(authorization_code.code, None)
        return self._issue_tokens(
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            resource=authorization_code.resource or self.resource_url,
            subject=authorization_code.subject or "owner",
        )

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        self._prune()
        token = self._refresh_tokens.get(refresh_token)
        if token is None or token.client_id != client.client_id:
            return None
        return token

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        self._refresh_tokens.pop(refresh_token.token, None)
        old_access = self._refresh_to_access.pop(refresh_token.token, None)
        if old_access is not None:
            self._access_tokens.pop(old_access, None)
            self._access_to_refresh.pop(old_access, None)
        return self._issue_tokens(
            client_id=client.client_id,
            scopes=scopes,
            resource=refresh_token.resource or self.resource_url,
            subject=refresh_token.subject or "owner",
        )

    def _issue_tokens(
        self,
        *,
        client_id: str,
        scopes: list[str],
        resource: str,
        subject: str,
    ) -> OAuthToken:
        now = int(self._clock())
        access_token = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(32)
        access = AccessToken(
            token=access_token,
            client_id=client_id,
            scopes=scopes,
            expires_at=now + int(self.access_token_ttl_seconds),
            resource=resource,
            subject=subject,
        )
        refresh = RefreshToken(
            token=refresh_token,
            client_id=client_id,
            scopes=scopes,
            expires_at=now + int(self.refresh_token_ttl_seconds),
            resource=resource,
            subject=subject,
        )
        self._put(self._access_tokens, access_token, access, MAX_ACCESS_TOKENS)
        self._put(self._refresh_tokens, refresh_token, refresh, MAX_REFRESH_TOKENS)
        self._access_to_refresh[access_token] = refresh_token
        self._refresh_to_access[refresh_token] = access_token
        return OAuthToken(
            access_token=access_token,
            token_type=OAUTH_TOKEN_TYPE,
            expires_in=int(self.access_token_ttl_seconds),
            scope=" ".join(scopes),
            refresh_token=refresh_token,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        self._prune()
        if self.static_token is not None and hmac.compare_digest(
            token.encode("utf-8"), self.static_token.encode("utf-8")
        ):
            return AccessToken(
                token=token,
                client_id="static",
                scopes=[MCP_SCOPE],
                resource=self.resource_url,
                subject="owner",
            )
        access = self._access_tokens.get(token)
        if access is None:
            return None
        if access.expires_at is not None and access.expires_at < int(self._clock()):
            self._access_tokens.pop(token, None)
            return None
        return access

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        if isinstance(token, RefreshToken):
            self._refresh_tokens.pop(token.token, None)
            access_token = self._refresh_to_access.pop(token.token, None)
            if access_token is not None:
                self._access_tokens.pop(access_token, None)
                self._access_to_refresh.pop(access_token, None)
            return
        self._access_tokens.pop(token.token, None)
        refresh_token = self._access_to_refresh.pop(token.token, None)
        if refresh_token is not None:
            self._refresh_tokens.pop(refresh_token, None)
            self._refresh_to_access.pop(refresh_token, None)


def build_oauth_provider(settings: Settings) -> StptOAuthProvider | None:
    """Create the provider when the public OAuth configuration is enabled."""
    if not settings.oauth_enabled():
        return None
    password = settings.oauth_login_password()
    resource_url = settings.resource_url()
    if password is None or settings.public_url is None or resource_url is None:
        return None
    return StptOAuthProvider(
        issuer_url=settings.public_url,
        resource_url=resource_url,
        password=password,
        static_token=settings.auth_token,
        allowed_redirect_uris=settings.oauth_allowed_redirect_uris,
        access_token_ttl_seconds=settings.oauth_access_token_ttl_seconds,
        refresh_token_ttl_seconds=settings.oauth_refresh_token_ttl_seconds,
        login_ttl_seconds=settings.oauth_login_ttl_seconds,
    )


class OAuthMetadataMiddleware:
    """Advertise public-client token requests in the SDK metadata response.

    The SDK currently lists only the two client-secret methods even though its
    registration handler and token authenticator support ``none``. Claude and
    other MCP clients use public clients with PKCE, so add that method to the
    discovery document without replacing the SDK handlers.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "GET"
            or scope.get("path") != "/.well-known/oauth-authorization-server"
        ):
            await self.app(scope, receive, send)
            return

        messages: list[Message] = []

        async def capture(message: Message) -> None:
            messages.append(message)

        await self.app(scope, receive, capture)
        if not messages or messages[0].get("type") != "http.response.start":
            for message in messages:
                await send(message)
            return

        start = messages[0]
        body = b"".join(
            message.get("body", b"")
            for message in messages[1:]
            if message.get("type") == "http.response.body"
        )
        if start.get("status") == 200:
            try:
                metadata = json.loads(body)
            except (TypeError, ValueError):
                metadata = None
            if isinstance(metadata, dict):
                for key in (
                    "token_endpoint_auth_methods_supported",
                    "revocation_endpoint_auth_methods_supported",
                ):
                    methods = metadata.get(key)
                    if isinstance(methods, list) and "none" not in methods:
                        methods.append("none")
                body = json.dumps(metadata, separators=(",", ":")).encode()
                headers = [
                    (key, value)
                    for key, value in start.get("headers", [])
                    if key.lower() != b"content-length"
                ]
                headers.append((b"content-length", str(len(body)).encode()))
                start = {**start, "headers": headers}

        await send(start)
        await send({"type": "http.response.body", "body": body})


def build_auth_settings(settings: Settings) -> AuthSettings | None:
    """Build SDK auth settings for the public OAuth mode."""
    resource_url = settings.resource_url()
    if settings.public_url is None or resource_url is None:
        return None
    issuer = settings.public_url.rstrip("/")
    return AuthSettings(
        issuer_url=cast(AnyHttpUrl, issuer),
        service_documentation_url=AnyHttpUrl(
            "https://github.com/vladirocox/stpt-mcp/blob/main/docs/OAUTH.md"
        ),
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=[MCP_SCOPE],
            default_scopes=[MCP_SCOPE],
        ),
        revocation_options=RevocationOptions(enabled=True),
        required_scopes=[MCP_SCOPE],
        resource_server_url=cast(AnyHttpUrl, resource_url),
        validate_token_resource=True,
    )
