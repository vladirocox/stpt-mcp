"""STPT service-alert provider."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from stpt_mcp.config import Settings
from stpt_mcp.errors import ErrorCode, StptError
from stpt_mcp.network import NetworkIndex
from stpt_mcp.providers.base import AlertRecord, ProviderResult, parse_epoch
from stpt_mcp.upstream import UpstreamClient


class AlertsProvider:
    def __init__(self, client: UpstreamClient, settings: Settings, network: NetworkIndex) -> None:
        self._client = client
        self._settings = settings
        self._network = network

    async def fetch(self) -> ProviderResult[tuple[AlertRecord, ...]]:
        raw, fetched_at = await self._client.get_json(
            self._settings.alerts_url,
            source="alerts",
            headers={"Referer": "https://live.stpt.ro/"},
        )
        return parse_alerts(raw, fetched_at=fetched_at, network=self._network)


def parse_alerts(
    raw: Any,
    *,
    fetched_at: datetime,
    network: NetworkIndex,
) -> ProviderResult[tuple[AlertRecord, ...]]:
    if isinstance(raw, dict):
        raw = raw.get("alerts", raw.get("entity"))
    if not isinstance(raw, list):
        raise StptError(
            ErrorCode.UPSTREAM_INVALID_RESPONSE,
            "alerts response does not contain an alert list",
            retryable=True,
            upstream="alerts",
        )

    alerts: list[AlertRecord] = []
    warnings: list[str] = []
    skipped = 0
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            skipped += 1
            continue
        nested = item.get("alert")
        alert = nested if isinstance(nested, dict) else item
        try:
            record, record_warnings = _parse_alert(alert, index, network)
        except (TypeError, ValueError):
            skipped += 1
            continue
        if record is None:
            skipped += 1
            continue
        alerts.append(record)
        warnings.extend(record_warnings)
    if skipped:
        warnings.append(f"Ignored {skipped} malformed alert record(s).")
    alerts.sort(key=lambda alert: alert.alert_id)
    return ProviderResult(
        value=tuple(alerts),
        fetched_at=fetched_at or datetime.now(UTC),
        warnings=tuple(warnings[:20]),
        complete=not skipped,
    )


def _parse_alert(
    alert: dict[str, Any], index: int, network: NetworkIndex
) -> tuple[AlertRecord | None, tuple[str, ...]]:
    title = _best_text(
        alert.get("headerText"),
        alert.get("header_text"),
        alert.get("header"),
        alert.get("title"),
    )
    if not title:
        return None, ()
    description = _best_text(
        alert.get("descriptionText"),
        alert.get("description_text"),
        alert.get("description"),
        alert.get("text"),
    )
    periods, period_warnings = _periods(alert.get("activePeriod") or alert.get("active_period"))
    routes: list[str] = []
    stop_ids: list[str] = []
    for entity in _flatten_entities(alert.get("informedEntity") or alert.get("informed_entity")):
        if not isinstance(entity, dict):
            continue
        raw_route = entity.get("routeId") or entity.get("route_id")
        if raw_route:
            route = _normalize_route_id(str(raw_route), network)
            if route and route not in routes:
                routes.append(route)
        raw_stop = entity.get("stopId") or entity.get("stop_id")
        if raw_stop:
            stop_id = str(raw_stop).strip()
            if stop_id and stop_id not in stop_ids:
                stop_ids.append(stop_id)
    alert_id = str(alert.get("id") or f"alert-{index + 1}").strip()
    warnings = list(period_warnings)
    if not periods:
        warnings.append(f"Alert {alert_id} has no parseable active period; treating it as active.")
    return (
        AlertRecord(
            alert_id=alert_id,
            title=title,
            description=description,
            cause=_optional_upper(alert.get("cause")),
            effect=_optional_upper(alert.get("effect")),
            periods=tuple(periods),
            routes=tuple(routes),
            stop_ids=tuple(stop_ids),
        ),
        tuple(warnings),
    )


def _best_text(*values: object) -> str:
    for value in values:
        text = _extract_text(value)
        if text:
            return text
    return ""


def _extract_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        translations = value.get("translation") or value.get("translations")
        if isinstance(translations, list):
            for translation in translations:
                if isinstance(translation, dict):
                    text = translation.get("text") or translation.get("value")
                    if isinstance(text, str) and text.strip():
                        return text.strip()
        text = value.get("text") or value.get("value")
        if isinstance(text, str):
            return text.strip()
    return ""


def _periods(
    value: object,
) -> tuple[list[tuple[datetime | None, datetime | None]], tuple[str, ...]]:
    if value is None:
        return [], ()
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return [], ()
    periods: list[tuple[datetime | None, datetime | None]] = []
    warnings: list[str] = []
    for entry in value:
        if not isinstance(entry, dict):
            warnings.append("Ignored a malformed alert active period.")
            continue
        start = parse_epoch(entry.get("start"))
        end = parse_epoch(entry.get("end"))
        if start is None and end is None:
            warnings.append("Ignored an alert active period with no valid timestamps.")
            continue
        periods.append((start, end))
    return periods, tuple(warnings)


def _flatten_entities(value: object) -> list[object]:
    if not isinstance(value, list):
        return []
    flattened: list[object] = []
    for entry in value:
        if isinstance(entry, list):
            flattened.extend(_flatten_entities(entry))
        else:
            flattened.append(entry)
    return flattened


def _normalize_route_id(raw: str, network: NetworkIndex) -> str:
    code = raw.strip()
    if code.startswith("R_"):
        code = code.rsplit("_", 1)[-1]
    route = network.get_route(code)
    return route.line if route is not None else code


def _optional_upper(value: object) -> str | None:
    text = str(value).strip().upper() if value is not None else ""
    return text or None
