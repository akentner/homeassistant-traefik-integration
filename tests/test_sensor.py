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

import pytest
from homeassistant.util import slugify

from custom_components.traefik.sensor import (
    TraefikEntrypointSensor,
    TraefikMiddlewaresCountSensor,
    TraefikRoutersCountSensor,
    TraefikServicesCountSensor,
    TraefikServiceSensor,
    _count_by_status,
)


def _entry(name: str = "traefik.example.com") -> MagicMock:
    """Build a mock TraefikConfigEntry for sensor instantiation.

    Note: ``runtime_data`` is left unset — for sensors that read state
    from the coordinator, use :func:`_entry_with_data` instead which
    wires the coordinator into ``entry.runtime_data`` so that
    ``TraefikEntity.__init__`` resolves ``self.coordinator`` to the
    data-bearing mock.
    """
    e = MagicMock()
    e.entry_id = "test-entry"
    e.data = {"url": f"https://{name}:8080"}
    return e


def _coordinator_with(data: dict[str, Any] | None) -> MagicMock:
    """Build a MagicMock coordinator with the given data payload."""
    coord = MagicMock()
    coord.data = data
    coord.last_update_success = True
    return coord


def _entry_with_data(data: dict[str, Any] | None) -> tuple[MagicMock, MagicMock]:
    """Build a (entry, coordinator) pair wired together for entity tests.

    The entry's ``runtime_data`` slot holds the coordinator so
    ``TraefikEntity.__init__`` resolves ``self.coordinator`` to the mock
    that owns the data dict. Required for the v0.1.4+ property-based
    state derivation on the aggregate count sensors.
    """
    entry = _entry()
    coord = _coordinator_with(data)
    entry.runtime_data = coord
    return entry, coord


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
    entry, _coord = _entry_with_data(
        {
            "http_routers": [
                {"name": "user-router"},
                {"name": "api@internal"},
            ],
        }
    )
    entity = TraefikRoutersCountSensor(entry)
    assert entity.native_value == 1  # api@internal filtered out


def test_aggregate_sensor_attributes_include_breakdown() -> None:
    """extra_state_attributes read http_count/tcp_count/udp_count from /api/overview."""
    entry, _coord = _entry_with_data(
        {
            "http_routers": [
                {"name": "r1"},
                {"name": "r2"},
            ],
            "overview": {
                "http": {"routers": 3},
                "tcp": {"routers": 1},
                "udp": {},
            },
        }
    )
    entity = TraefikRoutersCountSensor(entry)
    attrs = entity.extra_state_attributes
    assert attrs["filtered_count"] == 2
    assert attrs["http_count"] == 3
    assert attrs["tcp_count"] == 1
    assert attrs["udp_count"] == 0


def test_aggregate_count_updates_on_coordinator_cycle() -> None:
    """v0.1.4 regression: counts MUST refresh on every coordinator cycle.

    The pre-v0.1.4 implementation captured ``filtered_count`` at __init__
    time in ``self._attr_native_value`` and never updated it. Counts of a
    freshly-bootstrapped Traefik with empty router lists read "0" forever
    even after routers were configured.

    The new implementation reads from ``coordinator.data`` on every
    property access; this test mutates ``coord.data`` between two reads
    to assert that the count tracks the current data, not the initial.
    """
    entry, _coord = _entry_with_data({"http_routers": []})
    entity = TraefikRoutersCountSensor(entry)
    assert entity.native_value == 0

    # Coordinator refresh brings new data
    entry.runtime_data.data = {"http_routers": [{"name": "new"}, {"name": "another"}]}
    assert entity.native_value == 2  # updated without re-instantiation


def test_aggregate_attributes_refresh_with_coordinator_data() -> None:
    """v0.1.4 regression: overview breakdown attributes also refresh."""
    entry, _coord = _entry_with_data(
        {
            "http_routers": [],
            "overview": {"http": {"routers": 0}},
        }
    )
    entity = TraefikRoutersCountSensor(entry)
    assert entity.extra_state_attributes["http_count"] == 0

    entry.runtime_data.data = {
        "http_routers": [{"name": "a"}],
        "overview": {"http": {"routers": 1}},
    }
    assert entity.extra_state_attributes["filtered_count"] == 1
    assert entity.extra_state_attributes["http_count"] == 1


def test_aggregate_returns_zero_when_coordinator_data_missing() -> None:
    """Cold-start case (coordinator.data is None): all counts 0, breakdown 0."""
    entry, _coord = _entry_with_data(None)
    entry.runtime_data.last_update_success = False
    entity = TraefikRoutersCountSensor(entry)
    assert entity.native_value == 0
    assert entity.extra_state_attributes["filtered_count"] == 0
    assert entity.extra_state_attributes["http_count"] == 0


def test_services_count_sensor_uses_filtered_count() -> None:
    """Services count drops @<provider> internal items (api@internal)."""
    entry, _coord = _entry_with_data(
        {
            "http_services": [
                {"name": "backend-api"},
                {"name": "backend-warn"},
                {"name": "api@internal"},
            ],
        }
    )
    entity = TraefikServicesCountSensor(entry)
    assert entity.native_value == 2


def test_middlewares_count_sensor_uses_filtered_count() -> None:
    """Middlewares count drops ``@internal`` items (Traefik's auto-generated
    API/dashboard/redirect internals).

    Middlewares are HTTP-only — no http_count/tcp_count/udp breakdown.
    User-named provider-suffixed middlewares (``strip@docker``) are NOT
    internal and should be counted.
    """
    entry, _coord = _entry_with_data(
        {
            "http_middlewares": [
                {"name": "auth-headers", "status": "enabled"},
                {"name": "rate-limit", "status": "enabled"},
                {"name": "strip@docker", "status": "enabled"},  # user middleware, kept
                {"name": "dashboard_stripprefix@internal", "status": "enabled"},  # Traefik-internal, dropped
            ],
        }
    )
    entity = TraefikMiddlewaresCountSensor(entry)
    assert entity.native_value == 3
    # v0.2.0: now also exposes status breakdown (all 3 enabled → success=3).
    assert entity.extra_state_attributes["filtered_count"] == 3
    assert entity.extra_state_attributes["success_count"] == 3
    assert entity.extra_state_attributes["warning_count"] == 0
    assert entity.extra_state_attributes["error_count"] == 0
    assert entity.extra_state_attributes["disabled_count"] == 0
    assert entity.extra_state_attributes["success_pct"] == 100.0


def test_middlewares_no_overview_breakdown() -> None:
    """TraefikMiddlewaresCountSensor excludes http/tcp/udp breakdown attrs.

    Middlewares are HTTP-only per Traefik's API surface — there is no
    TCP/UDP middleware concept, so the breakdown is meaningless. The
    base class' ``_HAS_OVERVIEW_BREAKDOWN = False`` flag suppresses those
    keys entirely.
    """
    entry, _coord = _entry_with_data(
        {
            "http_middlewares": [{"name": "x", "status": "enabled"}],
            "overview": {"http": {"middlewares": 5}, "tcp": {"middlewares": 99}},
        }
    )
    entity = TraefikMiddlewaresCountSensor(entry)
    attrs = entity.extra_state_attributes
    assert attrs["filtered_count"] == 1
    assert attrs["success_count"] == 1
    assert "http_count" not in attrs
    assert "tcp_count" not in attrs
    assert "udp_count" not in attrs


# ---------------------------------------------------------------------------
# v0.2.0 — _count_by_status helper + breakdown attributes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "items,expected",
    [
        (
            [
                {"name": "r1", "status": "enabled"},
                {"name": "r2", "status": "enabled"},
            ],
            {"success": 2, "warning": 0, "error": 0, "disabled": 0},
        ),
        (
            [
                {"name": "r1", "status": "enabled"},
                {"name": "r2", "status": "warning"},
                {"name": "r3", "status": "error"},
                {"name": "r4", "status": "disabled"},
            ],
            {"success": 1, "warning": 1, "error": 1, "disabled": 1},
        ),
        # Traefik status missing → dropped (no bucket)
        (
            [{"name": "r1"}, {"name": "r2", "status": "enabled"}],
            {"success": 1, "warning": 0, "error": 0, "disabled": 0},
        ),
        # Future Traefik status value (e.g. "critical") → silently dropped
        (
            [
                {"name": "r1", "status": "enabled"},
                {"name": "r2", "status": "critical"},
            ],
            {"success": 1, "warning": 0, "error": 0, "disabled": 0},
        ),
        # Empty list
        ([], {"success": 0, "warning": 0, "error": 0, "disabled": 0}),
        # Non-dict entries skipped
        (
            ["not-a-dict", {"name": "r1", "status": "enabled"}, None],
            {"success": 1, "warning": 0, "error": 0, "disabled": 0},
        ),
    ],
)
def test_count_by_status_groups_items(items: list, expected: dict[str, int]) -> None:
    """_count_by_status maps Traefik API status values to bucket labels.

    The four buckets (success / warning / error / disabled) always
    appear in the result, even when no items map to them.
    """
    assert _count_by_status(items) == expected


def test_aggregate_routers_breakdown_attributes() -> None:
    """sensor.traefik_routers exposes success/warning/error/disabled counts.

    Mirrors the user's actual Traefik at 192.168.178.3:8080: 9 user
    routers (6 from file provider + 3 from internal that we filter out)
    all currently enabled.
    """
    entry, _coord = _entry_with_data(
        {
            "http_routers": [
                {"name": "ha-nextgen@file", "status": "enabled"},
                {"name": "ha@file", "status": "enabled"},
                {"name": "httpsredirect@file", "status": "enabled"},
                {"name": "n8n@file", "status": "enabled"},
                {"name": "opencode@file", "status": "enabled"},
                {"name": "traccar@file", "status": "enabled"},
                # Traefik-internal filtered out before counting.
                {"name": "api@internal", "status": "enabled"},
                {"name": "dashboard@internal", "status": "enabled"},
                {"name": "web-to-websecure@internal", "status": "enabled"},
            ],
        }
    )
    entity = TraefikRoutersCountSensor(entry)
    attrs = entity.extra_state_attributes
    assert attrs["filtered_count"] == 6
    assert attrs["success_count"] == 6
    assert attrs["warning_count"] == 0
    assert attrs["error_count"] == 0
    assert attrs["disabled_count"] == 0
    assert attrs["success_pct"] == 100.0
    assert attrs["status_breakdown"] == {
        "success": 6,
        "warning": 0,
        "error": 0,
        "disabled": 0,
    }


def test_aggregate_breakdown_reflects_mixed_status() -> None:
    """A router in 'warning' state shows up in warning_count, not success_count."""
    entry, _coord = _entry_with_data(
        {
            "http_routers": [
                {"name": "ok-1", "status": "enabled"},
                {"name": "ok-2", "status": "enabled"},
                {"name": "warn-1", "status": "warning"},
                {"name": "err-1", "status": "error"},
                {"name": "off-1", "status": "disabled"},
            ],
        }
    )
    entity = TraefikRoutersCountSensor(entry)
    attrs = entity.extra_state_attributes
    assert attrs["filtered_count"] == 5
    assert attrs["success_count"] == 2
    assert attrs["warning_count"] == 1
    assert attrs["error_count"] == 1
    assert attrs["disabled_count"] == 1
    # success_pct = success / (success + warning + error) = 2 / 4 = 50.0
    assert attrs["success_pct"] == 50.0


def test_aggregate_success_pct_excludes_disabled() -> None:
    """success_pct denominator excludes disabled items (admin opt-out)."""
    entry, _coord = _entry_with_data(
        {
            "http_routers": [
                {"name": "ok-1", "status": "enabled"},
                {"name": "off-1", "status": "disabled"},
                {"name": "off-2", "status": "disabled"},
            ],
        }
    )
    entity = TraefikRoutersCountSensor(entry)
    attrs = entity.extra_state_attributes
    # 1 success / 1 enabled-only = 100%
    assert attrs["success_pct"] == 100.0


def test_aggregate_success_pct_zero_when_no_non_disabled() -> None:
    """If everything is disabled (no enabled/warning/error), pct is 100% (vacuously)."""
    entry, _coord = _entry_with_data(
        {
            "http_routers": [
                {"name": "off-1", "status": "disabled"},
                {"name": "off-2", "status": "disabled"},
            ],
        }
    )
    entity = TraefikRoutersCountSensor(entry)
    attrs = entity.extra_state_attributes
    assert attrs["filtered_count"] == 2
    assert attrs["success_count"] == 0
    assert attrs["success_pct"] == 100.0


def test_aggregate_breakdown_refreshes_on_coordinator_cycle() -> None:
    """v0.2.0 regression: breakdown attributes refresh every cycle, not at __init__."""
    entry, _coord = _entry_with_data(
        {
            "http_routers": [
                {"name": "ok-1", "status": "enabled"},
            ],
        }
    )
    entity = TraefikRoutersCountSensor(entry)
    assert entity.extra_state_attributes["success_count"] == 1
    assert entity.extra_state_attributes["warning_count"] == 0

    # Coordinator refresh: warning shows up.
    entry.runtime_data.data = {
        "http_routers": [
            {"name": "ok-1", "status": "enabled"},
            {"name": "ok-2", "status": "warning"},
        ],
    }
    assert entity.extra_state_attributes["success_count"] == 1
    assert entity.extra_state_attributes["warning_count"] == 1
    assert entity.extra_state_attributes["success_pct"] == 50.0


def test_aggregate_breakdown_handles_missing_data() -> None:
    """Cold start: coordinator.data is None → all breakdown attrs are 0."""
    entry, _coord = _entry_with_data(None)
    entry.runtime_data.last_update_success = False
    entity = TraefikRoutersCountSensor(entry)
    attrs = entity.extra_state_attributes
    assert attrs["filtered_count"] == 0
    assert attrs["success_count"] == 0
    assert attrs["warning_count"] == 0
    assert attrs["error_count"] == 0
    assert attrs["disabled_count"] == 0
    # No non-disabled items → vacuously 100%
    assert attrs["success_pct"] == 100.0
