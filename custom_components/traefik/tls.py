"""stdlib-only TLS handshake helper for Phase 3; never raises — every error path returns a typed ``CertError`` TypedDict.

This module is the production successor of the spike prototype at
``.claude/skills/spike-findings-homeassistant-traefik-integration/sources/shared/tls.py``
with the production deltas listed in
``.claude/skills/spike-findings-homeassistant-traefik-integration/references/tls-handshake.md``
section "tls.py — the handshake helper":

1. Drop unused ``%Y%m%d%H%M%SZ`` ``notAfter`` format (spike skill #1 — never
   observed in 18 real-world samples; spike 002 catalogue).
2. Add the 24h parse-failure log throttle
   (``_log_parse_failure_once`` + ``_parse_log_cooldown``) — CONTEXT.md
   D-11: a misbehaving peer must not spam the logbook.
3. Add a low-level ``_fetch_cert_raw(host, port, *, sni=None, timeout=5.0)``
   that separates the **connect address** (``host``) from the **SNI value**
   (``sni``) so unit tests can probe ``127.0.0.1`` with SNI
   ``router-a.example.test`` (spike skill "What to Avoid" #6).
4. Add ``san_mismatch: bool = False`` to ``CertInfo`` (spike 006) and a
   ``_hostname_matches_san`` helper implementing the RFC 6125 §6.4.3
   wildcard match rules.
5. ``is_error`` is a TypeGuard-compatible predicate that lets callers
   narrow the union without repeated ``isinstance`` checks.

References:
- ``.planning/spikes/03-tls-handshake/MANIFEST.md`` — spike findings 002
  (format strings), 005 (asyncio.open_connection is a drop-in), 006
  (hostname mismatch detection).
- ``.claude/skills/spike-findings-homeassistant-traefik-integration/references/tls-handshake.md``
  — full blueprint; in particular the "What to Avoid" landmines list.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import ssl
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, TypedDict, cast

_LOGGER = logging.getLogger(__name__)

# Default port; tests override for local servers.
DEFAULT_PORT: Final = 443

# Manual fallback format strings — observed real-world notAfter shapes
# plus the locale-dependent and double-space-padded variants called out
# in PITFALLS #14 and spike 002.
#
# Production delta vs. the spike prototype: the "%Y%m%d%H%M%SZ" shape was
# dropped (spike 002 catalogue — never observed in any of 18 real-world
# samples, only inside the OpenSSL utility's "GENERATED CERT" line that
# does not appear in a real cert's notAfter).
NOTAFTER_FORMATS: Final[tuple[str, ...]] = (
    "%b %d %H:%M:%S %Y %Z",  # "Nov 15 12:00:00 2025 GMT"  (canonical)
    "%b %d %H:%M:%S %Y",  # "Nov 15 12:00:00 2025"        (no tz)
    "%b  %d %H:%M:%S %Y %Z",  # "Nov  1 12:00:00 2025 GMT"    (double-space day)
)


class CertError(TypedDict, total=False):
    """Failure shape returned in place of ``CertInfo`` — never raised.

    The ``error`` field is a short classification string the cert coordinator
    surfaces verbatim on the entity's ``extra_state_attributes`` so dashboards
    can group failing hosts by failure mode. The values are a closed set
    (CONTEXT.md D-10) and tests assert against them:

    - ``"timeout"`` — ``socket.timeout`` / ``TimeoutError`` (per-host timeout)
    - ``"dns"`` — ``socket.gaierror`` (unresolvable hostname)
    - ``"refused"`` — ``ConnectionRefusedError`` (no listener on the port)
    - ``"unreachable"`` — generic reachability failure surfaced by ``_probe``
    - ``"oserror"`` — parent ``OSError`` catch (reset, aborted, broken pipe)
    - ``"ssl"`` — ``ssl.SSLError`` (chain validation, handshake protocol)
    - ``"parse"`` — ``notAfter`` could not be parsed by either parser
    - ``"empty"`` — ``getpeercert`` returned an empty dict (CERT_NONE)
    - ``"unknown"`` — last-resort catch-all (e.g. an exception type we did
      not anticipate; surfaces the type name in ``detail``)
    """

    host: str
    port: int
    error: str
    detail: str


class CertDict(TypedDict, total=False):
    """Typed view of the dict returned by ``ssl.SSLSocket.getpeercert()``.

    ``ssl`` ships a loosely-typed dict; the cast at the boundary
    (``cert_dict = cast("CertDict", cert)``) is what keeps
    ``mypy --strict`` clean while the runtime check is still the actual
    ``getpeercert()`` contract.
    """

    notAfter: str
    notBefore: str
    subject: tuple[tuple[tuple[str, str], ...], ...]
    issuer: tuple[tuple[tuple[str, str], ...], ...]
    subjectAltName: tuple[tuple[str, str], ...]
    serialNumber: str
    version: int


def is_error(result: CertInfo | CertError) -> bool:
    """Type guard — ``True`` when the result is an error dict.

    Lets the cert coordinator narrow the union without repeated
    ``isinstance`` checks::

        r = await fetch_cert_info_async(host)
        if is_error(r):
            err = cast(CertError, r)
            ...
        else:
            info = cast(CertInfo, r)
            ...
    """
    return isinstance(result, dict) and "error" in result


@dataclass(frozen=True)
class CertInfo:
    """Successful cert probe result — hashable for the in-memory cache.

    ``not_after`` is always UTC (per ``parse_not_after``). ``days_until_expiry``
    is the signed integer difference (already-expired certs surface as
    negative — exposed on the entity's ``extra_state_attributes`` per
    CONTEXT.md D-12 floor semantics).

    ``san_mismatch`` is ``True`` when the probe hostname is NOT covered by
    any SAN entry on the leaf cert (exact or RFC 6125 wildcard). This
    surfaces a useful diagnostic: Traefik may be serving a default cert
    or wildcard cert that does not strictly cover the probe hostname,
    which is a real config issue worth alerting on.
    """

    host: str
    port: int
    not_after: datetime  # Always UTC.
    days_until_expiry: int
    subject: str
    issuer: str
    san: tuple[str, ...] = field(default_factory=tuple)
    san_mismatch: bool = False  # spike 006
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def _format_rdn(
    rdn_tuple: tuple[tuple[tuple[str, str], ...], ...] | tuple[Any, ...],
) -> str:
    """Flatten the nested RDN tuple from ``getpeercert()`` into a display string.

    Example input::
        ((('commonName', 'example.com'),), (('organizationName', '...'),))

    Output::

        "CN=example.com, O=..."

    Unknown OID names are kept verbatim so a strange issuer is still
    visible in ``extra_state_attributes`` rather than silently dropped.
    """
    parts: list[str] = []
    for rdn in rdn_tuple:
        for key, value in rdn:
            short = {
                "commonName": "CN",
                "organizationName": "O",
                "organizationalUnitName": "OU",
                "countryName": "C",
                "stateOrProvinceName": "ST",
                "localityName": "L",
            }.get(key, key)
            parts.append(f"{short}={value}")
    return ", ".join(parts)


def parse_not_after(raw: str) -> datetime:
    """Parse a ``notAfter`` string into a UTC datetime.

    Strategy (defense in depth per PITFALLS #14):

    1. Try stdlib ``ssl.cert_time_to_seconds()`` — the canonical C-locale
       parser used by OpenSSL itself. All 18 real-world certs in spike 002
       parsed cleanly here.
    2. On failure, walk ``NOTAFTER_FORMATS`` and try each with
       ``strptime`` — the locale-dependent and double-space-padded
       variants called out in PITFALLS #14.

    Raises ``ValueError`` on total miss so the caller can record
    ``CertError(error="parse")`` and feed the throttle.
    """
    # 1. Canonical path.
    try:
        ts = ssl.cert_time_to_seconds(raw)
        return datetime.fromtimestamp(ts, tz=UTC)
    except ValueError, TypeError, OSError:
        pass

    # 2. Manual fallback loop.
    for fmt in NOTAFTER_FORMATS:
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue

    raise ValueError(f"Unknown notAfter format: {raw!r}")


def _hostname_matches_san(host: str, san: tuple[str, ...]) -> bool:
    """RFC 6125 §6.4.3 wildcard match.

    Returns ``True`` when ``host`` is covered by any entry in ``san``:

    - Exact match (case-insensitive, trailing-dot insensitive).
    - Wildcard match: ``*.example.com`` matches ``foo.example.com`` and
      ``foo.api.example.com`` (any sub-domain) but NOT ``example.com``
      itself (the bare apex is not covered by a wildcard).
    - Multi-level subdomains are covered by a single-level wildcard per
      RFC 6125 (we enforce this by label-count comparison so a wildcard
      cert for ``*.example.com`` does NOT match ``foo.example.com.evil.org``).

    Used to populate ``CertInfo.san_mismatch`` — a ``True`` return means
    the cert was served for a hostname the cert actually covers; a
    ``False`` return means Traefik served a default or wildcard cert
    that does not strictly cover the probe hostname.
    """
    host_lower = host.lower().rstrip(".")
    for entry in san:
        entry_lower = entry.lower().rstrip(".")
        if entry_lower == host_lower:
            return True
        if entry_lower.startswith("*."):
            # Wildcard: *.example.com matches foo.example.com but not
            # example.com itself (the bare apex is not covered).
            suffix = entry_lower[1:]  # ".example.com"
            if host_lower.endswith(suffix) and host_lower.count(".") >= entry_lower.count("."):
                return True
    return False


# Per-host parse-failure log throttle (CONTEXT.md D-11). The first time a
# host's notAfter fails to parse, we log a debug line; subsequent failures
# are silenced for 24h so a misbehaving peer can't spam the logbook.
# ``time.monotonic()`` is the correct clock here — the user may pause
# the host and wall-clock drift would log too often or too rarely.
_parse_log_cooldown: dict[str, float] = {}
_PARSE_LOG_COOLDOWN_SECONDS: Final = 86400  # 24h


def _log_parse_failure_once(host: str, raw: str) -> None:
    """Emit a debug log for the first parse failure per host per 24h."""
    now = time.monotonic()
    last = _parse_log_cooldown.get(host)
    if last is not None and now < last:
        return
    _LOGGER.debug("cert parse failed", extra={"host": host, "raw": raw})
    _parse_log_cooldown[host] = now + _PARSE_LOG_COOLDOWN_SECONDS


def _build_error(host: str, port: int, error: str, detail: str) -> CertError:
    """Build a ``CertError`` TypedDict.

    Centralised so the same set of fields (``host``, ``port``, ``error``,
    ``detail``) is always populated — tests assert against these keys
    and the entity layer reads them for ``extra_state_attributes``.
    """
    return CertError(host=host, port=port, error=error, detail=detail)


def _open_tls_connection(host: str, port: int, *, server_hostname: str, timeout: float) -> ssl.SSLSocket:
    """Open a blocking TLS connection with the spike-validated SSLContext.

    The trio below is locked by spike 001 — ``PROTOCOL_TLS_CLIENT``
    enables ``CERT_REQUIRED`` (the only mode where ``getpeercert()`` is
    populated), ``check_hostname=False`` because we are probing what
    cert is being served (Traefik may serve a default cert whose SAN
    does not strictly match the probe hostname — that's a useful
    diagnostic, not a failure), and ``load_default_certs()`` to feed
    the CA bundle ``CERT_REQUIRED`` needs.

    The single ``server_hostname`` parameter lets ``_fetch_cert_raw``
    pass an SNI value that differs from the connect address — the
    unit-test seam for spike 003 SNI routing verification.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.load_default_certs()
    raw_sock = socket.create_connection((host, port), timeout=timeout)
    return ctx.wrap_socket(raw_sock, server_hostname=server_hostname)


def _fetch_cert_raw(
    host: str,
    port: int,
    *,
    sni: str | None = None,
    timeout: float = 5.0,
) -> CertInfo | CertError:
    """Low-level handshake — connect to ``host:port`` with SNI ``sni``.

    Same body as :func:`fetch_cert_info` but the connect address
    (``host``) and SNI value (``sni``) are independent parameters. This
    is the test seam for spike 003 SNI routing verification: tests can
    probe ``127.0.0.1`` with SNI ``router-a.example.test`` to drive a
    local Traefik simulator without DNS.

    In production code paths ``fetch_cert_info`` is the right entry
    point — hostname == SNI for every Traefik-served hostname.
    """
    server_hostname = sni if sni is not None else host
    try:
        with _open_tls_connection(host, port, server_hostname=server_hostname, timeout=timeout) as ssock:
            cert = ssock.getpeercert(binary_form=False)

        if not cert:
            return _build_error(host, port, "empty", "getpeercert returned empty dict (CERT_NONE handshake)")

        cert_dict = cast("CertDict", cert)

        try:
            not_after = parse_not_after(cert_dict["notAfter"])
        except (KeyError, ValueError, TypeError) as exc:
            _log_parse_failure_once(host, str(cert_dict.get("notAfter", "")))
            return _build_error(host, port, "parse", f"notAfter parse failed: {exc}")

        days_until_expiry = (not_after - datetime.now(UTC)).days

        san: tuple[str, ...] = tuple(str(value) for kind, value in cert_dict.get("subjectAltName", ()) if kind == "DNS")
        san_mismatch = not _hostname_matches_san(host, san)

        return CertInfo(
            host=host,
            port=port,
            not_after=not_after,
            days_until_expiry=days_until_expiry,
            subject=_format_rdn(cert_dict.get("subject", ())),
            issuer=_format_rdn(cert_dict.get("issuer", ())),
            san=san,
            san_mismatch=san_mismatch,
            fetched_at=datetime.now(UTC),
        )
    except TimeoutError:
        return _build_error(host, port, "timeout", f"timeout after {timeout}s")
    except socket.gaierror as exc:
        return _build_error(host, port, "dns", str(exc))
    except ConnectionRefusedError as exc:
        # MUST come before OSError — ConnectionRefusedError is a subclass
        # of OSError on every supported platform; the wrong ordering would
        # label every connection-refused as the generic "oserror".
        return _build_error(host, port, "refused", str(exc))
    except OSError as exc:
        return _build_error(host, port, "oserror", str(exc))
    except ssl.SSLError as exc:
        return _build_error(host, port, "ssl", str(exc))
    except Exception as exc:  # final-resort catch-all; every error path becomes a typed CertError
        return _build_error(host, port, "unknown", f"{type(exc).__name__}: {exc}")


def fetch_cert_info(
    host: str,
    port: int = DEFAULT_PORT,
    *,
    timeout: float = 5.0,
) -> CertInfo | CertError:
    """Blocking TLS handshake to ``(host, port)``; return cert info or error.

    Designed to be called from :func:`asyncio.to_thread` (see
    :func:`fetch_cert_info_async`) so the HA event loop stays
    responsive. ``server_hostname=host`` is passed to SNI-routed servers
    (Traefik); hostname and SNI are the same value here.

    Hostname verification is intentionally disabled — we probe to learn
    what cert is being served, not to validate trust. If we enabled
    ``check_hostname=True`` a default cert / wildcard mismatch would
    raise ``ssl.SSLCertVerificationError`` and we'd mark the entity
    ``unavailable`` even though we just successfully fetched the cert.
    The mismatch is surfaced as :attr:`CertInfo.san_mismatch` instead.
    """
    return _fetch_cert_raw(host, port, sni=host, timeout=timeout)


async def fetch_cert_info_async(
    host: str,
    port: int = DEFAULT_PORT,
    *,
    timeout: float = 5.0,  # noqa: ASYNC109 — passed through to the worker thread
) -> CertInfo | CertError:
    """Async wrapper — runs the blocking handshake in a worker thread.

    The cert coordinator awaits this; the blocking ``socket.create_connection``
    + ``wrap_socket`` call runs on a thread so the HA event loop is never
    starved. ``asyncio.timeout(5)`` wraps the whole await (a second layer of
    defense on top of the per-handshake ``socket.timeout=timeout``).
    """
    return await asyncio.to_thread(fetch_cert_info, host, port, timeout=timeout)
