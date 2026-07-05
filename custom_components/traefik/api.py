"""Async Traefik API client (no HA imports; pure aiohttp wrapper)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)


class TraefikApiError(Exception):
    """Raised for non-auth Traefik API failures (5xx, network, parse)."""


class TraefikAuthError(TraefikApiError):
    """Raised on 401/403 — caller maps to ConfigEntryAuthFailed."""


class TraefikApiClient:
    """Async client for Traefik's HTTP API (v2.11+ / v3.x)."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        api_key: str,
        *,
        verify_ssl: bool = True,
        request_timeout: float = 10.0,
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._verify_ssl = verify_ssl
        self._request_timeout = request_timeout

    def _headers(self) -> dict[str, str]:
        """Per-request auth header — never default header on session."""
        if self._api_key:
            return {"Authorization": f"Bearer {self._api_key}"}
        return {}

    async def _get(self, path: str) -> Any:
        url = f"{self._base_url}{path}"
        try:
            async with asyncio.timeout(self._request_timeout):
                async with self._session.get(
                    url,
                    headers=self._headers(),
                    ssl=self._verify_ssl,
                ) as response:
                    _LOGGER.debug("path=%s status=%s", path, response.status)
                    if response.status in (401, 403):
                        raise TraefikAuthError(f"Auth failed for {path}: {response.status}")
                    response.raise_for_status()
                    return await response.json(content_type=None)
        except aiohttp.ClientResponseError as err:
            # raise_for_status already raised; re-classify for safety
            if err.status in (401, 403):
                raise TraefikAuthError(str(err)) from err
            raise TraefikApiError(str(err)) from err
        except (TimeoutError, aiohttp.ClientConnectorError) as err:
            raise TraefikApiError(str(err)) from err

    # --- Endpoints used in Phase 1 ---
    async def get_version(self) -> dict[str, Any]:
        """GET /api/version — returns {Version, Codename, StartDate}."""
        result = await self._get("/api/version")
        assert isinstance(result, dict)
        return result

    async def get_routers(self) -> list[dict[str, Any]]:
        """GET /api/http/routers."""
        result = await self._get("/api/http/routers")
        assert isinstance(result, list)
        return result

    async def get_overview(self) -> dict[str, Any]:
        """GET /api/overview — used by config flow for auth probe."""
        result = await self._get("/api/overview")
        assert isinstance(result, dict)
        return result

    async def fetch_all(self) -> dict[str, Any]:
        """Phase 1 parallel fetch: version + routers wrapped in one asyncio.gather."""
        version, routers = await asyncio.gather(
            self.get_version(),
            self.get_routers(),
            return_exceptions=True,
        )
        # Phase 1: surface individual errors so coordinator can dispatch;
        # in v1.1 we collapse both into TraefikData shape.
        if isinstance(version, Exception):
            raise version
        if isinstance(routers, Exception):
            raise routers
        return {"version": version, "routers": routers}
