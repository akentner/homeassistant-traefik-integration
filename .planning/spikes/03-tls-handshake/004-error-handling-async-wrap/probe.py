"""Spike 004 — Error handling under TLS handshake failures.

Validates that every TLS error path (timeout, unreachable, SNI mismatch,
IPv6 failure, format-string parse failure) is caught and surfaced as a
typed CertError, never propagated. Also validates the asyncio.to_thread +
Semaphore wrapper for concurrent handshakes.

Scenarios:
  1. Timeout           — connect to a hanging server (open port, never responds)
  2. Connection refused — connect to a closed port on localhost
  3. IPv6 failure      — probe [::1] on a closed port
  4. Parse failure     — pass a malformed notAfter to parse_not_after directly
  5. Concurrent        — fire N handshakes via asyncio.to_thread + Semaphore;
                         all complete without crashing
  6. SNI routing       — re-test spike 003 patterns with the integration
                         wrapper (host=127.0.0.1, SNI=router-*.example.test)
"""

from __future__ import annotations

import asyncio
import socket
import ssl
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "shared"))
from tls import (
    CertInfo,
    fetch_cert_info_async,
    parse_not_after,
)

CERTS_DIR = Path(__file__).parent.parent / "003-sni-routing-multicert" / "certs"

# Reuse the SNI server from spike 003.
sys.path.insert(0, str(Path(__file__).parent.parent / "003-sni-routing-multicert"))
from server import TLSServer  # noqa: E402


def _sni_probe(server: TLSServer, sni: str, timeout: float = 5.0) -> CertInfo | dict:
    """Sync helper: TLS handshake to localhost:server.port with the given SNI.

    Trusts all self-signed certs in CERTS_DIR so CERT_REQUIRED populates
    the cert dict (per spike 001 finding).
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    for cert_path in CERTS_DIR.glob("*.crt"):
        ctx.load_verify_locations(str(cert_path))
    with socket.create_connection(("127.0.0.1", server.port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=sni) as ssock:
            cert = ssock.getpeercert(binary_form=False)
    from datetime import UTC, datetime
    subject_cn = None
    for rdn in cert.get("subject", ()):
        for k, v in rdn:
            if k == "commonName":
                subject_cn = v
    not_after = parse_not_after(cert["notAfter"])
    return CertInfo(
        host=sni,
        port=server.port,
        not_after=not_after,
        days_until_expiry=(not_after - datetime.now(UTC)).days,
        subject=f"CN={subject_cn}" if subject_cn else "",
        issuer="",
        san=tuple(v for k, v in cert.get("subjectAltName", ()) if k == "DNS"),
    )


async def scenario_timeout() -> tuple[str, bool, str]:
    """Connect to a port that accepts but never responds. Verify timeout error.

    Uses a sync socket server that accepts connections but never reads
    or writes — the TLS handshake hangs waiting for ServerHello.
    """
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(("127.0.0.1", 0))
    server_sock.listen(5)
    port = server_sock.getsockname()[1]

    # Accept connections in a background thread but never read/write.
    import threading
    stop_flag = threading.Event()
    accepted_conns: list[socket.socket] = []

    def accept_loop():
        server_sock.settimeout(0.5)
        while not stop_flag.is_set():
            try:
                conn, _ = server_sock.accept()
                # Don't close — leave it open so the SSL handshake hangs.
                accepted_conns.append(conn)
            except TimeoutError:
                continue
            except OSError:
                break

    t = threading.Thread(target=accept_loop, daemon=True)
    t.start()

    try:
        started = time.monotonic()
        result = await fetch_cert_info_async("127.0.0.1", port, timeout=2.0)
        elapsed = time.monotonic() - started
        ok = (
            isinstance(result, dict)
            and result.get("error") in ("timeout", "oserror")
            and 1.5 < elapsed < 5.0  # Should respect the 2s timeout
        )
        detail = f"elapsed={elapsed:.2f}s  result={result}"
        return ("timeout", ok, detail)
    finally:
        stop_flag.set()
        for conn in accepted_conns:
            try:
                conn.close()
            except OSError:
                pass
        server_sock.close()


async def scenario_refused() -> tuple[str, bool, str]:
    """Connect to a closed port. Verify refused/oserror."""
    result = await fetch_cert_info_async("127.0.0.1", 1, timeout=2.0)
    ok = isinstance(result, dict) and result.get("error") in ("refused", "oserror")
    return ("connection_refused", ok, f"result={result}")


async def scenario_ipv6_failure() -> tuple[str, bool, str]:
    """Connect to IPv6 ::1 on a closed port. Verify clean error, no crash."""
    try:
        result = await fetch_cert_info_async("::1", 1, timeout=2.0)
    except Exception as exc:
        return ("ipv6_failure", False, f"CRASHED: {type(exc).__name__}: {exc}")
    ok = isinstance(result, dict) and result.get("error") in (
        "refused", "oserror", "dns", "timeout",
    )
    return ("ipv6_failure", ok, f"result={result}")


def scenario_parse_failure() -> tuple[str, bool, str]:
    """Pass malformed notAfter to parse_not_after. Verify ValueError raised."""
    for raw in ["", "garbage", "2025-11-15T12:00:00Z", "not-a-date", "11/15/2025"]:
        try:
            dt = parse_not_after(raw)
            return ("parse_failure", False, f"{raw!r} unexpectedly parsed as {dt}")
        except ValueError:
            pass
    return ("parse_failure", True, "all 5 malformed inputs correctly rejected (ValueError)")


async def scenario_concurrent(server: TLSServer, n: int = 8) -> tuple[str, bool, str]:
    """Fire N concurrent handshakes via Semaphore(4). All should complete."""
    sem = asyncio.Semaphore(4)

    async def bounded_probe(sni: str) -> str:
        async with sem:
            r = await asyncio.to_thread(_sni_probe, server, sni, 5.0)
            return f"{sni}={'cert' if isinstance(r, CertInfo) else r.get('error')}"

    hosts = ["router-a.example.test"] * (n // 2) + ["router-b.example.test"] * (n - n // 2)
    started = time.monotonic()
    results = await asyncio.gather(*(bounded_probe(h) for h in hosts))
    elapsed = time.monotonic() - started
    certs = sum(1 for r in results if "=cert" in r)
    ok = certs == n
    detail = f"completed {certs}/{n} in {elapsed:.2f}s; first 3: {results[:3]}"
    return (f"concurrent_{n}_sem4", ok, detail)


async def scenario_sni(server: TLSServer, sni: str, expected_cn: str) -> tuple[str, bool, str]:
    """Probe SNI server with given SNI; confirm right cert returned."""
    result = await asyncio.to_thread(_sni_probe, server, sni)
    if isinstance(result, CertInfo):
        cn = result.subject.split(",")[0] if result.subject else "?"
        ok = cn == f"CN={expected_cn}"
        return (f"sni_{sni}", ok, f"CN={cn}  days={result.days_until_expiry}  san={list(result.san)}")
    return (f"sni_{sni}", False, f"got error: {result}")


async def main() -> int:
    print("=" * 70)
    print("Spike 004 — Error handling & async wrapping")
    print("=" * 70)

    server = TLSServer(host="127.0.0.1", port=0)
    await server.start()
    print(f"SNI test server listening on {server.host}:{server.port}")
    print()

    results: list[tuple[str, bool, str]] = []

    print("── Scenario: timeout (hangs on accept) ──────────────")
    r = await scenario_timeout()
    print(f"  [{'PASS' if r[1] else 'FAIL'}] {r[2]}")
    results.append(r)

    print("── Scenario: connection refused ────────────────────")
    r = await scenario_refused()
    print(f"  [{'PASS' if r[1] else 'FAIL'}] {r[2]}")
    results.append(r)

    print("── Scenario: IPv6 unreachable ─────────────────────")
    r = await scenario_ipv6_failure()
    print(f"  [{'PASS' if r[1] else 'FAIL'}] {r[2]}")
    results.append(r)

    print("── Scenario: parse failure (sync) ─────────────────")
    r = scenario_parse_failure()
    print(f"  [{'PASS' if r[1] else 'FAIL'}] {r[2]}")
    results.append(r)

    print("── Scenario: concurrent (8x, sem=4) ───────────────")
    r = await scenario_concurrent(server, n=8)
    print(f"  [{'PASS' if r[1] else 'FAIL'}] {r[2]}")
    results.append(r)

    print("── Scenario: SNI routing (host=127.0.0.1, SNI=…) ─")
    for sni, expected in [
        ("router-a.example.test", "router-a.example.test"),
        ("router-b.example.test", "router-b.example.test"),
        ("router-c-alt.example.test", "router-c.example.test"),  # SAN-routed
        ("unknown.example.test", "*.example.test"),  # wildcard fallback
    ]:
        r = await scenario_sni(server, sni, expected)
        print(f"  [{'PASS' if r[1] else 'FAIL'}] {r[2]}")
        results.append(r)

    await server.stop()

    print()
    print("=" * 70)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"Spike 004 — {passed}/{len(results)} scenarios passed")
    print(f"Verdict: {'VALIDATED ✓' if passed == len(results) else 'PARTIAL ⚠' if passed > 0 else 'INVALIDATED ✗'}")
    print("=" * 70)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))