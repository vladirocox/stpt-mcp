"""Bounded, retrying HTTP access to public STPT endpoints."""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from stpt_mcp.config import Settings
from stpt_mcp.errors import ErrorCode, StptError

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
SUCCESS_STATUS = frozenset({200})


@dataclass(frozen=True, slots=True)
class UpstreamResponse:
    body: bytes
    status_code: int
    fetched_at: datetime
    url: str
    retry_after: float | None = None


class UpstreamClient:
    """HTTP client shared for the process lifetime."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: random.Random | None = None,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._settings = settings
        self._sleep = sleep
        self._rng = rng or random.Random()  # nosec B311 - jitter is not security-sensitive
        self._wall_clock = wall_clock
        self._semaphore = asyncio.Semaphore(settings.upstream_concurrency)
        self._closed = False
        self._requests = 0
        self._retries = 0
        self._http = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(
                settings.request_timeout_seconds,
                connect=settings.connect_timeout_seconds,
            ),
            limits=httpx.Limits(
                max_connections=settings.upstream_concurrency,
                max_keepalive_connections=min(4, settings.upstream_concurrency),
            ),
            headers={
                "User-Agent": settings.user_agent,
                "Accept-Encoding": "gzip, deflate",
            },
            follow_redirects=False,
        )

    async def get_json(
        self,
        url: str,
        *,
        source: str,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[Any, datetime]:
        response = await self._get(url, source=source, params=params, headers=headers)
        try:
            payload = json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StptError(
                ErrorCode.UPSTREAM_INVALID_RESPONSE,
                f"{source} returned invalid JSON",
                retryable=True,
                upstream=source,
            ) from exc
        return payload, response.fetched_at

    async def get_text(
        self,
        url: str,
        *,
        source: str,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[str, datetime]:
        response = await self._get(url, source=source, params=params, headers=headers)
        try:
            return response.body.decode("utf-8"), response.fetched_at
        except UnicodeDecodeError as exc:
            raise StptError(
                ErrorCode.UPSTREAM_INVALID_RESPONSE,
                f"{source} returned non-UTF-8 text",
                retryable=False,
                upstream=source,
            ) from exc

    async def _get(
        self,
        url: str,
        *,
        source: str,
        params: Mapping[str, str] | None,
        headers: Mapping[str, str] | None,
    ) -> UpstreamResponse:
        if self._closed:
            raise StptError(
                ErrorCode.NOT_READY,
                "upstream client is closed",
                upstream=source,
            )
        request_headers = {"Accept": "*/*"}
        if headers:
            request_headers.update(headers)

        last_error: StptError | None = None
        for attempt in range(self._settings.max_retries + 1):
            if attempt:
                self._retries += 1
            try:
                response = await self._request_once(
                    url,
                    source=source,
                    params=params,
                    headers=request_headers,
                )
            except StptError as exc:
                last_error = exc
                if not exc.retryable or attempt >= self._settings.max_retries:
                    raise
                await self._sleep(self._backoff(attempt))
                continue

            if response.status_code in SUCCESS_STATUS:
                return response

            retryable = response.status_code in RETRYABLE_STATUS
            if response.status_code == 429:
                code = ErrorCode.UPSTREAM_RATE_LIMITED
                message = f"{source} rate-limited this server (HTTP 429)"
            elif response.status_code >= 500:
                code = ErrorCode.UPSTREAM_HTTP_ERROR
                message = f"{source} is unavailable (HTTP {response.status_code})"
            else:
                code = ErrorCode.UPSTREAM_HTTP_ERROR
                message = f"{source} returned unexpected HTTP {response.status_code}"
            last_error = StptError(
                code,
                message,
                retryable=retryable,
                upstream=source,
            )
            if not retryable or attempt >= self._settings.max_retries:
                raise last_error

            retry_after = response.retry_after
            delay = (
                min(retry_after, self._settings.max_retry_after_seconds)
                if retry_after is not None
                else self._backoff(attempt)
            )
            await self._sleep(delay)

        error = last_error
        if error is None:  # pragma: no cover - the loop assigns or returns
            error = StptError(
                ErrorCode.INTERNAL_ERROR,
                f"{source} request failed without an error",
                retryable=True,
                upstream=source,
            )
        raise error

    async def _request_once(
        self,
        url: str,
        *,
        source: str,
        params: Mapping[str, str] | None,
        headers: Mapping[str, str],
    ) -> UpstreamResponse:
        async with self._semaphore:
            self._requests += 1
            try:
                async with self._http.stream(
                    "GET",
                    url,
                    params=params,
                    headers=headers,
                ) as response:
                    if response.status_code in SUCCESS_STATUS:
                        content_length = response.headers.get("content-length")
                        if (
                            content_length is not None
                            and content_length.isdigit()
                            and int(content_length) > self._settings.max_response_bytes
                        ):
                            raise StptError(
                                ErrorCode.UPSTREAM_RESPONSE_TOO_LARGE,
                                f"{source} response exceeds the configured size limit",
                                retryable=False,
                                upstream=source,
                            )
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > self._settings.max_response_bytes:
                                raise StptError(
                                    ErrorCode.UPSTREAM_RESPONSE_TOO_LARGE,
                                    f"{source} response exceeds the configured size limit",
                                    retryable=False,
                                    upstream=source,
                                )
                        return UpstreamResponse(
                            body=bytes(body),
                            status_code=response.status_code,
                            fetched_at=self._wall_clock(),
                            url=str(response.url),
                        )
                    # Preserve only the small set of headers needed for retry policy.
                    # Response bodies of failed requests are never exposed.
                    return UpstreamResponse(
                        body=b"",
                        status_code=response.status_code,
                        fetched_at=self._wall_clock(),
                        url=str(response.url),
                        retry_after=parse_retry_after(
                            response.headers.get("retry-after"), self._wall_clock()
                        ),
                    )
            except httpx.TimeoutException as exc:
                raise StptError(
                    ErrorCode.UPSTREAM_TIMEOUT,
                    f"{source} timed out",
                    retryable=True,
                    upstream=source,
                ) from exc
            except httpx.HTTPError as exc:
                raise StptError(
                    ErrorCode.UPSTREAM_UNAVAILABLE,
                    f"{source} could not be reached",
                    retryable=True,
                    upstream=source,
                ) from exc

    def _backoff(self, attempt: int) -> float:
        ceiling = min(
            self._settings.backoff_max_seconds,
            self._settings.backoff_base_seconds * (2**attempt),
        )
        # Equal jitter: spread retries without sleeping near zero.
        return float(ceiling / 2 + self._rng.random() * ceiling / 2)

    @property
    def request_count(self) -> int:
        return self._requests

    @property
    def retry_count(self) -> int:
        return self._retries

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await self._http.aclose()


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Parse Retry-After seconds or an HTTP date into a non-negative delay."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    current = now or datetime.now(UTC)
    return max(0.0, (parsed - current).total_seconds())
