"""Binary sensor entities for the Traefik integration."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, cast

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from .api import filter_internal_items
from .entity import TraefikEntity
from .sensor import _cert_cache_availability
from .tls import CertError, CertInfo, is_error

if TYPE_CHECKING:
    from .cert_coordinator import CertCoordinator
    from .coordinator import TraefikConfigEntry, TraefikCoordinator

_LOGGER = logging.getLogger(__name__)

_HOST_FROM_RULE = re.compile(r"Host\(`([^`]+)`\)")


def _friendly_rule(rule: str | None) -> str | None:
    """Extract first `Host(...)` match for the extra_state_attribute hint."""
    if not rule:
        return None
    match = _HOST_FROM_RULE.search(rule)
    return match.group(1) if match else None


async def async_setup_entry(
    hass: Any,
    entry: TraefikConfigEntry,
    async_add_entities: Any,
) -> None:
    """Set up Traefik binary sensors for a config entry.

    Creates one ``TraefikRouterBinarySensor`` per user-visible Traefik HTTP
    router (CONTEXT.md D-06 / Phase 1 ROUTER-01) PLUS three aggregate
    binary sensors on the Diagnostics device (CONTEXT.md D-14/D-19):

    - ``TraefikAnyRouterFailingBinarySensor``
    - ``TraefikAnyServiceFailingBinarySensor`` (v0.2.0)
    - ``TraefikAnyMiddlewareFailingBinarySensor`` (v0.2.0)

    All aggregates are single instance per config entry — never deleted;
    if the relevant list disappears the sensor falls to OFF (no items
    failing) and stays.
    """
    coordinator: TraefikCoordinator = entry.runtime_data

    # Phase 2: read `http_routers` (was `routers` in Phase 1 — fetch_all now
    # returns the renamed key per CONTEXT.md D-04). filter_internal_items is
    # the canonical helper from api.py (replaces _filter_user_routers).
    routers = filter_internal_items(coordinator.data.get("http_routers") or [])
    router_entities = [TraefikRouterBinarySensor(entry, coordinator, router) for router in routers]
    any_failing_entity = TraefikAnyRouterFailingBinarySensor(entry, coordinator)
    # v0.2.0: parallel aggregates for services + middlewares so users get
    # the same any-X-failing alarm pattern across all three Traefik
    # categories. Single-instance (D-19), PROBLEM device_class, opt-in
    # (entity_registry_enabled_default=False, PITFALLS M-12).
    any_service_failing_entity = TraefikAnyServiceFailingBinarySensor(entry, coordinator)
    any_middleware_failing_entity = TraefikAnyMiddlewareFailingBinarySensor(entry, coordinator)
    async_add_entities(
        [
            *router_entities,
            any_failing_entity,
            any_service_failing_entity,
            any_middleware_failing_entity,
        ]
    )

    # Stale entity cleanup (CONTEXT.md D-18, gatus binary_sensor.py:49-71).
    # Routers that disappear from coordinator.data are removed from the
    # entity registry on the next refresh cycle. Aggregate entities
    # (``TraefikAnyRouterFailingBinarySensor``) are NEVER deleted
    # (CONTEXT.md D-19 — single instance per entry, category='diagnostics'
    # so its unique_id prefix differs from ``http_router_`` and is skipped
    # below).
    registry = er.async_get(hass)

    def _remove_stale_routers() -> None:
        """Drop registry entries for routers no longer in coordinator.data.

        Defensive: if the coordinator fetch failed, skip cleanup — we
        don't want a transient outage to delete every entity (PITFALLS
        "stale-state-on-network-blip").
        """
        if not coordinator.last_update_success:
            return
        current_routers: set[str] = set()
        data = coordinator.data if isinstance(coordinator.data, dict) else {}
        routers_data = data.get("http_routers") if isinstance(data, dict) else None
        if isinstance(routers_data, list):
            current_routers = {r["name"] for r in routers_data if isinstance(r, dict) and "name" in r}
        prefix = f"{entry.entry_id}_http_router_"
        for reg_entry in list(registry.entities.values()):
            unique_id = reg_entry.unique_id
            if not unique_id or not unique_id.startswith(prefix):
                continue
            router_name = unique_id.removeprefix(prefix)
            if router_name and router_name not in current_routers:
                _LOGGER.debug("Removing stale router entity: %s", reg_entry.entity_id)
                registry.async_remove(reg_entry.entity_id)

    entry.async_on_unload(coordinator.async_add_listener(_remove_stale_routers))

    # --- Phase 3 cert expiry binary sensors (TLS-02) ---
    # The cert coordinator is a sibling coordinator (PITFALLS #6 — NOT a
    # runtime_data shape migration). Defensive ``getattr`` tolerates the
    # brief window before Phase 3 wiring completes (e.g., during a partial
    # install / test harness without ``__init__.py`` Task 3 wiring).
    cert_coordinator: CertCoordinator | None = getattr(entry.runtime_data, "cert_coordinator", None)
    if cert_coordinator is not None:

        def _create_pending_cert_binary_sensor_entities() -> None:
            """Materialise one expiring binary sensor per cached cert row.

            BLOCKER #2 fix — entity creation must fire on EVERY cert
            cycle (not just on initial setup) so hosts discovered after
            the cold-start empty-cache fallback in plan 03-01 Task 3
            step 3d(iii) still get their entities registered. Mirrors
            the timestamp-sensor closure in ``sensor.py`` so the two
            platforms stay in sync — when the cert coordinator discovers
            a new host, BOTH the timestamp sensor AND the expiry binary
            sensor register on the same cycle.
            """
            cache = cert_coordinator.data
            if not isinstance(cache, dict) or not cache:
                return
            # Skip hosts that already have a registered expiring entity
            # so repeated cycle ticks are idempotent (no duplicate entities).
            existing: set[str] = {
                (reg.unique_id or "").removeprefix(f"{entry.entry_id}_tls_expiring_")
                for reg in registry.entities.values()
                if (reg.unique_id or "").startswith(f"{entry.entry_id}_tls_expiring_")
            }
            new_entities: list[TraefikCertExpiryBinarySensor] = []
            for host, cache_value in cache.items():
                host = host.lower()
                if host in existing:
                    continue
                new_entities.append(TraefikCertExpiryBinarySensor(entry, cert_coordinator, host, cache_value))
            if new_entities:
                async_add_entities(new_entities)

        def _remove_stale_cert_expiring() -> None:
            """Drop expiry-binary-sensor entities whose host is no longer probed.

            WARNING #1 fix — this listener is registered ONLY in
            ``binary_sensor.py``; the matching ``_remove_stale_cert_hosts``
            for the ``tls_cert_`` prefix lives in ``sensor.py``. No
            duplicate registration. Gate on
            ``cert_coordinator.last_update_success`` (Phase 2 D-18 pattern
            replicated verbatim) so a transient cert-cycle failure cannot
            mass-delete every TLS host entity.
            """
            if not cert_coordinator.last_update_success:
                return
            cache = cert_coordinator.data
            current: set[str] = {h.lower() for h in cache} if isinstance(cache, dict) else set()
            prefix = f"{entry.entry_id}_tls_expiring_"
            for reg_entry in list(registry.entities.values()):
                unique_id = reg_entry.unique_id or ""
                if not unique_id.startswith(prefix):
                    continue
                host = unique_id.removeprefix(prefix)
                if host and host not in current:
                    _LOGGER.debug("Removing stale cert expiring entity: %s", reg_entry.entity_id)
                    registry.async_remove(reg_entry.entity_id)

        def _on_cert_update() -> None:
            """Combined cert-cycle listener — creation + cleanup in one tick.

            Single ``async_add_listener`` registration drives both the
            BLOCKER #2 entity-creation closure (for newly-discovered
            hosts) AND the WARNING #1 stale-cleanup callback. Folding
            both into one function keeps the listener registration count
            to a minimum and ensures both paths fire on every cert cycle
            (every 6h).
            """
            _create_pending_cert_binary_sensor_entities()
            _remove_stale_cert_expiring()

        # Materialise any entities for hosts already in the cache at setup time.
        _create_pending_cert_binary_sensor_entities()
        # Register the combined listener for future cycles.
        entry.async_on_unload(cert_coordinator.async_add_listener(_on_cert_update))


class TraefikRouterBinarySensor(TraefikEntity, BinarySensorEntity):
    """One binary_sensor per Traefik HTTP router."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(
        self,
        entry: TraefikConfigEntry,
        coordinator: TraefikCoordinator,
        router: dict[str, Any],
    ) -> None:
        router_name = router["name"]
        # Phase 2: per-category device (CONTEXT.md D-01/D-02). The HTTP Routers
        # device identifier is (DOMAIN, f"{entry.entry_id}_http_routers").
        super().__init__(entry, category="http_routers", description_key=router_name)
        self._router = router
        self._attr_unique_id = f"{entry.entry_id}_http_router_{router_name}"
        # Explicit entity_id prefix per CONTEXT.md D-09/D-10.
        self.entity_id = f"binary_sensor.traefik_http_router_{slugify(router_name)}"
        self._attr_name = router_name

    @property
    def is_on(self) -> bool | None:
        status: Any = self._router.get("status") if isinstance(self._router, dict) else None
        if status is None:
            return None
        return bool(status == "enabled")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "status": self._router.get("status"),
            "rule": self._router.get("rule"),
            "friendly_rule": _friendly_rule(self._router.get("rule")),
            "service": self._router.get("service"),
            # ``name`` is the raw Traefik router identifier (CONTEXT.md D-20 /
            # ROUTER-02). Useful on dashboards even when the entity_id slug
            # mangles special characters.
            "name": self._router.get("name"),
            "router_name": self._router.get("name"),
        }

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success


class TraefikAnyRouterFailingBinarySensor(TraefikEntity, BinarySensorEntity):
    """Aggregates router health: ON when ANY router status != 'enabled'.

    Single instance per config entry — never deleted (CONTEXT.md D-19).
    Lives on the Diagnostics device alongside the reload button
    (CONTEXT.md D-14). ``entity_registry_enabled_default=False`` per
    PITFALLS M-12 so the diagnostic entity does not pollute the States
    panel by default — users opt in consciously when they want the
    "any router failing" alarm surfaced.

    Per CONTEXT.md D-14 the device class is ``PROBLEM`` so the UI shows the
    standard problem icon and groups the entity with HA's other health
    alarms. ``is_on`` is ``True`` when at least one router is anything other
    than ``enabled`` (``disabled``, ``warning``, ``error`` — matches the
    semantics used by ``TraefikRouterBinarySensor.is_on``).

    Reads the raw ``http_routers`` list (NOT ``filter_internal_items``-ed)
    so a failing Traefik-internal router like ``api@internal`` can also
    surface the alarm — internal routers are filtered from per-router
    entities (entity-id regex rejects ``@`) but the aggregate is
    internally a normal HA entity and can hold any name.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        entry: TraefikConfigEntry,
        coordinator: TraefikCoordinator,
    ) -> None:
        super().__init__(entry, category="diagnostics", description_key="any_router_failing")
        self._attr_unique_id = f"{entry.entry_id}_diagnostics_any_router_failing"
        self.entity_id = "binary_sensor.traefik_any_router_failing"
        self._attr_name = "Any router failing"

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if not isinstance(data, dict):
            return None
        routers = data.get("http_routers")
        if not isinstance(routers, list):
            # Transient gap in coordinator data — return None so HA shows
            # the entity as "unknown" rather than flipping to OFF and
            # potentially masking a real failure.
            return None
        failing = [r for r in routers if isinstance(r, dict) and r.get("status") != "enabled"]
        return bool(failing)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        routers = data.get("http_routers") if isinstance(data, dict) else None
        if not isinstance(routers, list):
            return {"failing_router_count": 0, "failing_router_names": []}
        failing = [r for r in routers if isinstance(r, dict) and r.get("status") != "enabled"]
        return {
            "failing_router_count": len(failing),
            "failing_router_names": [r.get("name") for r in failing if isinstance(r, dict)],
        }

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success


class TraefikAnyServiceFailingBinarySensor(TraefikEntity, BinarySensorEntity):
    """Aggregates HTTP service health: ON when ANY service status != 'enabled'.

    v0.2.0 mirror of ``TraefikAnyRouterFailingBinarySensor`` for services.
    Same PROBLEM device_class + ``entity_registry_enabled_default=False``
    pattern (PITFALLS M-12 + CONTEXT.md D-19). Reads the raw
    ``http_services`` list — Traefik-internal services like
    ``api@internal`` are NOT excluded here (a failing internal service
    is still operationally relevant even though it isn't surfaced as a
    per-service entity).
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        entry: TraefikConfigEntry,
        coordinator: TraefikCoordinator,
    ) -> None:
        super().__init__(entry, category="diagnostics", description_key="any_service_failing")
        self._attr_unique_id = f"{entry.entry_id}_diagnostics_any_service_failing"
        self.entity_id = "binary_sensor.traefik_any_service_failing"
        self._attr_name = "Any service failing"

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if not isinstance(data, dict):
            return None
        services = data.get("http_services")
        if not isinstance(services, list):
            return None
        failing = [s for s in services if isinstance(s, dict) and s.get("status") != "enabled"]
        return bool(failing)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        services = data.get("http_services") if isinstance(data, dict) else None
        if not isinstance(services, list):
            return {"failing_service_count": 0, "failing_service_names": []}
        failing = [s for s in services if isinstance(s, dict) and s.get("status") != "enabled"]
        return {
            "failing_service_count": len(failing),
            "failing_service_names": [s.get("name") for s in failing if isinstance(s, dict)],
        }

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success


class TraefikAnyMiddlewareFailingBinarySensor(TraefikEntity, BinarySensorEntity):
    """Aggregates HTTP middleware health: ON when ANY middleware status != 'enabled'.

    v0.2.0 mirror of ``TraefikAnyRouterFailingBinarySensor`` for
    middlewares. Middlewares are HTTP-only per Traefik's API surface, but
    they still report a ``status`` field (enabled / disabled / warning /
    error) that we aggregate here.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        entry: TraefikConfigEntry,
        coordinator: TraefikCoordinator,
    ) -> None:
        super().__init__(entry, category="diagnostics", description_key="any_middleware_failing")
        self._attr_unique_id = f"{entry.entry_id}_diagnostics_any_middleware_failing"
        self.entity_id = "binary_sensor.traefik_any_middleware_failing"
        self._attr_name = "Any middleware failing"

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if not isinstance(data, dict):
            return None
        middlewares = data.get("http_middlewares")
        if not isinstance(middlewares, list):
            return None
        failing = [m for m in middlewares if isinstance(m, dict) and m.get("status") != "enabled"]
        return bool(failing)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        middlewares = data.get("http_middlewares") if isinstance(data, dict) else None
        if not isinstance(middlewares, list):
            return {"failing_middleware_count": 0, "failing_middleware_names": []}
        failing = [m for m in middlewares if isinstance(m, dict) and m.get("status") != "enabled"]
        return {
            "failing_middleware_count": len(failing),
            "failing_middleware_names": [m.get("name") for m in failing if isinstance(m, dict)],
        }

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success


class TraefikCertExpiryBinarySensor(TraefikEntity, BinarySensorEntity):
    """One ``PROBLEM`` binary sensor per TLS-probed hostname (TLS-02).

    State is ``True`` when ``days_until_expiry <= threshold_days``
    (CONTEXT.md D-03 — signed-int semantics so already-expired certs
    surface as ``True``). The threshold is read live from
    ``cert_coordinator.threshold_days`` so a user Options change
    (``tls_warn_days``) flips the binary state within ~1s without
    requiring a re-handshake — the Options listener calls
    ``cert_coordinator.async_set_threshold`` which calls
    ``async_update_listeners`` (plan 03-01 Task 3 step 3e).

    CONTEXT.md D-03 explicitly diverges from Phase 2's M-12 default:
    ``_attr_entity_registry_enabled_default = True`` so the cert-expiry
    alarm is visible by default on a brand-new install. The user can
    still opt out per-entity via Settings → Devices & Services. The
    cert entity lives on the new "HTTP Routers TLS" device — the
    always-on default does NOT enable entities on any other device.

    CONTEXT.md PITFALLS M-12 — this divergence is intentional: the user
    wants the cert alarm always visible because cert expiry is a
    security-impacting event that should not require opt-in. The
    Phase 2 ``TraefikAnyRouterFailingBinarySensor`` keeps the
    ``entity_registry_enabled_default = False`` default because the
    router-failure aggregate is a noisier "any router is non-enabled"
    alarm that often reflects deployment churn (not a real outage).

    ``available`` DELEGATES to the shared
    ``sensor.py._cert_cache_availability`` helper (SUGGESTION #1 fix —
    single source of truth for cache availability across both
    platforms; no per-platform drift). The cert-expiring entity and
    the paired ``TraefikCertTimestampSensor`` for the same host will
    therefore flip to unavailable together, never out of sync.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    # D-03: ALWAYS ON by default — cert expiry is a security-impacting
    # alarm. See class docstring for the divergence rationale from M-12.
    _attr_entity_registry_enabled_default = True
    # ``mdi:lock-alert`` matches the cert-security semantic; distinct
    # from the timestamp sensor's ``mdi:certificate`` icon — the
    # binary_sensor is the alarm, the timestamp is informational.
    _attr_icon = "mdi:lock-alert"

    def __init__(
        self,
        entry: TraefikConfigEntry,
        coordinator: CertCoordinator,
        host: str,
        info: CertInfo | CertError | None,
    ) -> None:
        # Defensive lowercase normalisation (the cert coordinator
        # already lowercases, but a cache row populated from a test
        # harness could carry mixed casing — see threat model).
        host = host.lower()
        # Distinct ``description_key`` from the timestamp sensor
        # (``<host>_expiring`` vs ``<host>``) so the binary_sensor's
        # name in the States panel is uniquely identifiable.
        super().__init__(entry, category="http_routers_tls", description_key=f"{host}_expiring")
        self._host = host
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_tls_expiring_{host}"
        # Explicit entity_id prefix per CONTEXT.md D-09 — the
        # ``traefik_<slug>_expiring`` shape is the user-facing identity.
        self.entity_id = f"binary_sensor.traefik_{slugify(host)}_expiring"
        self._attr_name = f"{host} certificate expiring"

    @property
    def is_on(self) -> bool | None:
        """``True`` when ``days_until_expiry <= threshold_days`` (signed).

        CONTEXT.md D-03 — negative ``days_until_expiry`` (already-expired
        certs) is ``<= threshold`` for any reasonable threshold so the
        entity surfaces the breach as ``True``. ``None`` (unknown) is
        returned when the cache row is absent or a ``CertError`` — the
        ``available`` property drives the unavailable/unknown distinction.
        """
        cache = self._coordinator.data.get(self._host) if isinstance(self._coordinator.data, dict) else None
        if cache is None or is_error(cache):
            return None
        # Type narrowing: ``is_error`` returned False so ``cache`` is a
        # ``CertInfo`` dataclass.
        info = cast("CertInfo", cache)
        return info.days_until_expiry <= self._coordinator.threshold_days

    @property
    def available(self) -> bool:
        """Delegate to the shared ``sensor.py._cert_cache_availability`` helper.

        SUGGESTION #1 fix — both platforms consult this same function
        so the timestamp sensor and the paired expiry binary sensor can
        never disagree about whether a host's cache row is usable.
        """
        return _cert_cache_availability(self._coordinator, self._host)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Always-on diagnostic attrs (CONTEXT.md D-04 / D-08).

        ``days_until_expiry``, ``threshold_days``, ``not_after``,
        ``san_mismatch``, ``host``, ``fetched_at`` are always present
        (with ``None`` on the error path) so dashboards consistently
        show the comparison surface even when the cert probe failed.
        """
        cache = self._coordinator.data.get(self._host) if isinstance(self._coordinator.data, dict) else None
        threshold = self._coordinator.get_threshold()
        if cache is None or is_error(cache):
            # Error path — surface the cached error verbatim.
            err: CertError | None = cast("CertError", cache) if cache is not None and is_error(cache) else None
            return {
                "days_until_expiry": None,
                "threshold_days": threshold,
                "not_after": None,
                "last_error": err.get("error") if err else None,
                "san_mismatch": None,
                "host": self._host,
                "fetched_at": None,
            }
        # CertInfo path — full attribute surface. Cast documents the
        # post-is_error narrowing for mypy --strict.
        info = cast("CertInfo", cache)
        return {
            "days_until_expiry": info.days_until_expiry,
            "threshold_days": threshold,
            "not_after": info.not_after.isoformat(),
            "last_error": None,
            "san_mismatch": info.san_mismatch,
            "host": self._host,
            "fetched_at": info.fetched_at.isoformat(),
        }
