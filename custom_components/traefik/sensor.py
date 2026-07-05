"""Sensor platform for the Traefik integration.

Phase 2 plan 02-03 fills this out with `TraefikEntrypointSensor`,
`TraefikServiceSensor`, and the three aggregate count sensors on the Overview
device (D-15/D-16/D-17). For now this module is a forward-reference stub so
`PLATFORMS = ["binary_sensor", "sensor", "button"]` (added in plan 02-01)
does not crash `hass.config_entries.async_forward_entry_setups`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .coordinator import TraefikConfigEntry

if TYPE_CHECKING:
    pass


async def async_setup_entry(
    hass: Any,
    entry: TraefikConfigEntry,
    async_add_entities: Any,
) -> None:
    """Set up Traefik sensor entities (filled out in plan 02-03)."""
    return None
