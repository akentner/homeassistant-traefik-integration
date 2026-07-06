"""Spike 005 — Comparison: asyncio.open_connection vs raw socket + wrap_socket.

Validates that the cleaner asyncio API produces IDENTICAL results to the
raw-socket pattern in the shared prototype. If validated, Phase 3's
`tls.py` can use `asyncio.open_connection` directly and skip the
`asyncio.to_thread` wrapper (the handshake is no longer blocking from
the caller's perspective).

Head-to-head comparison on the same host:
  - Pattern A: socket.create_connection + ctx.wrap_socket + to_thread (current)
  - Pattern B: asyncio.open_connection(ssl=...) (candidate)
"""

from __future__ import annotations

import asyncio
import socket
import ssl
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

# Pattern A: raw socket + wrap_socket (matches shared/tls.py)
def fetch_cert_pattern_a(host: str, port: int = 443, timeout: float = 5.0):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.load_default_certs()
    started = time.monotonic()
    with (
        socket.create_connection((host, port), timeout=timeout) as raw_sock,
        ctx.wrap_socket(raw_sock, server_hostname=host) as ssock,
    ):
        elapsed_connect = (time.monotonic() - started) * 1000
        cert = ssock.getpeercert(binary_form=False)
    not_after_raw = cert["notAfter"]
    ts = ssl.cert_time_to_seconds(not_after_raw)
    not_after = datetime.fromtimestamp(ts, tz=UTC)
    san = tuple(v for k, v in cert.get("subjectAltName", ()) if k == "DNS")
    return {
        "pattern": "A (raw socket + to_thread)",
        "host": host,
        "connect_ms": round(elapsed_connect, 1),
        "not_after": not_after.isoformat(),
        "san_count": len(san),
        "san_first_3": list(san[:3]),
    }


# Pattern B: asyncio.open_connection (candidate)
async def fetch_cert_pattern_b(host: str, port: int = 443, timeout: float = 5.0):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.load_default_certs()
    started = time.monotonic()
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port, ssl=ctx, server_hostname=host),
        timeout=timeout,
    )
    elapsed_connect = (time.monotonic() - started) * 1000
    # Trigger app data so handshake completes (asyncio is lazy).
    writer.write(b"GET / HTTP/1.0\r\nHost: " + host.encode() + b"\r\n\r\n")
    await writer.drain()
    # Now read the cert (post-handshake).
    ssl_obj = writer.get_extra_info("ssl_object")
    cert = ssl_obj.getpeercert(binary_form=False) if ssl_obj else None
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    if not cert:
        return {"pattern": "B (asyncio.open_connection)", "host": host, "error": "empty cert"}
    not_after_raw = cert["notAfter"]
    ts = ssl.cert_time_to_seconds(not_after_raw)
    not_after = datetime.fromtimestamp(ts, tz=UTC)
    san = tuple(v for k, v in cert.get("subjectAltName", ()) if k == "DNS")
    return {
        "pattern": "B (asyncio.open_connection)",
        "host": host,
        "connect_ms": round(elapsed_connect, 1),
        "not_after": not_after.isoformat(),
        "san_count": len(san),
        "san_first_3": list(san[:3]),
    }


HOSTS = [
    "letsencrypt.org",
    "github.com",
    "traefik.io",
    "cloudflare.com",
    "www.google.com",
]


async def main() -> int:
    print("=" * 70)
    print("Spike 005 — asyncio.open_connection vs raw socket (comparison)")
    print("=" * 70)

    all_match = True
    for host in HOSTS:
        # Pattern A runs in this async fn via to_thread.
        a = await asyncio.to_thread(fetch_cert_pattern_a, host)
        b = await fetch_cert_pattern_b(host)

        # Compare semantically.
        match = (
            a.get("not_after") == b.get("not_after")
            and a.get("san_count") == b.get("san_count")
        )
        marker = "MATCH" if match else "DIFFER"
        if not match:
            all_match = False

        print(f"\n  Host: {host}")
        print(f"    [{marker}] not_after: A={a.get('not_after')} B={b.get('not_after')}")
        print(f"    [{marker}] san_count: A={a.get('san_count')} B={b.get('san_count')}")
        print(f"    [INFO]  connect_ms: A={a.get('connect_ms')} B={b.get('connect_ms')}")
        if "san_first_3" in a and "san_first_3" in b:
            same_sans = a["san_first_3"] == b["san_first_3"]
            print(f"    [{'MATCH' if same_sans else 'DIFFER'}] san_first_3: A={a['san_first_3']} B={b['san_first_3']}")
            if not same_sans:
                all_match = False

    print()
    print("=" * 70)
    if all_match:
        print("Verdict: VALIDATED ✓ — patterns produce identical cert data")
        print("RECOMMENDATION: Phase 3 tls.py can use asyncio.open_connection")
        print("  and skip the to_thread wrapper (cleaner, non-blocking natively)")
    else:
        print("Verdict: INVALIDATED ✗ — patterns differ; investigate")
    print("=" * 70)
    return 0 if all_match else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))