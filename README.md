# Home Assistant Traefik Proxy Integration

Expose your Traefik Proxy reverse-proxy state as Home Assistant entities.
See at a glance which routers are enabled, which are failing.

## What it does

- One `binary_sensor` per Traefik HTTP router (state: enabled / not enabled)
- Polls `/api/version` + `/api/http/routers` every 15 seconds (configurable in Phase 2)
- UI config flow (`Configuration → Integrations → Add → Traefik Proxy`) or YAML
- Bearer token per request — never logged; never stored as a session default

Phase 2+ (planned, not in this release): entrypoints, services, overview,
reload service, options flow, reauth flow, TLS certificate expiry.

## Installation

### HACS (recommended)

1. HACS → Integrations → ⋮ → Custom repositories
2. Add `https://github.com/akentner/homeassistant-traefik-proxy-integration` (Integration)
3. Install → Restart Home Assistant
4. Settings → Devices & Services → + Add Integration → Traefik Proxy

### Manual

```bash
scp -r custom_components/traefik_proxy haos-op3050-1:/config/custom_components/
ha core restart
```

(Replace `haos-op3050-1` with your HA host. `ha` is the user's HA CLI alias.)

## Configuration

### UI flow (recommended)

Settings → Devices & Services → + Add Integration → Traefik Proxy:

| Field       | Example                              | Notes |
|-------------|--------------------------------------|-------|
| URL         | `https://traefik.example.com:8080`   | Where Traefik Proxy's API is reachable. |
| API key     | (paste from Traefik static config)   | Bearer token from `api.dashboard` or your auth middleware. |
| Verify SSL  | `true`                               | Disable for self-signed certificates. |

### YAML (alternative)

```yaml
# configuration.yaml
traefik_proxy:
  url: https://traefik.example.com:8080
  api_key: "${traefik_bearer_token}"
  verify_ssl: true
```

## Troubleshooting

- **`invalid_auth`** — bearer token rejected (401/403). Generate a new one in Traefik's static config.
- **`api_disabled`** — Traefik Proxy's `api:` block isn't enabled. Add `api: insecure: true` (with HTTPS reverse-proxy in front) or enable the dashboard.
- **`cannot_connect`** — Traefik Proxy unreachable. Verify URL, network, firewall.

## Attribution

This integration uses the [Traefik](https://traefik.io) logo under the Apache License, Version 2.0.

## Entities

After setup the integration exposes the following entities on three
per-category devices (`HTTP Routers`, `HTTP Services`, `HTTP Middlewares`)
plus a `Diagnostics` device for problem aggregates.

### Overview device — aggregate counts

Three sensors on the **Overview** device, one per Traefik category:

- `sensor.traefik_proxy_routers`
- `sensor.traefik_proxy_services`
- `sensor.traefik_proxy_middlewares`

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

- `binary_sensor.traefik_proxy_any_router_failing` (Phase 2)
- `binary_sensor.traefik_proxy_any_service_failing` (v0.2.0)
- `binary_sensor.traefik_proxy_any_middleware_failing` (v0.2.0)

Each is `True` when at least one item has `status != "enabled"`.
Attributes expose the failing item names so dashboards can drill down.

### Replicating the Traefik dashboard pie charts in HA

The Traefik dashboard renders three pie slices per category (success /
warning / error). Replicate with `custom:apexcharts-card` (HACS
Frontend card) — three pie cards, one per category, fed from our
breakdown attributes:

```yaml
type: grid
columns: 3
cards:
  - type: custom:apexcharts-card
    chart_type: pie
    header:
      title: Routers — Status
      show: true
    series:
      - entity: sensor.traefik_proxy_routers
        attribute: success_count
        name: Success
      - entity: sensor.traefik_proxy_routers
        attribute: warning_count
        name: Warning
      - entity: sensor.traefik_proxy_routers
        attribute: error_count
        name: Error
    chart_options:
      chart:
        height: 280px
      plotOptions:
        pie:
          expandOnClick: false
      legend:
        position: bottom
      colors:
        - "#28a745"
        - "#ffc107"
        - "#dc3545"

  - type: custom:apexcharts-card
    chart_type: pie
    header:
      title: Services — Status
      show: true
    series:
      - entity: sensor.traefik_proxy_services
        attribute: success_count
        name: Success
      - entity: sensor.traefik_proxy_services
        attribute: warning_count
        name: Warning
      - entity: sensor.traefik_proxy_services
        attribute: error_count
        name: Error
    chart_options:
      chart:
        height: 280px
      plotOptions:
        pie:
          expandOnClick: false
      legend:
        position: bottom
      colors:
        - "#28a745"
        - "#ffc107"
        - "#dc3545"

  - type: custom:apexcharts-card
    chart_type: pie
    header:
      title: Middlewares — Status
      show: true
    series:
      - entity: sensor.traefik_proxy_middlewares
        attribute: success_count
        name: Success
      - entity: sensor.traefik_proxy_middlewares
        attribute: warning_count
        name: Warning
      - entity: sensor.traefik_proxy_middlewares
        attribute: error_count
        name: Error
    chart_options:
      chart:
        height: 280px
      plotOptions:
        pie:
          expandOnClick: false
      legend:
        position: bottom
      colors:
        - "#28a745"
        - "#ffc107"
        - "#dc3545"
```

### Success-rate radial bars

A composite gauge per category showing the success rate as a percentage
(`success_count / (success_count + warning_count + error_count) × 100`):

```yaml
type: grid
columns: 3
cards:
  - type: custom:apexcharts-card
    chart_type: radialBar
    header:
      title: Routers — Success Rate
      show: true
    series:
      - entity: sensor.traefik_proxy_routers
        attribute: success_pct
        name: Success Rate
    chart_options:
      chart:
        height: 280px
      plotOptions:
        radialBar:
          startAngle: -135
          endAngle: 135
          hollow:
            size: "60%"
      fill:
        colors:
          - "#28a745"
          - "#ffc107"
          - "#dc3545"
      legend:
        show: false

  - type: custom:apexcharts-card
    chart_type: radialBar
    header:
      title: Services — Success Rate
      show: true
    series:
      - entity: sensor.traefik_proxy_services
        attribute: success_pct
        name: Success Rate
    chart_options:
      chart:
        height: 280px
      plotOptions:
        radialBar:
          startAngle: -135
          endAngle: 135
          hollow:
            size: "60%"
      fill:
        colors:
          - "#28a745"
          - "#ffc107"
          - "#dc3545"
      legend:
        show: false

  - type: custom:apexcharts-card
    chart_type: radialBar
    header:
      title: Middlewares — Success Rate
      show: true
    series:
      - entity: sensor.traefik_proxy_middlewares
        attribute: success_pct
        name: Success Rate
    chart_options:
      chart:
        height: 280px
      plotOptions:
        radialBar:
          startAngle: -135
          endAngle: 135
          hollow:
            size: "60%"
      fill:
        colors:
          - "#28a745"
          - "#ffc107"
          - "#dc3545"
      legend:
        show: false
```

### Failure alarms

`Conditions` cards that fire when any item leaves the `enabled` state:

```yaml
type: conditional
conditions:
  - entity: binary_sensor.traefik_proxy_any_router_failing
    state: "on"
card:
  type: markdown
  content: "## ⚠️ A Traefik router is failing"
```

Repeat the same pattern for `any_service_failing` and
`any_middleware_failing`. Enable the entities explicitly (they ship
disabled-by-default per PITFALLS M-12).

### Certificate expiry

Per-host entities on the **HTTP Routers TLS** device:

- `sensor.traefik_proxy_<host>_cert` (`TIMESTAMP` device class) — cert's
  `not_after` datetime, plus `days_until_expiry`, `subject`, `issuer`,
  `san`, `san_mismatch`, `last_error` attributes.
- `binary_sensor.traefik_proxy_<host>_expiring` (`PROBLEM` device
  class, default-enabled) — `is_on = days_until_expiry <= threshold_days`
  (the threshold is configurable via Options).

The cert coordinator probes every distinct hostname every 6 h with a
`Semaphore(4)` + 5 s per-handshake timeout. Hosts whose TLS handshake
fails (timeout, refused, DNS error, parse error) get a typed
`CertError` row with `last_error` — they show up only as the
expiring-binary sensor (no timestamp sensor without a valid
`not_after`).