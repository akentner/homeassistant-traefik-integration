"""The Traefik integration."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant

from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    PLATFORMS,
)
from .const import (
    DOMAIN as DOMAIN,
)
from .coordinator import TraefikConfigEntry, TraefikCoordinator

_LOGGER = logging.getLogger(__name__)


async def _async_options_updated(hass: HomeAssistant, entry: TraefikConfigEntry) -> None:
    """Apply Options changes to the running coordinator (CONTEXT.md D-08).

    Options Flow writes the new scan_interval / verify_ssl / tls_warn_days
    into ``entry.options`` and HA fires this listener. We mutate the
    coordinator's ``update_interval`` directly so a scan-interval change
    takes effect on the next scheduled cycle without a full reload. URL
    changes (reconfigure flow) come through ``entry.data`` instead of
    ``entry.options`` — HA's standard entry-data-change reload handles those
    by re-running ``async_setup_entry`` with the new URL, which rebuilds
    the API client + coordinator from scratch.
    """
    coordinator: TraefikCoordinator = entry.runtime_data
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    coordinator.update_interval = timedelta(seconds=scan_interval)
    _LOGGER.debug(
        "Traefik options updated: entry_id=%s scan_interval=%ss",
        entry.entry_id,
        scan_interval,
    )


async def async_setup_entry(hass: HomeAssistant, entry: TraefikConfigEntry) -> bool:
    """Set up Traefik from a config entry."""
    coordinator = TraefikCoordinator(hass, entry)
    # first_refresh raises ConfigEntryNotReady on transient failure, or
    # ConfigEntryAuthFailed on 401/403 (auto-retried by HA on NotReady, or
    # surfaced to the reauth flow on AuthFailed).
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Bind the Options-update listener. HA fires it whenever entry.options
    # (Options Flow submit) OR entry.data (reconfigure flow / reauth flow)
    # changes. The listener mutates coordinator.update_interval live; HA's
    # standard data-change handling takes care of full reloads for URL
    # changes (see async_setup_entry being re-invoked).
    entry.add_update_listener(_async_options_updated)

    _LOGGER.debug(
        "Traefik integration ready: entry_id=%s, scan_interval=%ss",
        entry.entry_id,
        coordinator.update_interval.total_seconds() if coordinator.update_interval else 0.0,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TraefikConfigEntry) -> bool:
    """Unload a Traefik config entry.

    HA removes the update listener automatically as part of entry unload,
    so we do not need explicit teardown here.
    """
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
