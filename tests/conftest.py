"""Pytest fixtures for Traefik integration tests."""

from __future__ import annotations

import asyncio
import socket
import ssl
import subprocess
import sys
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from pytest_homeassistant_custom_component.common import MockConfigEntry

# Ensure custom_components is importable from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

# Pre-import the project's custom_components so the production
# ``tls`` module is bound in this conftest's frame BEFORE the
# pytest-homeassistant-custom-component namespace hijack of the
# ``custom_components`` name takes effect. Later in-fixture
# ``from custom_components.traefik import tls`` references then resolve
# to the real production module rather than the testing_config stub
# that ships with ``pytest_homeassistant_custom_component``.
from custom_components.traefik import tls as _tls_module  # pre-import for in-fixture patching

FIXTURES_DIR = Path(__file__).parent / "fixtures"

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def enable_custom_integrations(hass):  # type: ignore[no-untyped-def]
    """Force-reload the custom_components cache so our integration gets registered."""
    hass.data.pop("custom_components", None)
    return hass


@pytest.fixture
def mock_traefik_config_entry() -> MockConfigEntry:
    """A config entry for the Traefik integration, valid for happy-path tests."""
    return MockConfigEntry(
        domain="traefik",
        title="Traefik",
        data={
            "url": "https://traefik.example.com:8080",
            "api_key": "test-secret",
            "verify_ssl": True,
        },
        unique_id="traefik.example.com",
    )


@pytest_asyncio.fixture
async def mock_certificate_server(
    socket_enabled: None,  # NO default — required fixture, enables real socket access
    monkeypatch: pytest.MonkeyPatch,
    sni_hostname: str = "router-a.example.test",
) -> AsyncIterator[tuple[str, int, str]]:
    """Spin up a stdlib TLS server on 127.0.0.1 with a CA-signed cert.

    Yields ``(host, port, sni_hostname)`` so tests can probe
    ``fetch_cert_info("127.0.0.1", port, timeout=5.0)`` and
    ``_fetch_cert_raw(host="127.0.0.1", port=port, sni=<other-host>,
    timeout=...)`` (the latter for SNI-mismatch scenarios per spike 003).

    The cert is CA-signed in the per-test ``TemporaryDirectory``: the
    fixture generates a throwaway CA, signs the server leaf with it,
    and chains them into a single PEM for ``load_cert_chain``. The
    CA path is then monkey-patched into the production SSLContext via
    ``ssl.SSLContext.load_verify_locations`` so ``fetch_cert_info`` —
    which uses ``PROTOCOL_TLS_CLIENT`` + ``load_default_certs()`` —
    can complete the handshake against the test cert chain. Without
    this injection, the verifier rejects the self-signed CA root and
    the test surfaces ``oserror`` instead of a successful handshake.

    Requires the ``openssl`` CLI on PATH (the standard on every
    supported CI runner). Cleanup is automatic: ``tempfile.TemporaryDirectory``
    removes the cert + key at fixture exit, ``server.close()`` +
    ``wait_closed()`` shuts down the asyncio server, and
    ``monkeypatch.undo()`` cleans up the SSL context monkey-patch.
    """
    with tempfile.TemporaryDirectory() as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        ca_key = tmpdir / "ca.key"
        ca_cert = tmpdir / "ca.crt"
        server_key = tmpdir / "server.key"
        server_csr = tmpdir / "server.csr"
        server_ext = tmpdir / "server.ext"
        server_cert = tmpdir / "server.crt"
        chain_pem = tmpdir / "chain.pem"

        server_ext.write_text(f"subjectAltName=DNS:{sni_hostname}\n")

        # Run openssl in a worker thread so the blocking subprocess doesn't
        # stall the event loop (ASYNC221 — async functions should not
        # invoke blocking OS calls). All openssl calls happen sequentially
        # in the same thread so the cert chain references resolve.
        await asyncio.to_thread(
            _generate_ca_signed_cert,
            ca_key,
            ca_cert,
            server_key,
            server_csr,
            server_cert,
            server_ext,
            chain_pem,
            sni_hostname,
        )

        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(str(chain_pem), str(server_key))

        # Inject the test CA into the trust store the production SSLContext
        # uses. The production ``_open_tls_connection`` calls
        # ``ctx.load_default_certs()`` which pulls from the system CA
        # bundle — that bundle does NOT include our throwaway CA. We
        # patch the production entry point to ALSO load the test CA via
        # ``load_verify_locations`` so the handshake succeeds.
        _original_open = _tls_module._open_tls_connection

        def _patched_open_tls_connection(
            host: str, port: int, *, server_hostname: str, timeout: float
        ) -> ssl.SSLSocket:
            """Wrap the production context-creation with our test CA in the trust store."""
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.load_default_certs()
            ctx.load_verify_locations(cafile=str(ca_cert))
            raw_sock = socket.create_connection((host, port), timeout=timeout)
            return ctx.wrap_socket(raw_sock, server_hostname=server_hostname)

        monkeypatch.setattr(_tls_module, "_open_tls_connection", _patched_open_tls_connection)

        server = await asyncio.start_server(
            _silence_client,
            "127.0.0.1",
            0,
            ssl=ssl_ctx,
        )
        try:
            # ``sockets`` is non-None because we explicitly bound ``127.0.0.1``.
            sockets = server.sockets or ()
            sock = sockets[0]
            port = sock.getsockname()[1]
            yield ("127.0.0.1", port, sni_hostname)
        finally:
            server.close()
            await server.wait_closed()


async def _silence_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Tiny no-op connection handler for the stdlib TLS server.

    The handshake handler from ``tls._fetch_cert_raw`` only reads the
    handshake bytes; once the handshake completes the client closes.
    Send a minimal HTTP/1.0 200 response so a misbehaving client doesn't
    hang on a read.
    """
    try:
        writer.write(b"HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\nOK")
        await writer.drain()
    except ConnectionResetError, BrokenPipeError, OSError:
        # Client hung up after the handshake — expected.
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except OSError, RuntimeError:
            # Already closed by the peer; safe to ignore.
            pass


# ``socket`` import retained: some proxy/type-checkers warn otherwise. The
# fixture intentionally binds via ``asyncio.start_server`` rather than the
# low-level ``socket`` module so the asyncio cancel / cleanup semantics stay
# in one place.
_ = socket


def _generate_ca_signed_cert(
    ca_key: Path,
    ca_cert: Path,
    server_key: Path,
    server_csr: Path,
    server_cert: Path,
    server_ext: Path,
    chain_pem: Path,
    sni_hostname: str,
) -> None:
    """Generate a CA + sign a per-server leaf cert (runs in a worker thread).

    Synchronous so ``subprocess.run`` is the natural fit — pulled out of
    the async fixture proper to satisfy ASYNC221. Creates:

    1. A self-signed CA root (365-day validity, CN=Test CA).
    2. A server leaf signed by the CA (365-day validity, CN=sni_hostname,
       SAN=DNS:sni_hostname via ``server_ext`` config).
    3. A chained PEM ``chain_pem`` (leaf + CA) for ``ssl.SSLContext.load_cert_chain``.

    The server leaf's ``notAfter`` falls ~365d ahead of the test run
    (used by ``test_mock_tls_server_returns_CertInfo`` to assert the
    days-until-expiry window in ``tests/test_tls.py``).
    """
    # 1. CA key + self-signed CA cert.
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(ca_key),
            "-out",
            str(ca_cert),
            "-days",
            "365",
            "-nodes",
            "-subj",
            "/CN=Test CA",
        ],
        check=True,
        capture_output=True,
    )

    # 2. Server key + CSR.
    subprocess.run(
        [
            "openssl",
            "req",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(server_key),
            "-out",
            str(server_csr),
            "-nodes",
            "-subj",
            f"/CN={sni_hostname}",
        ],
        check=True,
        capture_output=True,
    )

    # 3. Sign server leaf with the CA + apply the SAN extension.
    subprocess.run(
        [
            "openssl",
            "x509",
            "-req",
            "-in",
            str(server_csr),
            "-CA",
            str(ca_cert),
            "-CAkey",
            str(ca_key),
            "-CAcreateserial",
            "-out",
            str(server_cert),
            "-days",
            "365",
            "-extfile",
            str(server_ext),
        ],
        check=True,
        capture_output=True,
    )

    # 4. Build the chained PEM (leaf + CA) for ``ssl.SSLContext.load_cert_chain``.
    chain_pem.write_text(server_cert.read_text() + ca_cert.read_text())
