"""Shared deterministic test doubles and helpers.

Nothing in this module contacts a live STPT service. HTTP is either an
``httpx.MockTransport`` or a loopback server owned by the test process.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import httpx
import uvicorn

from stpt_mcp.config import Settings
from stpt_mcp.service import TransitService
from stpt_mcp.upstream import UpstreamClient

FIXTURES = Path(__file__).parent / "fixtures"
FIXED_NOW = datetime(2026, 10, 21, 10, 30, tzinfo=UTC)
LIVE_URL = "https://live.test/proxy-smtt-cache.php"
ALERTS_URL = "https://live.test/proxy-alerts.php"
VEHICLES_URL = "https://live.test/gtfs-vehicles.php"
SCHEDULE_BASE_URL = "https://smtt.test"

SCHEDULE_PAGES = {
    "/linie-transport-public-1/": "line-1.html",
    "/linie-transport-public-2/": "line-2.html",
    "/linie-transport-public-4-r/": "line-4-r.html",
    "/linie-transport-public-7/": "line-7.html",
    "/linie-transport-public-8/": "line-8.html",
}


def fixture_path(name: str) -> Path:
    return FIXTURES / name


def load_text(name: str) -> str:
    return fixture_path(name).read_text(encoding="utf-8")


def load_json(name: str) -> Any:
    return json.loads(load_text(name))


def rebase_live_payload(payload: Any, *, now: datetime | None = None) -> Any:
    """Return a live payload whose display times track ``now``.

    Static fixtures are useful for deterministic unit tests, but a real feed
    cannot stand still. The loopback e2e server rebases each ``HH:MM|minutes``
    pair so the implied observation clock stays at the wall clock.
    """

    current = (now or datetime.now(UTC)).astimezone(ZoneInfo("Europe/Bucharest"))
    for item in payload:
        if not isinstance(item, dict):
            continue
        times = item.get("times")
        if not isinstance(times, list):
            continue
        rebased: list[Any] = []
        for entry in times:
            if not isinstance(entry, str):
                rebased.append(entry)
                continue
            _clock, separator, minutes_text = entry.partition("|")
            if not separator:
                rebased.append(entry)
                continue
            try:
                eta = int(minutes_text)
            except ValueError:
                rebased.append(entry)
                continue
            rebased.append(f"{current + timedelta(minutes=eta):%H:%M}|{eta}")
        item["times"] = rebased
    return payload


class MutableClock:
    """A clock whose monotonic and wall readings advance together."""

    def __init__(self, now: datetime = FIXED_NOW, *, monotonic: float = 1_000.0) -> None:
        self.now = now
        self._monotonic = monotonic

    def monotonic(self) -> float:
        return self._monotonic

    def wall(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self._monotonic += seconds
        self.now = self.now + timedelta(seconds=seconds)


@dataclass(frozen=True)
class FakeResponse:
    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    delay: float = 0.0


RouteSpec = FakeResponse | Callable[[httpx.Request], FakeResponse]


class FakeSTPT:
    """URL-keyed fake of the public STPT endpoints with request accounting."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self._routes: dict[str, RouteSpec] = {}
        self._faults: dict[str, list[FakeResponse]] = defaultdict(list)

    @staticmethod
    def key(request_or_url: httpx.Request | str) -> str:
        raw = request_or_url.url if isinstance(request_or_url, httpx.Request) else request_or_url
        url = raw if isinstance(raw, httpx.URL) else httpx.URL(raw)
        return str(url.copy_with(query=None))

    def set_response(self, url: str, response: RouteSpec) -> None:
        self._routes[self.key(url)] = response

    def set_json(self, url: str, payload: Any, *, status: int = 200) -> None:
        self.set_response(
            url,
            FakeResponse(
                status=status,
                body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"content-type": "application/json"},
            ),
        )

    def set_json_callback(
        self, url: str, callback: Callable[[httpx.Request], Any], *, status: int = 200
    ) -> None:
        def spec(request: httpx.Request) -> FakeResponse:
            return FakeResponse(
                status=status,
                body=json.dumps(callback(request), ensure_ascii=False).encode("utf-8"),
                headers={"content-type": "application/json"},
            )

        self.set_response(url, spec)

    def set_text(self, url: str, text: str, *, status: int = 200) -> None:
        self.set_response(
            url,
            FakeResponse(
                status=status,
                body=text.encode("utf-8"),
                headers={"content-type": "text/html; charset=utf-8"},
            ),
        )

    def set_delay(self, url: str, seconds: float) -> None:
        current = self._routes[self.key(url)]
        if callable(current):

            def delayed(request: httpx.Request) -> FakeResponse:
                response = current(request)
                return replace(response, delay=response.delay + seconds)

            self._routes[self.key(url)] = delayed
            return
        self._routes[self.key(url)] = replace(current, delay=current.delay + seconds)

    def fail_next(self, url: str, *, status: int = 503, times: int = 1) -> None:
        self._faults[self.key(url)].extend(
            FakeResponse(status=status, body=b"upstream failure") for _ in range(times)
        )

    def clear_requests(self) -> None:
        self.requests.clear()

    def requests_for(self, url: str) -> list[httpx.Request]:
        key = self.key(url)
        return [request for request in self.requests if self.key(request) == key]

    def count(self, url: str) -> int:
        return len(self.requests_for(url))

    def query_counts(self, url: str, parameter: str) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for request in self.requests_for(url):
            value = request.url.params.get(parameter)
            counts[value or ""] += 1
        return dict(counts)

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = self.key(request)
        faults = self._faults.get(key)
        if faults:
            spec: RouteSpec = faults.pop(0)
        else:
            registered = self._routes.get(key)
            if registered is None:
                return httpx.Response(404, content=b"not registered in FakeSTPT")
            spec = registered
        response = spec(request) if callable(spec) else spec
        if response.delay:
            await asyncio.sleep(response.delay)
        return httpx.Response(
            response.status,
            content=response.body,
            headers=response.headers,
        )


ServiceFactory = Callable[..., Awaitable[tuple[TransitService, FakeSTPT]]]


def seed_fake_stpt(fake: FakeSTPT, *, schedule_base_url: str = SCHEDULE_BASE_URL) -> FakeSTPT:
    """Populate a fake with the deterministic arrivals, alerts, and schedules."""

    live_85 = load_json("live_85.json")
    live_60 = load_json("live_60.json")

    def live_payload(request: httpx.Request) -> Any:
        stop_id = request.url.params.get("stopid")
        if stop_id == "85":
            return live_85
        if stop_id == "60":
            return live_60
        return []

    fake.set_json_callback(LIVE_URL, live_payload)
    fake.set_json(ALERTS_URL, load_json("alerts.json"))
    fake.set_json(VEHICLES_URL, load_json("vehicles.json"))
    for path, fixture in SCHEDULE_PAGES.items():
        fake.set_text(f"{schedule_base_url}{path}", load_text(f"schedule/{fixture}"))
    return fake


async def no_sleep(_seconds: float) -> None:
    return None


def make_test_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "live_url": LIVE_URL,
        "alerts_url": ALERTS_URL,
        "vehicles_url": VEHICLES_URL,
        "schedule_base_url": SCHEDULE_BASE_URL,
        "request_timeout_seconds": 1.0,
        "connect_timeout_seconds": 1.0,
        "max_retries": 2,
        "backoff_base_seconds": 0.001,
        "backoff_max_seconds": 0.01,
        "max_retry_after_seconds": 0.05,
        "upstream_concurrency": 8,
    }
    values.update(overrides)
    return Settings(**values)


def make_upstream(
    settings: Settings,
    fake: FakeSTPT,
    clock: MutableClock,
    *,
    sleep: Callable[[float], Awaitable[None]] = no_sleep,
) -> UpstreamClient:
    import random

    return UpstreamClient(
        settings,
        transport=httpx.MockTransport(fake.handler),
        sleep=sleep,
        rng=random.Random(0),
        wall_clock=clock.wall,
    )


def make_service(
    settings: Settings,
    fake: FakeSTPT,
    clock: MutableClock,
    *,
    network: Any = None,
    sleep: Callable[[float], Awaitable[None]] = no_sleep,
) -> TransitService:
    upstream = make_upstream(settings, fake, clock, sleep=sleep)
    return TransitService(
        settings,
        network=network,
        upstream=upstream,
        clock=clock.monotonic,
        wall_clock=clock.wall,
    )


@asynccontextmanager
async def run_uvicorn(app: Any, *, host: str = "127.0.0.1") -> AsyncIterator[str]:
    """Run an ASGI app on a random loopback port and return its base URL."""
    config = uvicorn.Config(
        app,
        host=host,
        port=0,
        log_level="error",
        lifespan="on",
        access_log=False,
        server_header=False,
    )
    server = uvicorn.Server(config)
    cast(Any, server).install_signal_handlers = lambda: None
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(500):
            if server.started:
                break
            if task.done():
                await task
            await asyncio.sleep(0.01)
        else:  # pragma: no cover - startup failure diagnostic
            raise RuntimeError("uvicorn did not start")

        sockets = server.servers[0].sockets
        port = sockets[0].getsockname()[1]
        yield f"http://{host}:{port}"
    finally:
        server.should_exit = True
        await task


class _FakeUpstreamHandler(BaseHTTPRequestHandler):
    server: _FakeUpstreamHTTPServer

    def do_GET(self) -> None:
        self.server.fixture.dispatch(self)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class _FakeUpstreamHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    fixture: FakeUpstreamHTTPServer


class FakeUpstreamHTTPServer:
    """A real loopback HTTP server for subprocess end-to-end tests."""

    def __init__(self) -> None:
        self.requests: list[str] = []
        self._lock = threading.Lock()
        self._httpd = _FakeUpstreamHTTPServer(("127.0.0.1", 0), _FakeUpstreamHandler)
        self._httpd.fixture = self
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        host, port = self._httpd.server_address[:2]
        assert isinstance(host, str)
        return f"http://{host}:{port}"

    def __enter__(self) -> FakeUpstreamHTTPServer:
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def path_counts(self) -> dict[str, int]:
        with self._lock:
            counts: dict[str, int] = defaultdict(int)
            for path in self.requests:
                counts[urlsplit(path).path] += 1
            return dict(counts)

    def dispatch(self, handler: BaseHTTPRequestHandler) -> None:
        path = handler.path
        with self._lock:
            self.requests.append(path)
        parsed = urlsplit(path)
        route = parsed.path
        query = parse_qs(parsed.query)
        if route == "/proxy-smtt-cache.php":
            stop_id = (query.get("stopid") or [""])[0]
            payload = load_json(f"live_{stop_id}.json") if stop_id in {"85", "60"} else []
            self._send_json(handler, rebase_live_payload(payload))
            return
        if route == "/proxy-alerts.php":
            self._send_json(handler, load_json("alerts.json"))
            return
        if route == "/gtfs-vehicles.php":
            self._send_json(handler, load_json("vehicles.json"))
            return
        fixture = SCHEDULE_PAGES.get(route)
        if fixture is not None:
            self._send(handler, 200, "text/html; charset=utf-8", load_text(f"schedule/{fixture}"))
            return
        self._send(handler, 404, "text/plain; charset=utf-8", b"not found")

    def _send_json(self, handler: BaseHTTPRequestHandler, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(handler, 200, "application/json", body)

    @staticmethod
    def _send(
        handler: BaseHTTPRequestHandler, status: int, content_type: str, body: bytes | str
    ) -> None:
        encoded = body.encode("utf-8") if isinstance(body, str) else body
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(encoded)))
        handler.end_headers()
        handler.wfile.write(encoded)


@dataclass(frozen=True)
class ASGIResponse:
    status: int
    headers: dict[str, str]
    body: bytes


async def asgi_request(
    app: Any,
    *,
    method: str = "GET",
    path: str = "/",
    headers: dict[str, str] | None = None,
    body: bytes = b"",
    query_string: bytes = b"",
) -> ASGIResponse:
    """Call an ASGI app once without a network listener."""
    raw_headers = [
        (name.lower().encode(), value.encode()) for name, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query_string,
        "root_path": "",
        "headers": raw_headers,
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8000),
    }
    request_sent = False

    async def receive() -> dict[str, Any]:
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    messages: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    await app(scope, receive, send)
    status = 500
    response_headers: dict[str, str] = {}
    chunks: list[bytes] = []
    for message in messages:
        if message["type"] == "http.response.start":
            status = int(message["status"])
            response_headers = {
                name.decode().lower(): value.decode() for name, value in message.get("headers", [])
            }
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))
    return ASGIResponse(status=status, headers=response_headers, body=b"".join(chunks))
