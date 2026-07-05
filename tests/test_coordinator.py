"""Integration tests for TraefikCoordinator + __init__.py lifecycle.

Mirrors the gatus project's test_init.py patterns: state-machine assertions
(LOADED / SETUP_RETRY / SETUP_ERROR) and runtime_data wiring verified via
hass.config_entries.async_setup (the proper HA entry machinery).
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.traefik.const import (
    CONF_API_KEY,
    CONF_URL,
    CONF_VERIFY_SSL,
    DOMAIN,
)

MOCK_URL = "https://traefik.example.com:8080"


def _make_entry(**kwargs) -> MockConfigEntry:
    """Build a MockConfigEntry with defaults for the Traefik integration."""
    defaults = {
        "domain": DOMAIN,
        "title": "Traefik",
        "data": {
            CONF_URL: MOCK_URL,
            CONF_API_KEY: "k",
            CONF_VERIFY_SSL: True,
        },
        "unique_id": "traefik.example.com",
    }
    defaults.update(kwargs)
    return MockConfigEntry(**defaults)


def _stub_all_endpoints(aioclient_mock, *, routers: list | None = None) -> None:
    """Mock all six endpoints fetch_all() now requests (Phase 2 / CONTEXT.md D-04)."""
    aioclient_mock.get(
        f"{MOCK_URL}/api/version",
        json={"Version": "3.1.4", "Codename": "rancher", "StartDate": "2026-07-01"},
    )
    aioclient_mock.get(f"{MOCK_URL}/api/entrypoints", json=[])
    aioclient_mock.get(
        f"{MOCK_URL}/api/http/routers",
        json=routers if routers is not None else [],
    )
    aioclient_mock.get(f"{MOCK_URL}/api/http/services", json=[])
    aioclient_mock.get(f"{MOCK_URL}/api/http/middlewares", json=[])
    aioclient_mock.get(
        f"{MOCK_URL}/api/overview",
        json={"http": {"routers": [], "services": [], "middlewares": []}},
    )


async def test_setup_creates_coordinator_in_runtime_data(hass, aioclient_mock) -> None:
    """Happy path: async_setup_entry creates the coordinator in entry.runtime_data."""
    from custom_components.traefik.coordinator import TraefikCoordinator

    _stub_all_endpoints(aioclient_mock)

    entry = _make_entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert result is True
    assert entry.state is ConfigEntryState.LOADED
    assert isinstance(entry.runtime_data, TraefikCoordinator)
    assert entry.runtime_data.data["version"]["Version"] == "3.1.4"


async def test_unload_returns_to_not_loaded(hass, aioclient_mock) -> None:
    """async_unload_entry clears state back to NOT_LOADED."""
    _stub_all_endpoints(aioclient_mock)

    entry = _make_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    result = await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert result is True
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_network_failure_triggers_setup_retry(hass, aioclient_mock) -> None:
    """Transient network failure => ConfigEntryNotReady => SETUP_RETRY."""
    aioclient_mock.get(f"{MOCK_URL}/api/version", exc=Exception("connection refused"))

    entry = _make_entry()
    entry.add_to_hass(hass)
    result = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert result is False
    # SETUP_RETRY is what HA shows when ConfigEntryNotReady is raised.
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_5xx_triggers_setup_retry(hass, aioclient_mock) -> None:
    """TraefikApiError (5xx) is mapped to UpdateFailed -> ConfigEntryNotReady -> SETUP_RETRY."""
    aioclient_mock.get(f"{MOCK_URL}/api/version", status=500)

    entry = _make_entry()
    entry.add_to_hass(hass)
    result = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert result is False
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_401_triggers_setup_error(hass, aioclient_mock) -> None:
    """TraefikAuthError is mapped to ConfigEntryAuthFailed -> SETUP_ERROR."""
    aioclient_mock.get(f"{MOCK_URL}/api/version", status=401)

    entry = _make_entry()
    entry.add_to_hass(hass)
    result = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert result is False
    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_runtime_data_verify_ssl_defaults_true(hass, aioclient_mock) -> None:
    """If data lacks verify_ssl (set via UI), entry.options overrides default=True."""
    _stub_all_endpoints(aioclient_mock)

    entry = _make_entry(data={CONF_URL: MOCK_URL, CONF_API_KEY: "k"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    # verify_ssl defaults to True when neither options nor data provide it.
    assert entry.runtime_data.client._verify_ssl is True  # type: ignore[attr-defined]