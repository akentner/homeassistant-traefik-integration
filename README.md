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

## Entities

After setup the integration exposes the following entities on three
per-category devices (`HTTP Routers`, `HTTP Services`, `HTTP Middlewares`)
plus a `Diagnostics` device for problem aggregates.

### Overview device — aggregate counts

Three sensors on the **Overview** device, one per Traefik category:

- `sensor.traefik_routers`
- `sensor.traefik_services`
- `sensor.traefik_middlewares`

Each sensor exposes the following attributes (read live from
`coordinator.data` on every cycle — see CHANGELOG for the v0.1.4 fix):

| Attribute | Type | Meaning |
|---|---|---|
| `filtered_count` | int | Items excluding Traefik internals (`api@internal`, `dashboard@internal`, …) |
| `success_count` | int | Items with `status == "enabled"` (Traefik-dashboard "success" slice) |
| `warning_count` | int | Items with `status == "warning"` |
| `error_count` | int | Items with `status == "error"` |
| `disabled_count` | int | Items with `status == "disabled"` (admin-opt-out) |
| `status_breakdown` | dict | `{success, warning, error, disabled}` for templating |
| `success_pct` | float | `success / (success + warning + error) × 100`, clamped 0–100 |
| `http_count` / `tcp_count` / `udp_count` | int | (Routers/Services only) Transport breakdown from `/api/overview` |

### Diagnostics device — problem aggregates

Three binary sensors with `device_class=PROBLEM` and
`entity_registry_enabled_default=False` (PITFALLS M-12 — opt-in, doesn't
pollute the States panel):

- `binary_sensor.traefik_any_router_failing` (Phase 2)
- `binary_sensor.traefik_any_service_failing` (v0.2.0)
- `binary_sensor.traefik_any_middleware_failing` (v0.2.0)

Each is `True` when at least one item has `status != "enabled"`.
Attributes expose the failing item names so dashboards can drill down.

### Replicating the Traefik dashboard pie charts in HA

The Traefik dashboard renders three pie slices per category (success /
warning / error). Replicate with `custom:modern-circular-gauge` (HACS
Frontend card):

```yaml
type: grid
columns: 4
square: false
cards:
  - type: custom:modern-circular-gauge
    entity: sensor.traefik_routers
    name: Routers — Success
    min: 0
    max: "{{ states('sensor.traefik_routers') | int(0) }}"
    value: "{{ state_attr('sensor.traefik_routers', 'success_count') | int(0) }}"
    color_stops:
      0: "#28a745"
      100: "#28a745"

  - type: custom:modern-circular-gauge
    entity: sensor.traefik_routers
    name: Routers — Warning
    value: "{{ state_attr('sensor.traefik_routers', 'warning_count') | int(0) }}"
    color_stops:
      0: "#ffc107"
      100: "#ffc107"

  - type: custom:modern-circular-gauge
    entity: sensor.traefik_routers
    name: Routers — Error
    value: "{{ state_attr('sensor.traefik_routers', 'error_count') | int(0) }}"
    color_stops:
      0: "#dc3545"
      100: "#dc3545"

  - type: custom:modern-circular-gauge
    entity: sensor.traefik_routers
    name: Routers — Disabled
    value: "{{ state_attr('sensor.traefik_routers', 'disabled_count') | int(0) }}"
    color_stops:
      0: "#6c757d"
      100: "#6c757d"
```

Repeat the four-card grid for `sensor.traefik_services` and
`sensor.traefik_middlewares`. The `max: "{{ states('sensor.traefik_routers') | int(0) }}"`
expression sets the gauge scale to the current filtered total so each
slice reads as "N of M".

For a single composite gauge showing the success rate as a percentage:

```yaml
type: custom:modern-circular-gauge
entity: sensor.traefik_routers
name: Routers — Success Rate
value: "{{ state_attr('sensor.traefik_routers', 'success_pct') | float(0) }}"
min: 0
max: 100
unit: "%"
color_stops:
  0: "#dc3545"
  80: "#ffc107"
  100: "#28a745"
```

Combine with a `binary_sensor.traefik_any_router_failing` card on a
**Conditions** card to alert when any item leaves the `enabled` state.
