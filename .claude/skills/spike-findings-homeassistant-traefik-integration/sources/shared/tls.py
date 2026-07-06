"""Prototype tls.py for the Traefik integration Phase 3 spike.

NOT production code — exercised by the 4 spike scripts under
`.planning/spikes/03-tls-handshake/0XX-*`. After the spike validates the
approach, the working version lands in `custom_components/traefik/tls.py`
during Phase 3 plan execution.

Design goals (from PROJECT.md, PITFALLS #14, CONTEXT.md D-10/D-11):
- Stdlib only — no `cryptography` import, no `manifest.json` requirements.
- Never raise — every error path returns a `CertError` TypedDict instead.
- SNI-aware — `server_hostname` is passed to `wrap_socket` so Traefik's
  SNI router serves the right cert.
- Hostname verification disabled — we are probing, not validating trust.
  The cert's CN/SAN may legitimately differ from the probe hostname
  (wildcard, default cert, IP-only probe).
- IPv6-capable — `socket.create_connection((host, port))` accepts
  `[::1]:443` syntax natively.
- Format-string defense — try `ssl.cert_time_to_seconds()` first (the
  canonical C-locale parser), fall back to a manual loop covering known
  variants.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import ssl
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final, TypedDict, cast

_LOGGER = logging.getLogger(__name__)

# Default port; tests override for local servers.
DEFAULT_PORT: Final = 443

# Manual fallback format strings — observed real-world notAfter shapes
# plus the locale-dependent and double-space-padded variants called out
# in PITFALLS #14.
NOTAFTER_FORMATS: Final[tuple[str, ...]] = (
    "%b %d %H:%M:%S %Y %Z",  # "Nov 15 12:00:00 2025 GMT"  (canonical)
    "%b %d %H:%M:%S %Y",     # "Nov 15 12:00:00 2025"        (no tz)
    "%b  %d %H:%M:%S %Y %Z", # "Nov  1 12:00:00 2025 GMT"    (double-space day)
)


class CertError(TypedDict, total=False):
    """Failure shape returned in place of CertInfo — never raised."""

    host: str
    port: int
    error: str  # Short classification: timeout, unreachable, sni, parse, ...
    detail: str  # Full exception message for debug logging.


class CertDict(TypedDict, total=False):
    """Typed view of the dict returned by ssl.SSLSocket.getpeercert()."""

    notAfter: str
    notBefore: str
    subject: tuple[tuple[tuple[str, str], ...], ...]
    issuer: tuple[tuple[tuple[str, str], ...], ...]
    subjectAltName: tuple[tuple[str, str], ...]
    serialNumber: str
    version: int


def is_error(result: CertInfo | CertError) -> bool:
    """Type guard — True when the result is an error dict."""
    return isinstance(result, dict) and "error" in result


@dataclass(frozen=True)
class CertInfo:
    """Successful cert probe result."""

    host: str
    port: int
    not_after: datetime  # Always UTC.
    days_until_expiry: int
    subject: str
    issuer: str
    san: tuple[str, ...] = field(default_factory=tuple)
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def _format_rdn(rdn_tuple: tuple[tuple[tuple[str, str], ...], ...]) -> str:
    """Flatten the nested RDN tuple from getpeercert() into a display string.

    Example input:
        ((('commonName', 'example.com'),), (('organizationName', '...'),))
    Output: "CN=example.com, O=..."
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
    """Parse a notAfter string into a UTC datetime.

    Strategy (defense in depth per PITFALLS #14):
      1. Try stdlib `ssl.cert_time_to_seconds()` — canonical C-locale parser.
      2. On failure, walk `NOTAFTER_FORMATS` and try each with `strptime`.

    Raises ValueError on total miss so the caller can record
    `last_error="notAfter parse failed"`.
    """
    # 1. Canonical path.
    try:
        ts = ssl.cert_time_to_seconds(raw)
        return datetime.fromtimestamp(ts, tz=UTC)
    except (ValueError, TypeError, OSError):
        pass

    # 2. Manual fallback loop.
    for fmt in NOTAFTER_FORMATS:
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue

    raise ValueError(f"Unknown notAfter format: {raw!r}")


def fetch_cert_info(
    host: str,
    port: int = DEFAULT_PORT,
    *,
    timeout: float = 5.0,  # noqa: ASYNC109 — timeout passed through to socket layer
) -> CertInfo | CertError:
    """Blocking TLS handshake to (host, port); return cert info or error.

    Designed to be called from `asyncio.to_thread(...)` so the HA event
    loop stays responsive. `server_hostname=host` is passed to SNI-routed
    servers (Traefik).

    Hostname verification is intentionally DISABLED — we probe to learn
    what cert is being served, not to validate trust. If we enabled
    `check_hostname=True`, a default cert / wildcard mismatch would raise
    `ssl.SSLCertVerificationError` and we'd mark the entity `unavailable`
    even though we just successfully fetched the cert.
    """
    error_host: CertError = {"host": host, "port": port, "error": "", "detail": ""}

    try:
        # PROTOCOL_TLS_CLIENT enables CERT_REQUIRED by default, which is
        # what populates the getpeercert() dict (CERT_NONE returns empty).
        # We disable check_hostname because we're probing what cert is
        # served, not validating trust — Traefik may serve a default cert
        # or wildcard whose SAN doesn't strictly match the probe hostname.
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.load_default_certs()  # Required for CERT_REQUIRED to validate the chain.

        with (
            socket.create_connection((host, port), timeout=timeout) as raw_sock,
            ctx.wrap_socket(raw_sock, server_hostname=host) as ssock,
        ):
                cert = ssock.getpeercert(binary_form=False)

        if not cert:
            return {
                **error_host,
                "error": "empty",
                "detail": "getpeercert returned empty dict (CERT_NONE handshake)",
            }

        # ssl.SSLSocket.getpeercert() returns a loosely-typed dict; cast to
        # a precise shape for mypy strict-mode.
        cert_dict = cast("CertDict", cert)

        # Parse notAfter — both primary and fallback paths exercised.
        try:
            not_after = parse_not_after(cert_dict["notAfter"])
        except (KeyError, ValueError, TypeError) as exc:
            return {
                **error_host,
                "error": "parse",
                "detail": f"notAfter parse failed: {exc}",
            }

        days_until_expiry = (not_after - datetime.now(UTC)).days

        san: tuple[str, ...] = tuple(
            str(value) for kind, value in cert_dict.get("subjectAltName", ()) if kind == "DNS"
        )

        return CertInfo(
            host=host,
            port=port,
            not_after=not_after,
            days_until_expiry=days_until_expiry,
            subject=_format_rdn(cert_dict.get("subject", ())),
            issuer=_format_rdn(cert_dict.get("issuer", ())),
            san=san,
            fetched_at=datetime.now(UTC),
        )

    except TimeoutError:
        return {**error_host, "error": "timeout", "detail": f"timeout after {timeout}s"}
    except socket.gaierror as exc:
        return {**error_host, "error": "dns", "detail": str(exc)}
    except ConnectionRefusedError as exc:
        return {**error_host, "error": "refused", "detail": str(exc)}
    except OSError as exc:
        return {**error_host, "error": "oserror", "detail": str(exc)}
    except ssl.SSLError as exc:
        return {**error_host, "error": "ssl", "detail": str(exc)}
    except Exception as exc:
        return {**error_host, "error": "unknown", "detail": f"{type(exc).__name__}: {exc}"}


async def fetch_cert_info_async(
    host: str,
    port: int = DEFAULT_PORT,
    *,
    timeout: float = 5.0,  # noqa: ASYNC109 — timeout is passed through to the worker thread
) -> CertInfo | CertError:
    """Async wrapper — runs the blocking handshake in a worker thread."""
    return await asyncio.to_thread(fetch_cert_info, host, port, timeout=timeout)


def _quick_probe(host: str, port: int = DEFAULT_PORT) -> None:
    """Helper for `python -m` smoke tests."""
    started = time.monotonic()
    result = fetch_cert_info(host, port)
    elapsed_ms = (time.monotonic() - started) * 1000
    if is_error(result):
        err = cast(CertError, result)
        print(f"FAIL {host}:{port}  err={err['error']}  detail={err['detail']}  ({elapsed_ms:.0f}ms)")
    else:
        info = cast(CertInfo, result)
        print(
            f"OK   {host}:{port}  expires={info.not_after.isoformat()}  "
            f"days={info.days_until_expiry:>4}  "
            f"subject={info.subject!r}  san={info.san}  ({elapsed_ms:.0f}ms)"
        )


if __name__ == "__main__":
    import sys

    for target in sys.argv[1:]:
        if ":" in target and not target.startswith("["):
            host, _, port_s = target.partition(":")
            _quick_probe(host, int(port_s))
        else:
            _quick_probe(target)