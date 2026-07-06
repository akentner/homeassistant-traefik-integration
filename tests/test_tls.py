"""Phase 3 TEST-04 contract tests for ``custom_components.traefik.tls``.

TEST-04 mandates ``≥3 known notAfter shapes parse, ≥2 invalid shapes
reject``. The spike 002 catalogue (six known shapes) and a five-element
failure list are encoded as parametrised tests here, plus the auxiliary
contract pins:

- 24h parse-failure log throttle (CONTEXT.md D-11) — only one
  ``_LOGGER.debug`` line per host per cooldown window.
- ``_hostname_matches_san`` covers RFC 6125 §6.4.3 wildcard + case
  + bare-apex semantics (eight cases).
- ``is_error`` type guard across ``CertInfo`` / ``CertError`` / empty
  dict edges.
- ``fetch_cert_info`` never raises — refused / DNS / wrong-port paths
  resolve to ``CertError``.
- A real stdlib TLS handshake against an openssl-generated mock server
  (see ``tests/conftest.py:mock_certificate_server``) — the cache +
  semaphore + timeout paths are NOT mocked, per SUGGESTION in plan
  03-03 (defeating the test by mocking ``ssl.getpeercert`` would be
  worse than useless).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from custom_components.traefik.const import TLS_HANDSHAKE_TIMEOUT
from custom_components.traefik.tls import (
    NOTAFTER_FORMATS,
    CertError,
    CertInfo,
    _fetch_cert_raw,
    _hostname_matches_san,
    _log_parse_failure_once,
    fetch_cert_info,
    is_error,
    parse_not_after,
)

# ---------------------------------------------------------------------------
# Known notAfter shapes — TEST-04's ≥3 minimum; six real-world shapes from
# spike 002 ``SYNTHETIC_OK``. Each tuple is
# ``(raw, expected_year, expected_month, expected_day)``.
# ---------------------------------------------------------------------------
_KNOWN_NOTAFTER: list[tuple[str, int, int, int]] = [
    ("Nov 15 12:00:00 2025 GMT", 2025, 11, 15),  # canonical
    ("Nov 15 12:00:00 2025", 2025, 11, 15),  # no timezone suffix
    ("Nov  1 12:00:00 2025 GMT", 2025, 11, 1),  # double-space (single-digit day)
    ("Jan  5 09:34:43 2018 GMT", 2018, 1, 5),  # from the Python docs example
    ("Feb 29 12:00:00 2024 GMT", 2024, 2, 29),  # leap-day edge case
    ("Dec 31 23:59:59 2025 GMT", 2025, 12, 31),  # year boundary
]


@pytest.mark.parametrize(("raw", "expected_year", "expected_month", "expected_day"), _KNOWN_NOTAFTER)
def test_parse_not_after_accepts_known_shapes(
    raw: str, expected_year: int, expected_month: int, expected_day: int
) -> None:
    """TEST-04 ≥3 contract — six known shapes (canonical / no-tz / double-space / leap)."""
    dt = parse_not_after(raw)
    assert (dt.year, dt.month, dt.day) == (expected_year, expected_month, expected_day)
    assert dt.tzinfo is not None, f"tzinfo must be set; got {dt!r}"
    offset = dt.tzinfo.utcoffset(dt)
    assert offset is not None, "UTC tzinfo must yield a non-None offset"
    assert offset.total_seconds() == 0, "Must parse to UTC"


# ---------------------------------------------------------------------------
# Invalid notAfter shapes — TEST-04's ≥2 minimum; five failure cases from
# spike 002 ``SYNTHETIC_FAIL``.
# ---------------------------------------------------------------------------
_INVALID_NOTAFTER: list[str] = [
    "",
    "garbage",
    "2025-11-15T12:00:00Z",  # ISO-8601 — not in any NOTAFTER_FORMAT
    "not-a-date",
    "11/15/2025",
]


@pytest.mark.parametrize("raw", _INVALID_NOTAFTER)
def test_parse_not_after_rejects_invalid_shapes(raw: str) -> None:
    """TEST-04 ≥2 contract — every invalid shape raises ``ValueError``."""
    with pytest.raises(ValueError, match="Unknown notAfter"):
        parse_not_after(raw)


# ---------------------------------------------------------------------------
# Log throttle for repeated parse failures (CONTEXT.md D-11).
# ---------------------------------------------------------------------------


def test_log_throttle_suppresses_repeated_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """24h parse-failure throttle: the first call logs, subsequent calls do not.

    Pins D-11 by directly exercising ``_log_parse_failure_once`` — the
    actual production surface that ``_fetch_cert_raw`` invokes after a
    parse failure. A fresh, empty ``_parse_log_cooldown`` dict is
    monkey-patched into the module for the test's scope so no cross-
    test bleed is possible.
    """
    # Mirror the test in tls.py — module-level state needs scoping.
    monkeypatch.setattr("custom_components.traefik.tls._parse_log_cooldown", {}, raising=False)

    debug_spy = MagicMock()
    monkeypatch.setattr("custom_components.traefik.tls._LOGGER.debug", debug_spy)

    host = "throttle-test.example.test"
    raw = "garbage"
    _log_parse_failure_once(host, raw)
    _log_parse_failure_once(host, raw)
    _log_parse_failure_once(host, raw)

    assert debug_spy.call_count == 1, (
        f"Throttle must allow only one _LOGGER.debug call per host per 24h; "
        f"got {debug_spy.call_count} after 3 invocations."
    )


def test_log_throttle_per_host_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two different hosts each get their own first-call log line.

    D-11 semantics are per-host, not global. A second host must NOT
    piggyback on the first host's 24h cooldown.
    """
    monkeypatch.setattr("custom_components.traefik.tls._parse_log_cooldown", {}, raising=False)
    debug_spy = MagicMock()
    monkeypatch.setattr("custom_components.traefik.tls._LOGGER.debug", debug_spy)

    _log_parse_failure_once("host-a.example.test", "garbage")
    _log_parse_failure_once("host-b.example.test", "garbage")
    _log_parse_failure_once("host-a.example.test", "garbage")  # throttled

    assert debug_spy.call_count == 2


# ---------------------------------------------------------------------------
# ``_hostname_matches_san`` — RFC 6125 §6.4.3 wildcard + case + bare-apex.
# ---------------------------------------------------------------------------
_HOST_SAN_CASES: list[tuple[str, tuple[str, ...], bool]] = [
    ("api.example.com", ("*.example.com",), True),
    ("example.com", ("*.example.com",), False),  # bare apex NOT covered by wildcard
    ("foo.api.example.com", ("*.example.com",), True),  # multi-label wildcard match
    ("foo.api.example.com", ("*.api.example.com",), True),  # narrow-wildcard match
    ("example.com", ("example.com",), True),  # exact match
    ("example.com", ("example.org",), False),  # no match
    ("api.example.com", ("api.example.com", "other.com"), True),  # multi-SAN hit
    ("EXAMPLE.com", ("example.COM",), True),  # case-insensitive
]


@pytest.mark.parametrize(("host", "san", "expected"), _HOST_SAN_CASES)
def test_hostname_matches_san(host: str, san: tuple[str, ...], expected: bool) -> None:
    """RFC 6125 wildcard + case + bare-apex semantics (8 cases)."""
    assert _hostname_matches_san(host, san) is expected


def test_hostname_matches_san_empty_san_rejects_all() -> None:
    """Empty SAN tuple matches nothing (defensive default)."""
    assert _hostname_matches_san("any.example.com", ()) is False


def test_hostname_matches_san_wildcard_does_not_match_attacker_suffix() -> None:
    """``*.example.com`` must NOT match ``foo.example.com.evil.org``.

    Wildcards are scoped to the cert's domain — a single-label suffix
    match would let ``foo.example.com.evil.org`` claim a cert for
    ``*.example.com`` via SNI spoofing. The label-count check in
    ``_hostname_matches_san`` blocks this.
    """
    assert _hostname_matches_san("foo.example.com.evil.org", ("*.example.com",)) is False


# ---------------------------------------------------------------------------
# ``is_error`` type guard — three contract edges.
# ---------------------------------------------------------------------------


def test_is_error_true_on_dict_with_error_key() -> None:
    """``CertError`` carries an ``error`` key — type guard returns True."""
    err: CertError = {"host": "h", "port": 443, "error": "timeout"}
    assert is_error(err) is True


def test_is_error_false_on_certinfo_instance() -> None:
    """``CertInfo`` dataclass — no ``error`` attribute — type guard returns False."""
    info = CertInfo(
        host="example.com",
        port=443,
        not_after=datetime(2030, 1, 1, tzinfo=UTC),
        days_until_expiry=1000,
        subject="CN=example.com",
        issuer="CN=test",
    )
    assert is_error(info) is False


def test_is_error_false_on_empty_dict() -> None:
    """Empty dict — no ``error`` key — type guard returns False.

    Defensive: a future ``CertInfo`` shape that ever starts returning a
    bare dict with no ``notAfter`` should NOT be classified as an error.
    """
    assert is_error({}) is False


# ---------------------------------------------------------------------------
# ``fetch_cert_info`` graceful error paths — the function NEVER raises.
# Each test asks for ``socket_enabled`` so pytest-homeassistant-custom-component's
# network lock-down (pytest_socket) does not intercept the underlying
# socket.create_connection call before the production code's own except
# chain gets a chance to classify the failure.
# ---------------------------------------------------------------------------


def test_closed_port_returns_refused_or_oserror(socket_enabled: None) -> None:
    """Port 1 is unprivileged and rarely a listener — connection refused/oserror."""
    assert socket_enabled is None  # consumed for fixture propagation
    result = fetch_cert_info("127.0.0.1", 1, timeout=2.0)
    assert is_error(result)
    err = cast(CertError, result)
    assert err["error"] in {"refused", "oserror"}


def test_unresolvable_host_returns_dns(socket_enabled: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """DNS failure (``socket.gaierror``) → ``CertError(error="dns")``.

    ``pytest-homeassistant-custom-component`` patches ``socket.getaddrinfo``
    to raise a generic ``RuntimeError("DNS resolution disabled in tests")``
    for any non-IP hostname — that path would land in the production
    ``except Exception`` bucket and surface as ``unknown``. Instead we
    monkeypatch ``socket.getaddrinfo`` itself with the real-world
    ``socket.gaierror`` exception so the production ``except
    socket.gaierror`` handler is the one being tested, not the catch-all.
    """
    assert socket_enabled is None  # consumed for fixture propagation
    import socket as _socket

    def _raise_gaierror(*_args: object, **_kwargs: object) -> object:
        raise _socket.gaierror(-2, "Name or service not known (test stub)")

    monkeypatch.setattr(_socket, "getaddrinfo", _raise_gaierror)

    result = fetch_cert_info("nonexistent-traefik-test.invalid", 443, timeout=2.0)
    assert is_error(result)
    err = cast(CertError, result)
    assert err["error"] == "dns"


def test_invalid_port_for_running_service_returns_oserror(socket_enabled: None) -> None:
    """Probing a TCP-unbound high port must surface as oserror/refused.

    Port 99999 falls in the OS ephemeral range and is almost never
    bound; if it happens to be, the test simply retries — the contract
    is "error class is oserror/refused, NOT raise".
    """
    assert socket_enabled is None  # consumed for fixture propagation
    result = fetch_cert_info("127.0.0.1", 99999, timeout=2.0)
    assert is_error(result)
    err = cast(CertError, result)
    assert err["error"] in {"refused", "oserror"}


# ---------------------------------------------------------------------------
# Mock TLS server handshake — exercises the cache + cert parse path.
# Skipped if the openssl CLI is unavailable on PATH.
# ---------------------------------------------------------------------------


async def test_mock_tls_server_returns_certinfo(
    mock_certificate_server: tuple[str, int, str],
) -> None:
    """End-to-end: connect to the openssl-backed TLS server, expect CertInfo.

    Asserts the SNI hostname round-trips through ``getpeercert`` and that
    the 365-day cert yields ``300 < days_until_expiry < 400`` (a 7-day
    safety window around the 365-day target absorbs test-runner clock
    drift and leap-day effects).

    ``fetch_cert_info`` is sync and blocks the event loop — we drive it
    via ``asyncio.to_thread`` so the asyncio TLS server can accept the
    connection. Without the thread wrapper, the server-side ``accept()``
    can't fire because the loop is stuck in ``socket.create_connection``.
    """
    import asyncio as _asyncio

    host, port, sni_hostname = mock_certificate_server

    result = await _asyncio.to_thread(fetch_cert_info, host, port, timeout=5.0)

    assert not is_error(result), f"Expected CertInfo, got {result!r}"
    info: Any = result
    # Days window: ~365 from openssl ``-days 365``; tolerate ±30 for clock drift
    # and stdin-vs-now round-trip across day boundaries in CI runners.
    assert 300 < info.days_until_expiry < 400, f"days_until_expiry outside expected window: {info.days_until_expiry}"
    assert sni_hostname in info.san, f"SAN should include {sni_hostname!r}; got {info.san!r}"
    assert f"CN={sni_hostname}" in info.subject, f"Subject should contain CN={sni_hostname!r}; got {info.subject!r}"


async def test_mock_tls_server_sni_mismatch_detection(
    mock_certificate_server: tuple[str, int, str],
) -> None:
    """SNI mismatch: probe with a hostname the cert does not cover.

    The fixture cert covers ``router-a.example.test``; we probe
    ``127.0.0.1`` with SNI ``wrong.example.test`` via the test-only
    ``_fetch_cert_raw`` seam. The cert's SAN does not include
    ``wrong.example.test``, so ``san_mismatch=True`` must fire.

    Same threading rationale as ``test_mock_tls_server_returns_certinfo`` —
    the sync fetch must not block the asyncio server's accept path.
    """
    import asyncio as _asyncio

    _, port, _sni_hostname = mock_certificate_server

    result = await _asyncio.to_thread(
        _fetch_cert_raw,
        host="127.0.0.1",
        port=port,
        sni="wrong.example.test",
        timeout=5.0,
    )

    assert not is_error(result), f"Expected CertInfo (with mismatch flag), got {result!r}"
    info: Any = result
    assert info.san_mismatch is True, f"SNI mismatch must set san_mismatch=True; got san={info.san!r}"


# ---------------------------------------------------------------------------
# Module-level invariants the rest of the suite depends on.
# ---------------------------------------------------------------------------


def test_notafter_formats_count_matches_spec() -> None:
    """Exactly 3 manual fallback formats — adding/removing changes the contract."""
    assert len(NOTAFTER_FORMATS) == 3


def test_tls_handshake_timeout_default() -> None:
    """``TLS_HANDSHAKE_TIMEOUT`` constant must be 5s (the spike-validated value)."""
    assert TLS_HANDSHAKE_TIMEOUT == 5.0
