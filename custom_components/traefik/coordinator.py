"""DataUpdateCoordinator for the Traefik integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, TypedDict

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


class TraefikData(TypedDict, total=False):
    """Shape of the coordinator's `data` attribute (Phase 2, CONTEXT.md D-04).

    All keys are optional at runtime (DataUpdateCoordinator stores a dict via
    its generic). ``total=False`` mirrors the Phase 1 behavior where any
    individual key may be missing transiently. Partial-failure policy lives
    in ``TraefikApiClient.fetch_all`` (CONTEXT.md D-07): on a non-auth error,
    the entire payload is dropped so callers see a stale cycle rather than
    mixed fresh+stale data.

    NOTE: Phase 1 used a PEP-695 ``type TraefikData = dict[str, Any]`` alias.
    The class form (TypedDict) is the canonical Phase 2 type — every
    ``from .coordinator import TraefikData`` continues to resolve to a valid
    type identifier (the TypedDict class itself). The PLAN.md 02-01 snippet
    ``type TraefikData = TraefikData`` is a self-referential alias that ruff
    + mypy both flag as a no-op redefinition; we keep the class form alone
    and surface this in SUMMARY 02-01 as a deviation.
    """

    version: dict[str, Any]
    entrypoints: list[dict[str, Any]]
    http_routers: list[dict[str, Any]]
    http_services: list[dict[str, Any]]
    http_middlewares: list[dict[str, Any]]
    overview: dict[str, Any]


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
        """Fetch the six Traefik endpoints in parallel; map errors per CONTEXT.md D-07.

        Partial-failure policy is enforced inside ``TraefikApiClient.fetch_all``
        (CONTEXT.md D-07): any non-auth error drops the entire payload so
        callers see a stale cycle rather than mixed fresh+stale data. Auth
        errors propagate unchanged so this method only needs to translate
        them to ``ConfigEntryAuthFailed``.
        """
        try:
            result = await self.client.fetch_all()
        except TraefikAuthError as err:
            raise ConfigEntryAuthFailed from err
        except TraefikApiError as err:
            raise UpdateFailed(str(err)) from err
        # ``fetch_all`` returns a plain dict that matches the TypedDict shape;
        # cast at the boundary so the static type stays TraefikData (TypedDict
        # instances ARE dicts at runtime).
        return result  # type: ignore[return-value]
