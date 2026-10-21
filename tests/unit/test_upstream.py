"""HTTP upstream retries, limits, and structured failures."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from stpt_mcp.errors import ErrorCode, StptError
from stpt_mcp.upstream import UpstreamClient, parse_retry_after
from tests.support import ALERTS_URL, FakeResponse, FakeSTPT, MutableClock, make_test_settings


class Sleeps:
    def __init__(self) -> None:
        self.values: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.values.append(seconds)


def make_client(
    fake: FakeSTPT,
    clock: MutableClock,
    *,
    sleeps: Sleeps | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    **overrides: Any,
) -> UpstreamClient:
    settings = make_test_settings(**overrides)
    return UpstreamClient(
        settings,
        transport=transport or httpx.MockTransport(fake.handler),
        sleep=sleeps or Sleeps(),
        wall_clock=clock.wall,
    )


async def test_retries_server_error_then_succeeds(clock: MutableClock) -> None:
    fake = FakeSTPT()
    fake.set_json(ALERTS_URL, {"alerts": []})
    fake.fail_next(ALERTS_URL, status=503)
    sleeps = Sleeps()
    client = make_client(fake, clock, sleeps=sleeps)
    try:
        payload, fetched_at = await client.get_json(ALERTS_URL, source="alerts")
        assert payload == {"alerts": []}
        assert fetched_at == clock.wall()
        assert client.request_count == 2
        assert client.retry_count == 1
        assert sleeps.values
    finally:
        await client.close()


async def test_retry_after_is_honoured_and_bounded(clock: MutableClock) -> None:
    fake = FakeSTPT()

    async def handler(request: httpx.Request) -> httpx.Response:
        fake.requests.append(request)
        if len(fake.requests) == 1:
            return httpx.Response(429, headers={"retry-after": "120"})
        return httpx.Response(200, json={"alerts": []})

    sleeps = Sleeps()
    client = make_client(
        fake,
        clock,
        sleeps=sleeps,
        transport=httpx.MockTransport(handler),
        max_retry_after_seconds=3.0,
    )
    try:
        await client.get_json(ALERTS_URL, source="alerts")
        assert sleeps.values == [3.0]
    finally:
        await client.close()


async def test_non_retryable_status_is_not_retried(clock: MutableClock) -> None:
    fake = FakeSTPT()
    fake.set_response(ALERTS_URL, FakeResponse(status=404))
    client = make_client(fake, clock)
    try:
        with pytest.raises(StptError) as caught:
            await client.get_json(ALERTS_URL, source="alerts")
        assert caught.value.code is ErrorCode.UPSTREAM_HTTP_ERROR
        assert caught.value.retryable is False
        assert client.request_count == 1
    finally:
        await client.close()


async def test_retry_exhaustion_raises_last_error(clock: MutableClock) -> None:
    fake = FakeSTPT()
    fake.fail_next(ALERTS_URL, status=502, times=3)
    client = make_client(fake, clock, max_retries=2)
    try:
        with pytest.raises(StptError) as caught:
            await client.get_json(ALERTS_URL, source="alerts")
        assert caught.value.code is ErrorCode.UPSTREAM_HTTP_ERROR
        assert caught.value.retryable is True
        assert client.request_count == 3
        assert client.retry_count == 2
    finally:
        await client.close()


async def test_timeout_is_retried_and_structured(clock: MutableClock) -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectTimeout("slow", request=request)

    client = make_client(
        FakeSTPT(),
        clock,
        transport=httpx.MockTransport(handler),
        max_retries=1,
    )
    try:
        with pytest.raises(StptError) as caught:
            await client.get_json(ALERTS_URL, source="alerts")
        assert caught.value.code is ErrorCode.UPSTREAM_TIMEOUT
        assert caught.value.retryable is True
        assert calls == 2
    finally:
        await client.close()


async def test_transport_error_is_structured(clock: MutableClock) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = make_client(
        FakeSTPT(),
        clock,
        transport=httpx.MockTransport(handler),
        max_retries=0,
    )
    try:
        with pytest.raises(StptError) as caught:
            await client.get_json(ALERTS_URL, source="alerts")
        assert caught.value.code is ErrorCode.UPSTREAM_UNAVAILABLE
        assert caught.value.retryable is True
    finally:
        await client.close()


async def test_invalid_json_is_rejected(clock: MutableClock) -> None:
    fake = FakeSTPT()
    fake.set_text(ALERTS_URL, "this is not json")
    client = make_client(fake, clock)
    try:
        with pytest.raises(StptError) as caught:
            await client.get_json(ALERTS_URL, source="alerts")
        assert caught.value.code is ErrorCode.UPSTREAM_INVALID_RESPONSE
        assert caught.value.retryable is True
    finally:
        await client.close()


async def test_invalid_utf8_text_is_rejected(clock: MutableClock) -> None:
    fake = FakeSTPT()
    fake.set_response(
        ALERTS_URL,
        FakeResponse(
            body=b"\xff\xfe\x00",
            headers={"content-type": "text/html"},
        ),
    )
    client = make_client(fake, clock)
    try:
        with pytest.raises(StptError) as caught:
            await client.get_text(ALERTS_URL, source="schedule")
        assert caught.value.code is ErrorCode.UPSTREAM_INVALID_RESPONSE
        assert caught.value.retryable is False
    finally:
        await client.close()


async def test_content_length_limit_is_enforced(clock: MutableClock) -> None:
    fake = FakeSTPT()
    fake.set_response(
        ALERTS_URL,
        FakeResponse(
            body=b"{" + b'"x":' + b'"' + b"a" * 2000 + b'"}',
            headers={"content-length": "3000", "content-type": "application/json"},
        ),
    )
    client = make_client(fake, clock, max_response_bytes=1024)
    try:
        with pytest.raises(StptError) as caught:
            await client.get_json(ALERTS_URL, source="alerts")
        assert caught.value.code is ErrorCode.UPSTREAM_RESPONSE_TOO_LARGE
        assert caught.value.retryable is False
    finally:
        await client.close()


async def test_streamed_body_limit_is_enforced(clock: MutableClock) -> None:
    fake = FakeSTPT()
    fake.set_response(
        ALERTS_URL,
        FakeResponse(
            body=b"x" * 4096,
            headers={"content-type": "application/json"},
        ),
    )
    client = make_client(fake, clock, max_response_bytes=1024)
    try:
        with pytest.raises(StptError) as caught:
            await client.get_json(ALERTS_URL, source="alerts")
        assert caught.value.code is ErrorCode.UPSTREAM_RESPONSE_TOO_LARGE
    finally:
        await client.close()


async def test_closed_client_reports_not_ready(clock: MutableClock) -> None:
    client = make_client(FakeSTPT(), clock)
    await client.close()
    with pytest.raises(StptError) as caught:
        await client.get_json(ALERTS_URL, source="alerts")
    assert caught.value.code is ErrorCode.NOT_READY


@pytest.mark.parametrize(
    ("value", "now", "expected"),
    [
        ("5", None, 5.0),
        ("0", None, 0.0),
        ("-3", None, 0.0),
        ("not-a-number", None, None),
        (
            "Wed, 21 Oct 2026 09:00:30 GMT",
            datetime(2026, 10, 21, 9, 0, tzinfo=UTC),
            30.0,
        ),
        (
            "Wed, 21 Oct 2026 09:00:30 GMT",
            datetime(2026, 10, 21, 9, 1, tzinfo=UTC),
            0.0,
        ),
        ("Wed, 99 Oct 2026 09:00:30 GMT", None, None),
        (None, None, None),
        ("", None, None),
    ],
)
def test_parse_retry_after(value: str | None, now: datetime | None, expected: float | None) -> None:
    result = parse_retry_after(value, now)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_parse_retry_after_naive_http_date_is_assumed_utc() -> None:
    now = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
    assert parse_retry_after("Wed, 21 Oct 2026 09:00:20 GMT", now) == pytest.approx(20.0)


async def test_fetched_at_is_timezone_aware(clock: MutableClock) -> None:
    fake = FakeSTPT()
    fake.set_json(ALERTS_URL, {"alerts": []})
    client = make_client(fake, clock)
    try:
        _, fetched_at = await client.get_json(ALERTS_URL, source="alerts")
        assert fetched_at.tzinfo is not None
        assert fetched_at.utcoffset() == timedelta(0)
    finally:
        await client.close()
