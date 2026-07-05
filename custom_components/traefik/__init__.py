"""The Traefik integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .coordinator import TraefikConfigEntry, TraefikCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: TraefikConfigEntry
) -> bool:
    """Set up Traefik from a config entry."""
    coordinator = TraefikCoordinator(hass, entry)
    # first_refresh raises ConfigEntryNotReady on transient failure, or
    # ConfigEntryAuthFailed on 401/403 (auto-retried by HA on NotReady).
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.debug(
        "Traefik integration ready: entry_id=%s, scan_interval=%ss",
        entry.entry_id,
        coordinator.update_interval.total_seconds(),
    )
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: TraefikConfigEntry
) -> bool:
    """Unload a Traefik config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
