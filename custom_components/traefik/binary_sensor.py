"""Binary sensor entities for the Traefik integration."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.util import slugify

from .api import filter_internal_items
from .entity import TraefikEntity

if TYPE_CHECKING:
    from .coordinator import TraefikConfigEntry, TraefikCoordinator

_HOST_FROM_RULE = re.compile(r"Host\(`([^`]+)`\)")


def _friendly_rule(rule: str | None) -> str | None:
    """Extract first `Host(...)` match for the extra_state_attribute hint."""
    if not rule:
        return None
    match = _HOST_FROM_RULE.search(rule)
    return match.group(1) if match else None


async def async_setup_entry(
    hass: Any,
    entry: TraefikConfigEntry,
    async_add_entities: Any,
) -> None:
    """Set up Traefik binary sensors for a config entry.

    Creates one ``TraefikRouterBinarySensor`` per user-visible Traefik HTTP
    router (CONTEXT.md D-06 / Phase 1 ROUTER-01) PLUS one
    ``TraefikAnyRouterFailingBinarySensor`` aggregate on the Diagnostics
    device (CONTEXT.md D-14/D-19). The aggregate is a single instance per
    config entry — never deleted; if all routers disappear the sensor falls
    to OFF (no routers failing) and stays.
    """
    coordinator: TraefikCoordinator = entry.runtime_data

    # Phase 2: read `http_routers` (was `routers` in Phase 1 — fetch_all now
    # returns the renamed key per CONTEXT.md D-04). filter_internal_items is
    # the canonical helper from api.py (replaces _filter_user_routers).
    routers = filter_internal_items(coordinator.data.get("http_routers") or [])
    router_entities = [TraefikRouterBinarySensor(entry, coordinator, router) for router in routers]
    any_failing_entity = TraefikAnyRouterFailingBinarySensor(entry, coordinator)
    async_add_entities([*router_entities, any_failing_entity])


class TraefikRouterBinarySensor(TraefikEntity, BinarySensorEntity):
    """One binary_sensor per Traefik HTTP router."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(
        self,
        entry: TraefikConfigEntry,
        coordinator: TraefikCoordinator,
        router: dict[str, Any],
    ) -> None:
        router_name = router["name"]
        # Phase 2: per-category device (CONTEXT.md D-01/D-02). The HTTP Routers
        # device identifier is (DOMAIN, f"{entry.entry_id}_http_routers").
        super().__init__(entry, category="http_routers", description_key=router_name)
        self._router = router
        self._attr_unique_id = f"{entry.entry_id}_http_router_{router_name}"
        # Explicit entity_id prefix per CONTEXT.md D-09/D-10.
        self.entity_id = f"binary_sensor.traefik_http_router_{slugify(router_name)}"
        self._attr_name = router_name

    @property
    def is_on(self) -> bool | None:
        status: Any = self._router.get("status") if isinstance(self._router, dict) else None
        if status is None:
            return None
        return bool(status == "enabled")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "status": self._router.get("status"),
            "rule": self._router.get("rule"),
            "friendly_rule": _friendly_rule(self._router.get("rule")),
            "service": self._router.get("service"),
            # ``name`` is the raw Traefik router identifier (CONTEXT.md D-20 /
            # ROUTER-02). Useful on dashboards even when the entity_id slug
            # mangles special characters.
            "name": self._router.get("name"),
            "router_name": self._router.get("name"),
        }

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success


class TraefikAnyRouterFailingBinarySensor(TraefikEntity, BinarySensorEntity):
    """Aggregates router health: ON when ANY router status != 'enabled'.

    Single instance per config entry — never deleted (CONTEXT.md D-19).
    Lives on the Diagnostics device alongside the reload button
    (CONTEXT.md D-14). ``entity_registry_enabled_default=False`` per
    PITFALLS M-12 so the diagnostic entity does not pollute the States
    panel by default — users opt in consciously when they want the
    "any router failing" alarm surfaced.

    Per CONTEXT.md D-14 the device class is ``PROBLEM`` so the UI shows the
    standard problem icon and groups the entity with HA's other health
    alarms. ``is_on`` is ``True`` when at least one router is anything other
    than ``enabled`` (``disabled``, ``warning``, ``error`` — matches the
    semantics used by ``TraefikRouterBinarySensor.is_on``).

    Reads the raw ``http_routers`` list (NOT ``filter_internal_items``-ed)
    so a failing Traefik-internal router like ``api@internal`` can also
    surface the alarm — internal routers are filtered from per-router
    entities (entity-id regex rejects ``@``) but the aggregate is
    internally a normal HA entity and can hold any name.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        entry: TraefikConfigEntry,
        coordinator: TraefikCoordinator,
    ) -> None:
        super().__init__(entry, category="diagnostics", description_key="any_router_failing")
        self._attr_unique_id = f"{entry.entry_id}_diagnostics_any_router_failing"
        self.entity_id = "binary_sensor.traefik_any_router_failing"
        self._attr_name = "Any router failing"

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if not isinstance(data, dict):
            return None
        routers = data.get("http_routers")
        if not isinstance(routers, list):
            # Transient gap in coordinator data — return None so HA shows
            # the entity as "unknown" rather than flipping to OFF and
            # potentially masking a real failure.
            return None
        failing = [r for r in routers if isinstance(r, dict) and r.get("status") != "enabled"]
        return bool(failing)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        routers = data.get("http_routers") if isinstance(data, dict) else None
        if not isinstance(routers, list):
            return {"failing_router_count": 0, "failing_router_names": []}
        failing = [r for r in routers if isinstance(r, dict) and r.get("status") != "enabled"]
        return {
            "failing_router_count": len(failing),
            "failing_router_names": [r.get("name") for r in failing if isinstance(r, dict)],
        }

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success
