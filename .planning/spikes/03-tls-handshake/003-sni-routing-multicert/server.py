"""Spike 003 — Local TLS server with SNI-routed cert selection (Traefik simulator).

Validates that the stdlib handshake honors `server_hostname` and returns the
right leaf cert per requested host, just like Traefik's SNI routing does.

Server picks the cert based on the client's SNI:
  - router-a.example.test   → router-a cert
  - router-b.example.test   → router-b cert
  - router-c.example.test   → router-c cert (also valid for router-c-alt)
  - *.example.test (wildcard) → wildcard cert
  - everything else          → wildcard cert (Traefik's default-cert fallback)

Spike probes the server with each hostname and verifies the returned cert
CN/SAN matches.
"""

from __future__ import annotations

import asyncio
import ssl
from pathlib import Path
from typing import Any

CERTS_DIR = Path(__file__).parent / "certs"


# Pre-build one SSLContext per cert so the SNI callback can swap to the
# right context cleanly. This is the standard pattern from the Python docs.
def _build_cert_contexts() -> dict[str, ssl.SSLContext]:
    """Build one SSLContext per cert with the cert already loaded."""
    ctxs: dict[str, ssl.SSLContext] = {}
    # SNI name → cert base. Includes SAN entries that belong to a cert
    # (Traefik does the same: a Host() rule on a SAN routes to the cert
    # configured for that domain).
    cert_map = {
        "router-a.example.test": "router-a",
        "router-b.example.test": "router-b",
        "router-c.example.test": "router-c",
        "router-c-alt.example.test": "router-c",  # SAN of router-c
        "wildcard": "wildcard",
    }
    for sni, base in cert_map.items():
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(
            certfile=str(CERTS_DIR / f"{base}.pem"),
        )
        ctxs[sni] = ctx
    return ctxs


def make_sni_server_context() -> ssl.SSLContext:
    """Build a server SSLContext with an SNI callback that swaps contexts."""
    cert_contexts = _build_cert_contexts()
    default_ctx = cert_contexts["wildcard"]
    # Load default cert on the outer context (required by SSLContext).
    default_ctx.load_cert_chain(certfile=str(CERTS_DIR / "wildcard.pem"))

    def sni_callback(
        ssl_sock: ssl.SSLSocket,
        sni_name: str | None,
        initial_context: ssl.SSLContext,
    ) -> None:
        """Pick the right cert context by SNI."""
        # Find the matching cert context.
        if sni_name and sni_name in cert_contexts:
            new_ctx = cert_contexts[sni_name]
        else:
            new_ctx = cert_contexts["wildcard"]
        # Swap the SSL object to use the chosen context for THIS handshake.
        # This is the canonical Python SNI pattern.
        ssl_sock.context = new_ctx

    default_ctx.sni_callback = sni_callback
    return default_ctx


async def probe(server: TLSServer, sni: str, *, timeout: float = 5.0) -> dict[str, Any]:
    """Connect to the server with a given SNI, return the cert that was served.

    Trusts all self-signed certs in CERTS_DIR so CERT_REQUIRED succeeds
    and the cert dict is populated (per spike 001 finding).
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    # Trust all the self-signed certs we generated.
    for cert_path in CERTS_DIR.glob("*.crt"):
        ctx.load_verify_locations(str(cert_path))

    try:
        reader, writer = await asyncio.open_connection(
            server.host, server.port, ssl=ctx, server_hostname=sni
        )
    except Exception as exc:
        return {"sni": sni, "error": f"{type(exc).__name__}: {exc}"}

    # Trigger application data (which forces handshake completion).
    writer.write(b"GET / HTTP/1.0\r\nHost: " + sni.encode() + b"\r\n\r\n")
    await writer.drain()

    try:
        data = await asyncio.wait_for(reader.read(1024), timeout=timeout)
    except TimeoutError:
        return {"sni": sni, "error": "read timeout"}

    # Read the cert BEFORE closing.
    ssl_obj = writer.get_extra_info("ssl_object")
    cert = ssl_obj.getpeercert(binary_form=False) if ssl_obj else None

    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass

    if not cert:
        return {"sni": sni, "error": "empty cert dict"}

    # Extract subject CN.
    subject_cn = None
    for rdn in cert.get("subject", ()):
        for key, value in rdn:
            if key == "commonName":
                subject_cn = value
                break
    san = tuple(value for kind, value in cert.get("subjectAltName", ()) if kind == "DNS")
    not_after_raw = cert.get("notAfter", "")

    return {
        "sni": sni,
        "subject_cn": subject_cn,
        "san": san,
        "not_after": not_after_raw,
        "response_bytes": len(data),
    }


class TLSServer:
    """Minimal TLS server that selects certs by SNI."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.port = port
        self._server: asyncio.base_events.Server | None = None
        self._served_certs: list[str | None] = []

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # Get the actual cert that was served (server-side).
        ssl_obj = writer.get_extra_info("ssl_object")
        cn = None
        if ssl_obj is not None:
            # On the server side, getpeercert() returns the CLIENT cert,
            # not the server's own cert. We need to look at the negotiated
            # cipher / our own cert. Use a different mechanism.
            # Trick: sni_callback already captured it via context swap.
            cn = getattr(ssl_sock := ssl_obj, "_current_server_cn", None)
        # Track via the ssl_obj's context reference (which was swapped).
        self._served_certs.append(writer.get_extra_info("ssl_object"))
        # Send response.
        writer.write(b"HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\nOK")
        await writer.drain()
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

    async def start(self) -> None:
        ctx = make_sni_server_context()
        self._server = await asyncio.start_server(self.handle, self.host, self.port, ssl=ctx)
        sock = self._server.sockets[0]
        self.port = sock.getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()


async def main() -> int:
    print("=" * 70)
    print("Spike 003 — SNI-routed multi-cert TLS server (Traefik simulator)")
    print("=" * 70)

    server = TLSServer(host="127.0.0.1", port=0)
    await server.start()
    print(f"Server listening on {server.host}:{server.port}")
    print()

    # Each host should receive its own cert (CN matches SNI).
    test_cases = [
        ("router-a.example.test", "router-a.example.test"),
        ("router-b.example.test", "router-b.example.test"),
        ("router-c.example.test", "router-c.example.test"),
        ("router-c-alt.example.test", "router-c.example.test"),  # alt SAN of router-c
        ("unknown.example.test", "*.example.test"),  # wildcard fallback
    ]

    all_passed = True
    for probe_sni, expected_cn in test_cases:
        result = await probe(server, probe_sni)
        if "error" in result:
            print(f"  [FAIL] SNI={probe_sni:40s} → error: {result['error']}")
            all_passed = False
            continue
        cn = result.get("subject_cn")
        san = result.get("san", [])
        ok = cn == expected_cn
        marker = "PASS" if ok else "FAIL"
        print(
            f"  [{marker}] SNI={probe_sni:40s} → CN={cn!r:35s}  SAN={san}"
        )
        if not ok:
            print(f"           expected CN={expected_cn!r}")
            all_passed = False

    print()
    await server.stop()

    print("=" * 70)
    print(f"Verdict: {'VALIDATED ✓' if all_passed else 'INVALIDATED ✗'}")
    print("=" * 70)
    return 0 if all_passed else 1


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(main()))