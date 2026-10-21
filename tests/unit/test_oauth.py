"""OAuth discovery, registration, login, and token flow tests."""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

from starlette.testclient import TestClient

from stpt_mcp.config import Settings
from stpt_mcp.http_app import create_http_app

PUBLIC_URL = "https://mcp.example.com"
REDIRECT_URI = "https://claude.ai/api/mcp/auth_callback"
STATIC_TOKEN = "test-token-1234567890"
PASSWORD = "test-password-1234"


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "public_url": PUBLIC_URL,
        "auth_token": STATIC_TOKEN,
        "oauth_password": PASSWORD,
        "allowed_hosts": ("mcp.example.com",),
        "allowed_origins": (PUBLIC_URL,),
    }
    values.update(overrides)
    return Settings(**values)


def app_client(settings: Settings | None = None) -> TestClient:
    return TestClient(
        create_http_app(settings or make_settings()),
        base_url=PUBLIC_URL,
        follow_redirects=False,
    )


def register_client(client: TestClient) -> str:
    response = client.post(
        "/register",
        json={
            "redirect_uris": [REDIRECT_URI],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "client_name": "Claude",
            "scope": "mcp",
        },
    )
    assert response.status_code == 201
    return str(response.json()["client_id"])


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    return verifier, challenge


def begin_authorization(
    client: TestClient,
    client_id: str,
    *,
    resource: str = f"{PUBLIC_URL}/mcp",
    scope: str = "mcp",
) -> tuple[str, str]:
    verifier, challenge = pkce_pair()
    response = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "state-123",
            "scope": scope,
            "resource": resource,
        },
    )
    assert response.status_code == 302
    login_url = response.headers["location"]
    return verifier, login_url


def complete_login(
    client: TestClient,
    login_url: str,
    *,
    password: str = PASSWORD,
) -> str:
    login_id = parse_qs(urlparse(login_url).query)["login_id"][0]
    response = client.post(
        "/oauth/login",
        data={"login_id": login_id, "password": password},
    )
    assert response.status_code == 302
    location = response.headers["location"]
    query = parse_qs(urlparse(location).query)
    assert query["state"] == ["state-123"]
    return str(query["code"][0])


def exchange_code(
    client: TestClient,
    client_id: str,
    code: str,
    verifier: str,
) -> dict[str, Any]:
    response = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def initialize(client: TestClient, token: str) -> int:
    return client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2026-07-28",
                "capabilities": {},
                "clientInfo": {"name": "oauth-test", "version": "1.0"},
            },
        },
        headers={
            "accept": "application/json, text/event-stream",
            "content-type": "application/json",
            "authorization": f"Bearer {token}",
        },
    ).status_code


def test_oauth_discovery_metadata() -> None:
    with app_client() as client:
        authorization = client.get("/.well-known/oauth-authorization-server")
        assert authorization.status_code == 200
        metadata = authorization.json()
        assert metadata["issuer"] == PUBLIC_URL
        assert metadata["authorization_endpoint"] == f"{PUBLIC_URL}/authorize"
        assert metadata["token_endpoint"] == f"{PUBLIC_URL}/token"
        assert metadata["registration_endpoint"] == f"{PUBLIC_URL}/register"
        assert metadata["code_challenge_methods_supported"] == ["S256"]
        assert "none" in metadata["token_endpoint_auth_methods_supported"]

        protected = client.get("/.well-known/oauth-protected-resource/mcp")
        assert protected.status_code == 200
        resource = protected.json()
        assert resource["resource"] == f"{PUBLIC_URL}/mcp"
        assert resource["authorization_servers"][0].rstrip("/") == PUBLIC_URL


def test_oauth_flow_issues_a_token_that_authorizes_mcp() -> None:
    with app_client() as client:
        client_id = register_client(client)
        verifier, login_url = begin_authorization(client, client_id)
        code = complete_login(client, login_url)
        tokens = exchange_code(client, client_id, code, verifier)

        assert tokens["token_type"] == "Bearer"
        assert tokens["scope"] == "mcp"
        assert tokens["expires_in"] == 3600
        assert tokens["refresh_token"]
        assert initialize(client, tokens["access_token"]) == 200


def test_static_bearer_token_still_works_with_oauth_enabled() -> None:
    with app_client() as client:
        assert initialize(client, STATIC_TOKEN) == 200
        assert initialize(client, "wrong-token") == 401


def test_wrong_password_does_not_issue_a_code() -> None:
    with app_client() as client:
        client_id = register_client(client)
        _, login_url = begin_authorization(client, client_id)
        login_id = parse_qs(urlparse(login_url).query)["login_id"][0]
        response = client.post(
            "/oauth/login",
            data={"login_id": login_id, "password": "wrong-password"},
        )
        assert response.status_code == 401
        assert "location" not in response.headers
        assert "Incorrect password" in response.text


def test_registration_rejects_untrusted_redirect_uri() -> None:
    with app_client() as client:
        response = client.post(
            "/register",
            json={
                "redirect_uris": ["https://evil.example/callback"],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
            },
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_redirect_uri"


def test_authorization_rejects_unexpected_resource() -> None:
    with app_client() as client:
        client_id = register_client(client)
        response = client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": REDIRECT_URI,
                "code_challenge": "x" * 43,
                "code_challenge_method": "S256",
                "resource": "https://other.example/mcp",
            },
        )
        assert response.status_code == 302
        query = parse_qs(urlparse(response.headers["location"]).query)
        assert query["error"] == ["invalid_target"]


def test_refresh_token_rotates_and_authorizes_mcp() -> None:
    with app_client() as client:
        client_id = register_client(client)
        verifier, login_url = begin_authorization(client, client_id)
        code = complete_login(client, login_url)
        tokens = exchange_code(client, client_id, code, verifier)

        response = client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "client_id": client_id,
            },
        )
        assert response.status_code == 200, response.text
        refreshed = response.json()
        assert refreshed["access_token"] != tokens["access_token"]
        assert refreshed["refresh_token"] != tokens["refresh_token"]
        assert initialize(client, refreshed["access_token"]) == 200
