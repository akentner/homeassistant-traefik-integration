# Project Research Summary

**Project:** homeassistant-traefik-integration
**Domain:** HACS-distributable Home Assistant custom integration polling Traefik reverse-proxy HTTP API
**Researched:** 2026-07-05
**Confidence:** HIGH

## Executive Summary

This is a **local-polling Home Assistant custom integration** that wraps Traefik's v2/v3 HTTP API and exposes proxy state as native HA entities (binary sensors, sensors, a button). It fits a well-trodden HA pattern (`gatus`, `kroki` are sibling reference integrations) — one `DataUpdateCoordinator` fans parallel `aiohttp` fetches to `CoordinatorEntity` subclasses, all bundled by HA Core with **zero pip dependencies**.

The recommended approach: scaffold `custom_components/traefik/` with `manifest.json` (no `quality_scale` key — hassfest blocks it for custom integrations), `hacs.json`, and a `TraefikApiClient` that calls `async_get_clientsession(hass)`. Ship the **per-router binary_sensor** as the Core Value in v1.0, layer in entrypoint/service/aggregate sensors and the `traefik.reload` service in v1.1, then attack the **differentiator feature (TLS cert expiry)** in v1.2 via a separate, slow-cadence coordinator — Traefik's HTTP API does NOT expose cert `notAfter`, so the integration must do an out-of-band TLS handshake using stdlib `ssl`.

The key risks: (1) **API token leakage** through logs and diagnostics (mitigate with `async_redact_data`, never-log discipline, request-built-per-call Authorization header); (2) **HA entity-ID regex rejects `@<provider>` characters** that Traefik routinely returns in service/middleware names (`api@internal`, `strip@docker`); (3) **TLS cert `notAfter` parsing** is fragile (locale- and case-dependent format strings); (4) **`UpdateFailed` vs `ConfigEntryAuthFailed`** dispatch determines whether users get a reauth flow or have to delete-and-re-add; (5) **`/api/http/routers/refresh` returns 200 before reload completes** — needs polling-based verification. All five have known, surgical fixes documented in PITFALLS.md.

## Key Findings

### Recommended Stack

This stack is the 2025/2026 baseline for HA polling integrations. Every dependency is provided by HA Core itself — `manifest.json` has `"requirements": []`. The HTTP client uses HA's shared `aiohttp.ClientSession`; Traefik's API is wrapped in a custom ~150-LOC client because **`aiotraefik` does not exist on PyPI** (verified 404). TLS cert expiry uses Python stdlib `ssl` (no extra deps).

**Core technologies:**
- **Python 3.13 / Home Assistant Core >= 2025.4.0** — gives `ConfigEntry.runtime_data`, PEP-695 type aliases, `asyncio.timeout()`, modern dataclass syntax
- **aiohttp (HA-bundled 3.14.x)** — via `async_get_clientsession(hass)`; never create your own `ClientSession()`
- **HA Core helpers** — `DataUpdateCoordinator`, `CoordinatorEntity`, `ConfigFlow`, `OptionsFlow`, `ConfigEntryNotReady`, `ConfigEntryAuthFailed`, `UpdateFailed`, `DeviceInfo`, `async_redact_data` — no pip install
- **Python stdlib `ssl` + `socket`** — for TLS cert expiry handshake (no `cryptography` import unless needed for chain validation)
- **Dev toolchain** — `pytest-homeassistant-custom-component>=0.13.345`, `pytest-asyncio>=1.4.0` (with `asyncio_mode = "auto"`), `pytest-cov>=7.0`, `ruff>=0.15.15`, `mypy>=2.1.0 --strict`, `uv` for dep management
- **Distribution** — `hacs.json` (no `country`/`zip_release`), GitHub release tags drive HACS versions, `custom_components/traefik/brand/icon.png` + `icon@2x.png` required

### Expected Features

The feature landscape for a read-mostly Traefik HA integration is remarkably well-defined. The closest existing peer is `InTheDaylight14/nginx-proxy-manager-switches` (34 stars) — Traefik fills a niche not covered by any current HACS integration.

**Must have (v1.0 — table stakes):**
- **Config Flow (CORE-01)** + YAML import support (CORE-02)
- **DataUpdateCoordinator** + `ConfigEntryNotReady` handling (transient errors) + `ConfigEntryAuthFailed` mapping (401 → reauth)
- **Per-router `binary_sensor`** (CORE-04) — *the Core Value*: `_attr_device_class = BinarySensorDeviceClass.RUNNING`, name = Traefik router `name`
- **Device registry grouping** under a single "Traefik" device with `sw_version` set from `/api/version`
- **Stale entity cleanup** via `coordinator.async_add_listener`
- **HACS distribution** + `brand/` icons + README
- **Traefik version sensor** (D-6) — surfaces `sw_version` on the device
- **Diagnostics dump** with `async_redact_data` stripping `api_key`/`token`/`password`/`basic_auth`

**Should have (v1.1 — differentiators):**
- **TLS cert expiry sensor** (D-1, TLS-01) — *the killer feature*: stdlib TLS handshake per router, parse `notAfter`, compute `days_until_expiry`
- **Cert-expiring-soon `binary_sensor`** (D-2, TLS-02) — `BinarySensorDeviceClass.PROBLEM`, user-configurable threshold (default 14d)
- **Per-entrypoint `sensor`** (CORE-05), per-service `sensor` (CORE-06), aggregate `sensor.traefik` (DIAG-01), "any router failing" `binary_sensor` (DIAG-02)
- **`traefik.reload_routers` service** (DIAG-03) — registered in `async_setup`, not `async_setup_entry`
- **Reauth + Reconfigure + Options Flow** (CFG-01)
- **Two-coordinator split** (state 30s + certs 6h) — prevents hammering TLS ports
- **`Reload` button entity**

**Defer to v2+:**
- Per-backend server health attrs (D-11) — only useful when Traefik `healthcheck` is server-side configured
- Rich per-router attributes (`using`, `provider`) (D-5) — low confidence on user value

**Anti-features (locked out by PROJECT.md "Out of Scope"):**
- Mutate Traefik dynamic config files on disk (Traefik dashboard exists; race conditions)
- ACME / Let's Encrypt provisioning in HA (Traefik owns cert lifecycle; surface expiry only)
- Traefik v1 support (EOL since 2021)
- WebSocket streaming (polling is sufficient; adds reconnect/diff logic)
- Switch entities (Traefik has no per-router enable/disable API)
- Per-middleware entities (middlewares are config-time constructs; use count only)
- TCP/UDP router+service entities (PROJECT.md explicitly HTTP-only)
- Reading `acme.json` from disk (TLS handshake works regardless of Traefik location)
- Auto-discovery via zeroconf/dhcp (Traefik doesn't advertise)

### Architecture Approach

The canonical HA custom-integration skeleton: `manifest.json` registers the domain, `const.py` holds constants, `__init__.py` owns `async_setup_entry` and stores the coordinator on `entry.runtime_data` (modern pattern; `hass.data[DOMAIN]` is deprecated). `api.py` is a pure-async aiohttp client with no HA imports (unit-testable in isolation); `coordinator.py` fans out one polling cycle via `asyncio.gather` across `/api/version`, `/api/entrypoints`, `/api/http/routers`, `/api/http/services`, `/api/http/middlewares`, `/api/overview`; `entity.py` is a shared `TraefikEntity(CoordinatorEntity)` base setting `has_entity_name=True` and the `DeviceInfo` block; platform files (`sensor.py`, `binary_sensor.py`, `button.py`) follow the strict HA convention of **one entity-kind-per-file**. `services.yaml` is paired with handlers registered in `async_setup` (not `async_setup_entry`); `diagnostics.py` uses `async_redact_data`. For TLS, `tls.py` is a stdlib helper called from the coordinator via `asyncio.to_thread` (blocking sockets).

**Major components:**
1. **`api.py` (`TraefikApiClient`)** — one async method per Traefik endpoint; raises typed `TraefikApiError` / `TraefikAuthError`; injects `Authorization: Bearer <api_key>` per request; uses HA's shared session
2. **`coordinator.py` (`TraefikCoordinator`)** — `DataUpdateCoordinator[TraefikData]`; `asyncio.gather` parallel fetch with `asyncio.timeout(10)`; maps auth errors → `ConfigEntryAuthFailed`, transient errors → `UpdateFailed`; for v1.2, a second `CertCoordinator` with 6h cadence
3. **`entity.py` (`TraefikEntity`)** — `CoordinatorEntity` base with `_attr_has_entity_name=True`, shared `DeviceInfo`, `_attr_unique_id = f"{entry.entry_id}_{kind}_{name}"` (incorporates `@` raw; HA slugifies the human-readable name)
4. **`config_flow.py`** — `ConfigFlow` (UI setup + `async_step_yaml` import + `async_step_reauth` + `async_step_reconfigure`) + `OptionsFlow` (bound via `entry.add_update_listener`)
5. **`__init__.py`** — `async_setup` (registers `traefik.reload_routers` service) + `async_setup_entry` (instantiates client+coordinator, first-refresh, stores on `runtime_data`, forwards `PLATFORMS = [SENSOR, BINARY_SENSOR, BUTTON]`) + `async_unload_entry`
6. **`tls.py`** — `fetch_cert_not_after(host, port)` via stdlib `ssl.SSLContext` + `socket.create_connection`; format-string loop for `notAfter` parsing; wraps in `asyncio.to_thread`

### Critical Pitfalls

1. **API token leaks via `_LOGGER.exception()` / diagnostics dump / repr** — Never log the API client; `diagnostics.py` must use `async_redact_data` with `TO_REDACT = {"api_key", "token", "password", "basic_auth"}`. Use `_LOGGER.debug("path=%s status=%s", ...)` lazy formatting. Build `Authorization` header per-request, never set as default header on a long-lived session. **(Phase 1)**

2. **Traefik service/middleware names containing `@<provider>` produce illegal entity IDs** — Traefik encodes provider namespace as `api@internal`, `strip@docker`, `default-auth@kubernetescrd`. HA's regex rejects `@`. Use Traefik `name` as `_attr_unique_id` directly (immutable, contains `@`), let HA slugify the display name via `_attr_has_entity_name=True`, and **filter `api@internal` at coordinator level** (it's Traefik's dashboard service, not a user-managed backend). **(Phase 2)**

3. **Creating own `aiohttp.ClientSession()` instead of HA's shared session** — Violates HA's Platinum rule, loses connector pooling/cookie jar/SSL lifecycle. **Always** use `async_get_clientsession(hass)`. `grep -rn "aiohttp.ClientSession\|ClientSession(" custom_components/traefik/` must return zero hits. **(Phase 1)**

4. **`UpdateFailed` vs `ConfigEntryAuthFailed` dispatch — wrong mapping kills reauth** — On 401 from `/api/http/routers`, raise `ConfigEntryAuthFailed` (not `UpdateFailed`) in BOTH `async_setup_entry` AND `_async_update_data`. Otherwise users see "Updating Traefik failed" forever instead of getting the reauth prompt. Use `aiohttp.ClientResponseError.status` dispatch. **(Phase 1 + Phase 4 reauth)**

5. **TLS cert parsing — `getpeercert()` format-string mismatch and locale bugs** — `notAfter` strings like `Nov 15 12:00:00 2025 GMT` are locale- and case-sensitive. Use a format-string loop with multiple known shapes; wrap in `asyncio.timeout(5)`; cache cert per scan interval; mark entity `unavailable` rather than crashing on parse failure. **Spike recommended before Phase 3.** **(Phase 3)**

6. **`/api/http/routers/refresh` returns 200 before reload completes** — Traefik's providers (Docker, file, Consul) reload asynchronously. Service must poll `/api/http/routers` for count/version change with backoff (200ms → 5s, max 10 attempts) and return `verified: bool` in the response. **(Phase 2, DIAG-03)**

7. **`quality_scale` key in custom-integration manifest → hassfest warning + HACS rejected** — Quality scale is a core-integration governance tool only. The user's `gatus` and `kroki` correctly omit it. `hassfest` enforces this. Optionally add `quality_scale.yaml` (metadata-only) for self-tracking. **(Phase 1)**

8. **Polling interval misconfiguration** — Too aggressive (<15s) triggers Traefik provider thrash (Docker label flapping); too slow (>5min) means state is stale on HA restart. Default 30s; clamp `[15s, 5min]` in Options; **always** call `await coordinator.async_config_entry_first_refresh()` in `async_setup_entry` so initial state appears immediately. **(Phase 1 + Phase 2 options)**

9. **`runtime_data` shape change without `async_migrate_entry`** — If v1 stores `entry.runtime_data = TraefikApi(...)` and v1.1 wraps to `TraefikRuntime(api=..., coordinator=...)`, existing users break. Bump `VERSION`/`MINOR_VERSION` and implement `async_migrate_entry` from day 1. Decide the final shape now and stick to it. **(Phase 1)**

10. **`@<provider>`-related: Traefik router `rule` accidentally used as entity name** — `Host(\`hass.example.com\`) && PathPrefix(\`/api\`)` is unreadable in the UI and breaks automations (backticks, parentheses, ampersands). Always derive display name from Traefik `name` (optionally with first-`Host(...)` match as a friendly hint); store full `rule` as `extra_state_attribute`. **(Phase 2)**

## Implications for Roadmap

Based on combined research (architectural dependency graph from ARCHITECTURE.md + feature dependency tree from FEATURES.md + pitfall-to-phase mapping from PITFALLS.md + stack-phase breakdown from STACK.md), suggested phase structure:

### Phase 1: Foundation — `custom_components/traefik/` scaffold + Config Flow + Coordinator + first binary_sensor
**Rationale:** Nothing works without `manifest.json` + `const.py` + `api.py` + `coordinator.py`. Config Flow must precede Options Flow. The dependency graph from ARCHITECTURE.md is strict: `const.py → api.py → coordinator.py → entity.py → config_flow.py → __init__.py → platform files`. Land one end-to-end binary_sensor to prove the polling loop.
**Delivers:**
- `manifest.json` (no `quality_scale` key), `hacs.json`, `pyproject.toml`, `ruff.toml`, `.gitignore`, `LICENSE`
- `const.py` with `DOMAIN`, `CONF_URL`, `CONF_API_KEY`, `CONF_VERIFY_SSL`, defaults
- `api.py` — `TraefikApiClient` (aiohttp wrapper, no HA imports, typed exceptions)
- `coordinator.py` — `TraefikCoordinator(DataUpdateCoordinator[TraefikData])`, parallel `asyncio.gather`, 10s timeout, exception mapping
- `entity.py` — `TraefikEntity(CoordinatorEntity)` base with `DeviceInfo`, `_attr_has_entity_name=True`, `_attr_unique_id = f"{entry.entry_id}_..."` pattern
- `__init__.py` — `async_setup_entry` (client + coordinator + first-refresh + `runtime_data` + forward `PLATFORMS`), `async_unload_entry`
- `config_flow.py` — `ConfigFlow.async_step_user` (URL + API key + verify_ssl, validates via `client.get_overview()`), `async_step_yaml` for CORE-02
- `binary_sensor.py` — `TraefikRouterBinarySensor(BinarySensorDeviceClass.RUNNING)` (CORE-04)
- `services.yaml` (empty for v1) + CI: hassfest + HACS Action + pytest
- README skeleton, brand/icon.png placeholders
**Addresses (from FEATURES.md):** T-1, T-2, T-3, T-4, T-9, T-12, T-13, T-15, D-6
**Avoids (from PITFALLS.md):** P1 (token leak), P4 (own ClientSession), P5 (tag drift), P6 (runtime_data shape), P7 (exception mapping), P8 (polling cadence), P9 (first refresh), P11 (unique_id), P13 (no quality_scale key), M1, M2, M3, M4, M6
**Research flag:** Standard patterns — well-documented across HA docs, integrations skill, and user's two sibling integrations. **Skip `gsd-research-phase`** for this phase.

### Phase 2: Core entities + Options Flow + Reauth + Reload service
**Rationale:** Now that the polling loop is proven, layer in the remaining table-stakes entity types and the user-config knobs. Per-router binary_sensor is the platform for `name`-slugification; per-entrypoint/per-service sensors add value; Options Flow must follow Config Flow. The reload service exercises both the API client and the service-registration-in-`async_setup` pattern.
**Delivers:**
- `sensor.py` — `TraefikEntrypointSensor` (CORE-05), `TraefikServiceSensor` (CORE-06), `TraefikOverviewSensor` (DIAG-01, aggregate counts), `TraefikVersionSensor` (D-6)
- `binary_sensor.py` — `TraefikAnyRouterFailingBinarySensor` (DIAG-02, `BinarySensorDeviceClass.PROBLEM`)
- `button.py` — `TraefikReloadButton` (DIAG-03, `ButtonEntityDeviceClass.RESTART`)
- `services.yaml` schema for `traefik.reload_routers` + handler in `async_setup` (NOT `async_setup_entry`)
- `OptionsFlow` (CFG-01) bound via `entry.add_update_listener` — scan interval (clamp 15s–5min), verify_ssl
- Reauth flow (`async_step_reauth`) — maps 401 → reauth UI
- Reconfigure flow (`async_step_reconfigure`) — URL change without delete+re-add
- Stale entity cleanup via `coordinator.async_add_listener`
- Expanded `strings.json` + `translations/en.json` (de.json optional)
- Full test suite: config flow 100% coverage, coordinator, sensor, binary_sensor, button, services
**Addresses (from FEATURES.md):** T-5, T-6, T-7, T-8, T-10, T-11, T-14, D-3, D-4, D-5, D-7
**Avoids (from PITFALLS.md):** P2 (`@<provider>` filtering), P3 (rule field as name), P10 (availability), P12 (noisy default-enabled → `entity_registry_enabled_default=False` on diagnostic sensors), P15 (refresh async polling), M5 (service in `async_setup`)
**Research flag:** Standard patterns for entities and services; well-documented. **Skip `gsd-research-phase`**.

### Phase 3: TLS cert expiry — spike → TLS coordinator → cert-expiry entities
**Rationale:** The killer differentiator (D-1, D-2). Requires a separate slow-cadence `CertCoordinator` because hammering TLS ports every 30s is wasteful and may get hosts to fingerprint the scanner. Has well-documented pitfalls (format-string parsing, hostname extraction from `Host(...)` rules, blocking socket calls). Spike first to validate the approach against real Traefik+Let's Encrypt deployments.
**Delivers:**
- `gsd-spike` (before Phase 3 plan): validate stdlib TLS handshake against 3+ real Traefik instances; test SNI, multi-cert chains, wildcard certs; confirm format strings
- `tls.py` — `fetch_cert_not_after(host, port)` with format-string loop; `parse_host_from_rule(rule)` regex helper (limited to one match); wrapped in `asyncio.to_thread`
- `CertCoordinator(DataUpdateCoordinator)` — 6h cadence, iterates routers with `tls` set, calls `fetch_cert_not_after` per host (with semaphore for >10 routers)
- `sensor.py` additions: `TraefikCertificateSensor(SensorDeviceClass.TIMESTAMP)` per TLS-enabled router (TLS-01)
- `binary_sensor.py` additions: `TraefikCertExpiryBinarySensor(BinarySensorDeviceClass.PROBLEM)` per TLS-enabled router (TLS-02)
- Options Flow additions: `CONF_TLS_WARN_DAYS` (default 14), separate `CONF_SCAN_INTERVAL` for cert cadence (D-8, D-9, D-10)
- Tests: 3+ valid `notAfter` format strings, 2+ invalid; mock TLS handshake; cache validation
**Addresses (from FEATURES.md):** D-1, D-2, D-8, D-9, D-10
**Avoids (from PITFALLS.md):** P14 (TLS cert parse — format string loop), m2 (parse_datetime), m4 (rule hostname quoting), cert fingerprint blacklisting (cache per scan interval)
**Research flag:** **STRONGLY FLAGGED for `gsd-research-phase`** (or `gsd-spike`) — TLS handshake has subtle edge cases (SNI routing, multi-cert chains, wildcard certs, IPv6, hostname mismatches). Real-world validation against 3+ production Traefik deployments before committing to approach.

### Phase 4: Quality scale + Diagnostics + Polish + HACS publication
**Rationale:** After v1.2 ships and users validate the architecture, layer in Silver-tier quality-scale features and finalize HACS publication. Diagnostics is the last major functional component (depends on stable `runtime_data` shape).
**Delivers:**
- `diagnostics.py` — `async_get_config_entry_diagnostics(hass, entry)` with explicit redaction (`TO_REDACT = {"api_key", "token", "password", "basic_auth"}`)
- `repairs.py` (optional) — repair issues for unreachable API / expired certs > 30 days
- `quality_scale.yaml` (metadata-only — not enforced for custom integrations, but tracks rule status)
- Finalized `README.md` with HACS install badge, manual install, example automations, FAQ addressing A-1 through A-11 anti-features
- `info.md` (HACS store card), `CHANGELOG.md`, full brand asset set (icon.png 256×256, icon@2x.png 512×512, dark_icon.png optional)
- `.github/workflows/hassfest.yaml`, `hacs-action.yaml`, `tests.yaml`, `release.yaml` (enforces manifest `version` == git tag)
- `.github/CODEOWNERS` matching manifest `codeowners`
- Pre-commit hooks: ruff, mypy, hassfest, manifest-version check
**Addresses (from FEATURES.md):** Polish, release engineering
**Avoids (from PITFALLS.md):** M7 (diagnostics leak), "Looks Done But Isn't" checklist items
**Research flag:** Standard polish work. Light research on `homeassistant.components.diagnostics` schema if it's moved since 2025. **Skip `gsd-research-phase`** unless diagnostics surface area changed.

### Phase Ordering Rationale

The order is dictated by four converging dependency chains:

1. **Architectural (ARCHITECTURE.md):** `const.py → api.py → coordinator.py → entity.py → config_flow.py → __init__.py → platforms` — must build bottom-up; cannot forward-setup platforms before coordinator exists.
2. **Feature (FEATURES.md):** T-1 (Config Flow) must precede everything; T-3 (Coordinator) precedes all entity features; T-9 (per-router binary_sensor) is the Core Value and must land in v1.0; D-1 (TLS) depends on a separate coordinator (D-9) and benefits from user validation of the v1 architecture first.
3. **Pitfall (PITFALLS.md):** Token-leak, runtime_data shape, exception-mapping, and ClientSession pitfalls must be locked in during Phase 1 — they are foundational and irreversible once baked in. `@<provider>`-name handling and rule-as-name pitfalls apply once entities exist (Phase 2). TLS pitfalls apply once TLS ships (Phase 3).
4. **Stack (STACK.md):** Modern HA features (`ConfigEntry.runtime_data`, PEP-695, `asyncio.timeout`) all require HA 2025.4+, so the manifest gate is set there. HACS submission requires `hacs.json` + `brand/` from day 1.

### Research Flags

**Phases likely needing deeper research during planning:**
- **Phase 3 (TLS):** **STRONGLY FLAGGED** — TLS handshake has subtle edge cases (SNI, multi-cert chains, wildcard certs, IPv6, clock skew). Run `gsd-spike` against 3+ real Traefik deployments before committing to the stdlib `ssl` approach. MEDIUM-HIGH implementation cost; HIGH user value (differentiator). Also revisit CFG-01 scan-interval override here — the HA quality-scale rule "Polling intervals are NOT user-configurable" conflicts with the explicit Options Flow knob; decide Bronze-only target or drop the knob.

**Phases with standard patterns (skip research-phase):**
- **Phase 1 (Foundation):** DataUpdateCoordinator + ConfigFlow + aiohttp + ConfigEntry.runtime_data are exhaustively documented in the integrations skill, HA developer docs, and user's two sibling integrations (`gatus`, `kroki`). Confidence HIGH.
- **Phase 2 (Core entities):** Standard HA entity patterns; one-entity-kind-per-file convention is well-established. Snapshot tests + 100% config-flow coverage is the same recipe as every HA core integration.
- **Phase 4 (Polish):** Release engineering, diagnostics, HACS publication — well-trodden path. Quality scale schema may have moved; verify on first pass.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | **HIGH** | Verified against `developers.home-assistant.io/docs/creating_integration_manifest`, `hacs.xyz/docs/publish/integration`, HA Core `requirements.txt` (`aiohttp==3.14.1`), user's two sibling integrations (`gatus`, `kroki`). `aiotraefik` 404 verified directly. |
| Features | **HIGH** | Table stakes verified against HA quality-scale + integrations skill; differentiators (TLS expiry, reload service, any-failing aggregate) anchored in PROJECT.md IDs and Traefik v3 API docs; anti-features locked by PROJECT.md "Out of Scope" + this research identified 6 additional anti-features (A-6..A-11). MEDIUM on TLS handshake approach pending spike. |
| Architecture | **HIGH** | Patterns verified against HA developer docs, integrations skill (lines 119, 121, 164, 306, 318, 326, 349, 359, 366, 411), two reference HA core integrations (`fully_kiosk`, `hassio` for diagnostics), Traefik v3 API docs. MEDIUM on per-entity-class file-split rationale (convention from HA Core style, not formalized in docs). |
| Pitfalls | **HIGH** | Cross-verified against HA Core source (entity ID regex `_OBJECT_ID = r"(?!_)[\da-z_]+(?<!_)"`), Traefik v3 docs (router naming, refresh endpoint semantics, `notAfter` non-exposure), HACS docs, and user's two sibling integrations. |

**Overall confidence:** **HIGH** — patterns are well-established, verified against multiple primary sources, and the user's prior integration work (`gatus`, `kroki`) provides local reference implementations.

### Gaps to Address

1. **TLS handshake edge cases (SNI, multi-cert chains, wildcard certs, IPv6)** — Phase 3 must run a `gsd-spike` against 3+ real Traefik deployments to validate the stdlib `ssl` approach before committing. Suggested spike deliverable: a 1-page spike document listing validated scenarios + a `tls.py` prototype with tests.

2. **`CFG-01` scan-interval override conflicts with HA quality-scale rule "Polling intervals are NOT user-configurable"** — Decide during Phase 2 or 3 discussion: either drop the scan-interval knob (use fixed 30s; Bronze tier achievable), or keep it (user-configurable; Silver tier blocked). Recommend (a) for cleanest quality-scale path; user can request override later via feedback.

3. **Quality Scale target tier** — Bronze minimum for HACS; Silver adds entity-unavailable handling + parallel updates + tests coverage. Recommend **Bronze for v1.0**, **Silver for v1.2** once we have feedback and TLS shape is stable.

4. **Cert re-fetch cadence policy** — If user has 200 routers with TLS, fetching all certs every scan interval is heavy. Need explicit policy: fetch once per cert coordinator cycle (6h), or only when `tls.domains` changes (less coverage but cheaper). **Decide during Phase 3 planning.**

5. **`brand/` assets** — Need to source Traefik logo (Apache 2.0; verify attribution) and provide light + dark variants at 256×256 and 512×512.

6. **Translations scope** — Only `en` is strictly required for HACS; `de.json` is nice-to-have given the user's locale. Add to Phase 4 polish.

7. **YAML configuration (CORE-02)** — `ConfigFlow.async_step_yaml` is the standard hook, but verify `hassfest` accepts the YAML schema matches `vol.Schema` of `async_step_user`. Validate in Phase 1.

## Sources

### Primary (HIGH confidence)
- HA integration manifest schema — `developers.home-assistant.io/docs/creating_integration_manifest`
- HA config flow patterns (reauth, reconfigure, migration) — `developers.home-assistant.io/docs/config_entries_config_flow_handler`
- HA setup failures (`ConfigEntryNotReady` vs `ConfigEntryAuthFailed` vs `UpdateFailed`) — `developers.home-assistant.io/docs/integration_setup_failures`
- HA entity ID regex — `homeassistant/core.py` `_OBJECT_ID = r"(?!_)[\da-z_]+(?<!_)"` (verified)
- Traefik v3 API & Dashboard endpoints — `doc.traefik.io/traefik/reference/install-configuration/api-dashboard` (cert `notAfter` non-exposure verified; refresh POST async verified)
- Traefik v3 router naming — `doc.traefik.io/traefik/reference/routing-configuration/http/routing/router` (`@` forbidden in router name; only services/middlewares use `<name>@<provider>`)
- Traefik v3 entrypoint schema — `doc.traefik.io/traefik/reference/install-configuration/entrypoints`
- HACS publish docs — `hacs.xyz/docs/publish/integration`, `hacs.xyz/docs/publish/start`
- HA Core pinned deps — `home-assistant/core/blob/dev/requirements.txt` (`aiohttp==3.14.1`)
- `pytest-homeassistant-custom-component` — `MatthewFlamm/pytest-homeassistant-custom-component` (v0.13.345, 2026-07-04)
- HA core reference code — `homeassistant/components/faa_delays/coordinator.py`, `homeassistant/components/gios/coordinator.py` (DataUpdateCoordinator + aiohttp patterns)
- Diagnostics reference — `homeassistant/components/fully_kiosk/diagnostics.py`, `homeassistant/components/hassio/diagnostics.py` (verified `async_redact_data` pattern)
- HA integrations skill — `/home/akentner/.opencode/skills/integrations/SKILL.md` (786 lines, primary reference)
- HA Home Assistant skill — `/home/akentner/.opencode/skills/home-assistant/SKILL.md` (API patterns)

### Secondary (MEDIUM confidence)
- User's local reference integrations — `/home/akentner/Projects/homeassistant-gatus-integration/`, `/home/akentner/Projects/homeassistant-kroki-integration/` (patterns reused: PEP-695 `type` alias, stale entity cleanup via `coordinator.async_add_listener`, `DeviceInfo` grouping, `async_step_yaml`)
- Closest existing peer — `InTheDaylight14/nginx-proxy-manager-switches` (34 stars, HACS, MIT) — feature gap analysis

### Negative results (verified 404 / NOT applicable)
- `pypi.org/pypi/aiotraefik` — **does not exist** (verified). Use custom aiohttp wrapper.
- `quality_scale` in custom-integration manifest — hassfest blocks this (verified against user's `gatus` & `kroki` integrations).

---
*Research completed: 2026-07-05*
*Ready for roadmap: yes*