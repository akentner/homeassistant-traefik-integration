"""Button entities for the Traefik integration.

Phase 2 plan 02-03 fills this out with `TraefikProxyReloadButton` on the Diagnostics
device (CONTEXT.md D-13). The button fires the same handler as the
`traefik.reload_routers` service registered in `__init__.py` (plan 02-04) —
going through the service (not `client.reload_routers()` directly) means HA's
trace log captures the response dict, and the verification loop is shared
between the service call and the button press.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .entity import TraefikProxyEntity

if TYPE_CHECKING:
    from .coordinator import TraefikProxyConfigEntry, TraefikProxyCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TraefikProxyConfigEntry,
    async_add_entities: Any,
) -> None:
    """Set up Traefik button entities for a config entry.

    Always creates exactly one `TraefikProxyReloadButton` (single instance per
    config entry — never deleted, lives on the Diagnostics device per
    CONTEXT.md D-14/D-19).
    """
    coordinator: TraefikProxyCoordinator = entry.runtime_data
    async_add_entities([TraefikProxyReloadButton(hass, entry, coordinator)])


class TraefikProxyReloadButton(TraefikProxyEntity, ButtonEntity):
    """Button that triggers `traefik.reload_routers` (CONTEXT.md D-13/D-14).

    Lives on the Diagnostics device. Press action invokes the HA service
    ``traefik.reload_routers`` (registered in `__init__.py`'s module-level
    `async_setup`, plan 02-04); the handler's return dict
    ``{verified, elapsed_ms, attempts, name_diff}`` surfaces in the HA trace
    log automatically — no extra attributes on this entity (CONTEXT.md D-13
    explicit "exposes nothing extra").

    `device_class=RESTART` so the UI surfaces the standard restart icon and
    groups the button with other HA restart-class controls.
    """

    _attr_device_class = ButtonDeviceClass.RESTART

    def __init__(
        self,
        hass: HomeAssistant,
        entry: TraefikProxyConfigEntry,
        coordinator: TraefikProxyCoordinator,
    ) -> None:
        super().__init__(entry, category="diagnostics", description_key="reload")
        self._hass = hass
        self._attr_unique_id = f"{entry.entry_id}_diagnostics_reload"
        self.entity_id = "button.traefik_reload"
        self._attr_name = "Reload"

    async def async_press(self) -> None:
        """Invoke the `traefik.reload_routers` service.

        The actual handler is registered in `__init__.py`'s module-level
        `async_setup` (NOT here) — this entity just dispatches. The handler
        returns ``{verified, elapsed_ms, attempts, name_diff}``; the response
        surfaces in the HA trace log automatically. ``blocking=True`` so the
        press action awaits the verification loop rather than firing and
        forgetting (keeps button press latency visible to the user).
        """
        await self._hass.services.async_call(DOMAIN, "reload_routers", blocking=True)
