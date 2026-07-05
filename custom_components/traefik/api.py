"""Async Traefik API client (no HA imports; pure aiohttp wrapper)."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

# Matches Traefik's `@<provider>` naming convention (CONTEXT.md D-06 / PITFALLS
# #2). Traefik exposes provider-suffixed items as internal: services like
# `api@internal`, middlewares like `strip@docker`, routers like `api@internal`.
# Traefik HA entity-IDs reject `@`, so we drop these before surfacing them.
_INTERNAL_ITEM_RE = re.compile(r"@\w+")


class TraefikApiError(Exception):
    """Raised for non-auth Traefik API failures (5xx, network, parse)."""


class TraefikAuthError(TraefikApiError):
    """Raised on 401/403 — caller maps to ConfigEntryAuthFailed."""


def filter_internal_items(
    items: list[dict[str, Any]],
    *,
    name_key: str = "name",
) -> list[dict[str, Any]]:
    """Drop Traefik-internal `@<provider>` suffixed items.

    Reused by services, middlewares, and routers platforms (CONTEXT.md D-06).
    A bare trailing `@` with no provider is preserved — Traefik-internal names
    always carry `<name>@<provider>` where `<provider>` is ``\\w+``.

    :param items: list of dicts each with a ``name`` (or ``name_key``) field.
    :param name_key: name of the key whose value carries the item's display
        name. Defaults to ``"name"``.
    :return: filtered list with internal items removed.
    """
    return [item for item in items if not _INTERNAL_ITEM_RE.search(str(item.get(name_key, "")))]


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

    # --- Read endpoints ---
    async def get_version(self) -> dict[str, Any]:
        """GET /api/version — returns {Version, Codename, StartDate}."""
        result = await self._get("/api/version")
        assert isinstance(result, dict)
        return result

    async def get_entrypoints(self) -> list[dict[str, Any]]:
        """GET /api/entrypoints — list of configured TCP/UDP entrypoints."""
        result = await self._get("/api/entrypoints")
        assert isinstance(result, list)
        return result

    async def get_routers(self) -> list[dict[str, Any]]:
        """GET /api/http/routers."""
        result = await self._get("/api/http/routers")
        assert isinstance(result, list)
        return result

    async def get_http_services(self) -> list[dict[str, Any]]:
        """GET /api/http/services — list of HTTP service definitions."""
        result = await self._get("/api/http/services")
        assert isinstance(result, list)
        return result

    async def get_http_middlewares(self) -> list[dict[str, Any]]:
        """GET /api/http/middlewares — list of HTTP middleware definitions."""
        result = await self._get("/api/http/middlewares")
        assert isinstance(result, list)
        return result

    async def get_overview(self) -> dict[str, Any]:
        """GET /api/overview — used by config flow for auth probe."""
        result = await self._get("/api/overview")
        assert isinstance(result, dict)
        return result

    # --- Write endpoints ---
    async def reload_routers(self) -> None:
        """POST /api/http/routers/refresh — ask Traefik to reload routers.

        Returns ``None`` on 2xx. Raises ``TraefikAuthError`` on 401/403 and
        ``TraefikApiError`` on other non-2xx. Does NOT poll — the reload
        service handler (plan 02-04) is responsible for verifying that the
        reload actually completed (CONTEXT.md D-05/D-12).

        PITFALLS #15 — Traefik returns 202 before the reload finishes; we do
        not block here. The empty POST body requires an explicit
        ``Content-Length: 0`` header — aiohttp would otherwise hang waiting
        for the writer to flush.
        """
        url = f"{self._base_url}/api/http/routers/refresh"
        try:
            async with asyncio.timeout(self._request_timeout):
                async with self._session.post(
                    url,
                    headers={**self._headers(), "Content-Length": "0"},
                    ssl=self._verify_ssl,
                ) as response:
                    _LOGGER.debug("path=%s status=%s", "/api/http/routers/refresh", response.status)
                    if response.status in (401, 403):
                        raise TraefikAuthError(f"Auth failed for /api/http/routers/refresh: {response.status}")
                    response.raise_for_status()
                    return None
        except aiohttp.ClientResponseError as err:
            if err.status in (401, 403):
                raise TraefikAuthError(str(err)) from err
            raise TraefikApiError(str(err)) from err
        except (TimeoutError, aiohttp.ClientConnectorError) as err:
            raise TraefikApiError(str(err)) from err

    # --- Aggregated fetch ---
    async def fetch_all(self) -> dict[str, Any]:
        """Phase 2 parallel fetch: all six endpoints in one asyncio.gather.

        Per CONTEXT.md D-07, partial-failure policy is: auth failures raise
        immediately (the coordinator maps them to ConfigEntryAuthFailed), and
        any other exception bubbles to the first non-auth error so the
        coordinator surfaces a single ``UpdateFailed`` per cycle. Successful
        sections are NOT returned on partial failure — the caller decides
        whether to fall back to stale coordinator data (the DataUpdateCoordinator
        default keeps last-known-good data).
        """
        version, entrypoints, http_routers, http_services, http_middlewares, overview = await asyncio.gather(
            self.get_version(),
            self.get_entrypoints(),
            self.get_routers(),
            self.get_http_services(),
            self.get_http_middlewares(),
            self.get_overview(),
            return_exceptions=True,
        )
        # Auth failures always win — never swallow them (PITFALLS).
        for result in (version, entrypoints, http_routers, http_services, http_middlewares, overview):
            if isinstance(result, TraefikAuthError):
                raise result
        # First non-auth exception wins (CONTEXT.md D-07).
        first_exc = next(
            (
                r
                for r in (
                    version,
                    entrypoints,
                    http_routers,
                    http_services,
                    http_middlewares,
                    overview,
                )
                if isinstance(r, BaseException)
            ),
            None,
        )
        if first_exc is not None:
            raise first_exc
        return {
            "version": version,
            "entrypoints": entrypoints,
            "http_routers": http_routers,
            "http_services": http_services,
            "http_middlewares": http_middlewares,
            "overview": overview,
        }
