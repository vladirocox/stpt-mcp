"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest

from stpt_mcp.config import Settings
from stpt_mcp.network import NetworkIndex
from stpt_mcp.providers.schedule import Timetable, load_timetable
from stpt_mcp.service import TransitService
from tests.support import (
    FakeSTPT,
    MutableClock,
    ServiceFactory,
    make_service,
    make_test_settings,
    seed_fake_stpt,
)


@pytest.fixture(scope="session")
def network() -> NetworkIndex:
    return NetworkIndex()


@pytest.fixture(scope="session")
def timetable() -> Timetable:
    return load_timetable()


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock()


@pytest.fixture
def fake() -> FakeSTPT:
    return seed_fake_stpt(FakeSTPT())


@pytest.fixture
def settings() -> Settings:
    return make_test_settings()


@pytest.fixture
async def service_factory(
    network: NetworkIndex,
    timetable: Timetable,
    clock: MutableClock,
) -> AsyncIterator[ServiceFactory]:
    created: list[TransitService] = []

    async def factory(
        *,
        settings: Settings | None = None,
        fake: FakeSTPT | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        timetable_override: Timetable | None = None,
    ) -> tuple[TransitService, FakeSTPT]:
        resolved_settings = settings or make_test_settings()
        resolved_fake = fake or seed_fake_stpt(FakeSTPT())
        kwargs: dict[str, Any] = {}
        if sleep is not None:
            kwargs["sleep"] = sleep
        service = make_service(
            resolved_settings,
            resolved_fake,
            clock,
            network=network,
            timetable=timetable_override or timetable,
            **kwargs,
        )
        await service.start()
        created.append(service)
        return service, resolved_fake

    yield factory
    for service in created:
        await service.close()
