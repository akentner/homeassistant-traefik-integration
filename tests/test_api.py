"""Unit tests for TraefikApiClient (no HA hass required; uses unittest.mock).

TraefikApiClient has zero HA imports by design (Pitfall 3 mitigation), so the
tests avoid HA fixtures too — pure aiohttp session, mocked at the session.get()
level. Each test owns its mock and verifies the contract: status dispatch,
header injection, error paths, no token leakage.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.traefik.api import (
    TraefikApiClient,
    TraefikApiError,
    TraefikAuthError,
)

FIXTURES = Path(__file__).parent / "fixtures"
BASE_URL = "https://traefik.example.com:8080"


def _load(name: str) -> object:
    return json.loads((FIXTURES / name).read_text())


def _mock_response(status: int, body: object | None = None) -> MagicMock:
    """Build an aiohttp-like response object for testing.

    `body` is JSON-encoded and exposed via `async json(content_type=None)`.
    `status` is read directly. `raise_for_status()` raises ClientResponseError
    when status is non-2xx (matches aiohttp's semantics).
    """
    resp = MagicMock()
    resp.status = status

    async def _json(*_args, **_kwargs):
        if body is None:
            raise ValueError("no body")
        return body

    resp.json = _json

    def _raise_for_status() -> None:
        if status >= 400:
            err = aiohttp.ClientResponseError(MagicMock(), MagicMock(), status=status)
            # aiohttp expects `request_info` and `history`; provide stubs
            err.request_info = MagicMock()
            err.history = ()
            raise err

    resp.raise_for_status = _raise_for_status
    return resp


class _MockSession:
    """Async-context-manager session that returns a queued response per path."""

    def __init__(self) -> None:
        self.responses: dict[str, MagicMock] = {}
        self.calls: list[tuple[str, dict]] = []

    def queue(self, path: str, status: int, body: object | None = None) -> MagicMock:
        resp = _mock_response(status, body)
        self.responses[path] = resp
        return resp

    def get(self, url: str, *, headers=None, ssl=None, **_kwargs):
        # Capture the call for assertions (esp. headers + ssl)
        self.calls.append((url, {"headers": headers, "ssl": ssl}))

        # Build the right response or default to a network-style error
        matched = None
        for path, resp in self.responses.items():
            if url.endswith(path):
                matched = resp
                break
        if matched is None:
            raise aiohttp.ClientConnectorError(MagicMock(), OSError("no route"))

        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=matched)
        cm.__aexit__ = AsyncMock(return_value=None)
        return cm


def _build_client(session: _MockSession, api_key: str = "k") -> TraefikApiClient:
    return TraefikApiClient(session, BASE_URL, api_key=api_key)  # type: ignore[arg-type]


async def test_get_version_returns_parsed_json():
    session = _MockSession()
    session.queue("/api/version", 200, _load("traefik_version.json"))
    client = _build_client(session)
    result = await client.get_version()
    assert result["Version"] == "3.1.4"
    assert session.calls[0][1]["headers"] == {"Authorization": "Bearer k"}


async def test_get_routers_returns_list():
    session = _MockSession()
    session.queue("/api/http/routers", 200, _load("traefik_routers.json"))
    client = _build_client(session)
    result = await client.get_routers()
    assert isinstance(result, list)
    assert {r["name"] for r in result} >= {"my-router", "broken-router"}


async def test_auth_error_on_401():
    session = _MockSession()
    session.queue("/api/overview", 401)
    client = TraefikApiClient(session, BASE_URL, api_key="SECRET-DO-NOT-LOG")  # type: ignore[arg-type]
    with pytest.raises(TraefikAuthError):
        await client.get_overview()


async def test_api_error_on_500():
    session = _MockSession()
    session.queue("/api/overview", 500)
    client = _build_client(session)
    with pytest.raises(TraefikApiError) as exc:
        await client.get_overview()
    assert not isinstance(exc.value, TraefikAuthError)


async def test_api_error_on_timeout():
    """Timeout errors are surfaced as TraefikApiError, not TraefikAuthError."""

    class _TimeoutSession(_MockSession):
        def get(self, *_args, **_kwargs):
            raise TimeoutError()

    session = _TimeoutSession()
    client = _build_client(session)
    with pytest.raises(TraefikApiError):
        await client.get_overview()


async def test_token_never_logged(caplog):
    session = _MockSession()
    session.queue("/api/overview", 401)
    client = TraefikApiClient(session, BASE_URL, api_key="ULTRA-SECRET")  # type: ignore[arg-type]
    with pytest.raises(TraefikAuthError):
        await client.get_overview()
    log_text = caplog.text
    assert "ULTRA-SECRET" not in log_text


def test_no_client_session_in_api_module():
    """Pitfall #3 mitigation: production code never instantiates its own session."""
    api_path = Path(__file__).parent.parent / "custom_components" / "traefik" / "api.py"
    text = api_path.read_text()
    for line in text.splitlines():
        if "aiohttp.ClientSession(" in line and "session: aiohttp.ClientSession" not in line:
            raise AssertionError(f"ClientSession instantiation found: {line!r}")


async def test_no_authorization_header_when_key_empty():
    session = _MockSession()
    session.queue("/api/version", 200, _load("traefik_version.json"))
    client = TraefikApiClient(session, BASE_URL, api_key="")  # type: ignore[arg-type]
    await client.get_version()
    headers = session.calls[0][1]["headers"]
    assert headers == {}


async def test_verify_ssl_passed_through():
    session = _MockSession()
    session.queue("/api/version", 200, _load("traefik_version.json"))
    client = TraefikApiClient(session, BASE_URL, api_key="k", verify_ssl=False)  # type: ignore[arg-type]
    await client.get_version()
    assert session.calls[0][1]["ssl"] is False
