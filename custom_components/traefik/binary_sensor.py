"""Binary sensor entities for the Traefik integration."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.util import slugify

from .const import DOMAIN
from .entity import TraefikEntity

if TYPE_CHECKING:
    from .coordinator import TraefikConfigEntry, TraefikCoordinator

_HOST_FROM_RULE = re.compile(r"Host\(`([^`]+)`\)")
_PROVIDER_SUFFIX_RE = re.compile(r"@\w+")  # Phase 1 defensive filter (PITFALLS #2)


def _filter_user_routers(routers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop Traefik-internal `@<provider>` suffixed routers."""
    return [r for r in routers if not _PROVIDER_SUFFIX_RE.search(r.get("name", ""))]


def _friendly_rule(rule: str | None) -> str | None:
    """Extract first `Host(...)` match for the extra_state_attribute hint."""
    if not rule:
        return None
    match = _HOST_FROM_RULE.search(rule)
    return match.group(1) if match else None


async def async_setup_entry(hass, entry: "TraefikConfigEntry", async_add_entities):
    """Set up Traefik router binary sensors for a config entry."""
    coordinator: "TraefikCoordinator" = entry.runtime_data

    routers = _filter_user_routers(coordinator.data.get("routers") or [])
    entities = [
        TraefikRouterBinarySensor(entry, coordinator, router)
        for router in routers
    ]
    async_add_entities(entities)


class TraefikRouterBinarySensor(TraefikEntity, BinarySensorEntity):
    """One binary_sensor per Traefik HTTP router."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(
        self,
        entry: "TraefikConfigEntry",
        coordinator: "TraefikCoordinator",
        router: dict[str, Any],
    ) -> None:
        router_name = router["name"]
        super().__init__(entry, router_name)
        self._router = router
        self._attr_unique_id = f"{entry.entry_id}_http_router_{router_name}"
        # Explicit entity_id prefix per CONTEXT.md D-09/D-10.
        self.entity_id = f"binary_sensor.traefik_http_router_{slugify(router_name)}"
        self._attr_name = router_name

    @property
    def is_on(self) -> bool | None:
        status = self._router.get("status") if isinstance(self._router, dict) else None
        if status is None:
            return None
        return status == "enabled"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "status": self._router.get("status"),
            "rule": self._router.get("rule"),
            "friendly_rule": _friendly_rule(self._router.get("rule")),
            "service": self._router.get("service"),
            "router_name": self._router.get("name"),
        }

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success
