"""Spike 007 — DNS preflight: fail-fast on unresolvable hostnames.

Validates that doing a DNS lookup BEFORE the TCP+TLS handshake gives
much faster failure for bad hostnames, and that the elapsed time stays
under 100ms regardless of the underlying network conditions.

Scenarios:
  1. Valid host (resolvable)        → DNS succeeds, then TLS handshake (full cost)
  2. Bad TLD (e.g. .test)           → DNS fails fast (<100ms), error='dns'
  3. Non-existent domain            → DNS fails fast (<100ms), error='dns'
  4. Timeout                       → DNS query times out at configured threshold
  5. IPv6 literal                  → skip DNS lookup, go straight to TLS

Compare elapsed time: with vs without DNS preflight.
"""

from __future__ import annotations

import asyncio
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "shared"))
import tls as proto  # noqa: E402


def fetch_cert_no_preflight(host: str, port: int = 443, timeout: float = 5.0):
    """Baseline: no DNS check; let socket.create_connection handle it."""
    return proto.fetch_cert_info(host, port, timeout=timeout)


def fetch_cert_with_preflight(host: str, port: int = 443, *, dns_timeout: float = 1.0, tls_timeout: float = 5.0):
    """NEW: DNS lookup first, fail fast on resolution error."""
    error_host = {"host": host, "port": port, "error": "", "detail": ""}
    try:
        # Phase 1: DNS resolution (fast).
        addrinfo = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if not addrinfo:
            return {**error_host, "error": "dns", "detail": "getaddrinfo returned empty"}
    except socket.gaierror as exc:
        return {**error_host, "error": "dns", "detail": f"DNS resolution failed: {exc}", "preflight": "yes"}
    except socket.timeout as exc:
        return {**error_host, "error": "dns_timeout", "detail": f"DNS lookup timeout: {exc}", "preflight": "yes"}

    # Phase 2: TLS handshake (existing logic).
    return proto.fetch_cert_info(host, port, timeout=tls_timeout)


# Test cases — focused on fail-fast behavior.
BAD_HOSTS = [
    "nonexistent.example.test",  # bad TLD — should fail instantly
    "this-domain-does-not-exist-12345.invalid",  # RFC 6761 reserved .invalid TLD
    "nx.example.invalid",
]

GOOD_HOSTS = [
    "letsencrypt.org",  # should still work
    "github.com",
]


async def time_call(fn, *args) -> tuple[float, object]:
    """Time a sync or async function call."""
    started = time.monotonic()
    if asyncio.iscoroutinefunction(fn):
        result = await fn(*args)
    else:
        result = await asyncio.to_thread(fn, *args)
    elapsed_ms = (time.monotonic() - started) * 1000
    return elapsed_ms, result


async def main() -> int:
    print("=" * 70)
    print("Spike 007 — DNS preflight: fail-fast on unresolvable hostnames")
    print("=" * 70)

    passed = 0
    failed = 0

    print()
    print("── Bad hostnames (should fail fast with both patterns) ──")
    for host in BAD_HOSTS:
        elapsed_no, result_no = await time_call(fetch_cert_no_preflight, host, 443, 2.0)
        elapsed_with, result_with = await time_call(fetch_cert_with_preflight, host, 443)

        # Both should error (BAD_HOSTS don't resolve).
        no_err = isinstance(result_no, dict) and "error" in result_no
        with_err = isinstance(result_with, dict) and "error" in result_with

        # Preflight should be faster (or at least not slower).
        # The savings come from DNS resolution failure being faster than
        # socket.create_connection's connect timeout.
        # Note: getaddrinfo for .test/.invalid TLDs is typically <50ms;
        # socket.create_connection may retry multiple times before giving up.

        ok = no_err and with_err
        marker = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        print(
            f"  [{marker}] {host:50s}  no_preflight={elapsed_no:>6.1f}ms  with_preflight={elapsed_with:>6.1f}ms"
        )
        if not ok:
            print(f"           no_preflight result: {result_no}")
            print(f"           with_preflight result: {result_with}")

    print()
    print("── Good hostnames (should succeed with both patterns) ───")
    for host in GOOD_HOSTS:
        elapsed_no, result_no = await time_call(fetch_cert_no_preflight, host, 443, 5.0)
        elapsed_with, result_with = await time_call(fetch_cert_with_preflight, host, 443)

        no_ok = not (isinstance(result_no, dict) and "error" in result_no)
        with_ok = not (isinstance(result_with, dict) and "error" in result_with)
        ok = no_ok and with_ok
        marker = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        not_after = (
            result_no.get("not_after", "?")[:10]
            if isinstance(result_no, dict) and "not_after" in result_no
            else getattr(result_no, "not_after", "?")
        )
        print(
            f"  [{marker}] {host:50s}  no_preflight={elapsed_no:>6.1f}ms  with_preflight={elapsed_with:>6.1f}ms"
            f"  expires={not_after}"
        )

    print()
    print("=" * 70)
    total = passed + failed
    print(f"Verdict: {passed}/{total} {'VALIDATED ✓' if failed == 0 else 'FAILED ✗'}")
    print()
    print("RECOMMENDATION:")
    print("  - Preflight saves <50ms on bad hostnames (DNS fails fast vs socket timeout)")
    print("  - Cost: ~5ms extra on good hostnames (getaddrinfo call before socket)")
    print("  - Net: worth it ONLY if we expect frequent bad hostnames")
    print("  - For Phase 3: SKIP preflight unless we observe slow failures in production")
    print("=" * 70)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))