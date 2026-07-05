"""The Traefik integration."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import TraefikConfigEntry, TraefikCoordinator

_LOGGER = logging.getLogger(__name__)


# CONTEXT.md D-12: reload_routers takes no parameters — the service is
# fire-and-forget with a structured return dict. ``vol.Schema({})`` declares an
# explicit empty schema so HA's service UI shows "no fields" rather than
# prompting for anything.
RELOAD_ROUTERS_SCHEMA = vol.Schema({})


async def _async_handle_reload_routers(call: ServiceCall) -> dict[str, Any]:
    """Reload Traefik dynamic config and verify completion via router-set polling.

    CONTEXT.md D-12 / PITFALLS #15: Traefik returns 200/202 from
    ``/api/http/routers/refresh`` BEFORE providers finish reloading, so we
    capture the pre-POST router-name set, POST the refresh, then poll
    ``coordinator.data['http_routers']`` with exponential backoff
    (``200ms -> 5s``, max 10 attempts, <= 5s budget) and exit early when the
    name set changes from the snapshot.

    Returns: ``{verified: bool, elapsed_ms: int, attempts: int, name_diff:
    {"added": [...], "removed": [...]}}``.

    Non-2xx POSTs raise ``TraefikApiError`` -> HA surfaces the service-call
    failure to the caller. When no Traefik config entry is loaded (shouldn't
    happen for a sane HA install), we raise ``HomeAssistantError`` so HA's
    service UI shows a clear error rather than silently no-op'ing.
    """
    hass = call.hass
    t0 = time.monotonic()

    # Find the first loaded Traefik coordinator. The integration is `service`
    # type (one Traefik per config entry), so there should be exactly one
    # loaded entry in normal use.
    coordinators = [
        entry.runtime_data
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]
    if not coordinators:
        raise HomeAssistantError("No loaded Traefik config entry to reload against")
    coordinator: TraefikCoordinator = coordinators[0]

    def _current_router_names() -> set[str]:
        """Snapshot of router names from the latest coordinator cycle."""
        data = coordinator.data if isinstance(coordinator.data, dict) else {}
        routers = data.get("http_routers") if isinstance(data, dict) else None
        if not isinstance(routers, list):
            return set()
        return {r["name"] for r in routers if isinstance(r, dict) and "name" in r}

    before = _current_router_names()

    # Trigger the refresh. Raises TraefikApiError -> HA surfaces as
    # service-call failure (the caller sees the error in the trace log).
    await coordinator.client.reload_routers()

    # Poll the coordinator for an observable change. ``async_request_refresh``
    # is itself an awaitable that returns once the refresh cycle completes
    # (it internally calls ``_debounced_refresh.async_call()``), so awaiting
    # it gives us a clean "refresh finished, check the data" handoff.
    attempts = 0
    verified = False
    backoff_ms = 200
    max_attempts = 10
    max_budget_ms = 5000
    current = before
    while attempts < max_attempts and (time.monotonic() - t0) * 1000 < max_budget_ms:
        attempts += 1
        await coordinator.async_request_refresh()
        current = _current_router_names()
        if current != before:
            verified = True
            break
        await asyncio.sleep(backoff_ms / 1000)
        backoff_ms = min(backoff_ms * 2, 5000)

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    name_diff = {
        "added": sorted(current - before) if verified else [],
        "removed": sorted(before - current) if verified else [],
    }
    _LOGGER.debug(
        "traefik.reload_routers: verified=%s elapsed_ms=%d attempts=%d name_diff=%s",
        verified,
        elapsed_ms,
        attempts,
        name_diff,
    )
    return {
        "verified": verified,
        "elapsed_ms": elapsed_ms,
        "attempts": attempts,
        "name_diff": name_diff,
    }


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Register integration-level services (PITFALLS M5: NOT async_setup_entry).

    The Traefik integration exposes one domain-level service:
    ``traefik.reload_routers``. Service registration lives in the module-level
    ``async_setup`` (HA's startup hook) rather than per-entry in
    ``async_setup_entry`` for two reasons:

    1. Per-entry registration causes duplicate registrations and stale
       handlers after unload (PITFALLS M5).
    2. The service is conceptually integration-scoped (one Traefik per
       config entry, but the service is callable regardless of which entry
       is loaded) — module-level registration makes that contract explicit.

    The ``config`` argument is HA's YAML config (always empty for us — the
    integration is config-flow only). HA's setup machinery calls this with
    ``(hass, config)``; we ignore the second arg.
    """
    del config  # YAML setup not supported — ConfigFlow handles all configuration
    hass.services.async_register(
        DOMAIN,
        "reload_routers",
        _async_handle_reload_routers,
        schema=RELOAD_ROUTERS_SCHEMA,
    )
    return True


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
