"""Base entity for the Traefik integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_NAME, DOMAIN
from .coordinator import TraefikConfigEntry, TraefikCoordinator

if TYPE_CHECKING:
    pass


class TraefikEntity(CoordinatorEntity[TraefikCoordinator]):
    """Common base for all Traefik entities (binary_sensor, sensor, button)."""

    _attr_has_entity_name = True

    def __init__(
        self,
        entry: TraefikConfigEntry,
        router_name: str,
    ) -> None:
        super().__init__(entry.runtime_data)
        self._entry = entry
        self._router_name = router_name

    @property
    def device_info(self) -> DeviceInfo:
        url_host = self._url_host()
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.entry_id}")},
            manufacturer="Traefik",
            model="HTTP Routers",
            name=f"{url_host} {DEFAULT_NAME}" if url_host else DEFAULT_NAME,
            sw_version=self._sw_version(),
            entry_type=None,
        )

    def _url_host(self) -> str | None:
        from urllib.parse import urlparse

        url = self._entry.data.get("url", "")
        try:
            return str(urlparse(url).hostname)
        except Exception:
            return None

    def _sw_version(self) -> str | None:
        data = self.coordinator.data or {}
        version = data.get("version") if isinstance(data, dict) else None
        if isinstance(version, dict):
            version_value = version.get("Version")
            return str(version_value) if version_value is not None else None
        return None
