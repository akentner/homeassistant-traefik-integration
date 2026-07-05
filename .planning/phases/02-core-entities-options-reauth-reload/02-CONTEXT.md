# Phase 2: Core Entities + Options + Reauth + Reload - Context

**Gathered:** 2026-07-06
**Status:** Ready for planning

<domain>
## Phase Boundary

Layer the remaining table-stakes entities on top of the Phase 1 polling loop,
expose user-configurable knobs through Options Flow, and add the lifecycle
flows (reauth + reconfigure) plus the reload service/button. Phase 2 ships:

- Per-Traefik-category devices (multi-device model — HTTP Routers, HTTP
  Services, HTTP Entrypoints, Overview).
- Per-entrypoint sensor, per-service sensor, three aggregate-count sensors,
  any-router-failing binary sensor, reload button.
- `traefik.reload_routers` HA service with completion verification.
- `OptionsFlow` (scan_interval + verify_ssl + cert-warn-threshold placeholder),
  `async_step_reauth` (token rotation), `async_step_reconfigure` (URL change).
- Coordinator-level `@<provider>` filtering for routers + every new platform.
- Stale entity cleanup via `coordinator.async_add_listener` (gatus pattern).

TLS handshake, certificate sensors, and diagnostics dump are out of scope
(Phase 3 / Phase 4 respectively).

</domain>

<decisions>
## Implementation Decisions

### Device model — multi-device per category

- **D-01 (architecture):** Each Traefik category becomes its own HA device,
  preserving Phase 1 CONTEXT.md D-07. Phase 1 code's single-device identifier
  `{(DOMAIN, entry.entry_id)}` is replaced with per-category identifiers
  `{(DOMAIN, f"{entry.entry_id}_{category}")}` where `category ∈ {"http_routers",
  "http_services", "http_entrypoints", "overview", "diagnostics"}`. Device
  names:
  - HTTP Routers → `f"{url_host} Traefik — HTTP Routers"`
  - HTTP Services → `f"{url_host} Traefik — HTTP Services"`
  - HTTP Entrypoints → `f"{url_host} Traefik — HTTP Entrypoints"`
  - Overview → `f"{url_host} Traefik — Overview"`
  - Diagnostics → `f"{url_host} Traefik — Diagnostics"` (Reload button +
    any-router-failing binary_sensor — these are top-level diagnostic concerns
    not tied to a single category)

- **D-02 (architecture):** `TraefikEntity` base class takes a `category: str`
  parameter. DeviceInfo `identifiers`, `model`, and `name` derive from
  `category`. Phase 1 binary_sensors pass `category="http_routers"`. Phase 2
  platforms pass their own category. `manufacturer="Traefik"` constant
  everywhere.

- **D-03 (architecture):** `sw_version` from `/api/version` set on every
  device — live-updated per coordinator cycle (Phase 1 behavior retained).

### API client — new endpoints

- **D-04 (api):** `TraefikApiClient.fetch_all()` expanded to parallel-fetch:
  `version`, `entrypoints`, `http_routers`, `http_services`, `http_middlewares`,
  `overview`. All in one `asyncio.gather(return_exceptions=True)` wrapped by
  `asyncio.timeout(10)` (Phase 1 D-13 retained). TraefikData shape becomes:
  ```python
  type TraefikData = {
      "version": dict[str, Any],
      "entrypoints": list[dict[str, Any]],
      "http_routers": list[dict[str, Any]],
      "http_services": list[dict[str, Any]],
      "http_middlewares": list[dict[str, Any]],
      "overview": dict[str, Any],
  }
  ```
  The Phase-1 keys `routers` (alias for `http_routers`) may continue to exist
  for back-compat in `binary_sensor.py` via a transitional alias — or migrate
  all readers in the same patch. Recommend migrate in this phase.

- **D-05 (api):** New endpoint `async def reload_routers(self) -> None` —
  POSTs `/api/http/routers/refresh`. Returns `None` on 2xx, raises
  `TraefikApiError` on non-2xx. Does NOT poll; verification lives in the
  service handler (D-12).

- **D-06 (api):** `_filter_user_routers` helper lifted into `api.py` as a
  reusable `filter_internal_items(items, name_key="name")` — same regex
  pattern `\w+@\w+`. Reused by services, middlewares, and routers platforms.
  (Traefik returns internal/provider-suffixed names for services like
  `api@internal` and middlewares like `strip@docker`; same filter applies.)

### Coordinator & error handling

- **D-07 (coordinator):** `_async_update_data` keeps Phase 1 D-15 exception
  dispatch: `TraefikAuthError` → `ConfigEntryAuthFailed`; `TraefikApiError` →
  `UpdateFailed`. Add: when `return_exceptions=True` surfaces partial failures
  (e.g., `/api/entrypoints` 503 but routers 200), the coordinator surfaces the
  first exception AND uses the partial data for entities. Phase 2 plan chooses
  whether to filter out null sections or leave them as `[]`/`{}` — recommend
  filtering (entities handle `[]` cleanly).

- **D-08 (coordinator):** On Options change, `entry.add_update_listener` calls
  `await coordinator.async_request_refresh()` (live) — does NOT trigger
  `async_reload` (that's reserved for URL change in Reconfigure). Scan-interval
  change is applied live via `coordinator.update_interval = timedelta(seconds=X)`
  matching gatus pattern (`config_flow.py:100`).

### Config flow — Options, Reauth, Reconfigure

- **D-09 (config flow):** `async_step_options` (OptionsFlow) — fields:
  - `scan_interval` (NumberSelector BOX mode, step=1, unit=s, **clamp
    15..300**; default `DEFAULT_SCAN_INTERVAL=15` from Phase 1 D-12). Out-of-
    range surfaces `options.step.init.errors.scan_interval_out_of_range`.
  - `verify_ssl` (BooleanSelector, default True; matches Phase 1 D-05).
  - `tls_warn_days` (NumberSelector BOX, step=1, unit=d, **clamp 1..90**;
    default 14 — placeholder for Phase 3 TLS-02). Phase 2 validates and
    stores; Phase 3 picks the value up.
  Submitting applies `coordinator.update_interval` live and stores in
  `entry.options`. Cert-warn threshold is a no-op in Phase 2; Phase 3 wires it.

- **D-10 (config flow):** `async_step_reauth` (fires when coordinator raises
  `ConfigEntryAuthFailed`) — same UX as `gatus` `async_step_reauth_confirm`:
  password selector for the new token, validated against `/api/overview`,
  success calls `hass.config_entries.async_update_entry(entry,
  data={**entry.data, CONF_API_KEY: new_key})` + `async_reload` + abort
  `reauth_successful`. Single step only (no separate confirm step — gatus
  pattern).

- **D-11 (config flow):** `async_step_reconfigure` — pre-fills URL + token from
  current `entry.data`. On submit, validates `/api/overview` and calls
  `async_update_reload_and_abort(entry, data_updates={CONF_URL,
  CONF_API_KEY})` (URL change rebuilds the API client + coordinator; matches
  gatus). `unique_id = urlparse(url).hostname` (Phase 1 D-08-impl) — if the
  new URL has a different host, HA warns via `_abort_if_unique_id_configured`
  or prompts.

### Service & button

- **D-12 (service):** `traefik.reload_routers` service registered in
  `async_setup` (NOT `async_setup_entry`, PITFALLS M-5) — single registration
  per HA instance, survives unload. Handler:
  1. Capture `before = set(r["name"] for r in coordinator.data["http_routers"])`.
  2. `await client.reload_routers()` (raises `TraefikApiError` on failure →
     service reports failure to caller).
  3. Poll `coordinator.data["http_routers"]` via `async_request_refresh()` +
     `await coordinator.async_wait_for_ready()` with exponential-backoff loop
     (`200ms → 5s`, max 10 attempts, total budget ≤ 5s): exit early when
     `set(r["name"] ... != before`. Verified? Then return `success: True`.
     Stale? Then `success: False` (refresh was POSTed but no observable change).
  4. Return dict `{verified: bool, elapsed_ms: int, attempts: int, name_diff:
     {"added": [...], "removed": [...]}}`.
  Service description (in `services.yaml`): "Trigger a Traefik dynamic-config
  reload and verify completion by polling `/api/http/routers`. Returns
  `{verified, elapsed_ms, attempts, name_diff}` — `verified=false` means the
  refresh was accepted but the polling timeout elapsed without a router-set
  change."

- **D-13 (button):** `TraefikReloadButton(ButtonEntity,
  ButtonEntityDeviceClass.RESTART)` on the Diagnostics device. Pressing fires
  the same handler as the service (DIAG-02). Exposes nothing extra; the press
  action invokes `hass.services.async_call(DOMAIN, "reload_routers")` and the
  log surfaces the response dict.

- **D-14 (button + any-failing):** Both `TraefikReloadButton` and
  `TraefikAnyRouterFailingBinarySensor` live on the **Diagnostics** device
  (per D-01). `TraefikAnyRouterFailingBinarySensor` has
  `BinarySensorDeviceClass.PROBLEM`, state ON when **any** router's `status
  != "enabled"` (matches `off` = problem per HA convention). Attributes:
  `{failing_router_count, failing_router_names}`.

### Sensors — per-category

- **D-15 (sensors):** `TraefikEntrypointSensor(SensorEntity)` on the Entrypoints
  device, one per entrypoint. State = `entrypoint["address"]` (e.g.,
  `:443`); attributes: `{name, address, protocol}`. **`request_count` is
  NOT exposed** — Traefik's runtime metrics live elsewhere (`/api/overview`
  or `/metrics`); entrypoint-list API only returns the static config schema.
  Decision aligns with reality: ENTRY-01's "request count" is reinterpreted as
  "expose what's actually present" and the missing piece deferred (noted in
  deferred section).

- **D-16 (sensors):** `TraefikServiceSensor(SensorEntity)` on the Services
  device, one per service (filtered through `_filter_internal_items`
  excluding `api@internal`). State = `service["loadbalancer"]["status"]`
  (e.g., `"OK"`, `"WARNING"`, `"HEALTHY"`); attributes: `{name, status,
  type, server_count, servers}` (server health from
  `loadbalancer.servers[]` only when present; absent if no healthcheck
  configured — enum not exposed in that case).

- **D-17 (sensors):** Three aggregate sensors on the **Overview** device:
  - `sensor.traefik_routers` — state = `len(http_routers_filtered)`;
    attributes: `{http_routers, tcp_routers, udp_routers}` (TCP/UDP counts
    from `/api/overview`, even though we don't expose entities for them yet
    — counts surface in attrs per Req 1.1 v1 backlog).
  - `sensor.traefik_services` — state = `len(http_services_filtered)`;
    attributes: `{http_services, tcp_services, udp_services}`.
  - `sensor.traefik_middlewares` — state = `len(http_middlewares_filtered)`.
  Naming pattern matches Phase 1's `traefik_http_router_<slug>` (D-09).
  These DO NOT collapse to a single "sensor.traefik" — per discussion, three
  separate sensors enable per-metric automations.

### Stale entity cleanup

- **D-18 (cleanup):** `coordinator.async_add_listener` callback (registered
  per-platform `async_setup_entry`) removes entities for routers/services/
  entrypoints that disappeared from the coordinator data. Pattern matches
  gatus `binary_sensor.py:49-71`: `entity_registry.async_remove(entity_id)`
  for any entity with a unique_id prefix matching the platform's category
  whose trailing key is not in the current data set. Stale → deleted
  immediately.

- **D-19 (cleanup):** `TraefikAnyRouterFailingBinarySensor` (single instance,
  one per config entry) — never deleted. If all routers disappear, the
  sensor falls to OFF (no routers failing) and stays. Aggregate sensors
  similarly persist (state becomes 0, not deleted).

### Entity attributes (UX-04)

- **D-20 (attributes):** All Phase 2 entities expose
  `extra_state_attributes` with the raw Traefik JSON (or a curated subset
  including the original `name` even after slugification — Phase 1 ROUTER-02
  pattern retained). For state-bearing entities, attributes MUST include
  enough raw data for dashboards to filter on (e.g., `_attr_extra_state_attributes`
  spread the dict excluding fields that became the `state`).

### the agent's Discretion

- Exact wording in `strings.json` for the new options/reauth/reconfigure/error
  messages (translate to match HA Core tone; user has German locale so a
  `de.json` translation is nice-to-have but NOT shipped Phase 2).
- Whether to add a `babel`-style hashable for `name_diff` in the reload
  service response (avoid noisy logs on every refresh).
- Aggregation of entrypoint listener protocol (`tcp` vs `udp`) into a single
  string (`"tcp"`) or expose as `transport` attribute separately.
- Exact mapping of `entrypoints[].address` to state when it includes the
  bind host (e.g., `":443"` vs `"0.0.0.0:443"` — strip leading `:` for
  HA display?).
- Reorganizing the `__pycache__`/legacy Phase 1 keys: drop `"routers"` key
  entirely vs keep a back-compat alias for one release.

### Folded Todos

None — `todo match-phase 2` returned `matches: []` (todo_count: 0).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project planning artifacts
- `.planning/PROJECT.md` — Vision, Core Value, Out-of-Scope, Key Decisions
- `.planning/REQUIREMENTS.md` — v1 requirements 1–46 with phase traceability
  (Phase 2 covers CFG-03..05, API-05, ROUTER-02..03, ENTRY-01..03, DIAG-01..03,
  UX-03..04, TEST-02 = 15 requirements)
- `.planning/ROADMAP.md` — Phase 2 success criteria (5 numbered points) and
  plan placeholders 02-01..02-04
- `.planning/STATE.md` — Accumulated context; Phase 2 pending decisions logged
- `.planning/research/SUMMARY.md` — Phase 2 rationale + confidence ratings
- `.planning/research/PITFALLS.md` — **Pitfall #15 (reload async), #2
  (@<provider>), #8 (polling), M1 (CONF_API_KEY in .data not .options), M5
  (service in async_setup)** and Phase-2 references
- `.planning/research/STACK.md` — Stack baseline (PEP-695, async_get_clientsession,
  asyncio.timeout, runtime_data)
- `.planning/phases/01-foundation/01-CONTEXT.md` — **Prior phase decisions**
  (D-01 bare-coordinator runtime_data, D-08 RUNNING binary_sensor, D-09
  entity_id prefix pattern, D-12 scan_interval default 15s, D-13
  asyncio.timeout(10), D-14 bearer per-request, D-15 exception dispatch,
  D-19 brand icon path)

### Home Assistant Core docs (verified)
- `https://developers.home-assistant.io/docs/config_entries_config_flow_handler/`
  — ConfigFlow / OptionsFlow / reauth / reconfigure / migration API
- `https://developers.home-assistant.io/docs/creating_integration_manifest/` —
  Manifest schema
- `https://developers.home-assistant.io/docs/integration_setup_failures/` —
  ConfigEntryNotReady vs ConfigEntryAuthFailed semantics

### Home Assistant skill references (local)
- `~/.opencode/skills/integrations/SKILL.md` — Primary HA integration reference
  (config flow lines 196, OptionsFlow line 309, device registry)
- `~/.opencode/skills/home-assistant/SKILL.md` — HA API reference

### User's reference integrations (local, sibling patterns)
- `/home/akentner/Projects/homeassistant-gatus-integration/` — **Primary
  pattern source for Phase 2**:
  - `config_flow.py:135-265` — `async_step_user`, `async_step_reauth`,
    `async_step_reauth_confirm`, `async_step_reconfigure`, `OptionsFlowHandler`
    — full reference for D-09/D-10/D-11
  - `binary_sensor.py:49-71` — `_remove_stale_entities` callback registered
    via `coordinator.async_add_listener` — full reference for D-18
- `/home/akentner/Projects/homeassistant-kroki-integration/` —
  `after_dependencies: ["http"]` manifest pattern
- `/home/akentner/Projects/homeassistant-traefik-integration/custom_components/traefik/`
  — Phase 1 shipped code; Phase 2 extends in place

### Traefik API docs (verified Jul 2026)
- `https://doc.traefik.io/traefik/reference/install-configuration/api-dashboard/`
  — Endpoints, auth model (`/api/*` shares auth boundary), `refresh` POST
  async semantics
- `https://doc.traefik.io/traefik/reference/install-configuration/entrypoints/`
  — Entrypoint schema (config only — runtime counters absent from
  `/api/entrypoints`)

### HACS distribution docs
- `https://hacs.xyz/docs/publish/start` — hacs.json (already shipped)
- `https://hacs.xyz/docs/publish/integration` — Repository layout (already
  shipped)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets

- **`TraefikEntity` base** (`custom_components/traefik/entity.py:17-58`) —
  Used by every entity platform. Extend constructor with `category: str`
  parameter (D-02); rebuild `DeviceInfo` to use per-category identifier
  `f"{entry.entry_id}_{category}"`. `_url_host()` and `_sw_version()` helpers
  preserved. `_attr_has_entity_name = True` retained.
- **`TraefikCoordinator` class** (`coordinator.py:34-64`) — `client` is
  exposed for service handlers to call `client.reload_routers()` directly
  (Phase 1 D-01 made this intentional). Update `fetch_all()` shape per D-04.
- **`TraefikApiClient` class** (`api.py:22-100`) — Bearer per-request header
  (Phase 1 D-14), typed exceptions (`TraefikApiError`, `TraefikAuthError`),
  shared `aiohttp.ClientSession` (Phase 1 D-04/PITFALLS #4). Add per D-04,
  D-05, D-06.
- **`_filter_user_routers`** (`binary_sensor.py:23-25`) — Lifted into
  `api.py` as `filter_internal_items(items, name_key="name")` per D-06.
- **`_friendly_rule` regex** (`binary_sensor.py:19, 28-33`) — Already
  extracts `Host(...)` match. Reused for `router_name` hint attribute (UX-04).
- **`async_step_user` template** (`config_flow.py:91-121`) — Adapted to
  `async_step_reauth`, `async_step_reconfigure`. Validation pattern
  (probe `/api/overview` → map error types) ported directly.
- **`coordinator.async_add_listener` pattern** from gatus integration
  (`/home/akentner/Projects/homeassistant-gatus-integration/custom_components/gatus/binary_sensor.py:49-71`)
  — `_remove_stale_entities` callback template. Phase 2 adapts for
  routers/services/entrypoints.
- **`services.yaml` placeholder** — Already exists
  (`custom_components/traefik/services.yaml`); Phase 2 fills it with the
  `traefik.reload_routers` schema.

### Established Patterns

- **PEP-695 type aliases** — `type TraefikConfigEntry = ConfigEntry[
  TraefikCoordinator]`; `type TraefikData = dict[str, Any]`. Modern
  Python 3.13+ syntax. Phase 2 updates `TraefikData` to a TypedDict
  shape per D-04.
- **`async_get_clientsession(hass)`** for all aiohttp calls (PITFALLS #4,
  Phase 1 D-14). Never creates own `ClientSession`.
- **Bearer token per-request header** — `headers = {"Authorization":
  f"Bearer {api_key}"}` only when truthy. Never default header on
  long-lived session.
- **Lazy log formatting** — `_LOGGER.debug("path=%s status=%s", path,
  status)`; never f-string interpolation of secrets.
- **`async_step_yaml` for YAML import** — Phase 1 shipped; not changed in
  Phase 2.
- **`coordinator.async_add_listener(callback)` for stale cleanup** —
  matched by `entry.async_on_unload` so the listener unloads with the
  config entry. Phase 2 replicates the gatus pattern verbatim.

### Integration Points

- `custom_components/traefik/__init__.py` — Add `async_setup` (module-level)
  to register `traefik.reload_routers` service handler; keep existing
  `async_setup_entry` (creates coordinator + first-refresh + forwards
  `PLATFORMS`).
- `custom_components/traefik/config_flow.py` — Add `OPTIONSFLOW_KEY`
  async_get_options_flow binding; `async_step_reauth`,
  `async_step_reauth_confirm`, `async_step_reconfigure` methods; existing
  `async_step_user`, `async_step_yaml` unchanged.
- `custom_components/traefik/const.py` — Add `CONF_TLS_WARN_DAYS`,
  `MIN_SCAN_INTERVAL=15`, `MAX_SCAN_INTERVAL=300`,
  `MIN_TLS_WARN_DAYS=1`, `MAX_TLS_WARN_DAYS=90`,
  `DEFAULT_TLS_WARN_DAYS=14`. Add `BUTTON_DOMAIN` to PLATFORMS list
  (now `["binary_sensor", "sensor", "button"]`).
- `custom_components/traefik/services.yaml` — Replace placeholder with
  `reload_routers` schema per D-12.
- `custom_components/traefik/strings.json` — Extend `options`,
  `reauth`, `reconfigure`, error keys; add `exceptions` keys for
  service failures.
- `custom_components/traefik/coordinator.py` — Expand `fetch_all()` per
  D-04; coordinator doesn't change shape (single coordinator per entry,
  no separate TLS coordinator in Phase 2).
- `custom_components/traefik/api.py` — Add endpoints per D-04/D-05/D-06.
- `custom_components/traefik/binary_sensor.py` — Update `TraefikEntity`
  instantiation to pass `category="http_routers"`; add
  `TraefikAnyRouterFailingBinarySensor` on Diagnostics device with
  `entity_registry_enabled_default=False` (PITFALLS M-12: noisy
  diagnostic entity off by default).
- `custom_components/traefik/sensor.py` — NEW. Hosts
  `TraefikEntrypointSensor`, `TraefikServiceSensor`,
  `TraefikRoutersCountSensor`, `TraefikServicesCountSensor`,
  `TraefikMiddlewaresCountSensor`.
- `custom_components/traefik/button.py` — NEW. Hosts
  `TraefikReloadButton`.
- `tests/` — `tests/components/traefik/` — NEW (TEST-02):
  `test_config_flow.py`, `test_options_flow.py`, `test_sensor.py`,
  `test_binary_sensor.py`, `test_button.py`,
  `test_init.py` (service registration), `test_stale_cleanup.py`.
  Use `pytest-homeassistant-custom-component` fixtures (`hass`,
  `mock_config_entry`, `aioclient_mock`).

</code_context>

<specifics>
## Specific Ideas

- **Multi-device rollout sequencing:** Phase 2 introduces 4 of the planned
  devices (HTTP Services, HTTP Entrypoints, Overview, Diagnostics). HTTP
  Routers device already exists (Phase 1, single-device-fallback in code
  must be migrated per D-02). Phase 3 will add HTTP Routers/TLS variants
  to the same HTTP Routers device under a different category perhaps
  (e.g., `http_routers_tls`); Phase 4 stays out of device additions.

- **Service-handler response shape:** The reload service returning
  `{verified, elapsed_ms, attempts, name_diff}` is a deliberate choice to
  make the service observable in HA's `trace` log AND scriptable via
  template sensors. Alternative was to swallow the response and log
  only; we expose structured data so users can write
  `{{ states('sensor.last_traefik_reload') }}`-style dashboards.

- **`@<provider>` filter pattern:** Traefik convention is
  `<user-chosen-name>@<provider>` for services and middlewares (only
  routers use plain names — confirmed in PITFALLS #2). The same regex
  `\w+@\w+` covers all three. The user's `gatus` integration handles
  similar provider naming with no filter; Traefik's stricter (internal
  `api@internal` must never appear as a user entity).

- **`sensor.traefik_*` aggregate naming** vs Phase 1's
  `traefik_http_router_*` — chosen deliberately so the three aggregate
  sensors don't collide with the per-router sensor IDs (which have
  the explicit `_http_router_` middle infix per Phase 1 D-10). The
  aggregate family uses bare `traefik_<thing>_count` semantics.

- **Diagnostics sensor default-off:** PITFALLS M-12 — diagnostic
  entities (any-router-failing) default to `entity_registry_enabled_default=
  False` so they don't pollute the States panel; users opt-in
  consciously.

- **`async_unload_entry` regression check:** The gatus unload path
  works because services are registered in module-level `async_setup`,
  not per-entry. Phase 2 must preserve this — the reload service stays
  registered across unload/reload of a config entry.

- **`url_host()` derived device naming:** `<url_host> Traefik — <Category>`.
  If the user points at multiple Traefik instances (e.g., a homelab +
  remote), devices differentiate by host: `homelab Traefik — HTTP
  Routers` vs `remote Traefik — HTTP Routers`. Phase 1 already
  applied this for the single device; D-01 extends.

- **Why Reload button is on Diagnostics device, not Overview:** The
  button fires a write/control action; Overview aggregates read-only
  state. Keeping them on separate devices prevents a future stats-poll
  cadence from also restarting the reload on a button press, and lets
  users hide Diagnostics entirely if they don't care.

</specifics>

<deferred>
## Deferred Ideas

### Reviewed Todos (not folded)

None — `todo match-phase 2` returned `matches: []` (todo_count: 0).

### Other deferred items from discussion

- **Entrypoint "current request count"** — Phase 2 ships
  `address+protocol+name` only. Traefik's runtime request counters
  live in `/api/overview` (`http statistics`) and the Prometheus
  metrics endpoint (`/metrics`) — not in `/api/entrypoints`. Adding
  this requires a separate per-route counter via metrics, which
  conflicts with the polling-cadence model (Prometheus scrapes vs
  on-demand counts). Defer to v2 (the user can open an issue if
  dashboards need request counters per entrypoint).
- **TCP/UDP router+service+entrypoint entities** — PROJECT.md
  explicitly HTTP-only; v2 only.
- **Per-router `using` chain visualization** — Traefik returns
  middlewares-as-array on the router. Could expose attribute; deferred
  to v2.
- **`traefik.reload_static_config` service** — Traefik also has
  `/api/overview/providers` and a static-config reload that requires
  a process signal. Out of scope; v2+.
- **YAML-mode options** — `configuration.yaml` is read-only for
  scan_interval etc.; user changes require UI or Postman-style
  config entry update. Document in Phase 4 README; not Phase 2.

### Phase scope reminders (NOT to be added in Phase 2)

These would be scope creep — flagged explicitly so the planner does NOT add them:

- ❌ TLS handshake helper / `tls.py` / per-router cert sensors (Phase 3,
  preceded by gsd-spike).
- ❌ `diagnostics.py` with credential redaction (Phase 4).
- ❌ Quality-scale metadata / `quality_scale.yaml` (Phase 4, file only,
  not in manifest).
- ❌ Button press → state change semantics (button is fire-and-forget;
  the response surfaces in log + HA trace, not on a state attribute).
- ❌ Tailwind-style entity registry hints (`entity_registry_enabled_default
  = False` IS allowed on any-failing — Phase 2 scope).
- ❌ Multi-language translations (`de.json` etc.; Phase 4).

### Pending research for later phases (locked by ROADMAP / Phase 1)

- **Phase 3 (TLS):** `gsd-spike` REQUIRED before planning Phase 3
  (PITFALLS #5 / #14). Spike validates SNI, multi-cert chains,
  wildcard certs, IPv6, hostname mismatch, format-string loop.
- **Phase 4 (Quality):** `homeassistant.components.diagnostics`
  schema may have shifted; verify before planning diagnostics dump
  and Bronze metadata.

</deferred>

---

*Phase: 02-core-entities-options-reauth-reload*
*Context gathered: 2026-07-06*
