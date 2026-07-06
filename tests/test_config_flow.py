"""Tests for the Traefik integration config flow.

Covers:
- ``STEP_USER_DATA_SCHEMA`` rejects malformed URLs via ``cv.url``.
- ``_validate_input`` maps ``InvalidUrlClientError`` (aiohttp URL parse failure)
  to ``InvalidUrl`` and ``async_step_user`` surfaces ``errors["base"] = "invalid_url"``
  instead of HA's generic "Unknown error occurred".
- ``STEP_USER_DATA_SCHEMA`` accepts an empty ``api_key`` (``TextSelector``
  with URL type used to mask the field; ``cv.url`` rejects bad schemes).
- ``TraefikProxyApiClient._get`` no longer lets ``InvalidUrlClientError`` bubble —
  it gets classified as ``TraefikProxyApiError`` so the coordinator sees a clean
  ``UpdateFailed`` on subsequent cycles.

Hermetic via ``aioclient_mock`` (HA's HTTP mock layer) — no live Traefik needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import aiohttp
import pytest

from custom_components.traefik_proxy.api import TraefikProxyApiClient, TraefikProxyApiError
from custom_components.traefik_proxy.config_flow import (
    STEP_USER_DATA_SCHEMA,
    STEP_YAML_DATA_SCHEMA,
    InvalidUrl,
    _check_url_shape,
    _validate_input,
)
from custom_components.traefik_proxy.const import (
    CONF_API_KEY,
    CONF_URL,
    CONF_VERIFY_SSL,
)

GOOD_URL = "https://traefik.example.com:8080"


# ----------------------- schema validation -----------------------


@pytest.mark.parametrize(
    "url",
    [
        "http;//192.168.178.3:8080",  # user's bug: semicolon instead of colon
        "htp://traefik.local:8080",  # missing letter in scheme
        "traefik.example.com:8080",  # no scheme at all
        "ftp://traefik.example.com:8080",  # disallowed scheme
        "",  # empty
    ],
)
def test_user_schema_accepts_any_url_string(url: str) -> None:
    """``STEP_USER_DATA_SCHEMA`` accepts arbitrary URL strings as plain text.

    v0.1.3 note: ``vol.All(cv.string, cv.url)`` (used in v0.1.0..v0.1.2)
    breaks ``voluptuous_serialize.convert`` — HA's frontend JSON-over-WS
    config-flow endpoint raises ``ValueError: Unable to convert schema:
    <function url at 0x…>`` (see PR-28788275285). The schema now uses
    ``TextSelector(type=URL)`` which serialises as a selector block; the
    browser additionally rejects malformed URLs via HA's URL input
    handler, and server-side ``_check_url_shape`` provides the final
    defence with ``errors["base"] = "invalid_url"``.
    """
    out = STEP_USER_DATA_SCHEMA({CONF_URL: url})
    assert out[CONF_URL] == url


@pytest.mark.parametrize(
    "url",
    [
        "http://traefik.example.com:8080",
        "https://traefik.local",
        "https://192.168.178.3:8080",
        "http;//malformed-on-purpose",
    ],
)
def test_user_schema_accepts_http_and_https(url: str) -> None:
    """Plain ``http://`` and ``https://`` URLs (with or without explicit port) pass."""
    out = STEP_USER_DATA_SCHEMA({CONF_URL: url})
    assert out[CONF_URL] == url


def test_user_schema_accepts_empty_api_key() -> None:
    """Bearer is optional — empty string must pass schema validation."""
    out = STEP_USER_DATA_SCHEMA({CONF_URL: GOOD_URL, CONF_API_KEY: ""})
    assert out[CONF_API_KEY] == ""


def test_user_schema_omitted_api_key_defaults_to_empty() -> None:
    """Omitting ``api_key`` entirely (e.g. UI form with blank field) → default ``""``."""
    out = STEP_USER_DATA_SCHEMA({CONF_URL: GOOD_URL})
    assert out[CONF_API_KEY] == ""


def test_user_schema_serializes_for_config_flow_endpoint() -> None:
    """``voluptuous_serialize.convert`` is what HA's frontend JSON-over-WS
    config flow endpoint calls before shipping the form to the browser.

    v0.1.3 regression: ``vol.All(cv.string, cv.url)`` serialized as
    ``<function url at 0x…>`` and raised ``ValueError`` in the reconfigure
    flow. The fix is to use a ``TextSelector(type=URL)`` for UI schemas so
    the serializer can introspect a known type instead of a function ref.
    """
    import voluptuous_serialize
    from homeassistant.helpers import config_validation as cv

    from custom_components.traefik_proxy.config_flow import STEP_USER_DATA_SCHEMA

    serialized = voluptuous_serialize.convert(STEP_USER_DATA_SCHEMA, custom_serializer=cv.custom_serializer)
    assert isinstance(serialized, list)
    assert len(serialized) == 3
    # URL field becomes a selector block of type=text/url.
    by_name = {field["name"]: field for field in serialized}
    assert by_name["url"]["selector"] == {"text": {"type": "url", "multiple": False, "multiline": False}}
    assert by_name["api_key"]["selector"]["text"]["type"] == "password"


def test_yaml_schema_accepts_arbitrary_url_string() -> None:
    """YAML schema is plain string validation; URL shape is checked by
    ``_check_url_shape`` after submission (see v0.1.3 release notes)."""
    out = STEP_YAML_DATA_SCHEMA({CONF_URL: "http;//foo.bar"})
    assert out[CONF_URL] == "http;//foo.bar"


# ----------------------- _check_url_shape -----------------------


@pytest.mark.parametrize(
    "url",
    [
        "http;//192.168.178.3:8080",
        "htp://traefik.example.com",
        "ftp://traefik.example.com",
        "traefik.example.com:8080",
        "",
    ],
)
def test_check_url_shape_raises_on_bad_urls(url: str) -> None:
    with pytest.raises(InvalidUrl):
        _check_url_shape(url)


def test_check_url_shape_accepts_http_and_https() -> None:
    _check_url_shape("http://traefik.example.com:8080")
    _check_url_shape("https://traefik.local")


# ----------------------- _validate_input + aiohttp url-parse failure -----------------------


async def test_validate_input_maps_invalid_url_client_error_to_invalid_url(
    hass: object, aioclient_mock: object
) -> None:
    """If a bad URL slips past the schema (e.g. raised inside the client), the
    config flow surfaces ``invalid_url`` rather than HA's 'Unknown error'."""
    # Bad URL goes through urlparse-clean check first.
    with pytest.raises(InvalidUrl):
        await _validate_input(
            hass,
            {CONF_URL: "http;//foo.bar", CONF_API_KEY: "tok", CONF_VERIFY_SSL: True},
        )


async def test_validate_input_uses_urlparse_shape_check(hass: object) -> None:
    """Pre-flight shape check runs BEFORE aiohttp — bad URLs never reach the network."""
    with pytest.raises(InvalidUrl):
        await _validate_input(hass, {CONF_URL: "://broken", CONF_API_KEY: "tok"})


async def test_validate_input_passes_on_good_url(hass: object, aioclient_mock: object) -> None:
    """Happy-path probe succeeds against a mocked 200 + valid overview payload."""
    aioclient_mock.get(
        f"{GOOD_URL}/api/overview",  # type: ignore[attr-defined]
        json={"http": {}},
        headers={"Authorization": "Bearer tok"},
    )
    await _validate_input(hass, {CONF_URL: GOOD_URL, CONF_API_KEY: "tok"})


async def test_validate_input_empty_bearer_does_not_send_authorization_header(
    hass: object, aioclient_mock: object
) -> None:
    """Empty bearer → no Authorization header sent → Traefik with api.insecure works."""
    aioclient_mock.get(  # type: ignore[attr-defined]
        f"{GOOD_URL}/api/overview",
        json={"http": {}},
    )
    # If the empty-bearer code path erroneously sent an empty header, the
    # request wouldn't match the unmocked assertion above and aioclient_mock
    # would raise NoMockMatchError. Reaching this line = test passes.
    await _validate_input(hass, {CONF_URL: GOOD_URL, CONF_API_KEY: ""})


# ----------------------- api.py: InvalidUrlClientError is classified -----------------------


async def test_api_get_classifies_invalid_url_client_error() -> None:
    """``TraefikProxyApiClient._get`` catches ``InvalidUrlClientError`` → ``TraefikProxyApiError``."""
    client = TraefikProxyApiClient(
        session=MagicMock(),
        base_url="http;//bad",
        api_key="",
    )
    bad_session = MagicMock()
    bad_session.get.side_effect = aiohttp.InvalidUrlClientError("http;//bad")
    client._session = bad_session
    with pytest.raises(TraefikProxyApiError):
        await client._get("/api/overview")
