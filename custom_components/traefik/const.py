"""Constants for the Traefik integration."""

from typing import Final

DOMAIN: Final = "traefik"

# Configuration keys (entry.data)
CONF_URL: Final = "url"
CONF_API_KEY: Final = "api_key"
CONF_VERIFY_SSL: Final = "verify_ssl"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_TLS_WARN_DAYS: Final = "tls_warn_days"

# Defaults
DEFAULT_VERIFY_SSL: Final = True
DEFAULT_SCAN_INTERVAL: Final = 15  # seconds (CONTEXT.md D-12)
DEFAULT_NAME: Final = "Traefik"

# Clamps for Options Flow (Phase 2 — Plan 02-02 fills these in).
# MIN_SCAN_INTERVAL/MAX_SCAN_INTERVAL bound the scan-interval knob
# (CONTEXT.md D-09 — 15s lower bound keeps the API polite; 300s upper bound
# keeps entity states fresh enough to be useful). MIN_TLS_WARN_DAYS /
# MAX_TLS_WARN_DAYS bound the cert-expiry warning threshold placeholder
# (CONTEXT.md D-09 — Phase 3 picks the value up).
MIN_SCAN_INTERVAL: Final = 15
MAX_SCAN_INTERVAL: Final = 300
MIN_TLS_WARN_DAYS: Final = 1
MAX_TLS_WARN_DAYS: Final = 90
DEFAULT_TLS_WARN_DAYS: Final = 14

# Phase 3 cert-cycle knobs (CONTEXT.md D-05). ``TLS_HANDSHAKE_TIMEOUT`` caps
# the per-host TLS handshake at 5s — a hanging host cannot stall the cycle
# indefinitely. ``TLS_SEMAPHORE`` bounds concurrent handshakes to 4 (a
# hostile config with 10k routers would otherwise fan out a thundering
# herd). ``DEFAULT_TLS_CERT_COOLDOWN`` is the 6h cadence (21600s) — the
# spike's 6h default; a 14d warning threshold with 6h probes means a
# user is alerted within ±6h of a cert entering the warning window.
TLS_HANDSHAKE_TIMEOUT: Final = 5.0
TLS_SEMAPHORE: Final = 4
DEFAULT_TLS_CERT_COOLDOWN: Final = 21600

# Version - bumped manually; CI in Plan 04 enforces match with git tag
VERSION: Final = "1.0.0"

# Platforms forwarded in async_setup_entry.
# Phase 2 adds sensor (entrypoint/service/aggregate) and button (reload).
PLATFORMS: Final = ["binary_sensor", "sensor", "button"]
