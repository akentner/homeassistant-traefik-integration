"""Config flow for the Traefik integration.

Implements four flows against the Phase 1 probe-and-validate foundation:

- :class:`TraefikConfigFlow.async_step_user` / ``async_step_yaml`` — initial
  setup (CFG-01 / CFG-02). Preserved byte-for-byte from Phase 1 so the
  integration tests against ``async_step_user`` keep passing.
- :class:`TraefikOptionsFlow.async_step_init` — post-setup scan_interval /
  verify_ssl / tls_warn_days knobs (CFG-05, CONTEXT.md D-09). Validates via
  ``vol.Range`` and aborts on out-of-range via the ``scan_interval_out_of_range``
  translation key.
- :class:`TraefikConfigFlow.async_step_reauth` / ``async_step_reauth_confirm``
  — token rotation (CFG-04, CONTEXT.md D-10). Fires when the coordinator
  raises ``ConfigEntryAuthFailed``; the new bearer token is validated against
  ``/api/overview`` and the entry's data is updated in place + reloaded.
- :class:`TraefikConfigFlow.async_step_reconfigure` — in-place URL + token
  change (CFG-03, CONTEXT.md D-11). Pre-fills from ``entry.data``, validates,
  and calls ``async_update_reload_and_abort`` with ``data_updates``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol
from aiohttp import ClientConnectorError, ClientResponseError, InvalidUrlClientError
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
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
    CONF_SCAN_INTERVAL,
    CONF_TLS_WARN_DAYS,
    CONF_URL,
    CONF_VERIFY_SSL,
    DEFAULT_NAME,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TLS_WARN_DAYS,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MAX_TLS_WARN_DAYS,
    MIN_SCAN_INTERVAL,
    MIN_TLS_WARN_DAYS,
)

_LOGGER = logging.getLogger(__name__)

# UI schemas use Home Assistant's typed selectors so HA's frontend can render
# the right input widget AND so ``voluptuous_serialize`` can serialise the
# schema for the JSON-over-WS config flow (a raw ``cv.url`` function ref in
# ``vol.All(cv.string, cv.url)`` breaks that serializer — see v0.1.3 release
# notes). URL shape is additionally enforced server-side by
# :func:`_check_url_shape` so a malformed URL still surfaces
# ``errors["base"] = "invalid_url"``.
STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
        vol.Optional(CONF_API_KEY, default=""): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
        vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): BooleanSelector(),
    }
)

# YAML schema has no frontend — plain string validators only; URL shape is
# revalidated by :func:`_validate_input` → :func:`_check_url_shape`.
STEP_YAML_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): cv.string,
        vol.Optional(CONF_API_KEY, default=""): cv.string,
        vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): cv.boolean,
    }
)

# Options Flow schema — scan_interval (15..300s), verify_ssl (bool),
# tls_warn_days (1..90d) per CONTEXT.md D-09. Clamps enforced via
# ``vol.Range``; the matching translation keys live at
# ``options.step.init.errors.scan_interval_out_of_range`` in strings.json.
STEP_OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(
            int, vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)
        ),
        vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): cv.boolean,
        vol.Optional(CONF_TLS_WARN_DAYS, default=DEFAULT_TLS_WARN_DAYS): vol.All(
            int, vol.Range(min=MIN_TLS_WARN_DAYS, max=MAX_TLS_WARN_DAYS)
        ),
    }
)


# Sentinel exceptions for the probe flow
class CannotConnect(Exception):
    """Network or Traefik unreachable."""


class InvalidAuth(Exception):
    """Traefik returned 401/403."""


class ApiDisabled(Exception):
    """Traefik's `api: {}` block not enabled (404 on /api/overview)."""


class InvalidUrl(Exception):
    """The provided URL is malformed (no scheme, bad scheme, missing host)."""


_ALLOWED_URL_SCHEMES = frozenset({"http", "https"})


def _check_url_shape(url: str) -> None:
    """Pre-flight URL shape check before handing it to aiohttp.

    Catches the common class of malformed-input bugs that the
    ``TextSelector(type=URL)`` field cannot block (e.g. ``http;//`` from
    autocomplete / paste mistakes). Raises :class:`InvalidUrl` if the URL
    is missing a scheme, has a non-HTTP scheme, or has no host.
    """
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_URL_SCHEMES or not parsed.netloc:
        raise InvalidUrl(url)


async def _validate_input(hass: Any, data: dict[str, Any]) -> None:
    """Probe /api/overview and map errors to user-friendly exceptions.

    Shared by the user / yaml / reauth / reconfigure flows so that the
    401 -> InvalidAuth, 404 -> ApiDisabled, invalid URL -> InvalidUrl,
    and everything-else -> CannotConnect mapping is defined exactly once.
    """
    _check_url_shape(data[CONF_URL])
    client = TraefikApiClient(
        session=async_get_clientsession(hass),
        base_url=data[CONF_URL],
        api_key=data.get(CONF_API_KEY, ""),
        verify_ssl=data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
    )
    try:
        async with asyncio.timeout(10):
            await client.get_overview()
    except InvalidUrlClientError as err:
        raise InvalidUrl(data[CONF_URL]) from err
    except TraefikAuthError:
        raise InvalidAuth from None
    except ClientResponseError as err:
        if err.status == 404:
            raise ApiDisabled from err
        raise CannotConnect from err
    except TimeoutError, ClientConnectorError, TraefikApiError:
        raise CannotConnect from None


class TraefikOptionsFlow(OptionsFlow):
    """Post-setup options flow (CFG-05, CONTEXT.md D-09).

    Exposes three knobs:

    - ``scan_interval`` (15..300s, default 15) — applied live by the
      ``_async_options_updated`` listener in ``__init__.py`` so a value
      change does not require an integration reload.
    - ``verify_ssl`` (bool, default True) — takes effect on the next
      coordinator cycle (full entry reload via HA's standard data-change
      trigger when the URL is unchanged).
    - ``tls_warn_days`` (1..90d, default 14) — stored as a placeholder for
      Phase 3 TLS binary sensors (CONTEXT.md D-09). Phase 3 picks the value
      up via ``coordinator.tls_warn_days``.
    """

    config_entry: ConfigEntry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Render the options form and persist user input."""
        errors: dict[str, str] = {}

        # Pre-fill defaults from the entry's currently-saved options so the
        # form shows the active values (not the integration defaults).
        current = self.config_entry.options
        defaults = {
            CONF_SCAN_INTERVAL: current.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            CONF_VERIFY_SSL: current.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            CONF_TLS_WARN_DAYS: current.get(CONF_TLS_WARN_DAYS, DEFAULT_TLS_WARN_DAYS),
        }

        if user_input is not None:
            try:
                validated = STEP_OPTIONS_SCHEMA(user_input)
            except vol.Invalid as err:
                # Per-field error so the UI can highlight the failing knob.
                # ``options.step.init.errors.scan_interval_out_of_range`` is
                # the shared translation key for both scan_interval and
                # tls_warn_days out-of-range (the bounds differ but the
                # user-facing message is the same shape).
                if err.path and err.path[0] in (CONF_SCAN_INTERVAL, CONF_TLS_WARN_DAYS):
                    errors[str(err.path[0])] = "scan_interval_out_of_range"
                else:
                    errors["base"] = "scan_interval_out_of_range"
            else:
                return self.async_create_entry(title="", data=validated)

        # Build a schema whose default values match the current entry options.
        schema_dict: dict[Any, Any] = {
            vol.Required(CONF_SCAN_INTERVAL, default=defaults[CONF_SCAN_INTERVAL]): vol.All(
                int, vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)
            ),
            vol.Optional(CONF_VERIFY_SSL, default=defaults[CONF_VERIFY_SSL]): cv.boolean,
            vol.Optional(CONF_TLS_WARN_DAYS, default=defaults[CONF_TLS_WARN_DAYS]): vol.All(
                int, vol.Range(min=MIN_TLS_WARN_DAYS, max=MAX_TLS_WARN_DAYS)
            ),
        }
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
        )


class TraefikConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Traefik."""

    VERSION = 1
    MINOR_VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> TraefikOptionsFlow:
        """Return the options flow handler (CFG-05 / CONTEXT.md D-09).

        The flow itself stores its values in ``entry.options``; the
        ``_async_options_updated`` listener in ``__init__.py`` applies the
        new ``scan_interval`` to the running coordinator so the change is
        live (no integration reload required for interval tweaks).
        """
        return TraefikOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the UI setup step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await _validate_input(self.hass, user_input)
            except InvalidUrl:
                errors["base"] = "invalid_url"
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

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_yaml(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle YAML import (CFG-02)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            validated = STEP_YAML_DATA_SCHEMA(user_input)
            try:
                await _validate_input(self.hass, validated)
            except InvalidUrl:
                errors["base"] = "invalid_url"
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

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle reauth entry point (CFG-04 / CONTEXT.md D-10).

        Triggered by HA when the coordinator raises ``ConfigEntryAuthFailed``
        (e.g., the user rotated the bearer token in Traefik's static config).
        Delegates straight to :meth:`async_step_reauth_confirm` so the
        ``step_id`` of the rendered form is ``reauth_confirm`` and HA's
        submission loop dispatches back to the same handler.
        """
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Validate the new bearer token and persist it (CONTEXT.md D-10).

        On success: ``async_update_entry`` writes the new token into
        ``entry.data`` (URL and verify_ssl untouched), ``async_reload``
        triggers HA's standard entry reload so the new token is picked up,
        and the flow aborts with ``reauth_successful``.
        """
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            new_api_key = user_input.get(CONF_API_KEY, "")
            probe_data = {
                CONF_URL: entry.data[CONF_URL],
                CONF_API_KEY: new_api_key,
                CONF_VERIFY_SSL: entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            }
            try:
                await _validate_input(self.hass, probe_data)
            except InvalidUrl:
                errors["base"] = "invalid_url"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except ApiDisabled:
                errors["base"] = "api_disabled"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            else:
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={**entry.data, CONF_API_KEY: new_api_key},
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        schema = vol.Schema(
            {
                vol.Optional(CONF_API_KEY, default=""): cv.string,
            }
        )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle in-place URL + token change (CFG-03 / CONTEXT.md D-11).

        Pre-fills the form from ``entry.data``. On submit, validates the new
        endpoint via ``/api/overview`` and calls
        ``async_update_reload_and_abort`` with ``data_updates`` so HA
        rewrites the entry's data in place and reloads — no delete + re-add
        required.
        """
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            try:
                await _validate_input(self.hass, user_input)
            except InvalidUrl:
                errors["base"] = "invalid_url"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except ApiDisabled:
                errors["base"] = "api_disabled"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            else:
                # Re-set the flow's unique_id against the new URL host so we
                # can abort if another Traefik instance already claims it.
                host = urlparse(user_input[CONF_URL]).hostname or user_input[CONF_URL]
                await self.async_set_unique_id(host)
                # Default ``updates=None`` => abort on conflict with another
                # entry. We do NOT pass ``updates=`` because we never want to
                # silently overwrite a different Traefik entry.
                self._abort_if_unique_id_configured()
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_URL: user_input[CONF_URL],
                        CONF_API_KEY: user_input.get(CONF_API_KEY, ""),
                        CONF_VERIFY_SSL: user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                    },
                )

        # Pre-fill defaults from current entry data so the form shows the
        # currently-saved URL / token / verify_ssl.
        defaults = {
            CONF_URL: entry.data.get(CONF_URL, ""),
            CONF_API_KEY: entry.data.get(CONF_API_KEY, ""),
            CONF_VERIFY_SSL: entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
        }
        schema = vol.Schema(
            {
                vol.Required(CONF_URL, default=defaults[CONF_URL]): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.URL)
                ),
                vol.Optional(CONF_API_KEY, default=defaults[CONF_API_KEY]): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
                vol.Optional(CONF_VERIFY_SSL, default=defaults[CONF_VERIFY_SSL]): BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            errors=errors,
        )
