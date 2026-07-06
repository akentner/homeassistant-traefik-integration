"""Sensor entities for the Traefik integration.

Phase 2 plan 02-03 fills this out with `TraefikEntrypointSensor`,
`TraefikServiceSensor`, and the three aggregate count sensors on the Overview
device (CONTEXT.md D-15/D-16/D-17).

Entity count per config entry (after `async_setup_entry` returns):
- 1 per `TraefikEntrypointSensor` per Traefik entrypoint (HTTP Entrypoints device)
- 1 per `TraefikServiceSensor` per Traefik service (HTTP Services device;
  provider internal `api@internal` is filtered out via `filter_internal_items`)
- 1 each of `TraefikRoutersCountSensor`, `TraefikServicesCountSensor`,
  `TraefikMiddlewaresCountSensor` on the Overview device
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar, Final, cast

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from .api import filter_internal_items
from .entity import TraefikEntity
from .tls import CertError, CertInfo, is_error

if TYPE_CHECKING:
    from .cert_coordinator import CertCoordinator
    from .coordinator import TraefikConfigEntry, TraefikCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: Any,
    entry: TraefikConfigEntry,
    async_add_entities: Any,
) -> None:
    """Set up Traefik sensor entities for a config entry.

    Mirrors the `binary_sensor.py` pattern (Phase 1 — coordinator from
    `entry.runtime_data`, single `async_add_entities(...)` call with all
    entities for this platform). Coordinator cycle may surface partial data
    (any key missing -> empty list) so we defensively coerce via the
    ``_dict_or_empty`` / ``_list_or_empty`` helpers below.
    """
    coordinator: TraefikCoordinator = entry.runtime_data
    data = _dict_or_empty(coordinator.data)

    # Per-entrypoint sensors (CONTEXT.md D-15).
    # Entrypoints are NOT filtered via filter_internal_items — Traefik entrypoint
    # names like `websecure@internal` are first-class configuration objects the
    # user expects to see.
    entrypoints: list[dict[str, Any]] = _list_or_empty(data.get("entrypoints"))
    entrypoint_entities = [TraefikEntrypointSensor(entry, coordinator, ep) for ep in entrypoints]

    # Per-service sensors (CONTEXT.md D-16). Filter `@<provider>` internal items
    # via the canonical helper in api.py (CONTEXT.md D-06).
    services = filter_internal_items(_list_or_empty(data.get("http_services")))
    service_entities = [TraefikServiceSensor(entry, coordinator, svc) for svc in services]

    # Aggregate counters on the Overview device (CONTEXT.md D-17). The
    # sensors compute their state and breakdown attributes lazily from
    # ``coordinator.data`` on every property access (see
    # ``_TraefikAggregateCountSensor`` docstring for the v0.1.4 fix) —
    # no per-construction count kwargs needed.
    aggregate_entities = [
        TraefikRoutersCountSensor(entry),
        TraefikServicesCountSensor(entry),
        TraefikMiddlewaresCountSensor(entry),
    ]

    async_add_entities(entrypoint_entities + service_entities + aggregate_entities)

    # --- Phase 3 cert timestamp sensors (TLS-01) ---
    # The cert coordinator is a sibling coordinator (PITFALLS #6 — NOT a
    # runtime_data shape migration). Defensive ``getattr`` tolerates the
    # brief window before Phase 3 wiring completes (e.g., during a partial
    # install / test harness without ``__init__.py`` Task 3 wiring).
    cert_coordinator: CertCoordinator | None = getattr(entry.runtime_data, "cert_coordinator", None)
    if cert_coordinator is not None:
        registry = er.async_get(hass)

        def _create_pending_cert_sensor_entities() -> None:
            """Materialise one timestamp sensor per cached ``CertInfo`` row.

            BLOCKER #2 fix — entity creation must fire on EVERY cert cycle
            (not just on initial setup) so hosts discovered after the
            cold-start empty-cache fallback in plan 03-01 Task 3 step
            3d(iii) still get their entities registered. The cert cache is
            populated asynchronously, so the first ``async_setup_entry``
            call may see an empty cache (zero entities registered — correct)
            and the next 6h cycle will fill the cache and re-trigger this
            closure to add the missing entities.
            """
            cache = cert_coordinator.data
            if not isinstance(cache, dict) or not cache:
                return
            # Skip hosts that already have a registered cert entity so
            # repeated cycle ticks are idempotent (no duplicate entities).
            existing: set[str] = {
                (reg.unique_id or "").removeprefix(f"{entry.entry_id}_tls_cert_")
                for reg in registry.entities.values()
                if (reg.unique_id or "").startswith(f"{entry.entry_id}_tls_cert_")
            }
            new_entities: list[TraefikCertTimestampSensor] = []
            for host, cache_value in cache.items():
                host = host.lower()
                if host in existing:
                    continue
                # Only timestamp sensors go on ``CertInfo`` rows. Error
                # hosts get a ``binary_sensor`` only (per D-03 — the
                # timestamp sensor makes no sense without a ``not_after``).
                if is_error(cache_value):
                    continue
                # Type narrowing: ``is_error`` returned False so
                # ``cache_value`` is a ``CertInfo`` dataclass.
                info: CertInfo = cache_value  # type: ignore[assignment]
                new_entities.append(TraefikCertTimestampSensor(entry, cert_coordinator, host, info))
            if new_entities:
                async_add_entities(new_entities)

        def _remove_stale_cert_hosts() -> None:
            """Drop timestamp-sensor entities whose host is no longer probed.

            WARNING #1 fix — this listener is registered ONLY in
            ``sensor.py``; the matching ``_remove_stale_cert_expiring``
            for the ``tls_expiring_`` prefix lives in ``binary_sensor.py``.
            No duplicate registration. Gate on
            ``cert_coordinator.last_update_success`` (Phase 2 D-18 pattern
            replicated verbatim) so a transient cert-cycle failure cannot
            mass-delete every TLS host entity.
            """
            if not cert_coordinator.last_update_success:
                return
            cache = cert_coordinator.data
            current: set[str] = {h.lower() for h in cache} if isinstance(cache, dict) else set()
            prefix = f"{entry.entry_id}_tls_cert_"
            for reg_entry in list(registry.entities.values()):
                unique_id = reg_entry.unique_id or ""
                if not unique_id.startswith(prefix):
                    continue
                host = unique_id.removeprefix(prefix)
                if host and host not in current:
                    _LOGGER.debug("Removing stale cert timestamp entity: %s", reg_entry.entity_id)
                    registry.async_remove(reg_entry.entity_id)

        def _cert_update_listener() -> None:
            """Combined cert-cycle listener — creation + cleanup in one tick.

            A single ``async_add_listener`` registration drives both the
            BLOCKER #2 entity-creation closure (for newly-discovered hosts)
            AND the WARNING #1 stale-cleanup callback. Folding both into
            one function keeps the listener registration count to a minimum
            and ensures both paths fire on every cert cycle (every 6h).
            """
            _create_pending_cert_sensor_entities()
            _remove_stale_cert_hosts()

        # Materialise any entities for hosts already in the cache at setup time.
        _create_pending_cert_sensor_entities()
        # Register the combined listener for future cycles.
        entry.async_on_unload(cert_coordinator.async_add_listener(_cert_update_listener))

    # Stale entity cleanup (CONTEXT.md D-18, gatus binary_sensor.py:49-71).
    # Entrypoints + services that disappear from coordinator.data are removed
    # from the entity registry on the next refresh cycle. Aggregate counters
    # on the Overview device (CONTEXT.md D-19) are NEVER deleted — their
    # unique_id prefix is ``_overview_`` and is skipped below.
    registry = er.async_get(hass)

    def _remove_stale_entrypoints() -> None:
        if not coordinator.last_update_success:
            return
        current: set[str] = set()
        data = coordinator.data if isinstance(coordinator.data, dict) else {}
        eps = data.get("entrypoints") if isinstance(data, dict) else None
        if isinstance(eps, list):
            current = {ep["name"] for ep in eps if isinstance(ep, dict) and "name" in ep}
        prefix = f"{entry.entry_id}_http_entrypoint_"
        for reg_entry in list(registry.entities.values()):
            unique_id = reg_entry.unique_id or ""
            if not unique_id.startswith(prefix):
                continue
            ep_name = unique_id.removeprefix(prefix)
            if ep_name and ep_name not in current:
                _LOGGER.debug("Removing stale entrypoint entity: %s", reg_entry.entity_id)
                registry.async_remove(reg_entry.entity_id)

    def _remove_stale_services() -> None:
        if not coordinator.last_update_success:
            return
        current: set[str] = set()
        data = coordinator.data if isinstance(coordinator.data, dict) else {}
        svcs = data.get("http_services") if isinstance(data, dict) else None
        if isinstance(svcs, list):
            current = {s["name"] for s in filter_internal_items([item for item in svcs if isinstance(item, dict)])}
        prefix = f"{entry.entry_id}_http_service_"
        for reg_entry in list(registry.entities.values()):
            unique_id = reg_entry.unique_id or ""
            if not unique_id.startswith(prefix):
                continue
            svc_name = unique_id.removeprefix(prefix)
            if svc_name and svc_name not in current:
                _LOGGER.debug("Removing stale service entity: %s", reg_entry.entity_id)
                registry.async_remove(reg_entry.entity_id)

    entry.async_on_unload(coordinator.async_add_listener(_remove_stale_entrypoints))
    entry.async_on_unload(coordinator.async_add_listener(_remove_stale_services))


def _dict_or_empty(value: Any) -> dict[str, Any]:
    """Coerce an arbitrary value to ``dict[str, Any]`` (``{}`` on absence/bad type).

    Used to consume ``TraefikData`` TypedDict (total=False) keys safely
    under ``mypy --strict`` — every key is ``None`` at the type level, so
    chaining ``.get(...)`` on the raw value triggers ``union-attr`` errors.
    """
    return value if isinstance(value, dict) else {}


def _list_or_empty(value: Any) -> list[dict[str, Any]]:
    """Coerce an arbitrary value to ``list[dict[str, Any]]`` (``[]`` on absence/bad type).

    Mirrors ``_dict_or_empty`` for list-shaped TypedDict keys.
    """
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _safe_int(value: Any) -> int:
    """Coerce a possibly-missing overview counter to int (0 on absence/bad type)."""
    if isinstance(value, bool):
        # bool is a subclass of int — treat as 0 to avoid True → 1 in counts.
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    return 0


# Map Traefik's API `status` strings onto the dashboard buckets used in
# ``custom:modern-circular-gauge`` cards. Traefik v3 returns
# ``enabled | disabled | warning | error``; we surface those as
# ``success | disabled | warning | error`` (matching Traefik's dashboard
# pie-chart slice labels). Items whose status is missing or unmapped are
# silently skipped — they don't show up in any bucket (avoids polluting
# the count when Traefik adds a new status class in a future version).
_STATUS_TO_BUCKET: Final[dict[str, str]] = {
    "enabled": "success",
    "warning": "warning",
    "error": "error",
    "disabled": "disabled",
}


def _count_by_status(
    items: list[dict[str, Any]],
    *,
    name_key: str = "name",
    status_key: str = "status",
) -> dict[str, int]:
    """Count ``items`` grouped by their ``status_key`` mapped to bucket labels.

    Items missing ``status_key`` or with an unmapped status value are
    silently dropped (no bucket for them). Items that aren't dicts are
    also dropped. The caller is responsible for ``filter_internal_items``
    BEFORE this helper — we count whatever we receive.

    :return: ``{"success": N, "warning": N, "error": N, "disabled": N}``
        with all four keys always present (zero when no matches).
    """
    counts: dict[str, int] = {
        "success": 0,
        "warning": 0,
        "error": 0,
        "disabled": 0,
    }
    for item in items:
        if not isinstance(item, dict):
            continue
        bucket = _STATUS_TO_BUCKET.get(str(item.get(status_key, "")), None)
        if bucket is not None:
            counts[bucket] += 1
    return counts


class TraefikEntrypointSensor(TraefikEntity, SensorEntity):
    """One sensor per Traefik HTTP entrypoint (CONTEXT.md D-15).

    State = ``entrypoint["address"]`` (e.g., ``":443"``); attributes expose the
    raw ``name`` and ``transport`` so dashboards can group / colour by protocol
    without re-fetching from Traefik. Entrypoints are config-time constructs
    in Traefik's API — runtime request counts live in ``/api/overview`` or
    ``/metrics`` (deferred to v2 per CONTEXT.md deferred section).
    """

    def __init__(
        self,
        entry: TraefikConfigEntry,
        coordinator: TraefikCoordinator,
        entrypoint: dict[str, Any],
    ) -> None:
        entrypoint_name = entrypoint.get("name") or "unknown"
        super().__init__(entry, category="http_entrypoints", description_key=entrypoint_name)
        self._entrypoint = entrypoint
        self._attr_unique_id = f"{entry.entry_id}_http_entrypoint_{entrypoint_name}"
        self.entity_id = f"sensor.traefik_http_entrypoint_{slugify(entrypoint_name)}"
        self._attr_name = entrypoint_name

    @property
    def native_value(self) -> str:
        """Return the listening address (e.g., ``":443"``).

        Kept raw including the leading ``:`` — this matches Traefik's own
        display format and avoids ambiguity when the bind host is included
        (``"0.0.0.0:443"`` vs ``":443"``).
        """
        address = self._entrypoint.get("address") if isinstance(self._entrypoint, dict) else None
        return str(address) if address is not None else "unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ep = self._entrypoint if isinstance(self._entrypoint, dict) else {}
        return {
            "name": ep.get("name"),
            "address": ep.get("address"),
            "transport": ep.get("transport"),
            # ``entrypoint_name`` mirrors ``name`` for consistency with the
            # ``router_name`` alias on TraefikRouterBinarySensor (Phase 1
            # ROUTER-02 contract pin).
            "entrypoint_name": ep.get("name"),
        }

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success


class TraefikServiceSensor(TraefikEntity, SensorEntity):
    """One sensor per Traefik HTTP service (CONTEXT.md D-16).

    State = ``loadbalancer.status`` when present, else ``service.status``.
    Attributes expose the ``type``, the ``server_count`` from
    ``loadbalancer.servers[]`` (omitted when no loadbalancer is configured —
    e.g., redirect services), and the raw servers list so dashboards can
    surface backend health.
    """

    def __init__(
        self,
        entry: TraefikConfigEntry,
        coordinator: TraefikCoordinator,
        service: dict[str, Any],
    ) -> None:
        service_name = service.get("name") or "unknown"
        super().__init__(entry, category="http_services", description_key=service_name)
        self._service = service
        self._attr_unique_id = f"{entry.entry_id}_http_service_{service_name}"
        self.entity_id = f"sensor.traefik_http_service_{slugify(service_name)}"
        self._attr_name = service_name

    @property
    def native_value(self) -> str:
        """Return ``loadbalancer.status`` (preferred), else ``service.status``."""
        svc = self._service if isinstance(self._service, dict) else {}
        loadbalancer = svc.get("loadbalancer") if isinstance(svc.get("loadbalancer"), dict) else None
        if loadbalancer is not None:
            status = loadbalancer.get("status")
            if status is not None:
                return str(status)
        # Fall back to the service-level status (Traefik also exposes this).
        status = svc.get("status")
        return str(status) if status is not None else "unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        svc = self._service if isinstance(self._service, dict) else {}
        loadbalancer = svc.get("loadbalancer") if isinstance(svc.get("loadbalancer"), dict) else None
        if loadbalancer is not None:
            servers = loadbalancer.get("servers")
            server_count = len(servers) if isinstance(servers, list) else 0
        else:
            servers = None
            server_count = 0
        return {
            "name": svc.get("name"),
            "status": svc.get("status"),
            "type": svc.get("type"),
            "server_count": server_count,
            # ``servers`` is the raw backend list (URL + optional healthcheck
            # status when healthcheck is configured). Absent when no
            # loadbalancer is configured (e.g., redirect services).
            "servers": servers,
            "service_name": svc.get("name"),
        }

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success


class _TraefikAggregateCountSensor(TraefikEntity, SensorEntity):
    """Shared base for the three Overview device aggregate counters.

    State and breakdown attributes are **properties** that recompute from
    ``coordinator.data`` on every access. Coordinator refreshes fire
    ``async_write_ha_state`` which causes HA to re-read these properties,
    so the displayed value reflects Traefik's current roster — not
    whatever the first fetch happened to return.

    v0.1.4 fix: the previous implementation captured ``filtered_count``
    in ``self._attr_native_value`` at ``__init__`` time and never updated
    it. After the very first coordinator cycle, the value froze — counts
    of the freshly-bootstrapped Traefik with empty router lists would
    read "0" forever even after routers were configured.

    ``state_class=MEASUREMENT`` so HA can graph trends over time — users
    may want to chart how many routers / services / middlewares are
    configured across deploys.

    Subclasses override:

    - ``_DATA_KEY`` — coordinator.data key to count and filter
      (e.g. ``"http_routers"``)
    - ``_OVERVIEW_KEY`` — sub-key under each ``/api/overview`` transport
      block (e.g. ``"routers"`` for routers / services, ``None`` for
      middlewares which have no transport breakdown).
    - ``_HAS_OVERVIEW_BREAKDOWN`` — ``False`` for middlewares.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT

    _DATA_KEY: ClassVar[str]
    _OVERVIEW_KEY: ClassVar[str]
    _HAS_OVERVIEW_BREAKDOWN: ClassVar[bool] = True

    def __init__(
        self,
        entry: TraefikConfigEntry,
        *,
        unique_id: str,
        entity_id: str,
        name: str,
    ) -> None:
        super().__init__(entry, category="overview", description_key=unique_id)
        self._attr_unique_id = unique_id
        self.entity_id = entity_id
        self._attr_name = name

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def native_value(self) -> int:
        """Filtered count of ``_DATA_KEY`` from the most recent coordinator data.

        Returns 0 on cold start (``coordinator.data`` is None) or when
        the key is missing from the response.
        """
        data = self.coordinator.data
        if not isinstance(data, dict):
            return 0
        return len(filter_internal_items(_list_or_empty(data.get(self._DATA_KEY))))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Breakdown counts per transport (HTTP / TCP / UDP) from ``/api/overview``.

        Empty breakdown (``_HAS_OVERVIEW_BREAKDOWN = False``) returns
        just ``{"filtered_count": ...}`` — middlewares are HTTP-only per
        Traefik's API surface.

        v0.2.0 additions: per-bucket counts (success / warning / error /
        disabled) and a derived ``success_pct`` for dashboard pies /
        gauges. Reads from ``coordinator.data`` on every call so the
        counts refresh on each coordinator cycle (same pattern as
        ``native_value`` — see v0.1.4 fix).
        """
        attrs: dict[str, Any] = {"filtered_count": self.native_value}
        # Status breakdown (success/warning/error/disabled) — applied to
        # both routers+services (which have _HAS_OVERVIEW_BREAKDOWN=True)
        # and middlewares (False). The breakdown reads the per-item status
        # directly from /api/http/{routers,services,middlewares}, which is
        # independent of the /api/overview breakdown above.
        data = self.coordinator.data if isinstance(self.coordinator.data, dict) else {}
        items = filter_internal_items(_list_or_empty(data.get(self._DATA_KEY)))
        breakdown = _count_by_status(items)
        attrs.update({f"{bucket}_count": count for bucket, count in breakdown.items()})
        attrs["status_breakdown"] = breakdown
        # success_pct = success / (success + warning + error). Excludes
        # disabled (admin has explicitly turned it off — not a quality
        # signal). Clamped to [0.0, 100.0]. 100.0 if no non-disabled items
        # at all.
        non_disabled = breakdown["success"] + breakdown["warning"] + breakdown["error"]
        pct = breakdown["success"] / non_disabled * 100.0 if non_disabled > 0 else 100.0
        attrs["success_pct"] = max(0.0, min(100.0, pct))

        if not self._HAS_OVERVIEW_BREAKDOWN:
            return attrs
        overview = _dict_or_empty(data.get("overview")) if isinstance(data, dict) else {}
        attrs["http_count"] = _safe_int(_dict_or_empty(overview.get("http")).get(self._OVERVIEW_KEY))
        attrs["tcp_count"] = _safe_int(_dict_or_empty(overview.get("tcp")).get(self._OVERVIEW_KEY))
        attrs["udp_count"] = _safe_int(_dict_or_empty(overview.get("udp")).get(self._OVERVIEW_KEY))
        return attrs


class TraefikRoutersCountSensor(_TraefikAggregateCountSensor):
    """Total Traefik routers — filtered count (CONTEXT.md D-17).

    State = ``len(filter_internal_items(coordinator.data["http_routers"]))``;
    attributes break down by transport (HTTP / TCP / UDP) from ``/api/overview``.
    """

    _DATA_KEY = "http_routers"
    _OVERVIEW_KEY = "routers"

    def __init__(self, entry: TraefikConfigEntry) -> None:
        super().__init__(
            entry,
            unique_id=f"{entry.entry_id}_overview_routers_count",
            entity_id="sensor.traefik_routers",
            name="Routers",
        )


class TraefikServicesCountSensor(_TraefikAggregateCountSensor):
    """Total Traefik services — filtered count (CONTEXT.md D-17)."""

    _DATA_KEY = "http_services"
    _OVERVIEW_KEY = "services"

    def __init__(self, entry: TraefikConfigEntry) -> None:
        super().__init__(
            entry,
            unique_id=f"{entry.entry_id}_overview_services_count",
            entity_id="sensor.traefik_services",
            name="Services",
        )


class TraefikMiddlewaresCountSensor(_TraefikAggregateCountSensor):
    """Total Traefik middlewares — filtered count (CONTEXT.md D-17).

    Middlewares are HTTP-only per Traefik's API surface (no TCP/UDP
    middleware concept), so the breakdown is just the filtered count.
    """

    _DATA_KEY = "http_middlewares"
    _OVERVIEW_KEY = ""  # unused — overridden by _HAS_OVERVIEW_BREAKDOWN = False
    _HAS_OVERVIEW_BREAKDOWN = False

    def __init__(self, entry: TraefikConfigEntry) -> None:
        super().__init__(
            entry,
            unique_id=f"{entry.entry_id}_overview_middlewares_count",
            entity_id="sensor.traefik_middlewares",
            name="Middlewares",
        )


class TraefikCertTimestampSensor(TraefikEntity, SensorEntity):
    """One timestamp sensor per TLS-probed hostname (TLS-01).

    Surfaces the leaf cert's ``notAfter`` datetime via HA's standard
    ``SensorDeviceClass.TIMESTAMP`` device class — the timestamp renders
    as a human-readable date in the States panel and is exposed as a
    ``datetime`` to automations so they can compare against
    ``now() + timedelta(days=14)`` without parsing strings.

    The entity reads from the sibling ``CertCoordinator`` cache (NOT the
    main coordinator — Phase 3's sibling coordinator pattern keeps the
    6h TLS-handshake cadence decoupled from the 15s main coordinator).
    Hosts are deduplicated by the cert coordinator's
    ``_collect_hosts_from_main_coordinator`` (union of
    ``tls.domains[].main`` + ``tls.domains[].sans[]`` + ``Host(...)`` rule
    matches); each unique hostname gets exactly one timestamp sensor on
    the new "HTTP Routers TLS" device (CONTEXT.md D-02).

    CONTEXT.md D-04 / D-08: ``days_until_expiry`` is ALWAYS exposed on
    the entity's ``extra_state_attributes`` — even when the cert probe
    failed and the entity is unavailable — so dashboards consistently
    show the countdown attribute. The same field is mirrored on the
    paired ``TraefikCertExpiryBinarySensor`` for the ``is_on`` threshold
    comparison; both entities read from the SAME cache row so they
    never disagree about the underlying cert state.

    The ``san_mismatch`` attribute (spike 006) is surfaced verbatim so
    dashboards can flag a router whose probe hostname is not strictly
    covered by the cert's SAN entries (Traefik may be serving a default
    or wildcard cert — useful diagnostic, not a failure).
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    # TIMESTAMP device class uses state_class=None by convention — HA
    # doesn't accumulate statistics on a future datetime. MEASUREMENT
    # would force every renderer to treat the cert as a gauge.
    _attr_state_class = None
    # ``mdi:certificate`` matches the TLS-certificate semantic of the
    # entity cluster. Distinct from the ``mdi:lock-alert`` used by the
    # paired binary_sensor — the timestamp sensor is informational, the
    # binary_sensor is the alarm.
    _attr_icon = "mdi:certificate"

    def __init__(
        self,
        entry: TraefikConfigEntry,
        coordinator: CertCoordinator,
        host: str,
        info: CertInfo,
    ) -> None:
        # Defensive lowercase normalisation (the cert coordinator
        # already lowercases, but a cache row populated from a test
        # harness could carry mixed casing — see threat model).
        host = host.lower()
        super().__init__(entry, category="http_routers_tls", description_key=host)
        self._host = host
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_tls_cert_{host}"
        # Explicit entity_id prefix per CONTEXT.md D-09 — the
        # ``traefik_<slug>_cert`` shape is the user-facing identity.
        self.entity_id = f"sensor.traefik_{slugify(host)}_cert"
        self._attr_name = f"{host} certificate"

    @property
    def native_value(self) -> datetime | None:
        """Return ``not_after`` UTC datetime (or ``None`` when probe failed).

        ``None`` rather than a sentinel so HA renders "unknown" instead
        of a misleading past date — the ``available`` property below
        drives the unavailable/unknown distinction; ``native_value`` is
        the authoritative timestamp render.
        """
        cache = self._coordinator.data.get(self._host) if isinstance(self._coordinator.data, dict) else None
        if cache is None or is_error(cache):
            return None
        # Type narrowing: ``is_error`` returned False so ``cache`` is a
        # ``CertInfo`` dataclass. The cast documents the post-check state
        # for mypy --strict (the ``is_error`` predicate isn't a
        # TypeGuard-aware assignment target here because the dict.get
        # path widened the union).
        info = cast("CertInfo", cache)
        return info.not_after

    @property
    def available(self) -> bool:
        """Delegate to the shared ``_cert_cache_availability`` helper.

        SUGGESTION #1 fix — the helper is the single source of truth for
        cache availability so the timestamp sensor can never show
        "unavailable" while the paired ``TraefikCertExpiryBinarySensor``
        for the same host still shows a stale "ON". Both platforms
        consult this same function; the alternative — a duplicate
        per-platform helper — would inevitably drift out of sync.
        """
        return _cert_cache_availability(self._coordinator, self._host)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Diagnostic attrs (CONTEXT.md D-04 — always present, even on error)."""
        cache = self._coordinator.data.get(self._host) if isinstance(self._coordinator.data, dict) else None
        if cache is None or is_error(cache):
            # Error path: surface the cached error verbatim; ``days_until_expiry``
            # stays None so dashboards consistently see the attribute.
            err: CertError | None = cast("CertError", cache) if cache is not None and is_error(cache) else None
            return {
                "days_until_expiry": None,
                "subject": None,
                "issuer": None,
                "san": None,
                "san_mismatch": None,
                "host": self._host,
                "port": err.get("port") if err else None,
                "fetched_at": None,
                "last_error": err.get("error") if err else None,
            }
        # CertInfo path — full attribute surface. Cast documents the
        # post-is_error narrowing for mypy --strict.
        info = cast("CertInfo", cache)
        san_sorted: tuple[str, ...] = tuple(sorted(info.san))
        return {
            "days_until_expiry": info.days_until_expiry,
            "subject": info.subject,
            "issuer": info.issuer,
            "san": san_sorted,
            "san_mismatch": info.san_mismatch,
            "host": self._host,
            "port": info.port,
            "fetched_at": info.fetched_at.isoformat(),
            "last_error": None,
        }


def _cert_cache_availability(coordinator: CertCoordinator, host: str) -> bool:
    """Single-source-of-truth helper: is the cert cache row for ``host`` usable?

    SUGGESTION #1 fix — this helper is defined in ``sensor.py`` and
    imported by ``binary_sensor.py`` so both platforms consult the
    EXACT same availability semantics. Returns ``False`` when:

    1. The coordinator's last cycle failed (``last_update_success`` is
       ``False``) — the cache is stale and we don't trust it.
    2. The cache is missing/empty (defensive — the brief window before
       the first cycle completes).
    3. The cache row for ``host`` is absent.
    4. The cache row is a ``CertError`` (probe failed) — the cert data
       is not present, the entity should be unavailable so the user
       sees "unavailable" instead of a stale timestamp.

    The paired ``TraefikCertExpiryBinarySensor`` imports this helper
    directly (``from .sensor import _cert_cache_availability``) — NO
    duplicate helper exists in ``binary_sensor.py``.
    """
    if not coordinator.last_update_success:
        return False
    cache = coordinator.data
    if not isinstance(cache, dict):
        return False
    row = cache.get(host)
    if row is None:
        return False
    return not is_error(row)


# Public alias for the shared helper (SUGGESTION #1 — keep the underscore
# form canonical for the per-platform imports, but expose a public name
# for tests / future cross-module callers that prefer the non-prefixed
# import). The two names point at the SAME function object; this is
# deliberately NOT a re-export from ``__all__`` — the alias is for
# consumers that want a non-private name without renaming the
# implementation.
cert_cache_availability = _cert_cache_availability
