"""Tests for TraefikRouterBinarySensor."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.util import slugify

from custom_components.traefik.api import filter_internal_items
from custom_components.traefik.binary_sensor import (
    TraefikAnyMiddlewareFailingBinarySensor,
    TraefikAnyServiceFailingBinarySensor,
    TraefikRouterBinarySensor,
)


def _router(name: str, status: str) -> dict:
    return {
        "name": name,
        "rule": f"Host(`{name}.example.com`)",
        "service": f"backend-{name}",
        "status": status,
        "tls": None,
    }


def test_filter_internal_items_drops_internal():
    """Traefik's auto-generated ``@internal`` items are dropped; user-named
    provider-suffixed items (``@docker``, ``@file``, ``@kubernetes``, …)
    are kept — those are user resources that Traefik just suffixes with
    the provider name.

    v0.1.5 regression: the previous regex ``r"@\w+"`` matched ANY
    provider-suffix and dropped user routers. Real-world Traefik setups
    with file / docker providers all have provider-suffixed user
    routers (e.g. ``ha-nextgen@file``, ``n8n@docker``) — those are
    legitimate user items, not Traefik internals.
    """
    routers = [
        _router("my-router", "enabled"),  # kept (no @-suffix)
        _router("api@internal", "enabled"),  # dropped (Traefik-internal)
        _router("dashboard@internal", "enabled"),  # dropped (Traefik-internal)
        _router("strip@docker", "enabled"),  # kept (user-named middleware)
        _router("ha-nextgen@file", "enabled"),  # kept (user-named router)
        _router("warn-router", "warning"),  # kept (no @-suffix)
    ]
    filtered = filter_internal_items(routers)
    assert {r["name"] for r in filtered} == {
        "my-router",
        "strip@docker",
        "ha-nextgen@file",
        "warn-router",
    }


def test_filter_preserves_special_chars_in_user_names():
    """Trailing ``@`` and provider-suffixes that aren't ``internal`` are kept.

    v0.1.5 update: only ``@internal`` is treated as internal. Traefik's
    universal naming convention appends the provider name to every
    user-defined resource (so ``strip`` middleware in docker labels
    becomes ``strip@docker``). Those items are NOT internal; they are
    user resources that HA should surface.
    """
    routers = [
        _router("router-with-at-edge@", "enabled"),  # trailing @ only -> kept
        _router("router@feature@v2", "enabled"),  # in the middle, not @internal -> kept
        _router("plain-router", "enabled"),  # no @ -> kept
        _router("router@internal-thing", "enabled"),  # @internal followed by hyphen -> kept
    ]
    filtered = filter_internal_items(routers)
    assert {r["name"] for r in filtered} == {
        "router-with-at-edge@",
        "router@feature@v2",
        "plain-router",
        "router@internal-thing",
    }


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


# ---------------------------------------------------------------------------
# v0.2.0 — TraefikAnyServiceFailingBinarySensor + TraefikAnyMiddlewareFailingBinarySensor
# ---------------------------------------------------------------------------


def _entry_with_data(data: Any) -> MagicMock:
    """Helper: build a MagicMock entry with the given coordinator data.

    Mirrors ``_entry_with_data`` in test_sensor.py — wiring the coordinator
    into ``entry.runtime_data`` so ``TraefikEntity.__init__`` resolves
    ``self.coordinator`` to the data-bearing mock.
    """
    coord = MagicMock()
    coord.data = data
    coord.last_update_success = True
    entry = MagicMock()
    entry.entry_id = "test-entry"
    entry.data = {"url": "https://traefik.example.com:8080"}
    entry.runtime_data = coord
    return entry


def test_any_service_failing_aggregates_status() -> None:
    """TraefikAnyServiceFailingBinarySensor.is_on is True when any service != enabled."""
    entry = _entry_with_data(
        {
            "http_services": [
                {"name": "ok", "status": "enabled"},
                {"name": "broken", "status": "error"},
            ],
        }
    )
    entity = TraefikAnyServiceFailingBinarySensor(entry, entry.runtime_data)
    assert entity.is_on is True
    attrs = entity.extra_state_attributes
    assert attrs["failing_service_count"] == 1
    assert attrs["failing_service_names"] == ["broken"]


def test_any_service_failing_disabled_when_all_enabled() -> None:
    """All-enabled services → is_on=False, count=0, names=[]."""
    entry = _entry_with_data(
        {
            "http_services": [
                {"name": "ok-1", "status": "enabled"},
                {"name": "ok-2", "status": "enabled"},
            ],
        }
    )
    entity = TraefikAnyServiceFailingBinarySensor(entry, entry.runtime_data)
    assert entity.is_on is False
    assert entity.extra_state_attributes["failing_service_count"] == 0
    assert entity.extra_state_attributes["failing_service_names"] == []


def test_any_middleware_failing_aggregates_status() -> None:
    """TraefikAnyMiddlewareFailingBinarySensor mirrors the service variant for middlewares."""
    entry = _entry_with_data(
        {
            "http_middlewares": [
                {"name": "auth", "status": "enabled"},
                {"name": "rate-limit", "status": "warning"},
                {"name": "strip@docker", "status": "enabled"},
                # Disabled counts as failing (consistent with the router aggregate).
                {"name": "off-mw", "status": "disabled"},
            ],
        }
    )
    entity = TraefikAnyMiddlewareFailingBinarySensor(entry, entry.runtime_data)
    assert entity.is_on is True
    attrs = entity.extra_state_attributes
    assert attrs["failing_middleware_count"] == 2
    assert set(attrs["failing_middleware_names"]) == {"rate-limit", "off-mw"}


def test_any_failing_handles_missing_data() -> None:
    """Cold start: coordinator.data is None → is_on=None, attributes empty."""
    entry = _entry_with_data(None)
    entry.runtime_data.last_update_success = False
    for cls in (
        TraefikAnyServiceFailingBinarySensor,
        TraefikAnyMiddlewareFailingBinarySensor,
    ):
        entity = cls(entry, entry.runtime_data)
        assert entity.is_on is None
        # attributes always return a safe default
        attrs = entity.extra_state_attributes
        assert attrs[next(k for k in attrs if k.startswith("failing_") and k.endswith("_count"))] == 0


def test_any_failing_disabled_by_default() -> None:
    """PITFALLS M-12: PROBLEM aggregates are entity_registry_enabled_default=False.

    Both ``TraefikAnyServiceFailingBinarySensor`` and
    ``TraefikAnyMiddlewareFailingBinarySensor`` follow the same opt-in
    pattern as the router variant — they don't pollute the States panel
    by default.

    Note: HA's ``CachedProperties`` metaclass moves class-level
    ``_attr_*`` attrs to ``__attr_*`` private names and wraps them in a
    property; the public class-level read returns the property
    descriptor, not the underlying value. We read the raw value out of
    ``cls.__dict__`` (same pattern as
    ``test_binary_sensor_tls_expiring.py::test_cert_expiry_disabled_by_default``).
    """
    for cls in (
        TraefikAnyServiceFailingBinarySensor,
        TraefikAnyMiddlewareFailingBinarySensor,
    ):
        assert cls.__dict__.get("__attr_entity_registry_enabled_default") is False
