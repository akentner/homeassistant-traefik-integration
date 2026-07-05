"""Pytest fixtures for Traefik integration tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

# Ensure custom_components is importable from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

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
