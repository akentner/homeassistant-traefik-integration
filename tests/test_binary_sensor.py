"""Tests for TraefikRouterBinarySensor."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.util import slugify

from custom_components.traefik.api import filter_internal_items
from custom_components.traefik.binary_sensor import TraefikRouterBinarySensor


def _router(name: str, status: str) -> dict:
    return {
        "name": name,
        "rule": f"Host(`{name}.example.com`)",
        "service": f"backend-{name}",
        "status": status,
        "tls": None,
    }


def test_filter_internal_items_drops_internal():
    routers = [
        _router("my-router", "enabled"),
        _router("api@internal", "enabled"),
        _router("strip@docker", "enabled"),
        _router("warn-router", "warning"),
    ]
    filtered = filter_internal_items(routers)
    assert {r["name"] for r in filtered} == {"my-router", "warn-router"}


def test_filter_preserves_special_chars_in_user_names():
    """Only `@<provider>` is the trigger; lone `@` is not enough.

    Traefik-internal routers always have the form `<name>@<provider>` where
    `<provider>` is ``\\w+``. A trailing `@` with no provider is treated as
    a regular character (the regex requires `@<word>` to match).

    This test pins that exact behavior so future refactors don't widen the
    filter accidentally.
    """
    routers = [
        _router("router-with-at-edge@", "enabled"),  # trailing @ only -> kept
        _router("router@feature@v2", "enabled"),  # in the middle with provider -> filtered
        _router("plain-router", "enabled"),  # no @ -> kept
    ]
    filtered = filter_internal_items(routers)
    assert {r["name"] for r in filtered} == {"router-with-at-edge@", "plain-router"}


@pytest.mark.parametrize(
    "status,expected",
    [
        ("enabled", True),
        ("disabled", False),
        ("warning", False),
        ("error", False),
    ],
)
def test_ison_derives_from_status(status, expected):
    """is_on is True ONLY if Traefik status == 'enabled'."""
    router = _router("test", status)
    mock_coordinator = MagicMock()
    mock_coordinator.last_update_success = True
    # Phase 2 fetch_all shape: 'http_routers' (was 'routers' in Phase 1).
    mock_coordinator.data = {
        "version": {"Version": "3.x"},
        "http_routers": [router],
        "entrypoints": [],
        "http_services": [],
        "http_middlewares": [],
        "overview": {},
    }
    mock_entry = MagicMock()
    mock_entry.entry_id = "test-entry"
    mock_entry.data = {"url": "https://traefik.example.com:8080"}
    mock_entry.runtime_data = mock_coordinator

    entity = TraefikRouterBinarySensor(mock_entry, mock_coordinator, router)
    assert entity.is_on is expected


def test_entity_id_uses_traefik_http_router_prefix():
    router = _router("my-router", "enabled")
    mock_coordinator = MagicMock()
    mock_coordinator.last_update_success = True
    mock_coordinator.data = {
        "version": {"Version": "3.x"},
        "http_routers": [router],
        "entrypoints": [],
        "http_services": [],
        "http_middlewares": [],
        "overview": {},
    }
    mock_entry = MagicMock()
    mock_entry.entry_id = "test-entry"
    mock_entry.data = {"url": "https://traefik.example.com:8080"}
    mock_entry.runtime_data = mock_coordinator

    entity = TraefikRouterBinarySensor(mock_entry, mock_coordinator, router)
    assert entity.entity_id == f"binary_sensor.traefik_http_router_{slugify('my-router')}"


def test_extra_state_attributes_include_status_and_friendly_rule():
    router = _router("r", "warning")
    mock_coordinator = MagicMock()
    mock_coordinator.last_update_success = True
    mock_coordinator.data = {
        "version": {"Version": "3.x"},
        "http_routers": [router],
        "entrypoints": [],
        "http_services": [],
        "http_middlewares": [],
        "overview": {},
    }
    mock_entry = MagicMock()
    mock_entry.entry_id = "test-entry"
    mock_entry.data = {"url": "https://traefik.example.com:8080"}
    mock_entry.runtime_data = mock_coordinator

    entity = TraefikRouterBinarySensor(mock_entry, mock_coordinator, router)
    attrs = entity.extra_state_attributes
    assert attrs["status"] == "warning"
    assert attrs["friendly_rule"] == "r.example.com"
    assert attrs["service"] == "backend-r"


def test_extra_state_attributes_exposes_raw_name_for_dashboards():
    """ROUTER-02 + D-20: raw Traefik router name surfaced alongside the slug."""
    router = _router("weird@host.example.com", "enabled")
    mock_coordinator = MagicMock()
    mock_coordinator.last_update_success = True
    mock_coordinator.data = {
        "version": {"Version": "3.x"},
        "http_routers": [router],
        "entrypoints": [],
        "http_services": [],
        "http_middlewares": [],
        "overview": {},
    }
    mock_entry = MagicMock()
    mock_entry.entry_id = "test-entry"
    mock_entry.data = {"url": "https://traefik.example.com:8080"}
    mock_entry.runtime_data = mock_coordinator

    entity = TraefikRouterBinarySensor(mock_entry, mock_coordinator, router)
    attrs = entity.extra_state_attributes
    assert attrs["name"] == "weird@host.example.com"
    assert attrs["router_name"] == "weird@host.example.com"


def test_device_info_uses_per_category_identifier():
    """Phase 2 multi-device model: identifier is (DOMAIN, f'{entry_id}_http_routers')."""
    router = _router("r", "enabled")
    mock_coordinator = MagicMock()
    mock_coordinator.last_update_success = True
    mock_coordinator.data = {
        "version": {"Version": "3.1.4"},
        "http_routers": [router],
        "entrypoints": [],
        "http_services": [],
        "http_middlewares": [],
        "overview": {},
    }
    mock_entry = MagicMock()
    mock_entry.entry_id = "test-entry"
    mock_entry.data = {"url": "https://traefik.example.com:8080"}
    mock_entry.runtime_data = mock_coordinator

    entity = TraefikRouterBinarySensor(mock_entry, mock_coordinator, router)
    info = entity.device_info
    assert ("traefik", "test-entry_http_routers") in info["identifiers"]
    assert info["model"] == "HTTP Routers"
    assert info["manufacturer"] == "Traefik"
    assert info["sw_version"] == "3.1.4"
    assert "HTTP Routers" in info["name"]
