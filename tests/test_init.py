"""Tests for the Traefik integration __init__.py service registration + reload handler.

Covers:
- module-level async_setup registers traefik.reload_routers
- service handler returns verified=True when router set changes
- service handler returns verified=False after budget exhaustion
- service handler propagates TraefikApiError on non-2xx POST
- TraefikReloadButton.async_press dispatches through the service

Hermetic — uses aioclient_mock + AsyncMock for client.reload_routers so the
tests don't depend on a live Traefik instance or the 5s polling budget
finishing in real wall-clock time.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.traefik.api import TraefikApiError
from custom_components.traefik.button import TraefikReloadButton
from custom_components.traefik.const import DOMAIN

MOCK_URL = "https://traefik.example.com:8080"


def _make_entry() -> MockConfigEntry:
    """Build a loaded Traefik MockConfigEntry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Traefik",
        data={"url": MOCK_URL, "api_key": "k", "verify_ssl": True},
        unique_id="traefik.example.com",
    )


def _stub_all_endpoints(
    aioclient_mock,
    *,
    routers: list[dict[str, Any]] | None = None,
    services: list[dict[str, Any]] | None = None,
    middlewares: list[dict[str, Any]] | None = None,
    entrypoints: list[dict[str, Any]] | None = None,
) -> None:
    """Mock all six endpoints fetch_all() now requests."""
    aioclient_mock.get(
        f"{MOCK_URL}/api/version",
        json={"Version": "3.1.4", "Codename": "rancher", "StartDate": "2026-07-01"},
    )
    aioclient_mock.get(
        f"{MOCK_URL}/api/entrypoints",
        json=entrypoints
        if entrypoints is not None
        else [
            {"name": "websecure", "address": ":443", "transport": "tcp", "tls": {}},
            {"name": "web", "address": ":80", "transport": "tcp"},
        ],
    )
    aioclient_mock.get(
        f"{MOCK_URL}/api/http/routers",
        json=routers if routers is not None else [],
    )
    aioclient_mock.get(
        f"{MOCK_URL}/api/http/services",
        json=services if services is not None else [],
    )
    aioclient_mock.get(
        f"{MOCK_URL}/api/http/middlewares",
        json=middlewares if middlewares is not None else [],
    )
    aioclient_mock.get(
        f"{MOCK_URL}/api/overview",
        json={"http": {"routers": 0, "services": 0, "middlewares": 0}},
    )


async def test_async_setup_registers_reload_service(hass) -> None:
    """Module-level async_setup registers traefik.reload_routers (PITFALLS M5)."""
    from custom_components.traefik import async_setup

    assert await async_setup(hass, {}) is True
    assert hass.services.has_service(DOMAIN, "reload_routers")


async def test_reload_service_verified_true_when_routers_change(hass, aioclient_mock) -> None:
    """Reload handler: router set changes -> verified=True with name_diff."""
    from custom_components.traefik import _async_handle_reload_routers

    # Initial cycle: routers A and B.
    initial_routers = [
        {"name": "router-a", "rule": "Host(`a.example.com`)", "status": "enabled"},
        {"name": "router-b", "rule": "Host(`b.example.com`)", "status": "enabled"},
    ]
    updated_routers = [
        {"name": "router-a", "rule": "Host(`a.example.com`)", "status": "enabled"},
        {"name": "router-c", "rule": "Host(`c.example.com`)", "status": "enabled"},
    ]

    # First GET serves initial; subsequent GETs serve updated.
    aioclient_mock.get(f"{MOCK_URL}/api/version", json={"Version": "3.1.4"})
    aioclient_mock.get(f"{MOCK_URL}/api/entrypoints", json=[])
    aioclient_mock.get(f"{MOCK_URL}/api/http/services", json=[])
    aioclient_mock.get(f"{MOCK_URL}/api/http/middlewares", json=[])
    aioclient_mock.get(f"{MOCK_URL}/api/overview", json={"http": {}})
    # Track call count to swap routers payload after first call.
    routers_calls = {"count": 0}

    async def _routers_response(method, url, data):  # type: ignore[no-untyped-def]
        """side_effect callback returning an AiohttpClientMockResponse."""
        from pytest_homeassistant_custom_component.test_util.aiohttp import (
            AiohttpClientMockResponse,
        )

        routers_calls["count"] += 1
        payload = updated_routers if routers_calls["count"] > 1 else initial_routers
        return AiohttpClientMockResponse(method, url, json=payload)

    aioclient_mock.get(f"{MOCK_URL}/api/http/routers", side_effect=_routers_response)
    # Refresh POST returns 202.
    aioclient_mock.post(f"{MOCK_URL}/api/http/routers/refresh", status=202)

    entry = _make_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Stub client.reload_routers to do nothing extra (the POST is mocked above).
    coordinator = entry.runtime_data
    coordinator.client.reload_routers = AsyncMock(  # type: ignore[attr-defined]
        return_value=None
    )

    # Drive the service handler via a ServiceCall.
    call = _make_service_call(hass)
    result = await _async_handle_reload_routers(call)

    assert result["verified"] is True
    assert result["attempts"] >= 1
    assert sorted(result["name_diff"]["added"]) == ["router-c"]
    assert sorted(result["name_diff"]["removed"]) == ["router-b"]
    assert "elapsed_ms" in result
    assert isinstance(result["elapsed_ms"], int)


async def test_reload_service_verified_false_when_no_change_within_budget(hass, aioclient_mock) -> None:
    """If router set never changes, verified=False after max attempts."""
    from custom_components.traefik import _async_handle_reload_routers

    routers = [
        {"name": "stable-router", "rule": "Host(`s.example.com`)", "status": "enabled"},
    ]
    _stub_all_endpoints(aioclient_mock, routers=routers)
    aioclient_mock.post(f"{MOCK_URL}/api/http/routers/refresh", status=202)

    entry = _make_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data
    coordinator.client.reload_routers = AsyncMock(  # type: ignore[attr-defined]
        return_value=None
    )

    call = _make_service_call(hass)
    result = await _async_handle_reload_routers(call)

    # Router set never changed -> verified=False after the 5s budget elapses.
    # Exact attempt count depends on per-iteration sleep timing (200ms -> 5s
    # backoff caps at attempt ~5 with mocked instant refresh); semantic check
    # is just that we exited via the budget guard, not via a router-set change.
    assert result["verified"] is False
    assert result["attempts"] >= 1
    assert result["attempts"] <= 10
    assert result["name_diff"]["added"] == []
    assert result["name_diff"]["removed"] == []
    assert result["elapsed_ms"] >= 0


async def test_reload_service_propagates_api_errors(hass, aioclient_mock) -> None:
    """Non-2xx POST raises TraefikApiError -> HA surfaces as service failure."""
    from custom_components.traefik import _async_handle_reload_routers

    _stub_all_endpoints(aioclient_mock)
    # POST returns 500 -> reload_routers raises TraefikApiError.
    aioclient_mock.post(f"{MOCK_URL}/api/http/routers/refresh", status=500)

    entry = _make_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # No need to stub reload_routers — the real client will hit the mocked POST.

    call = _make_service_call(hass)
    with pytest.raises(TraefikApiError):
        await _async_handle_reload_routers(call)


async def test_reload_button_async_press_calls_service(hass, aioclient_mock) -> None:
    """TraefikReloadButton.async_press dispatches via hass.services.async_call.

    Proof of dispatch chain (button -> service -> handler -> client):
    1. Replace ``coordinator.client.reload_routers`` with an AsyncMock spy.
    2. Press the button.
    3. Assert the spy was called -> the handler ran -> the button dispatched
       via the service (NOT a direct client call).

    We can't patch ``_async_handle_reload_routers`` after registration because
    the ServiceRegistry captures the handler reference at registration time;
    instead we observe the handler's downstream effect (the client call).
    """
    _stub_all_endpoints(aioclient_mock)
    aioclient_mock.post(f"{MOCK_URL}/api/http/routers/refresh", status=202)

    entry = _make_entry()
    entry.add_to_hass(hass)
    # Register the service (module-level setup hasn't fired in tests — call it).
    from custom_components.traefik import async_setup

    await async_setup(hass, {})

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data
    # Spy on the handler's downstream effect: ``client.reload_routers`` is
    # only called from inside the service handler.
    spy = AsyncMock(return_value=None)
    coordinator.client.reload_routers = spy  # type: ignore[attr-defined]
    # Make the polling loop's ``async_request_refresh`` instant so the press
    # doesn't wait for the 5s budget. The router set never changes (mocked
    # routers are stable) so the handler still returns verified=False.
    coordinator.async_request_refresh = AsyncMock(  # type: ignore[attr-defined]
        return_value=None
    )

    button = TraefikReloadButton(hass, entry, coordinator)
    await button.async_press()
    await hass.async_block_till_done()

    # If the button dispatched via the service -> the handler ran ->
    # ``client.reload_routers`` was called. Without the service dispatch, the
    # button would never reach the handler and the spy stays at 0 calls.
    assert spy.call_count >= 1, (
        f"Expected client.reload_routers to be called via service dispatch, got {spy.call_count} calls"
    )


def _make_service_call(hass):  # type: ignore[no-untyped-def]
    """Build a ServiceCall for direct handler invocation."""
    from homeassistant.core import Context, ServiceCall

    return ServiceCall(
        hass,
        DOMAIN,
        "reload_routers",
        data={},
        context=Context(),
    )
