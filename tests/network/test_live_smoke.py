"""Opt-in live smoke test against the public STPT endpoints.

Excluded from CI and skipped unless ``STPT_MCP_RUN_NETWORK_TESTS=1`` is set.
"""

from __future__ import annotations

import os

import pytest

from stpt_mcp.config import Settings
from stpt_mcp.models import DataSource, Status
from stpt_mcp.service import TransitService

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.environ.get("STPT_MCP_RUN_NETWORK_TESTS") != "1",
        reason="set STPT_MCP_RUN_NETWORK_TESTS=1 to contact live STPT services",
    ),
]


async def test_live_public_endpoints_smoke() -> None:
    service = TransitService(Settings())
    await service.start()
    try:
        arrivals = await service.get_arrivals("85", limit=5)
        assert arrivals.stop_id == "85"
        assert arrivals.status is not Status.ERROR
        assert arrivals.provenance is not None
        assert arrivals.provenance.source in {DataSource.LIVE, DataSource.SCHEDULE}

        alerts = await service.get_alerts(limit=5)
        assert alerts.status is not Status.ERROR
        assert alerts.provenance is not None

        vehicles = await service.get_vehicles(limit=5)
        assert vehicles.status is not Status.ERROR
        assert vehicles.provenance is not None

        status = await service.get_status()
        assert status.ready is True
        assert status.version
        assert status.network_feed_version
    finally:
        await service.close()
