"""Config flow for the Traefik integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol
from aiohttp import ClientConnectorError, ClientResponseError
from homeassistant.config_entries import ConfigFlow
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import TraefikApiClient, TraefikApiError, TraefikAuthError
from .const import (
    CONF_API_KEY,
    CONF_URL,
    CONF_VERIFY_SSL,
    DEFAULT_NAME,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.URL)
        ),
        vol.Required(CONF_API_KEY): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): BooleanSelector(),
    }
)

STEP_YAML_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): cv.string,
        vol.Required(CONF_API_KEY): cv.string,
        vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): cv.boolean,
    }
)


# Sentinel exceptions for the probe flow
class CannotConnect(Exception):
    """Network or Traefik unreachable."""


class InvalidAuth(Exception):
    """Traefik returned 401/403."""


class ApiDisabled(Exception):
    """Traefik's `api: {}` block not enabled (404 on /api/overview)."""


async def _validate_input(hass, data: dict[str, Any]) -> None:
    """Probe /api/overview and map errors to user-friendly exceptions."""
    client = TraefikApiClient(
        session=async_get_clientsession(hass),
        base_url=data[CONF_URL],
        api_key=data[CONF_API_KEY],
        verify_ssl=data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
    )
    try:
        async with asyncio.timeout(10):
            await client.get_overview()
    except TraefikAuthError:
        raise InvalidAuth from None
    except ClientResponseError as err:
        if err.status == 404:
            raise ApiDisabled from err
        raise CannotConnect from err
    except (ClientConnectorError, asyncio.TimeoutError, TraefikApiError):
        raise CannotConnect from None


class TraefikConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Traefik."""

    VERSION = 1
    MINOR_VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle the UI setup step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await _validate_input(self.hass, user_input)
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except ApiDisabled:
                errors["base"] = "api_disabled"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            else:
                # unique_id = URL host avoids double-add for same Traefik
                host = urlparse(user_input[CONF_URL]).hostname or user_input[CONF_URL]
                await self.async_set_unique_id(host)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=DEFAULT_NAME,
                    data=user_input,
                )

        http_warning = (
            ""
            if (user_input or {}).get(CONF_URL, "").startswith("https://")
            else "config_flow_warning_http"
        )
        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders={
                "http_warning": http_warning,
            },
        )

    async def async_step_yaml(self, user_input: dict[str, Any] | None = None):
        """Handle YAML import (CFG-02)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            validated = STEP_YAML_DATA_SCHEMA(user_input)
            try:
                await _validate_input(self.hass, validated)
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except ApiDisabled:
                errors["base"] = "api_disabled"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            else:
                host = urlparse(validated[CONF_URL]).hostname or validated[CONF_URL]
                await self.async_set_unique_id(host)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=DEFAULT_NAME,
                    data=validated,
                )
        return self.async_show_form(
            step_id="yaml",
            data_schema=STEP_YAML_DATA_SCHEMA,
            errors=errors,
        )
