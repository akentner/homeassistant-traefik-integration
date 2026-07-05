"""Base entity for the Traefik integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_NAME, DOMAIN
from .coordinator import TraefikConfigEntry, TraefikCoordinator

if TYPE_CHECKING:
    pass


# Per-category model labels for the multi-device model (CONTEXT.md D-01).
# Each Traefik category becomes its own HA device; the Traefik integration is
# the manufacturer and the category is the model.
_CATEGORY_TO_MODEL: Final[dict[str, str]] = {
    "http_routers": "HTTP Routers",
    "http_services": "HTTP Services",
    "http_entrypoints": "HTTP Entrypoints",
    "overview": "Overview",
    "diagnostics": "Diagnostics",
}


def _category_to_model(category: str) -> str:
    """Return the human-readable model label for a category.

    Falls back to the raw category string so a typo in a new platform's
    category still produces a usable device model rather than crashing.
    """
    return _CATEGORY_TO_MODEL.get(category, category)


class TraefikEntity(CoordinatorEntity[TraefikCoordinator]):
    """Common base for all Traefik entities (binary_sensor, sensor, button).

    Each entity registers under a per-category HA device — the integration
    identifier is ``(DOMAIN, f"{entry.entry_id}_{category}")``. The legacy
    Phase 1 single-device identifier ``(DOMAIN, entry.entry_id)`` is gone;
    existing HA installations will see a new device row in the registry on
    first restart after this migration (device-registry IDs are opaque so
    this is unavoidable when the shape of the identifier changes). See
    SUMMARY 02-01 for the regression-risk note.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        entry: TraefikConfigEntry,
        category: str,
        *,
        description_key: str | None = None,
    ) -> None:
        super().__init__(entry.runtime_data)
        self._entry = entry
        self._category = category
        # Back-compat alias: the Phase 1 binary_sensor code reads
        # ``self._router_name`` for the router label. New platforms can ignore
        # the attribute — it's a thin alias for ``description_key`` (or "" when
        # the entity is single-instance, e.g. aggregates on the Overview device).
        self._router_name = description_key or ""

    @property
    def device_info(self) -> DeviceInfo:
        url_host = self._url_host()
        model = _category_to_model(self._category)
        name = f"{url_host} Traefik \u2014 {model}" if url_host else f"{DEFAULT_NAME} \u2014 {model}"
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.entry_id}_{self._category}")},
            manufacturer="Traefik",
            model=model,
            name=name,
            sw_version=self._sw_version(),
            entry_type=DeviceEntryType.SERVICE,
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
