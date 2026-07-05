# Home Assistant Traefik Integration

## What This Is

A custom Home Assistant integration that connects to a Traefik reverse proxy
and surfaces its operational state inside Home Assistant — routers, services,
entrypoints, middleware, and TLS certificate health are exposed as entities
the user can monitor, automate against, and visualize in dashboards.

Built for self-hosters running Traefik in front of their Home Assistant
and other homelab services who want a single pane of glass for reverse-proxy
health instead of having to log into the Traefik dashboard separately.

## Core Value

If nothing else works, the user must be able to see — at a glance inside
Home Assistant — which Traefik routers are enabled, which are failing,
and which TLS certificates are expiring soon.

## Requirements

### Validated

<!-- Shipped and confirmed valuable. -->

(None yet — ship to validate)

### Active

<!-- Current scope. Building toward these. Hypotheses until shipped. -->

- [ ] **CORE-01**: User can configure the integration via the Home Assistant
      config flow by providing the Traefik API URL and an API key (or bearer
      token).
- [ ] **CORE-02**: User can alternatively configure the integration by editing
      `configuration.yaml`, for users who prefer YAML / pinned releases.
- [ ] **CORE-03**: The integration connects to the Traefik API over HTTP using
      the `/api/entrypoints`, `/api/http/routers`, `/api/http/services`, and
      `/api/http/middlewares` endpoints.
- [ ] **CORE-04**: The integration exposes each discovered Traefik router as a
      binary sensor named after its `@router` rule identifier, with state
      derived from the router's `status` field.
- [ ] **CORE-05**: The integration exposes each Traefik entrypoint as a sensor
      reporting the listening address and current request count.
- [ ] **CORE-06**: The integration exposes each Traefik service as a sensor
      reporting its load-balancer status and backend server health.
- [ ] **TLS-01**: For every router terminating TLS, the integration exposes
      a sensor with the certificate's `notAfter` date and a calculated
      "days until expiry" attribute.
- [ ] **TLS-02**: The integration creates a dedicated `binary_sensor` per
      certificate that turns `on` when the certificate is within a
      user-configurable expiry threshold (default: 14 days).
- [ ] **DIAG-01**: The integration aggregates a top-level `sensor.traefik`
      reporting the total number of routers, services, and middlewares.
- [ ] **DIAG-02**: The integration creates a `binary_sensor` that becomes `on`
      when any router is reporting a non-`enabled` status.
- [ ] **DIAG-03**: The integration supports a "reload" service that calls the
      Traefik `/api/http/routers/refresh` endpoint, allowing HA automations
      to trigger a hot reload after route changes.
- [ ] **CFG-01**: User can override the API base URL, TLS verification, scan
      interval, and certificate warning threshold from integration options.
- [ ] **DOCS-01**: A `README.md` documents install via HACS and manual,
      configuration options, and example dashboards / automations.
- [ ] **TEST-01**: Unit tests cover the Traefik API client (parsing, error
      paths) and the entity state derivation logic.

### Out of Scope

<!-- Explicit boundaries. Includes reasoning to prevent re-adding. -->

- **Mutating Traefik configuration files on disk** — Traefik's dynamic
  configuration is intentionally external; this integration only reads
  state and triggers a refresh, it does not write files.
- **Auto-provisioning TLS certificates (Let's Encrypt / ACME flow)** —
  Traefik already handles this; duplicating it in HA would fight the
  source of truth.
- **A built-in web UI for editing Traefik routes** — out of scope; the
  Traefik dashboard already exists.
- **Traefik v1 support** — only Traefik v2.x and v3.x are targeted
  (only one API shape to maintain).
- **Cloud-managed Traefik / Traefik Enterprise** — only the OSS proxy
  is targeted.
- **Streaming WebSocket (`/api/websocket`) integration for live updates**
  — polling at the user-configurable interval is sufficient for v1 and
  dramatically simpler to implement.

## Context

The user already runs multiple Home Assistant instances reachable over
Tailscale, maintains several custom HA integrations (chargefinder, gatus,
kroki, etc.), and uses Traefik as the fronting reverse proxy in their
homelab. This integration slots into that existing ecosystem as another
self-hosted component — a HACS-distributable custom component that other
self-hosters can adopt.

Home Assistant integrations follow a well-documented convention:
`custom_components/<domain>/` with `__init__.py`, `manifest.json`,
`config_flow.py`, `const.py`, and one coordinator/entity file per
concern. The HA Core `DataUpdateCoordinator` pattern is the standard way
to poll an external API and fan updates out to entities.

The Traefik HTTP API (`/api/...` with a `Bearer` token) provides
read-only access to operational state and one mutate endpoint
(`/api/http/routers/refresh`). All endpoints return JSON.

## Constraints

- **Tech stack**: Python 3.12+, Home Assistant Core (min version 2025.4.x),
  `aiohttp` for HTTP, HACS-compatible.
- **Distribution**: HACS default repository structure (`hacs.json`,
  `info.md`, `README.md`, version tags via releases).
- **Compatibility**: Must work against Traefik v2.11+ and v3.x.
- **Performance**: API polling must not exceed one call-set per scan
  interval (default 30s) per integration instance; multiple endpoints
  fetched in parallel via `asyncio.gather`.
- **Security**: API tokens never logged; integration must support
  self-signed certificates via user option.
- **No external service dependencies**: integration talks only to the
  Traefik API the user points it at — no SaaS, no telemetry.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Polling, not WebSocket | Simpler, fewer failure modes, scan interval is fine for v1 | — Pending |
| v2/v3 Traefik API only | One API shape to maintain; v1 is EOL | — Pending |
| HACS-distributable, not core | Matches pattern of user's other custom integrations | — Pending |
| Config flow + YAML | Config flow is the modern HA path, YAML still needed for power users | — Pending |
| Per-router / per-cert entities | Maps cleanly to HA entity model; users can build dashboards on top | — Pending |
| `aiohttp` over `requests` | Async, matches HA Core's loop, no thread-pool | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2025-07-05 after initialization*
