"""Tests for ``TraefikProxyCertExpiryBinarySensor`` (Phase 3 TLS-02).

Phase-3-specific contract pins:

- ``is_on`` is ``True`` when ``days_until_expiry <= threshold_days``
  (CONTEXT.md D-03 signed-int semantics — already-expired certs surface
  as ``True``); ``None`` (NOT ``False``) when the cache row is missing
  or a ``CertError`` so HA renders 'unknown' rather than 'off'.
- ``threshold_days`` mutations flip the state IMMEDIATELY (D-08 live
  re-eval — no re-handshake needed).
- ``_attr_entity_registry_enabled_default`` is ``True`` (D-03) — the
  intentional inversion from Phase 2's M-12 default-off for
  ``TraefikProxyAnyRouterFailingBinarySensor``.
- ``days_until_expiry`` + ``threshold_days`` are ALWAYS present in
  ``extra_state_attributes`` (D-04 + D-08).
- ``device_class == PROBLEM`` (D-14).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.util import slugify

from custom_components.traefik_proxy.binary_sensor import (
    TraefikProxyAnyRouterFailingBinarySensor,
    TraefikProxyCertExpiryBinarySensor,
)
from custom_components.traefik_proxy.tls import CertError, CertInfo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry() -> MagicMock:
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
    threshold_days: int = 14,
    last_update_success: bool = True,
) -> MagicMock:
    coord: MagicMock = MagicMock()
    coord.data = cache if cache is not None else {host: _cert_info(host=host)}
    coord.threshold_days = threshold_days
    # ``get_threshold()`` is the production access path used by
    # ``extra_state_attributes``; wire it to return ``threshold_days``.
    coord.get_threshold = MagicMock(return_value=threshold_days)
    coord.last_update_success = last_update_success
    return coord


def _entity(
    *,
    host: str = "api.example.com",
    cache_value: CertInfo | CertError | None = None,
    threshold_days: int = 14,
    last_update_success: bool = True,
) -> TraefikProxyCertExpiryBinarySensor:
    if cache_value is None:
        cache_value = _cert_info(host=host)
    cache: dict[str, Any] = {host: cast(Any, cache_value)}
    coord = _coord(host=host, cache=cache, threshold_days=threshold_days, last_update_success=last_update_success)
    return TraefikProxyCertExpiryBinarySensor(_entry(), coord, host, cache_value)


# ---------------------------------------------------------------------------
# is_on — D-03 signed-int semantics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("days_until_expiry", "threshold", "expected"),
    [
        (30, 14, False),  # comfortably above threshold
        (14, 14, True),  # AT threshold (D-03 `<=` semantics)
        (10, 14, True),  # under threshold
        (-1, 14, True),  # already-expired cert (breach case)
    ],
)
def test_is_on_signed_int_semantics(days_until_expiry: int, threshold: int, expected: bool) -> None:
    """``is_on`` is ``True`` whenever ``days_until_expiry <= threshold``."""
    entity = _entity(threshold_days=threshold, cache_value=_cert_info(days_until_expiry=days_until_expiry))
    assert entity.is_on is expected


def test_is_on_unknown_when_cache_is_certerror() -> None:
    """``CertError`` cache row → ``is_on=None`` (HA 'unknown'; NOT False)."""
    entity = _entity(cache_value=_cert_error(), last_update_success=False)
    assert entity.is_on is None


def test_is_on_unknown_when_host_not_in_cache() -> None:
    """Cold start with empty cache → ``is_on=None``."""
    entry = _entry()
    coord = _coord(cache={}, last_update_success=False)
    entity = TraefikProxyCertExpiryBinarySensor(entry, coord, "api.example.com", None)
    assert entity.is_on is None


# ---------------------------------------------------------------------------
# available
# ---------------------------------------------------------------------------


def test_available_false_on_certerror() -> None:
    """``CertError`` row → unavailable (delegated to ``_cert_cache_availability``)."""
    entity = _entity(cache_value=_cert_error(), last_update_success=False)
    assert entity.available is False


def test_available_false_on_cold_start() -> None:
    """Empty cache + ``last_update_success=False`` → unavailable."""
    entry = _entry()
    coord = _coord(cache={}, last_update_success=False)
    entity = TraefikProxyCertExpiryBinarySensor(entry, coord, "api.example.com", None)
    assert entity.available is False


def test_available_true_on_certinfo() -> None:
    """Fresh ``CertInfo`` row → available."""
    entity = _entity(cache_value=_cert_info())
    assert entity.available is True


# ---------------------------------------------------------------------------
# extra_state_attributes — D-04 + D-08 always-on
# ---------------------------------------------------------------------------


def test_extra_state_attributes_always_expose_days_until_expiry() -> None:
    """``days_until_expiry`` is ``None`` (not missing) on cold start."""
    entity_no_cache = _entity(cache_value=None, last_update_success=False)
    entity_no_cache._coordinator.data = {}  # cold start
    attrs_no_cache = entity_no_cache.extra_state_attributes
    assert attrs_no_cache["days_until_expiry"] is None
    assert "days_until_expiry" in attrs_no_cache
    assert attrs_no_cache["threshold_days"] == 14

    info_entity = _entity(cache_value=_cert_info(days_until_expiry=10))
    attrs_info = info_entity.extra_state_attributes
    assert attrs_info["days_until_expiry"] == 10
    assert attrs_info["threshold_days"] == 14


def test_extra_state_attributes_expose_san_mismatch() -> None:
    """``san_mismatch`` attribute present on both success + error rows."""
    info_entity = _entity(cache_value=_cert_info(san_mismatch=True))
    assert info_entity.extra_state_attributes["san_mismatch"] is True

    err_entity = _entity(cache_value=_cert_error(), last_update_success=False)
    err_entity._coordinator.data = {"api.example.com": _cert_error()}
    # The cert_error path surfaces san_mismatch=None
    assert err_entity.extra_state_attributes["san_mismatch"] is None


def test_extra_state_attributes_expose_last_error_on_certerror() -> None:
    """On ``CertError`` row, ``last_error`` carries the error code."""
    err = _cert_error(code="timeout")
    cache = cast(dict[str, Any], {"api.example.com": err})
    coord = _coord(cache=cache, last_update_success=False)
    entity = TraefikProxyCertExpiryBinarySensor(_entry(), coord, "api.example.com", err)
    attrs = entity.extra_state_attributes
    assert attrs["last_error"] == "timeout"
    assert attrs["days_until_expiry"] is None


# ---------------------------------------------------------------------------
# unique_id / entity_id / device_class
# ---------------------------------------------------------------------------


def test_unique_id_format() -> None:
    """``unique_id`` mirrors ``f"{entry_id}_tls_expiring_{host}"``."""
    entity = _entity()
    assert entity.unique_id == "test-entry_tls_expiring_api.example.com"


def test_entity_id_format() -> None:
    """``entity_id`` follows ``binary_sensor.traefik_<slug>_expiring`` convention."""
    entity = _entity()
    assert entity.entity_id == f"binary_sensor.traefik_{slugify('api.example.com')}_expiring"


def test_device_class_is_problem() -> None:
    """HA renders a PROBLEM icon for the cert-expiry alarm (D-14)."""
    entity = _entity()
    assert entity.device_class == BinarySensorDeviceClass.PROBLEM


def test_entity_registry_enabled_default_is_true() -> None:
    """D-03: cert expiry is a security-impacting alarm — always enabled by default.

    HA's ``CachedProperties`` metaclass moves ``_attr_*`` to ``__attr_*``;
    the test reads the private name to pin the boolean value.
    """
    assert TraefikProxyCertExpiryBinarySensor.__dict__.get("__attr_entity_registry_enabled_default") is True


# ---------------------------------------------------------------------------
# D-08 live re-eval — threshold mutation flips state immediately
# ---------------------------------------------------------------------------


def test_state_transitions_when_threshold_changes() -> None:
    """D-08: mutating ``threshold_days`` flips ``is_on`` without a re-handshake.

    A cert with 10 days remaining is "expiring" relative to a 14-day
    threshold (10 <= 14 → ON). Raising the threshold to 7 makes the
    cert "comfortable" (10 > 7 → OFF). The flip works in both
    directions (raise-then-lower returns the original state) so the
    live re-eval is proven to follow ``threshold_days`` directly.
    """
    info = _cert_info(days_until_expiry=10)
    coord = _coord(cache={"api.example.com": info}, threshold_days=14)
    entity = TraefikProxyCertExpiryBinarySensor(_entry(), coord, "api.example.com", info)
    assert entity.is_on is True  # baseline: 10 <= 14 — alarm ON

    # Raise the threshold so the cert becomes "comfortable".
    coord.threshold_days = 7
    coord.get_threshold = MagicMock(return_value=7)
    assert entity.is_on is False  # 10 > 7 — alarm OFF

    # Drop the threshold back below days_until_expiry to flip the alarm ON again.
    coord.threshold_days = 14
    coord.get_threshold = MagicMock(return_value=14)
    assert entity.is_on is True  # 10 <= 14 — alarm ON (live re-eval proven both ways)


# ---------------------------------------------------------------------------
# D-03 inversion pin vs. Phase 2's M-12
# ---------------------------------------------------------------------------


def test_distinct_from_any_router_failing_enabled_default() -> None:
    """Phase 3 inverts Phase 2's default — pin the intentional divergence.

    ``TraefikProxyAnyRouterFailingBinarySensor`` (Phase 2 M-12) is opt-in via
    ``entity_registry_enabled_default=False`` because the
    router-failure alarm is noisy. ``TraefikProxyCertExpiryBinarySensor``
    (Phase 3 D-03) is ALWAYS ON by default because cert expiry is a
    security-impacting event. The two classes MUST invert.
    """
    any_router_default: Any = TraefikProxyAnyRouterFailingBinarySensor.__dict__.get(
        "__attr_entity_registry_enabled_default"
    )
    cert_expiry_default: Any = TraefikProxyCertExpiryBinarySensor.__dict__.get("__attr_entity_registry_enabled_default")
    assert any_router_default is False, (
        f"Phase 2 TraefikProxyAnyRouterFailingBinarySensor default must stay False; got {any_router_default!r}"
    )
    assert cert_expiry_default is True, (
        f"Phase 3 TraefikProxyCertExpiryBinarySensor default must be True; got {cert_expiry_default!r}"
    )
    assert any_router_default is not cert_expiry_default  # explicit: inverted
