# Phase 1: Foundation - Context

**Gathered:** 2026-07-05
**Status:** Ready for planning

<domain>
## Phase Boundary

Installable, HACS-distributable Home Assistant integration that talks to a single
Traefik v2/v3 instance via its HTTP API. After Phase 1 the user can:

1. Install via HACS or manual copy into `custom_components/traefik/`.
2. Complete the UI config flow with Traefik URL + bearer token + verify_ssl, OR
   place a YAML entry in `configuration.yaml`.
3. See a single device in HA's device registry called "HTTP Routers" (named after
   the Traefik instance hostname), with one `binary_sensor` per Traefik HTTP
   router reporting enabled/disabled state. Traefik's raw router status string
   is exposed as an attribute.

Phase 1 only ships the **HTTP Routers** device (the Core Value category).
Phase 2 adds the remaining 7 device categories (HTTP Services, HTTP Middlewares,
TCP Routers/Services/Middlewares, UDP Routers/Services). Phase 3 adds
Certificates.

**Architectural shift from ROADMAP:** The ROADMAP previously described a
"single Traefik device + binary_sensor per router" model. The user vision
confirmed during discuss-phase is a **9-device model**, one device per
category, populated incrementally across phases. Phase 1 still ships only the
HTTP Routers device; subsequent phases introduce their own devices.

</domain>

<decisions>
## Implementation Decisions

### Architecture: `runtime_data` shape

- **D-01 (architecture):** `entry.runtime_data` holds the **bare coordinator**.
  PEP-695 typed as `type TraefikConfigEntry = ConfigEntry[TraefikCoordinator]`.
  The `TraefikApiClient` is constructed inside `TraefikCoordinator.__init__`
  and exposed as `self.client` so Phase 2's `traefik.reload_routers` service
  handler (DIAG-03) can call `entry.runtime_data.client.async_reload_routers()`
  directly. No wrapper class.

  Rationale: matches the HA Core dev team's official pattern (verified against
  `homeassistant/components/faa_delays/__init__.py` and the integrations skill),
  matches the user's existing `gatus` integration, and avoids premature
  architectural complexity. PITFALLS #6's wrapper recommendation was
  over-engineering.

- **D-02 (architecture):** `VERSION = 1`, `MINOR_VERSION = 1` declared
  explicitly in `TraefikConfigFlow`. No `async_migrate_entry` stub needed on
  day 1 (will be added when runtime_data shape actually changes).

### Configuration flow

- **D-03 (config flow):** `async_step_user` validates URL + bearer token by
  probing `GET /api/overview` with `asyncio.timeout(10)`. All `/api/*`
  endpoints share the same auth boundary per Traefik's API contract, so a 200
  on `/api/overview` confirms URL + token + API enabled. Error mapping:
  - 401 / 403 → "invalid_auth" (user-fixable: rotate token)
  - 404 → "api_disabled" (Traefik's `api: {}` is not enabled — show distinct
    translation key so user knows to enable it)
  - Timeout / network / 5xx → "cannot_connect"
  - Other → "unknown"

- **D-04 (config flow):** `async_step_yaml` handles YAML import (CFG-02).
  YAML schema is minimal — same as the UI form:
  ```yaml
  traefik:
    url: https://traefik.example.com:8080
    api_key: "${traefik_bearer_token}"
    verify_ssl: true
  ```
  `verify_ssl` defaults to `true` if omitted (secure-by-default, matches UI).

- **D-05 (config flow):** `verify_ssl` is a top-level boolean checkbox in the
  UI form, default `True`. Translation note: "Disable if Traefik uses a
  self-signed certificate". No "Advanced" toggle.

- **D-06 (config flow):** When the user enters an `http://` URL (not
  `https://`), show an inline description under the URL field:
  "Warning: bearer token will be sent over plaintext if URL uses http://.
  Use https:// for production." Allow submission as-is — user might be on a
  trusted LAN.

### Device & entity model

- **D-07 (device model):** Each of 9 categories registers as its own device in
  HA's device registry (not a single "Traefik" device). Phase 1 ships only the
  **HTTP Routers** device. Other devices come in later phases.
  Device identifier: `(DOMAIN, "<entry_id>_http_routers")` (so each config
  entry has its own set of 9 devices, distinguishable across multiple Traefik
  instances).
  Device name: `f"{url_host} HTTP Routers"` for Phase 1 — uses the hostname
  parsed from the configured URL so users with multiple Traefik instances
  distinguish them.
  Manufacturer: "Traefik"; model: "HTTP Routers".

- **D-08 (entity model):** Each HTTP router becomes a `binary_sensor` with
  `BinarySensorDeviceClass.RUNNING`. State on = Traefik `status == "enabled"`,
  state off = anything else (`"warning"`, `"error"`, missing). Raw Traefik
  status string exposed as `extra_state_attributes["status"]` for dashboards
  that want the granular value. This replaces the ROADMAP's sensor-with-enum
  design — simpler to consume in automations (no string matching).

- **D-09 (entity naming):** Entity ID pattern:
  `binary_sensor.traefik_http_router_<slugified_router_name>`. Set explicitly
  via `self.entity_id = f"binary_sensor.traefik_http_router_{slug}"` (does
  NOT rely on HA auto-slugify because the `traefik_http_router_` prefix must
  be present). `_attr_has_entity_name = True` is still set so the UI shows
  `"<device_name> <router_name>"` (e.g. "traefik.example.com HTTP Routers my-router").
  The `name` attribute is the raw Traefik router `name`. Slugify helper uses
  `homeassistant.util.slugify` to handle `@`, `:`, `.` correctly.

- **D-10 (entity ID prefix rationale):** The `traefik_http_router_` prefix
  survives Phase 2/3 additions cleanly — Phase 2 sensors/binary_sensors will
  use `traefik_http_service_`, `traefik_tcp_router_`, etc. without colliding.

- **D-11 (device sw_version):** Each device's `sw_version` is set from
  `/api/version` and **live-updated** on every coordinator refresh. The
  coordinator includes `/api/version` in its parallel fetch (alongside
  `/api/http/routers` for Phase 1). When Traefik is upgraded, the device card
  reflects the new version automatically.

### Polling

- **D-12 (polling):** Default `update_interval = timedelta(seconds=15)`. User
  accepts PITFALLS #8 risk (provider thrash); this is intentional for
  near-real-time visibility. Phase 2 Options Flow clamps to `[15s, 5min]` so
  users experiencing thrash can tune up.

- **D-13 (polling):** All parallel fetches in `_async_update_data` wrapped in
  `asyncio.timeout(10)`. For Phase 1 the parallel fetch targets are:
  - `GET /api/version` (for `sw_version`)
  - `GET /api/http/routers` (for binary_sensor data)
  Both wrapped together in one `asyncio.gather` + `asyncio.timeout(10)`.
  `asyncio.gather(return_exceptions=True)` so a transient failure of one
  endpoint doesn't cancel the other.

### Error handling

- **D-14 (errors):** Bearer token built **per request** as
  `Authorization: Bearer <entry.data[CONF_API_KEY]>` — never as a default
  header on a long-lived session. The TraefikApiClient never logs the token.
  `_LOGGER.debug("path=%s status=%s", path, status)` lazy formatting only;
  client instance never passed to logger.

- **D-15 (errors):** Exception dispatch in coordinator `_async_update_data`:
  - `aiohttp.ClientResponseError.status in (401, 403)` → raise
    `ConfigEntryAuthFailed` (triggers reauth — wired in Phase 2's
    `async_step_reauth`, but the exception type must be correct from day 1).
  - `aiohttp.ClientConnectorError` / `asyncio.TimeoutError` / other network
    errors → raise `UpdateFailed("...")` (steady-state transient).
  - `KeyError` / `ValueError` from JSON parse → `UpdateFailed`.
  `ConfigEntryNotReady` is NEVER raised directly in `_async_update_data` —
  the first-refresh call (`async_config_entry_first_refresh()` in
  `async_setup_entry`) auto-converts `UpdateFailed` from the first cycle into
  `ConfigEntryNotReady`.

- **D-16 (errors):** On first-refresh failure, `await
  coordinator.async_config_entry_first_refresh()` propagates
  `ConfigEntryNotReady` (transient) or `ConfigEntryAuthFailed` (401/403)
  unchanged. HA handles retry with backoff.

### Manifest & distribution

- **D-17 (manifest):** `manifest.json` final fields:
  ```json
  {
    "domain": "traefik",
    "name": "Traefik",
    "codeowners": ["@akentner"],
    "config_flow": true,
    "documentation": "https://github.com/akentner/homeassistant-traefik-integration",
    "integration_type": "service",
    "iot_class": "local_polling",
    "issue_tracker": "https://github.com/akentner/homeassistant-traefik-integration/issues",
    "after_dependencies": ["http"],
    "requirements": [],
    "version": "1.0.0"
  }
  ```
  **No `quality_scale` key** — hassfest blocks it for custom integrations.

- **D-18 (hacs.json):**
  ```json
  { "name": "Traefik", "homeassistant": "2025.4.0", "hacs": "2.0.5" }
  ```
  No `filename`, no `zip_release`, no `country` (standard folder distribution).

- **D-19 (brand icons):** Use Traefik's official logo (Apache 2.0). Files:
  - `custom_components/traefik/brand/icon.png` (256x256)
  - `custom_components/traefik/brand/icon@2x.png` (512x512)
  README includes attribution: "Traefik logo used under Apache License 2.0."
  Phase 4 may add `dark_icon.png` if needed for HA theme switching.

- **D-20 (CI gates, PITFALLS #5):** The release workflow (added in Phase 4,
  but the policy is locked in Phase 1) will fail if `git tag` ≠
  `manifest.json:version`. Phase 1's pyproject.toml + ruff config establishes
  the dev toolchain so Phase 4 only adds the GitHub Actions.

### the agent's Discretion

- Exact error message wording in `strings.json` / `translations/en.json` —
  translator should match HA Core's tone.
- Whether to bundle a `services.yaml` placeholder in Phase 1 (empty file
  ready for Phase 2's `traefik.reload_routers`) or wait until Phase 2 ships.
  Default: defer to Phase 2 to keep Phase 1's diff smaller.
- Test fixture capture: capture from the user's `haos-op3050-1` Traefik
  instance OR hand-craft minimal JSON. Both satisfy TEST-03; planner picks.

### Folded Todos

None — no todos match this phase (todo_count: 0).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project planning artifacts
- `.planning/PROJECT.md` — Vision, Core Value, requirements list, constraints, Key Decisions log
- `.planning/REQUIREMENTS.md` — Detailed v1 requirements (46 total) with phase traceability
- `.planning/ROADMAP.md` — Phase breakdown, success criteria, plan placeholders
- `.planning/STATE.md` — Current position, accumulated decisions, blockers
- `.planning/research/SUMMARY.md` — Research synthesis, recommended architecture, pitfalls summary
- `.planning/research/PITFALLS.md` — 15 critical + 7 moderate + 7 minor pitfalls with prevention
- `.planning/research/STACK.md` — Recommended stack with rationale and "what NOT to use"

### Home Assistant Core docs (current as of fetch)
- `https://developers.home-assistant.io/docs/creating_integration_manifest/` — Manifest schema
- `https://developers.home-assistant.io/docs/config_entries_config_flow_handler/` — ConfigFlow API
- `https://developers.home-assistant.io/docs/integration_setup_failures/` — ConfigEntryNotReady vs ConfigEntryAuthFailed semantics

### Home Assistant skill references (local)
- `~/.opencode/skills/integrations/SKILL.md` — Primary HA integration reference (786 lines; covers config flow, DataUpdateCoordinator, entity naming, device registry, diagnostics)
- `~/.opencode/skills/home-assistant/SKILL.md` — HA API reference

### User's reference integrations (local, sibling patterns)
- `/home/akentner/Projects/homeassistant-gatus-integration/` — Proves the bare-coordinator-in-runtime_data pattern (mirrors HA Core's faa_delays). Reuse the `ConfigEntry[GatusDataUpdateCoordinator]` PEP-695 alias, exception mapping pattern (ConfigEntryAuthFailed on 401, UpdateFailed on transient), stale-entity cleanup via `coordinator.async_add_listener`. The `gatus` binary_sensor.py's `_remove_stale_entities` listener pattern is exactly what we need for ROUTER-02/03 in Phase 2.
- `/home/akentner/Projects/homeassistant-kroki-integration/` — Reference for `after_dependencies: ["http"]` pattern in manifest.

### Traefik API docs (verified Jul 2026)
- `https://doc.traefik.io/traefik/reference/install-configuration/api-dashboard/` — Endpoint list, auth model (all `/api/*` share the same auth boundary), router naming rules
- `https://doc.traefik.io/traefik/reference/install-configuration/entrypoints/` — Entrypoint schema (Phase 2)

### HACS distribution docs
- `https://hacs.xyz/docs/publish/start` — hacs.json schema
- `https://hacs.xyz/docs/publish/integration` — Repository layout requirements

### HA Core source pattern (verified)
- `https://github.com/home-assistant/core/blob/dev/homeassistant/components/faa_delays/__init__.py` — Canonical "bare coordinator in runtime_data" pattern

</canonical_refs>

## Existing Code Insights

### Reusable Assets

- **gatus integration pattern** (`/home/akentner/Projects/homeassistant-gatus-integration/custom_components/gatus/`):
  - `coordinator.py:44-114` — `GatusDataUpdateCoordinator(DataUpdateCoordinator[dict[str, GatusEndpoint]])` template; copy the constructor + `_async_update_data` shape, replace endpoint with the parallel `asyncio.gather(version, routers)` and exception mapping.
  - `__init__.py:25-53` — `async_setup_entry` template (create coordinator, `async_config_entry_first_refresh()`, set `runtime_data`, forward platforms).
  - `config_flow.py:135-175` — `async_step_user` template; copy the validation pattern, replace probe endpoint with `/api/overview`.
  - `entity.py:12-66` — Base entity pattern (DeviceInfo + unique_id); adapt for HTTP Routers device.
  - `binary_sensor.py:22-71` — Stale entity cleanup via `coordinator.async_add_listener`; Phase 1 ships no stale-cleanup yet (single-shot setup), Phase 2 picks this up.

### Established Patterns

- **PEP-695 type aliases** for `ConfigEntry[<Coordinator>]` — used by gatus, matches HA Core 2025.4+.
- **`async_get_clientsession(hass)`** for all aiohttp calls — never `aiohttp.ClientSession()` directly.
- **Bearer token per-request header** — `headers = {"Authorization": f"Bearer {api_key}"}` only when api_key truthy.
- **Lazy log formatting** — `_LOGGER.debug("path=%s", path)`, never f-string interpolation of secrets.
- **StringSelector with PASSWORD type** for token field in config flow.
- **`async_step_yaml` for YAML import** — standard HA pattern.

### Integration Points

- `custom_components/traefik/` — new folder, no existing code in this project.
- `pyproject.toml` (project root) — new file; dev deps + ruff config.
- `.github/workflows/hassfest.yaml` (Phase 4) — new file.
- `tests/components/traefik/` — new folder for pytest-homeassistant-custom-component.

</canonical_refs>

<specifics>
## Specific Ideas

- **9-device model is a user-driven architectural change** from the ROADMAP's
  "single Traefik device" plan. The planner should treat each device category
  as an independent `DeviceInfo` block with `(DOMAIN, f"{entry_id}_{category}")`
  identifier. Phase 1 ships only the HTTP Routers device; the other 8 device
  categories are scaffolded (empty `identifiers` registration is not needed —
  just plan their addition in Phase 2/3).

- **Per-router status string in attributes:** Traefik's `router.status` field
  returns values like `"enabled"`, `"disabled"`, `"warning"`, `"error"`. Phase 1
  collapses these to `True`/`False` (enabled → True, anything else → False).
  Phase 2 (DIAG-01 "any router failing" binary_sensor) consumes the same data
  via the coordinator.

- **Traefik logo sourcing:** Apache 2.0 logo is available from
  `https://doc.traefik.io/traefik/` (the official Traefik brand assets page).
  If Phase 4 adds a dark variant, use the official wordmark on dark background.

- **`hostname` extraction for device name:** Use `urllib.parse.urlparse(url).hostname`
  in `async_setup_entry`. If hostname is `None` (malformed URL), fall back to
  the full URL string. This is how the device gets a user-friendly
  "traefik.example.com HTTP Routers" name.

- **Traefik routing rule = attribute, never name** (PITFALLS #3): The
  `binary_sensor`'s `name` is the Traefik router `name` (slug-safe). The full
  `rule` (e.g. `Host(\`hass.example.com\`) && PathPrefix(\`/api\`)`) goes into
  `extra_state_attributes["rule"]` for dashboards to render.

- **`_attr_name` vs explicit `entity_id`:** HA's `_attr_has_entity_name=True`
  normally derives the entity_id from `name` + platform. Because the user wants
  the explicit `traefik_http_router_<slug>` prefix, set `entity_id` directly
  in the constructor and also set `_attr_name = router_name` so the UI shows
  the readable name. `_attr_has_entity_name=True` keeps device-grouping UI
  intact (entity shows as "<device> <name>").

- **Local testing:** User has `haos-op3050-1` HA host (Tailscale) reachable
  via SSH. `~/.local/bin/ha` CLI for restarts. Phase 1 plans should include a
  one-line `scp` deploy command for live testing, mirroring the gatus CLAUDE.md.

</specifics>

<deferred>
## Deferred Ideas

### Reviewed Todos (not folded)

None — no todos matched this phase (todo_count: 0).

### Other deferred items from discussion

- **YAML configuration scope:** Discussion skipped this area; the planner
  should keep YAML schema minimal (URL + token + verify_ssl) per CFG-02.
  Expanding YAML to expose `scan_interval` is a possible Phase 4 polish item.
- **Test fixture sourcing:** Either capture from `haos-op3050-1` or
  hand-craft — planner's choice. Capture is preferred for realism; hand-craft
  is faster and more portable.
- **`services.yaml` placeholder:** May or may not be added in Phase 1 — defer
  to planner. Phase 2 will add the real `traefik.reload_routers` service.
- **`dark_icon.png` brand asset:** Phase 4 polish if HA theme switching matters.

### Phase scope reminders (NOT to be added in Phase 1)

These would be scope creep — flagged explicitly so the planner does NOT add them:

- ❌ Any `sensor` entity besides binary_sensor (the user's model is binary-only
  in Phase 1; enum-state sensors are a Phase 2+ consideration).
- ❌ `entrypoint` or `service` devices or entities (Phase 2).
- ❌ TCP / UDP routers/services/middlewares (Phase 2).
- ❌ Certificate / TLS entities (Phase 3 — flagged for `gsd-spike` first).
- ❌ `traefik.reload_routers` service handler (Phase 2 DIAG-03).
- ❌ Options Flow (Phase 2 — scan_interval clamp, verify_ssl rotation).
- ❌ Reauth / Reconfigure flows (Phase 2 — but the coordinator MUST raise
  `ConfigEntryAuthFailed` on 401 from day 1 so the Phase 2 wiring Just Works).
- ❌ Diagnostics.py (Phase 4).
- ❌ Repairs flow (Phase 4).
- ❌ Quality-scale metadata (Phase 4 — for tracking only, not in manifest).

### Pending research for later phases (locked by ROADMAP)

- **Phase 3 (TLS):** `gsd-spike` REQUIRED before planning. PITFALLS #14 has
  detailed edge cases (format strings, locale bugs, SNI, chain validation).
- **Phase 2 (Options Flow):** The HA quality-scale rule "Polling intervals are
  NOT user-configurable" conflicts with Phase 2's planned scan-interval
  knob. Decide during Phase 2 discuss-phase: either drop the knob (cleanest
  quality-scale path; user can request via feedback) or keep it (Silver tier
  blocked, Bronze only). The 15s default in Phase 1 works for both paths.

</deferred>

---

*Phase: 01-foundation*
*Context gathered: 2026-07-05*