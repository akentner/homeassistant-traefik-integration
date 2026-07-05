"""DataUpdateCoordinator for the Traefik integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import TraefikApiClient, TraefikApiError, TraefikAuthError
from .const import (
    CONF_API_KEY,
    CONF_SCAN_INTERVAL,
    CONF_URL,
    CONF_VERIFY_SSL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_VERIFY_SSL,
)

_LOGGER = logging.getLogger(__name__)

type TraefikData = dict[str, Any]
type TraefikConfigEntry = ConfigEntry["TraefikCoordinator"]


class TraefikCoordinator(DataUpdateCoordinator[TraefikData]):
    """Single polling point for Traefik state."""

    config_entry: TraefikConfigEntry
    client: TraefikApiClient

    def __init__(self, hass: HomeAssistant, entry: TraefikConfigEntry) -> None:
        """Construct coordinator from a config entry's data + options."""
        scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=entry.title or "Traefik",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = TraefikApiClient(
            session=aiohttp_client.async_get_clientsession(hass),
            base_url=entry.data[CONF_URL],
            api_key=entry.data[CONF_API_KEY],
            verify_ssl=entry.options.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
        )

    async def _async_update_data(self) -> TraefikData:
        """Fetch version + routers in parallel; map errors per CONTEXT.md D-15."""
        try:
            return await self.client.fetch_all()
        except TraefikAuthError as err:
            raise ConfigEntryAuthFailed from err
        except TraefikApiError as err:
            raise UpdateFailed(str(err)) from err
