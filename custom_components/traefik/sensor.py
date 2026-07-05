"""Sensor entities for the Traefik integration.

Phase 2 plan 02-03 fills this out with `TraefikEntrypointSensor`,
`TraefikServiceSensor`, and the three aggregate count sensors on the Overview
device (CONTEXT.md D-15/D-16/D-17).

Entity count per config entry (after `async_setup_entry` returns):
- 1 per `TraefikEntrypointSensor` per Traefik entrypoint (HTTP Entrypoints device)
- 1 per `TraefikServiceSensor` per Traefik service (HTTP Services device;
  provider internal `api@internal` is filtered out via `filter_internal_items`)
- 1 each of `TraefikRoutersCountSensor`, `TraefikServicesCountSensor`,
  `TraefikMiddlewaresCountSensor` on the Overview device
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from .api import filter_internal_items
from .entity import TraefikEntity

if TYPE_CHECKING:
    from .coordinator import TraefikConfigEntry, TraefikCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: Any,
    entry: TraefikConfigEntry,
    async_add_entities: Any,
) -> None:
    """Set up Traefik sensor entities for a config entry.

    Mirrors the `binary_sensor.py` pattern (Phase 1 — coordinator from
    `entry.runtime_data`, single `async_add_entities(...)` call with all
    entities for this platform). Coordinator cycle may surface partial data
    (any key missing -> empty list) so we defensively coerce via the
    ``_dict_or_empty`` / ``_list_or_empty`` helpers below.
    """
    coordinator: TraefikCoordinator = entry.runtime_data
    data = _dict_or_empty(coordinator.data)

    # Per-entrypoint sensors (CONTEXT.md D-15).
    # Entrypoints are NOT filtered via filter_internal_items — Traefik entrypoint
    # names like `websecure@internal` are first-class configuration objects the
    # user expects to see.
    entrypoints: list[dict[str, Any]] = _list_or_empty(data.get("entrypoints"))
    entrypoint_entities = [TraefikEntrypointSensor(entry, coordinator, ep) for ep in entrypoints]

    # Per-service sensors (CONTEXT.md D-16). Filter `@<provider>` internal items
    # via the canonical helper in api.py (CONTEXT.md D-06).
    services = filter_internal_items(_list_or_empty(data.get("http_services")))
    service_entities = [TraefikServiceSensor(entry, coordinator, svc) for svc in services]

    # Three aggregate sensors (CONTEXT.md D-17). Counts derive from the
    # *filtered* lists (so `@<provider>` internals never inflate the totals)
    # while TCP/UDP breakdowns come from `/api/overview` for visibility.
    overview = _dict_or_empty(data.get("overview"))
    http_overview = _dict_or_empty(overview.get("http"))
    tcp_overview = _dict_or_empty(overview.get("tcp"))
    udp_overview = _dict_or_empty(overview.get("udp"))

    routers_filtered = filter_internal_items(_list_or_empty(data.get("http_routers")))
    middlewares_filtered = filter_internal_items(_list_or_empty(data.get("http_middlewares")))

    aggregate_entities = [
        TraefikRoutersCountSensor(
            entry,
            coordinator,
            filtered_count=len(routers_filtered),
            http_count=_safe_int(http_overview.get("routers")),
            tcp_count=_safe_int(tcp_overview.get("routers")),
            udp_count=_safe_int(udp_overview.get("routers")),
        ),
        TraefikServicesCountSensor(
            entry,
            coordinator,
            filtered_count=len(services),
            http_count=_safe_int(http_overview.get("services")),
            tcp_count=_safe_int(tcp_overview.get("services")),
            udp_count=_safe_int(udp_overview.get("services")),
        ),
        TraefikMiddlewaresCountSensor(
            entry,
            coordinator,
            filtered_count=len(middlewares_filtered),
        ),
    ]

    async_add_entities(entrypoint_entities + service_entities + aggregate_entities)

    # Stale entity cleanup (CONTEXT.md D-18, gatus binary_sensor.py:49-71).
    # Entrypoints + services that disappear from coordinator.data are removed
    # from the entity registry on the next refresh cycle. Aggregate counters
    # on the Overview device (CONTEXT.md D-19) are NEVER deleted — their
    # unique_id prefix is ``_overview_`` and is skipped below.
    registry = er.async_get(hass)

    def _remove_stale_entrypoints() -> None:
        if not coordinator.last_update_success:
            return
        current: set[str] = set()
        data = coordinator.data if isinstance(coordinator.data, dict) else {}
        eps = data.get("entrypoints") if isinstance(data, dict) else None
        if isinstance(eps, list):
            current = {ep["name"] for ep in eps if isinstance(ep, dict) and "name" in ep}
        prefix = f"{entry.entry_id}_http_entrypoint_"
        for reg_entry in list(registry.entities.values()):
            unique_id = reg_entry.unique_id or ""
            if not unique_id.startswith(prefix):
                continue
            ep_name = unique_id.removeprefix(prefix)
            if ep_name and ep_name not in current:
                _LOGGER.debug("Removing stale entrypoint entity: %s", reg_entry.entity_id)
                registry.async_remove(reg_entry.entity_id)

    def _remove_stale_services() -> None:
        if not coordinator.last_update_success:
            return
        current: set[str] = set()
        data = coordinator.data if isinstance(coordinator.data, dict) else {}
        svcs = data.get("http_services") if isinstance(data, dict) else None
        if isinstance(svcs, list):
            current = {
                s["name"]
                for s in filter_internal_items(
                    [item for item in svcs if isinstance(item, dict)]
                )
            }
        prefix = f"{entry.entry_id}_http_service_"
        for reg_entry in list(registry.entities.values()):
            unique_id = reg_entry.unique_id or ""
            if not unique_id.startswith(prefix):
                continue
            svc_name = unique_id.removeprefix(prefix)
            if svc_name and svc_name not in current:
                _LOGGER.debug("Removing stale service entity: %s", reg_entry.entity_id)
                registry.async_remove(reg_entry.entity_id)

    entry.async_on_unload(coordinator.async_add_listener(_remove_stale_entrypoints))
    entry.async_on_unload(coordinator.async_add_listener(_remove_stale_services))


def _dict_or_empty(value: Any) -> dict[str, Any]:
    """Coerce an arbitrary value to ``dict[str, Any]`` (``{}`` on absence/bad type).

    Used to consume ``TraefikData`` TypedDict (total=False) keys safely
    under ``mypy --strict`` — every key is ``None`` at the type level, so
    chaining ``.get(...)`` on the raw value triggers ``union-attr`` errors.
    """
    return value if isinstance(value, dict) else {}


def _list_or_empty(value: Any) -> list[dict[str, Any]]:
    """Coerce an arbitrary value to ``list[dict[str, Any]]`` (``[]`` on absence/bad type).

    Mirrors ``_dict_or_empty`` for list-shaped TypedDict keys.
    """
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _safe_int(value: Any) -> int:
    """Coerce a possibly-missing overview counter to int (0 on absence/bad type)."""
    if isinstance(value, bool):
        # bool is a subclass of int — treat as 0 to avoid True → 1 in counts.
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    return 0


class TraefikEntrypointSensor(TraefikEntity, SensorEntity):
    """One sensor per Traefik HTTP entrypoint (CONTEXT.md D-15).

    State = ``entrypoint["address"]`` (e.g., ``":443"``); attributes expose the
    raw ``name`` and ``transport`` so dashboards can group / colour by protocol
    without re-fetching from Traefik. Entrypoints are config-time constructs
    in Traefik's API — runtime request counts live in ``/api/overview`` or
    ``/metrics`` (deferred to v2 per CONTEXT.md deferred section).
    """

    def __init__(
        self,
        entry: TraefikConfigEntry,
        coordinator: TraefikCoordinator,
        entrypoint: dict[str, Any],
    ) -> None:
        entrypoint_name = entrypoint.get("name") or "unknown"
        super().__init__(entry, category="http_entrypoints", description_key=entrypoint_name)
        self._entrypoint = entrypoint
        self._attr_unique_id = f"{entry.entry_id}_http_entrypoint_{entrypoint_name}"
        self.entity_id = f"sensor.traefik_http_entrypoint_{slugify(entrypoint_name)}"
        self._attr_name = entrypoint_name

    @property
    def native_value(self) -> str:
        """Return the listening address (e.g., ``":443"``).

        Kept raw including the leading ``:`` — this matches Traefik's own
        display format and avoids ambiguity when the bind host is included
        (``"0.0.0.0:443"`` vs ``":443"``).
        """
        address = self._entrypoint.get("address") if isinstance(self._entrypoint, dict) else None
        return str(address) if address is not None else "unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ep = self._entrypoint if isinstance(self._entrypoint, dict) else {}
        return {
            "name": ep.get("name"),
            "address": ep.get("address"),
            "transport": ep.get("transport"),
            # ``entrypoint_name`` mirrors ``name`` for consistency with the
            # ``router_name`` alias on TraefikRouterBinarySensor (Phase 1
            # ROUTER-02 contract pin).
            "entrypoint_name": ep.get("name"),
        }

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success


class TraefikServiceSensor(TraefikEntity, SensorEntity):
    """One sensor per Traefik HTTP service (CONTEXT.md D-16).

    State = ``loadbalancer.status`` when present, else ``service.status``.
    Attributes expose the ``type``, the ``server_count`` from
    ``loadbalancer.servers[]`` (omitted when no loadbalancer is configured —
    e.g., redirect services), and the raw servers list so dashboards can
    surface backend health.
    """

    def __init__(
        self,
        entry: TraefikConfigEntry,
        coordinator: TraefikCoordinator,
        service: dict[str, Any],
    ) -> None:
        service_name = service.get("name") or "unknown"
        super().__init__(entry, category="http_services", description_key=service_name)
        self._service = service
        self._attr_unique_id = f"{entry.entry_id}_http_service_{service_name}"
        self.entity_id = f"sensor.traefik_http_service_{slugify(service_name)}"
        self._attr_name = service_name

    @property
    def native_value(self) -> str:
        """Return ``loadbalancer.status`` (preferred), else ``service.status``."""
        svc = self._service if isinstance(self._service, dict) else {}
        loadbalancer = svc.get("loadbalancer") if isinstance(svc.get("loadbalancer"), dict) else None
        if loadbalancer is not None:
            status = loadbalancer.get("status")
            if status is not None:
                return str(status)
        # Fall back to the service-level status (Traefik also exposes this).
        status = svc.get("status")
        return str(status) if status is not None else "unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        svc = self._service if isinstance(self._service, dict) else {}
        loadbalancer = svc.get("loadbalancer") if isinstance(svc.get("loadbalancer"), dict) else None
        if loadbalancer is not None:
            servers = loadbalancer.get("servers")
            server_count = len(servers) if isinstance(servers, list) else 0
        else:
            servers = None
            server_count = 0
        return {
            "name": svc.get("name"),
            "status": svc.get("status"),
            "type": svc.get("type"),
            "server_count": server_count,
            # ``servers`` is the raw backend list (URL + optional healthcheck
            # status when healthcheck is configured). Absent when no
            # loadbalancer is configured (e.g., redirect services).
            "servers": servers,
            "service_name": svc.get("name"),
        }

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success


class _TraefikAggregateCountSensor(TraefikEntity, SensorEntity):
    """Shared base for the three Overview device aggregate counters.

    Each instance reports a ``filtered_count`` (the primary state — what
    the user actually sees) plus a breakdown across HTTP/TCP/UDP from
    ``/api/overview`` for visibility (CONTEXT.md D-17 + UX-04).

    ``state_class=MEASUREMENT`` so HA can graph trends over time — users
    may want to chart how many routers / services / middlewares are
    configured across deploys.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        entry: TraefikConfigEntry,
        coordinator: TraefikCoordinator,
        *,
        description_key: str,
        filtered_count: int,
        unique_id: str,
        entity_id: str,
        name: str,
    ) -> None:
        super().__init__(entry, category="overview", description_key=description_key)
        self._attr_unique_id = unique_id
        self.entity_id = entity_id
        self._attr_name = name
        # Computed once at setup; the value does not change inside the
        # constructor. Updates flow via ``async_add_entities`` re-instantiation
        # when the platform reloads (the platform currently doesn't, but the
        # count is recomputed every setup cycle).
        self._attr_native_value = int(filtered_count)

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success


class TraefikRoutersCountSensor(_TraefikAggregateCountSensor):
    """Total Traefik routers — filtered count (CONTEXT.md D-17).

    State = ``len(filter_internal_items(coordinator.data["http_routers"]))``;
    attributes break down by transport (HTTP / TCP / UDP) from ``/api/overview``.
    """

    def __init__(
        self,
        entry: TraefikConfigEntry,
        coordinator: TraefikCoordinator,
        *,
        filtered_count: int,
        http_count: int = 0,
        tcp_count: int = 0,
        udp_count: int = 0,
    ) -> None:
        super().__init__(
            entry,
            coordinator,
            description_key="routers_count",
            filtered_count=filtered_count,
            unique_id=f"{entry.entry_id}_overview_routers_count",
            entity_id="sensor.traefik_routers",
            name="Routers",
        )
        self._http_count = http_count
        self._tcp_count = tcp_count
        self._udp_count = udp_count

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "filtered_count": self._attr_native_value,
            "http_count": self._http_count,
            "tcp_count": self._tcp_count,
            "udp_count": self._udp_count,
        }


class TraefikServicesCountSensor(_TraefikAggregateCountSensor):
    """Total Traefik services — filtered count (CONTEXT.md D-17)."""

    def __init__(
        self,
        entry: TraefikConfigEntry,
        coordinator: TraefikCoordinator,
        *,
        filtered_count: int,
        http_count: int = 0,
        tcp_count: int = 0,
        udp_count: int = 0,
    ) -> None:
        super().__init__(
            entry,
            coordinator,
            description_key="services_count",
            filtered_count=filtered_count,
            unique_id=f"{entry.entry_id}_overview_services_count",
            entity_id="sensor.traefik_services",
            name="Services",
        )
        self._http_count = http_count
        self._tcp_count = tcp_count
        self._udp_count = udp_count

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "filtered_count": self._attr_native_value,
            "http_count": self._http_count,
            "tcp_count": self._tcp_count,
            "udp_count": self._udp_count,
        }


class TraefikMiddlewaresCountSensor(_TraefikAggregateCountSensor):
    """Total Traefik middlewares — filtered count (CONTEXT.md D-17).

    Middlewares are HTTP-only per Traefik's API surface (no TCP/UDP
    middleware concept), so the breakdown is just the filtered count.
    """

    def __init__(
        self,
        entry: TraefikConfigEntry,
        coordinator: TraefikCoordinator,
        *,
        filtered_count: int,
    ) -> None:
        super().__init__(
            entry,
            coordinator,
            description_key="middlewares_count",
            filtered_count=filtered_count,
            unique_id=f"{entry.entry_id}_overview_middlewares_count",
            entity_id="sensor.traefik_middlewares",
            name="Middlewares",
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"filtered_count": self._attr_native_value}
