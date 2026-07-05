# Requirements: Home Assistant Traefik Integration

**Defined:** 2026-07-05
**Core Value:** If nothing else works, the user must be able to see — at a glance inside Home Assistant — which Traefik routers are enabled, which are failing, and which TLS certificates are expiring soon.

## v1 Requirements

Requirements for initial release. Each maps to a roadmap phase.

### Configuration & Setup (CFG)

- [x] **CFG-01**: User can configure the integration via the Home Assistant UI
      config flow by providing the Traefik API URL and a bearer token.
- [x] **CFG-02**: User can alternatively configure the integration via
      `configuration.yaml` for users who prefer YAML / pinned releases.
- [ ] **CFG-03**: User can reconfigure the Traefik URL without deleting and
      re-adding the integration entry.
- [ ] **CFG-04**: When the API token becomes invalid (401), the integration
      triggers Home Assistant's reauth flow automatically.
- [ ] **CFG-05**: User can configure integration options after setup (TLS
      verification, scan interval, certificate warning threshold).
- [x] **CFG-06**: User is shielded from "Updating Traefik failed" spam
      during transient API outages by surfacing `ConfigEntryNotReady` and
      retrying with backoff.

### API Client (API)

- [x] **API-01**: The integration connects to Traefik over HTTP using the
      shared HA `aiohttp.ClientSession` (never creates its own).
- [x] **API-02**: The integration calls `/api/version`, `/api/entrypoints`,
      `/api/http/routers`, `/api/http/services`, `/api/http/middlewares`,
      and `/api/overview` in parallel via `asyncio.gather`.
- [x] **API-03**: All API requests carry the bearer token in an
      `Authorization` header built per-request — never as a default header
      on a long-lived session.
- [x] **API-04**: The API client raises `TraefikAuthError` on HTTP 401 and
      `TraefikApiError` on other non-2xx responses — never logs the token.
- [x] **API-05**: The API client can trigger `traefik.reload_routers` via
      `POST /api/http/routers/refresh` and report whether the refresh
      actually completed (Traefik returns 200 before reload finishes).
- [x] **API-06**: The API client supports HTTP and HTTPS targets, with
      optional TLS verification configurable per-entry.

### Coordinator (COORD)

- [x] **COORD-01**: Polling cycle completes in `≤10s` per fetch and runs on
      the HA event loop without blocking.
- [x] **COORD-02**: All parallel fetches wrapped in `asyncio.timeout(10)` so
      one slow endpoint does not stall the cycle.
- [x] **COORD-03**: Coordinator raises `ConfigEntryAuthFailed` on 401 and
      `UpdateFailed` on transient errors — the right exception determines
      whether the user sees reauth or retry.
- [x] **COORD-04**: First refresh awaited via
      `coordinator.async_config_entry_first_refresh()` in
      `async_setup_entry`, so initial state appears immediately on restart.

### Entities — Routers (CORE-04)

- [x] **ROUTER-01**: A `binary_sensor` exists for each Traefik router,
      with state `on` when the router is `enabled` and `off` when
      `disabled` or errored — `BinarySensorDeviceClass.RUNNING`.
- [x] **ROUTER-02**: Each router entity exposes the Traefik router `name`,
      friendly rule (first `Host(...)` match), and full `rule` as
      extra-state-attributes for dashboards and automations.
- [x] **ROUTER-03**: Traefik router names containing `@` are filtered at
      coordinator level (HA entity-ID regex rejects `@`).
- [x] **ROUTER-04**: Every router entity has a stable `unique_id` so
      re-setup does not duplicate entries.

### Entities — Entrypoints, Services, Overview (CORE-05, CORE-06, DIAG-01)

- [x] **ENTRY-01**: A `sensor` per Traefik entrypoint reports the
      listening address and current request count.
- [x] **ENTRY-02**: A `sensor` per Traefik service reports load-balancer
      status and backend server health (when healthcheck is configured).
- [x] **ENTRY-03**: An aggregate `sensor.traefik` reports the total
      number of routers, services, and middlewares.

### Entities — Diagnostics & Reload (DIAG-02, DIAG-03)

- [x] **DIAG-01**: A top-level `binary_sensor` becomes `on` when any
      router is reporting a non-`enabled` status — `BinarySensorDeviceClass.PROBLEM`.
- [x] **DIAG-02**: A `button` entity triggers the `Reload` button device
      class and posts a refresh to Traefik when pressed.
- [ ] **DIAG-03**: A `traefik.reload_routers` service is registered
      during `async_setup` (not `async_setup_entry`) and verifies reload
      completion via polling.

### Entities — TLS Certificate Expiry (TLS-01, TLS-02)

- [ ] **TLS-01**: For every router terminating TLS, a `sensor` exposes
      the certificate's `notAfter` timestamp (with `device_class:
      timestamp`) and a `days_until_expiry` attribute.
- [ ] **TLS-02**: For every TLS-enabled router, a `binary_sensor` turns
      `on` when the certificate is within the user-configurable warning
      threshold (default 14 days) — `BinarySensorDeviceClass.PROBLEM`.
- [ ] **TLS-03**: TLS fetches use a separate `CertCoordinator` with a
      6-hour cadence (not the 30s state cycle).
- [ ] **TLS-04**: TLS handshakes run via stdlib `ssl` inside
      `asyncio.to_thread`; failures mark the entity as `unavailable`
      rather than crashing the integration.
- [ ] **TLS-05**: TLS state caches per cycle to avoid hammering ports on
      large router counts; cadence is bounded by semaphore.

### Platform & UX (UX)

- [x] **UX-01**: Entities grouped under a single "Traefik" device with
      `sw_version` set from `/api/version`.
- [x] **UX-02**: Entities use HA's modern `_attr_has_entity_name=True`
      convention so the UI displays `<Device> <Entity Name>`.
- [ ] **UX-03**: Stale entities (e.g. a router removed in Traefik) are
      pruned via `coordinator.async_add_listener` cleanup hook.
- [x] **UX-04**: All entities expose additional state attributes
      (Traefik IDs, rule excerpts, raw timestamps) so dashboards and
      automations can drill in.

### Distribution & Quality (DIST)

- [x] **DIST-01**: Integration ships with `manifest.json` registered for
      domain `traefik`, `hacs.json` ready for HACS, and `brand/` icon
      assets (`icon.png` 256×256, `icon@2x.png` 512×512).
- [x] **DIST-02**: `manifest.json` declares `homeassistant: "2025.4.0"`
      minimum and `"requirements": []` (HA Core bundles everything).
- [x] **DIST-03**: `manifest.json` deliberately omits `quality_scale`
      (hassfest blocks it for custom integrations).
- [ ] **DIST-04**: GitHub Actions include `hassfest`, HACS Action, and
      pytest workflows; the release tag is enforced to match the manifest
      `version`.
- [ ] **DIST-05**: The integration targets **Bronze** quality scale for
      v1.0 and **Silver** as a stretch goal for v1.2.

### Documentation (DOCS-01)

- [x] **DOCS-01**: A `README.md` documents HACS install, manual install,
      every configuration option, and example dashboards / automations
      (including a "notify when a router goes down" pattern and a
      "warn me N days before TLS expiry" pattern).
- [ ] **DOCS-02**: A `CHANGELOG.md` is added for the first release and
      updated on every release tag.
- [ ] **DOCS-03**: A `info.md` (HACS store card) summarizes the
      integration for the HACS browse view.
- [ ] **DOCS-04**: An FAQ addresses the locked-out anti-features
      (config-file edits, ACME in HA, v1 Traefik, etc.) so users do not
      file duplicate issues.

### Testing (TEST-01)

- [x] **TEST-01**: Unit tests cover the API client (parsing, error paths,
      redaction safety) and the coordinator (poll cycle, exception
      mapping).
- [ ] **TEST-02**: Integration tests using
      `pytest-homeassistant-custom-component` cover Config Flow
      (success, invalid auth, unreachable host), all entity platforms,
      options flow, reload service, and reauth flow.
- [x] **TEST-03**: A test fixture captures a realistic Traefik `/api/...`
      payload so integration tests stay hermetic.
- [ ] **TEST-04**: TLS parsing tests cover ≥3 known `notAfter` format
      strings and ≥2 invalid format strings (graceful unavailable state).

### Diagnostics (DIAG-04)

- [ ] **DIAG-04**: A `diagnostics.py` exports config-entry diagnostics
      with `async_redact_data` stripping `api_key`, `token`, `password`,
      `basic_auth` and any other credential-shaped keys.

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Routing

- **ROUTER-V2-01**: Per-router transport security / TLS version attributes
- **ROUTER-V2-02**: Per-router `using` array (chain of middlewares)
  visualized in the UI
- **ROUTER-V2-03**: Per-backend server health attributes (only valuable
  when Traefik server-side `healthcheck` is configured)

### TLS

- **TLS-V2-01**: TLS chain validation (intermediates, SAN match)
- **TLS-V2-02**: Show chain trust path per certificate
- **TLS-V2-03**: TLS subject/issuer attributes in addition to expiry

### Operations

- **OPS-V2-01**: Switch entities to toggle per-router status (requires
  Traefik to add an enable/disable endpoint)
- **OPS-V2-02**: Per-middleware entities (middlewares are config-time
  constructs with no runtime state — value is dubious)
- **OPS-V2-03**: WebSocket streaming for live updates (sub-second state;
  adds reconnect/diff complexity)
- **OPS-V2-04**: TCP/UDP router+service entities (PROJECT.md HTTP-only)
- **OPS-V2-05**: Traefik v1 compatibility (EOL since 2021)
- **OPS-V2-06**: Auto-discovery via zeroconf / DHCP (Traefik does not
  advertise)

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Mutating Traefik dynamic-config files on disk | Traefik dashboard already exists; race conditions between provider and file writes; dangerous default |
| Auto-provisioning TLS certificates (ACME/LE) inside HA | Traefik owns the cert lifecycle; the integration only surfaces expiry |
| A built-in web UI for editing Traefik routes | Out of scope — Traefik dashboard exists; duplicating UI is wasted effort |
| Traefik v1 support | EOL since 2021; two API shapes would double the maintenance surface |
| Traefik Enterprise / cloud-managed Traefik | Only OSS reverse proxy is targeted |
| Reading `/api/rawdata` dependency graph | API endpoint exists but visualization value is unclear; defer until user feedback |
| Reading `acme.json` from disk | Coupling to Traefik's internal file layout; TLS handshake works without it |
| Per-middleware entities | Middlewares are config-time constructs with no runtime state to expose |
| TCP/UDP router+service entities | PROJECT.md explicitly HTTP-only; TCP uses a different API surface |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| CFG-01 | Phase 1 | Complete |
| CFG-02 | Phase 1 | Complete |
| CFG-03 | Phase 2 | Pending |
| CFG-04 | Phase 2 | Pending |
| CFG-05 | Phase 2 | Pending |
| CFG-06 | Phase 1 | Complete |
| API-01 | Phase 1 | Complete |
| API-02 | Phase 1 | Complete |
| API-03 | Phase 1 | Complete |
| API-04 | Phase 1 | Complete |
| API-05 | Phase 2 | Complete |
| API-06 | Phase 1 | Complete |
| COORD-01 | Phase 1 | Complete |
| COORD-02 | Phase 1 | Complete |
| COORD-03 | Phase 1 | Complete |
| COORD-04 | Phase 1 | Complete |
| ROUTER-01 | Phase 1 | Complete |
| ROUTER-02 | Phase 2 | Complete |
| ROUTER-03 | Phase 2 | Complete |
| ROUTER-04 | Phase 1 | Complete |
| ENTRY-01 | Phase 2 | Complete |
| ENTRY-02 | Phase 2 | Complete |
| ENTRY-03 | Phase 2 | Complete |
| DIAG-01 | Phase 2 | Complete |
| DIAG-02 | Phase 2 | Complete |
| DIAG-03 | Phase 2 | Pending |
| DIAG-04 | Phase 4 | Pending |
| TLS-01 | Phase 3 | Pending |
| TLS-02 | Phase 3 | Pending |
| TLS-03 | Phase 3 | Pending |
| TLS-04 | Phase 3 | Pending |
| TLS-05 | Phase 3 | Pending |
| UX-01 | Phase 1 | Complete |
| UX-02 | Phase 1 | Complete |
| UX-03 | Phase 2 | Pending |
| UX-04 | Phase 2 | Complete |
| DIST-01 | Phase 1 | Complete |
| DIST-02 | Phase 1 | Complete |
| DIST-03 | Phase 1 | Complete |
| DIST-04 | Phase 4 | Pending |
| DIST-05 | Phase 4 | Pending |
| DOCS-01 | Phase 1 | Complete |
| DOCS-02 | Phase 4 | Pending |
| DOCS-03 | Phase 4 | Pending |
| DOCS-04 | Phase 4 | Pending |
| TEST-01 | Phase 1 | Complete |
| TEST-02 | Phase 2 | Pending |
| TEST-03 | Phase 1 | Complete |
| TEST-04 | Phase 3 | Pending |

**Coverage:**
- v1 requirements: 46 total
- Mapped to phases: 46
- Unmapped: 0 ✓

---
*Requirements defined: 2026-07-05*
*Last updated: 2026-07-05 after initial definition*
