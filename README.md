# Home Assistant Traefik Integration

Expose your Traefik reverse-proxy state as Home Assistant entities.
See at a glance which routers are enabled, which are failing.

## What it does

Phase 1 (this release):

- One `binary_sensor` per Traefik HTTP router (state: enabled / not enabled)
- Polls `/api/version` + `/api/http/routers` every 15 seconds (configurable in Phase 2)
- UI config flow (`Configuration → Integrations → Add → Traefik`) or YAML
- Bearer token per request — never logged; never stored as a session default

Phase 2+ (planned, not in this release): entrypoints, services, overview,
reload service, options flow, reauth flow, TLS certificate expiry.

## Installation

### HACS (recommended)

1. HACS → Integrations → ⋮ → Custom repositories
2. Add `https://github.com/akentner/homeassistant-traefik-integration` (Integration)
3. Install → Restart Home Assistant
4. Settings → Devices & Services → + Add Integration → Traefik

### Manual

```bash
scp -r custom_components/traefik haos-op3050-1:/config/custom_components/
ha core restart
```

(Replace `haos-op3050-1` with your HA host. `ha` is the user's HA CLI alias.)

## Configuration

### UI flow (recommended)

Settings → Devices & Services → + Add Integration → Traefik:

| Field       | Example                              | Notes |
|-------------|--------------------------------------|-------|
| URL         | `https://traefik.example.com:8080`   | Where Traefik's API is reachable. |
| API key     | (paste from Traefik static config)   | Bearer token from `api.dashboard` or your auth middleware. |
| Verify SSL  | `true`                               | Disable for self-signed certificates. |

### YAML (alternative)

```yaml
# configuration.yaml
traefik:
  url: https://traefik.example.com:8080
  api_key: "${traefik_bearer_token}"
  verify_ssl: true
```

## Troubleshooting

- **`invalid_auth`** — bearer token rejected (401/403). Generate a new one in Traefik's static config.
- **`api_disabled`** — Traefik's `api:` block isn't enabled. Add `api: insecure: true` (with HTTPS reverse-proxy in front) or enable the dashboard.
- **`cannot_connect`** — Traefik unreachable. Verify URL, network, firewall.

## Attribution

This integration uses the [Traefik](https://traefik.io) logo under the Apache License, Version 2.0.
