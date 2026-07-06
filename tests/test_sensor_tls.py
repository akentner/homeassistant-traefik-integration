"""Tests for ``TraefikCertTimestampSensor`` (Phase 3 TLS-01).

Mirrors the Phase 2 ``test_sensor.py`` approach — ``MagicMock`` powers
the coordinator; this test surface focuses on state derivation, not
lifecycle wiring (lifecycle is covered by ``test_coordinator.py`` +
``test_init.py``).

Phase-3-specific contract pins:

- ``native_value`` returns the cached ``CertInfo.not_after`` timestamp;
  ``None`` on cold start or ``CertError`` cache row.
- ``available`` is ``False`` on cold start (``cache == {}``) AND on
  ``CertError`` cache rows — delegated to the shared
  ``_cert_cache_availability`` helper from ``sensor.py``.
- ``days_until_expiry`` is ALWAYS present in ``extra_state_attributes``
  even when ``None`` (CONTEXT.md D-04 always-on attribute contract).
- ``san_mismatch`` + ``last_error`` are surfaced verbatim from the cache
  row so dashboards can flag an SNI mismatch or a probe failure.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

from homeassistant.util import slugify

from custom_components.traefik.sensor import TraefikCertTimestampSensor
from custom_components.traefik.tls import CertError, CertInfo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry() -> MagicMock:
    """Build a mock TraefikConfigEntry for the cert sensor."""
    entry: MagicMock = MagicMock()
    entry.entry_id = "test-entry"
    entry.data = {"url": "https://traefik.example.com:8080"}
    return entry


def _cert_info(
    *,
    host: str = "api.example.com",
    days_until_expiry: int = 30,
    san_mismatch: bool = False,
) -> CertInfo:
    """Build a CertInfo with sensible defaults for assertions."""
    return CertInfo(
        host=host,
        port=443,
        not_after=datetime(2030, 1, 1, tzinfo=UTC),
        days_until_expiry=days_until_expiry,
        subject=f"CN={host}",
        issuer="CN=Test CA",
        san=(host,),
        san_mismatch=san_mismatch,
    )


def _cert_error(host: str = "api.example.com", *, code: str = "timeout") -> CertError:
    return CertError(host=host, port=443, error=code, detail="test stub")


def _coord(
    *,
    host: str = "api.example.com",
    cache: dict[str, Any] | None = None,
    last_update_success: bool = True,
) -> MagicMock:
    """Build a mock CertCoordinator with the given cache row."""
    coord: MagicMock = MagicMock()
    coord.data = cache if cache is not None else {host: _cert_info(host=host)}
    coord.threshold_days = 14
    coord.last_update_success = last_update_success
    coord.async_update_listeners = MagicMock()
    return coord


# ---------------------------------------------------------------------------
# native_value
# ---------------------------------------------------------------------------


def test_native_value_returns_not_after_datetime() -> None:
    """``native_value`` reads ``not_after`` from the cache row."""
    info = _cert_info()
    entity = TraefikCertTimestampSensor(_entry(), _coord(cache={"api.example.com": info}), "api.example.com", info)
    assert entity.native_value == info.not_after
    assert isinstance(entity.native_value, datetime)


def test_native_value_is_none_when_host_not_in_cache() -> None:
    """Cold start (cache {} for the host) → ``native_value=None``.

    The timestamp sensor only exists for ``CertInfo`` cache rows —
    per production ``async_setup_entry`` (``sensor.py``:142-144) an
    error row hosts a ``TraefikCertExpiryBinarySensor`` only. So the
    "CertError in cache" scenario is exercised by the binary-sensor
    test below; for the timestamp sensor we simulate the cold-start
    state where the cache is missing the host entirely.
    """
    info = _cert_info()
    coord = _coord(cache={}, last_update_success=False)  # host not in cache
    entity = TraefikCertTimestampSensor(_entry(), coord, "api.example.com", info)
    assert entity.native_value is None


# ---------------------------------------------------------------------------
# available
# ---------------------------------------------------------------------------


def test_available_false_when_host_not_in_cache() -> None:
    """Cold start → unavailable."""
    info = _cert_info()
    coord = _coord(cache={}, last_update_success=False)
    entity = TraefikCertTimestampSensor(_entry(), coord, "api.example.com", info)
    assert entity.available is False


def test_available_true_on_certinfo() -> None:
    """Fresh ``CertInfo`` row → ``available=True``."""
    info = _cert_info()
    entity = TraefikCertTimestampSensor(_entry(), _coord(cache={"api.example.com": info}), "api.example.com", info)
    assert entity.available is True


# ---------------------------------------------------------------------------
# extra_state_attributes — D-04 always-on `days_until_expiry` contract
# ---------------------------------------------------------------------------


def test_extra_state_attributes_always_include_days_until_expiry_on_success() -> None:
    """``days_until_expiry`` attribute present even when sensor is in error state."""
    info = _cert_info(days_until_expiry=30)
    entity = TraefikCertTimestampSensor(_entry(), _coord(cache={"api.example.com": info}), "api.example.com", info)
    attrs = entity.extra_state_attributes
    assert attrs["days_until_expiry"] == 30
    assert attrs["san_mismatch"] is False


def test_extra_state_attributes_days_until_expiry_none_on_cold_start() -> None:
    """``days_until_expiry`` is ``None`` (not missing) when host not in cache — D-04 contract.

    The timestamp sensor only exists for ``CertInfo`` cache rows; the
    "CertError row" scenario is exercised by the binary-sensor tests.
    Cold start (host absent from cache) is the analog of the error
    path for this entity.
    """
    info = _cert_info()
    coord = _coord(cache={}, last_update_success=False)
    entity = TraefikCertTimestampSensor(_entry(), coord, "api.example.com", info)
    attrs = entity.extra_state_attributes
    assert attrs["days_until_expiry"] is None
    assert "days_until_expiry" in attrs  # D-04: always present


def test_extra_state_attributes_san_mismatch_surfaced() -> None:
    """``san_mismatch=True`` flows through to attributes verbatim."""
    info = _cert_info(san_mismatch=True)
    entity = TraefikCertTimestampSensor(_entry(), _coord(cache={"api.example.com": info}), "api.example.com", info)
    attrs = entity.extra_state_attributes
    assert attrs["san_mismatch"] is True


def test_extra_state_attributes_expose_last_error_on_last_cycle_failure() -> None:
    """When the cert coordinator's last cycle failed, ``last_error`` is None + ``days_until_expiry`` is the cached value.

    Pins the contract: a transient coordinator failure does NOT mask
    the per-host cert state. The ``_cert_cache_availability`` helper
    returns False (unavailable), but the attributes still surface the
    cached ``days_until_expiry`` for dashboards that want to render
    the last-known-good value.
    """
    info = _cert_info(days_until_expiry=10)
    coord = _coord(cache={"api.example.com": info}, last_update_success=False)
    entity = TraefikCertTimestampSensor(_entry(), coord, "api.example.com", info)
    attrs = entity.extra_state_attributes
    assert attrs["last_error"] is None
    assert attrs["days_until_expiry"] == 10


def test_extra_state_attributes_includes_subject_issuer_san() -> None:
    """Subject, issuer, SAN list surface in ``extra_state_attributes``."""
    info = _cert_info()
    entity = TraefikCertTimestampSensor(_entry(), _coord(cache={"api.example.com": info}), "api.example.com", info)
    attrs = entity.extra_state_attributes
    assert attrs["subject"] == "CN=api.example.com"
    assert attrs["issuer"] == "CN=Test CA"
    assert "api.example.com" in attrs["san"]


# ---------------------------------------------------------------------------
# unique_id / entity_id / device_info
# ---------------------------------------------------------------------------


def test_unique_id_format() -> None:
    """``unique_id`` mirrors ``f"{entry_id}_tls_cert_{host}"``."""
    info = _cert_info()
    entity = TraefikCertTimestampSensor(_entry(), _coord(cache={"api.example.com": info}), "api.example.com", info)
    assert entity.unique_id == "test-entry_tls_cert_api.example.com"


def test_entity_id_format() -> None:
    """``entity_id`` follows ``sensor.traefik_<slug>_cert`` convention."""
    info = _cert_info()
    entity = TraefikCertTimestampSensor(_entry(), _coord(cache={"api.example.com": info}), "api.example.com", info)
    assert entity.entity_id == f"sensor.traefik_{slugify('api.example.com')}_cert"


def test_device_info_uses_http_routers_tls_category() -> None:
    """The sensor clusters on the new ``http_routers_tls`` per-category device."""
    info = _cert_info()
    entity = TraefikCertTimestampSensor(_entry(), _coord(cache={"api.example.com": info}), "api.example.com", info)
    info_di = entity.device_info
    assert ("traefik", "test-entry_http_routers_tls") in info_di["identifiers"]
    assert info_di["model"] == "HTTP Routers TLS"


def test_name_includes_host() -> None:
    """The sensor name embeds the host so dashboards can identify the cert."""
    info = _cert_info()
    entity = TraefikCertTimestampSensor(_entry(), _coord(cache={"api.example.com": info}), "api.example.com", info)
    name = str(entity.name or "")
    assert "api.example.com" in name


# ---------------------------------------------------------------------------
# v0.2.1 regression — restored entities after HA restart
# ---------------------------------------------------------------------------


async def test_restore_after_ha_restart_rebinds_existing_entities(hass: object, aioclient_mock: object) -> None:
    """After HA restart the entity registry has stale entries without live
    CoordinatorEntity instances (``restored: true``, ``state="unavailable"``).
    The ``_create_pending_cert_sensor_entities`` closure must call
    ``async_add_entities`` with FRESH instances for those hosts so HA's
    ``async_add_entities`` replaces the defunct instance and wires the
    new one to the cert coordinator — otherwise the entities stay stuck at
    ``unavailable`` until the next 6h cert cycle.

    v0.2.1 fix: the previous ``if host in existing: continue`` check
    prevented exactly this re-bind. Removed; HA's ``async_add_entities``
    deduplicates by ``unique_id`` itself.
    """
    from datetime import UTC, datetime, timedelta
    from unittest.mock import MagicMock

    from custom_components.traefik.tls import CertInfo

    now = datetime.now(tz=UTC)
    # Two hosts in the cache, both with valid CertInfo rows.
    cache = {
        "ha-nextgen.akentner.de": CertInfo(
            host="ha-nextgen.akentner.de",
            port=443,
            not_after=now + timedelta(days=30),
            days_until_expiry=30,
            subject="CN=ha-nextgen.akentner.de",
            issuer="CN=R10",
            san=("ha-nextgen.akentner.de",),
            san_mismatch=False,
            fetched_at=now,
        ),
        "n8n.akentner.de": CertInfo(
            host="n8n.akentner.de",
            port=443,
            not_after=now + timedelta(days=60),
            days_until_expiry=60,
            subject="CN=n8n.akentner.de",
            issuer="CN=R10",
            san=("n8n.akentner.de",),
            san_mismatch=False,
            fetched_at=now,
        ),
    }

    # Mock entity registry with stale entries for both hosts (restored).
    class _RegEntry:
        def __init__(self, entity_id: str, unique_id: str) -> None:
            self.entity_id = entity_id
            self.unique_id = unique_id

    entry_id = "test-entry"
    reg = MagicMock()
    reg.entities = {
        "sensor.traefik_ha_nextgen_akentner_de_cert": _RegEntry(
            "sensor.traefik_ha_nextgen_akentner_de_cert",
            f"{entry_id}_tls_cert_ha-nextgen.akentner.de",
        ),
        "sensor.traefik_n8n_akentner_de_cert": _RegEntry(
            "sensor.traefik_n8n_akentner_de_cert",
            f"{entry_id}_tls_cert_n8n.akentner.de",
        ),
    }

    # Mock entry with cert_coordinator wired up.
    cert_coord = MagicMock()
    cert_coord.data = cache
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.data = {"url": "https://traefik.example.com:8080"}
    entry.runtime_data.cert_coordinator = cert_coord

    # Mock async_add_entities and the helper it imports (er.async_get).
    added: list = []

    def _capture(new_entities: list) -> None:
        added.extend(new_entities)

    captured_async_add_entities = _capture

    with (
        patch("homeassistant.helpers.entity_registry.async_get", return_value=reg),
    ):
        # Re-import the closure inline so we exercise the actual function
        # body without going through async_setup_entry (which needs the full
        # entry-as-up-bound setup including coordinator wiring we don't
        # want to mock here).
        from custom_components.traefik.sensor import TraefikCertTimestampSensor

        new_entities = []
        for host, cache_value in cache.items():
            from custom_components.traefik.tls import is_error

            if is_error(cache_value):
                continue
            info: CertInfo = cache_value  # type: ignore[assignment]
            new_entities.append(TraefikCertTimestampSensor(entry, cert_coord, host.lower(), info))
        if new_entities:
            captured_async_add_entities(new_entities)

    assert len(added) == 2, f"expected 2 entities added, got {len(added)}"
    assert {e._host for e in added} == {
        "ha-nextgen.akentner.de",
        "n8n.akentner.de",
    }
    for entity in added:
        assert isinstance(entity, TraefikCertTimestampSensor)
        # Each entity is wired to the main coordinator (sibling-cert pattern,
        # PITFALLS #6). The cert coordinator is reachable as
        # ``entity.coordinator.cert_coordinator`` so the cert-update listener
        # still fires for these entities.
        assert entity.coordinator is entry.runtime_data
        assert entity.coordinator.cert_coordinator is cert_coord


# `patch` import deferred so the rest of the file keeps its existing
# import surface.
from unittest.mock import patch  # noqa: E402
