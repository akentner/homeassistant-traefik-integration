"""Unit tests for the Phase 2 sensor platform entities.

Covers TraefikEntrypointSensor, TraefikServiceSensor, and the three
TraefikXxxCountSensor aggregate counters. Uses MagicMock for the coordinator
(unlike test_coordinator.py which exercises the real DataUpdateCoordinator);
this keeps the assertions focused on entity state derivation rather than
lifecycle wiring (already covered by test_coordinator.py).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from homeassistant.util import slugify

from custom_components.traefik.api import filter_internal_items
from custom_components.traefik.sensor import (
    TraefikEntrypointSensor,
    TraefikMiddlewaresCountSensor,
    TraefikRoutersCountSensor,
    TraefikServicesCountSensor,
    TraefikServiceSensor,
)


def _entry(name: str = "traefik.example.com") -> MagicMock:
    """Build a mock TraefikConfigEntry for sensor instantiation."""
    e = MagicMock()
    e.entry_id = "test-entry"
    e.data = {"url": f"https://{name}:8080"}
    return e


def _coordinator_with(data: dict[str, Any]) -> MagicMock:
    """Build a MagicMock coordinator with the given data payload."""
    coord = MagicMock()
    coord.data = data
    coord.last_update_success = True
    return coord


# ---------------------------------------------------------------------------
# Entrypoint sensors
# ---------------------------------------------------------------------------


def test_entrypoint_sensor_state_from_address() -> None:
    """TraefikEntrypointSensor.native_value == entrypoint['address']."""
    ep = {"name": "websecure", "address": ":443", "transport": "tcp"}
    coord = _coordinator_with({"entrypoints": [ep]})
    entity = TraefikEntrypointSensor(_entry(), coord, ep)
    assert entity.native_value == ":443"


def test_entrypoint_sensor_entity_id_and_unique_id() -> None:
    """Entrypoint entity_id follows the traefik_http_entrypoint_<slug> convention."""
    ep = {"name": "websecure", "address": ":443", "transport": "tcp"}
    coord = _coordinator_with({"entrypoints": [ep]})
    entity = TraefikEntrypointSensor(_entry(), coord, ep)
    assert entity.entity_id == f"sensor.traefik_http_entrypoint_{slugify('websecure')}"
    assert entity.unique_id == "test-entry_http_entrypoint_websecure"


def test_entrypoint_sensor_attributes_include_name_address_transport() -> None:
    """extra_state_attributes surface raw fields per UX-04."""
    ep = {"name": "websecure", "address": ":443", "transport": "tcp", "tls": {}}
    coord = _coordinator_with({"entrypoints": [ep]})
    entity = TraefikEntrypointSensor(_entry(), coord, ep)
    attrs = entity.extra_state_attributes
    assert attrs["name"] == "websecure"
    assert attrs["address"] == ":443"
    assert attrs["transport"] == "tcp"
    assert attrs["entrypoint_name"] == "websecure"


# ---------------------------------------------------------------------------
# Service sensors
# ---------------------------------------------------------------------------


def test_service_sensor_state_from_loadbalancer_status() -> None:
    """TraefikServiceSensor.native_value == loadbalancer.status when present."""
    svc = {
        "name": "backend-api",
        "type": "loadbalancer",
        "loadbalancer": {"servers": [{"url": "http://10.0.0.1:8000"}], "status": "OK"},
        "status": "enabled",
    }
    coord = _coordinator_with({"http_services": [svc]})
    entity = TraefikServiceSensor(_entry(), coord, svc)
    assert entity.native_value == "OK"


def test_service_sensor_state_falls_back_to_top_level_status() -> None:
    """When loadbalancer.status is absent, fall back to service.status."""
    svc = {
        "name": "redirect-svc",
        "type": "redirect",
        # No loadbalancer key — redirect service.
        "status": "WARNING",
    }
    coord = _coordinator_with({"http_services": [svc]})
    entity = TraefikServiceSensor(_entry(), coord, svc)
    assert entity.native_value == "WARNING"


def test_service_sensor_attributes_include_servers_and_count() -> None:
    """extra_state_attributes include server_count + servers list."""
    svc = {
        "name": "backend-api",
        "type": "loadbalancer",
        "loadbalancer": {
            "servers": [
                {"url": "http://10.0.0.1:8000"},
                {"url": "http://10.0.0.2:8000"},
            ],
            "status": "OK",
        },
        "status": "enabled",
    }
    coord = _coordinator_with({"http_services": [svc]})
    entity = TraefikServiceSensor(_entry(), coord, svc)
    attrs = entity.extra_state_attributes
    assert attrs["server_count"] == 2
    assert attrs["type"] == "loadbalancer"
    assert len(attrs["servers"]) == 2


# ---------------------------------------------------------------------------
# Aggregate count sensors
# ---------------------------------------------------------------------------


def test_routers_count_sensor_uses_filtered_count() -> None:
    """Routers count drops @<provider> internal items."""
    coord = _coordinator_with(
        {
            "http_routers": [
                {"name": "user-router"},
                {"name": "api@internal"},
            ],
        }
    )
    filtered = filter_internal_items(coord.data["http_routers"])
    entity = TraefikRoutersCountSensor(_entry(), coord, filtered_count=len(filtered))
    assert entity.native_value == 1


def test_aggregate_sensor_attributes_include_breakdown() -> None:
    """extra_state_attributes include http_count/tcp_count/udp_count from overview."""
    coord = _coordinator_with(
        {
            "overview": {
                "http": {"routers": 3},
                "tcp": {"routers": 1},
                "udp": {},
            },
        }
    )
    entity = TraefikRoutersCountSensor(
        _entry(),
        coord,
        filtered_count=2,
        http_count=3,
        tcp_count=1,
        udp_count=0,
    )
    attrs = entity.extra_state_attributes
    assert attrs["http_count"] == 3
    assert attrs["tcp_count"] == 1
    assert attrs["udp_count"] == 0
    assert attrs["filtered_count"] == 2


def test_services_count_sensor_uses_filtered_count() -> None:
    """Services count drops @<provider> internal items (api@internal)."""
    coord = _coordinator_with(
        {
            "http_services": [
                {"name": "backend-api"},
                {"name": "backend-warn"},
                {"name": "api@internal"},
            ],
        }
    )
    filtered = filter_internal_items(coord.data["http_services"])
    entity = TraefikServicesCountSensor(_entry(), coord, filtered_count=len(filtered))
    assert entity.native_value == 2


def test_middlewares_count_sensor_uses_filtered_count() -> None:
    """Middlewares count drops @<provider> internal items (strip@docker)."""
    coord = _coordinator_with(
        {
            "http_middlewares": [
                {"name": "auth-headers"},
                {"name": "rate-limit"},
                {"name": "strip@docker"},
            ],
        }
    )
    filtered = filter_internal_items(coord.data["http_middlewares"])
    entity = TraefikMiddlewaresCountSensor(_entry(), coord, filtered_count=len(filtered))
    assert entity.native_value == 2
