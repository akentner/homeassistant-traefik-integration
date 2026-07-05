"""Constants for the Traefik integration."""

from typing import Final

DOMAIN: Final = "traefik"

# Configuration keys (entry.data)
CONF_URL: Final = "url"
CONF_API_KEY: Final = "api_key"
CONF_VERIFY_SSL: Final = "verify_ssl"
CONF_SCAN_INTERVAL: Final = "scan_interval"

# Defaults
DEFAULT_VERIFY_SSL: Final = True
DEFAULT_SCAN_INTERVAL: Final = 15  # seconds (CONTEXT.md D-12)
DEFAULT_NAME: Final = "Traefik"

# Version - bumped manually; CI in Plan 04 enforces match with git tag
VERSION: Final = "1.0.0"

# Platforms forwarded in async_setup_entry
PLATFORMS: Final = ["binary_sensor"]
