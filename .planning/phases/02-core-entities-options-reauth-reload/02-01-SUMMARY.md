---
phase: 02-core-entities-options-reauth-reload
plan: 01
subsystem: api
tags: [traefik-api, typeddict, multi-device, filter, reload, ruff, mypy, pytest]

# Dependency graph
requires:
  - phase: 01-foundation
    provides: "TraefikApiClient, TraefikCoordinator, TraefikEntity, TraefikRouterBinarySensor"
provides:
  - "TraefikApiClient exposes 6 GET endpoints + POST reload_routers + filter_internal_items helper"
  - "TraefikCoordinator.TraefikData TypedDict covering all 6 endpoints"
  - "TraefikEntity per-category multi-device model with category-derived DeviceInfo"
  - "binary_sensor migrated to filter_internal_items + per-category device identifier"
  - "const.py extended with CONF_TLS_WARN_DAYS + MIN/MAX clamps + DEFAULT_TLS_WARN_DAYS"
  - "PLATFORMS extended to ['binary_sensor', 'sensor', 'button']"
affects:
  - Phase 02-02 (Options Flow consumes CONF_TLS_WARN_DAYS + MIN/MAX scan-interval clamps)
  - Phase 02-03 (sensor + button platforms instantiate via the new TraefikEntity signature)
  - Phase 02-04 (reload service handler calls client.reload_routers() and verifies completion)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "TypedDict (PEP-589, total=False) for TraefikData — optional at runtime, structured for mypy"
    - "Per-category DeviceInfo identifier: (DOMAIN, f'{entry.entry_id}_{category}')"
    - "DeviceEntryType.SERVICE marker on every Traefik device"
    - "filter_internal_items(items, *, name_key='name') helper in api.py — shared across platforms"
    - "POST with explicit Content-Length: 0 for empty-body endpoints (aiohttp requirement)"
    - "Parallel six-endpoint fetch_all with auth-first exception propagation"

key-files:
  created:
    - custom_components/traefik/sensor.py (Phase 2-03 stub; async_setup_entry no-op)
    - custom_components/traefik/button.py (Phase 2-03 stub; async_setup_entry no-op)
  modified:
    - custom_components/traefik/const.py (CONF_TLS_WARN_DAYS + clamps + extended PLATFORMS)
    - custom_components/traefik/entity.py (TraefikEntity multi-device refactor)
    - custom_components/traefik/api.py (filter_internal_items + 4 new endpoints + reload_routers + new fetch_all shape)
    - custom_components/traefik/coordinator.py (TraefikData TypedDict)
    - custom_components/traefik/binary_sensor.py (filter_internal_items import + new entity signature)
    - tests/test_coordinator.py (six-endpoint mock helper)
    - tests/test_binary_sensor.py (filter_internal_items import + http_routers key + 2 new tests)

key-decisions:
  - "Phase 1's single-device identifier (DOMAIN, entry_id) replaced with per-category (DOMAIN, f'{entry_id}_{category}'). Existing HA device-registry rows will become orphans; a new device row appears per category on first restart after upgrade (device-registry IDs are opaque so this migration is unavoidable when the identifier shape changes)."
  - "filter_internal_items lifted from binary_sensor to api.py so all four Phase 2 platforms (routers + services + middlewares + entrypoints) share one regex pattern. The function accepts name_key for non-'name' dicts; defaults to 'name' for back-compat with Phase 1 callers."
  - "Phase 1's PEP-695 `type TraefikData = dict[str, Any]` alias is replaced by a TypedDict class. TraefikData as a name still resolves (the class itself), but the literal `type TraefikData = TraefikData` snippet from PLAN.md is a self-referential no-op that ruff + mypy both flag as a redefinition. Class form is canonical; documented as a deviation."
  - "fetch_all drops the entire payload on any non-auth error (not partial data) — keeps entities consistent with a single coordinator cycle rather than showing mixed fresh+stale data. Partial-failure policy lives in TraefikApiClient.fetch_all, not the coordinator."
  - "reload_routers POSTs /api/http/routers/refresh with explicit `Content-Length: 0` header. aiohttp requires this for empty-body POSTs; without it the request hangs waiting for the writer to flush. Traefik returns 202 before reload completes — verification lives in the reload service handler (plan 02-04), not here."
  - "PLATFORMS extended to include 'sensor' + 'button' but stub modules added now so platform loading doesn't crash in Phase 1 integration tests. Plan 02-03 fills in the real platform implementations."
  - "extra_state_attributes gains a raw 'name' field alongside router_name (CONTEXT.md D-20) so dashboards can drill into the Traefik router identifier even when the slug mangles special characters."

patterns-established:
  - "All new entities call super().__init__(entry, category='<cat>', description_key=<key>). The category drives the device identifier and model label; description_key is the per-entity suffix used in unique_id/entity_id slugs (or None for single-instance entities)."
  - "Module-level filter_internal_items is the canonical helper for `@<provider>` filtering across all platforms. Local _filter_user_routers / _PROVIDER_SUFFIX_RE in binary_sensor are removed."
  - "TraefikData TypedDict (total=False) is the canonical contract between coordinator and platforms. Downstream code reads via coordinator.data.get('<key>') or [] — no KeyError on transient gaps."

requirements-completed:
  - API-05
  - ROUTER-02
  - ROUTER-03

# Metrics
duration: ~17 min
completed: 2026-07-05
---

# Phase 2 Plan 1: Foundation Summary

**TraefikEntity multi-device refactor + TraefikApiClient extension (6 endpoints + reload_routers + filter_internal_items) + TraefikData TypedDict + binary_sensor migration; Phase 1 contract preserved (23 + 2 new tests, 25/25 green).**

## Performance

- **Duration:** ~17 min
- **Started:** 2026-07-05T23:08:34Z
- **Completed:** 2026-07-05T23:25:00Z
- **Tasks:** 3 / 3
- **Files modified:** 12 (4 created + 8 modified; 9 reformatted by ruff format)
- **Pytest runtime:** 0.61s wall-clock for the full suite (was 0.62s in Phase 1)

## Accomplishments

- `TraefikEntity` now takes `(entry, category, *, description_key=None)` and registers under a per-category HA device keyed by `(DOMAIN, f"{entry_id}_{category}")` (CONTEXT.md D-01/D-02). Five categories wired: `http_routers`, `http_services`, `http_entrypoints`, `overview`, `diagnostics`. Device name format is `{url_host} Traefik — {Model}`. `_router_name` retained as alias of `description_key` for Phase 1 binary_sensor back-compat.
- `TraefikApiClient` exposes six read endpoints (`get_version`, `get_entrypoints`, `get_routers`, `get_http_services`, `get_http_middlewares`, `get_overview`) plus the `reload_routers` POST. New module-level `filter_internal_items(items, *, name_key="name")` helper drops Traefik-internal `@<provider>` items for all four Phase 2 platforms.
- `TraefikCoordinator.TraefikData` is now a `TypedDict` (PEP-589, `total=False`) with `version`, `entrypoints`, `http_routers`, `http_services`, `http_middlewares`, `overview` keys (CONTEXT.md D-04). `fetch_all()` fans out six endpoints in one `asyncio.gather(return_exceptions=True)`, surfaces auth errors immediately (never swallowed per PITFALLS), and propagates the first non-auth exception so callers see a stale cycle rather than mixed fresh+stale data (CONTEXT.md D-07).
- `binary_sensor.py` migrated to the new `TraefikEntity` signature (`category="http_routers"`, `description_key=router_name`), uses `filter_internal_items` from `api.py`, reads `coordinator.data.get("http_routers")` (the renamed key), and exposes `extra_state_attributes["name"]` alongside `router_name` for raw Traefik ID surfacing on dashboards (ROUTER-02 / CONTEXT.md D-20).
- `const.py` extended: `CONF_TLS_WARN_DAYS = "tls_warn_days"`, scan-interval clamps (`MIN=15`, `MAX=300` seconds), TLS-warn clamps (`MIN=1`, `MAX=90` days), `DEFAULT_TLS_WARN_DAYS = 14`. `PLATFORMS = ["binary_sensor", "sensor", "button"]` (Phase 2-03 fills in the latter two).
- 25/25 tests pass (Phase 1's 23 + 2 new binary_sensor tests): `test_extra_state_attributes_exposes_raw_name_for_dashboards` (ROUTER-02 + D-20 contract pin) and `test_device_info_uses_per_category_identifier` (per-category DeviceInfo contract pin). `test_coordinator.py` updated with a `_stub_all_endpoints` helper that mocks all six endpoints `fetch_all()` now requests.

## Task Commits

Each task was committed atomically:

1. **Task 1: Refactor TraefikEntity to per-category multi-device model + expand const.py** — `43f0a2f` (feat) — entity.py + const.py + new stub sensor.py + button.py
2. **Task 2: Extend TraefikApiClient with new endpoints + filter helper + reload_routers** — `e525748` (feat) — api.py
3. **Task 3: Update coordinator TraefikData TypedDict + migrate binary_sensor to new TraefikEntity signature** — `5494739` (feat) — coordinator.py + binary_sensor.py + test updates

**Plan metadata:** `abfb5de` (style: ruff format sweep across touched files)

_Note: A final style commit ran ruff format across the 9 files touched by plan 02-01 (CI mirror gate)._

## Files Created/Modified

- `custom_components/traefik/const.py` — CONF_TLS_WARN_DAYS + MIN/MAX clamps + DEFAULT_TLS_WARN_DAYS + PLATFORMS extension
- `custom_components/traefik/entity.py` — TraefikEntity multi-device refactor (category parameter, _category_to_model mapping, DeviceEntryType.SERVICE)
- `custom_components/traefik/api.py` — `filter_internal_items` helper + 4 new GET endpoints + `reload_routers` POST + `fetch_all` reshaped for 6 endpoints
- `custom_components/traefik/coordinator.py` — `TraefikData` TypedDict + partial-failure policy documented in `_async_update_data`
- `custom_components/traefik/binary_sensor.py` — local `_filter_user_routers` removed; `from .api import filter_internal_items`; `super().__init__(entry, category="http_routers", description_key=router_name)`; `extra_state_attributes["name"]` added
- `custom_components/traefik/sensor.py` — **NEW** — Phase 2-03 stub (async_setup_entry no-op)
- `custom_components/traefik/button.py` — **NEW** — Phase 2-03 stub (async_setup_entry no-op)
- `tests/test_binary_sensor.py` — `_filter_user_routers` import replaced with `filter_internal_items` from api.py; coordinator.data now uses `http_routers`; +2 new tests (raw name attribute + per-category device_info)
- `tests/test_coordinator.py` — `_stub_all_endpoints` helper mocks all six endpoints `fetch_all()` now requests

## Decisions Made

- **TraefikEntity multi-device identifier change.** Phase 1 used `(DOMAIN, entry.entry_id)` as the device identifier for one "Traefik" device. Phase 2 uses `(DOMAIN, f"{entry_id}_{category}")` so each category is its own device. Existing HA installations will see a new device row per category on first restart (the device-registry uses the identifier as a stable handle, so changing the identifier shape creates a new row). Documented in code comments and below as a regression-risk deviation.
- **TypedDict class over PEP-695 alias for TraefikData.** PLAN.md 02-01 suggests `type TraefikData = TraefikData` as a back-compat alias. This is a self-referential no-op (the left side equals the right side) that ruff's `F811` and mypy's `no-redef` both flag. We keep the class form (TypedDict) as the canonical type identifier; `from .coordinator import TraefikData` resolves to the TypedDict class for every downstream consumer.
- **filter_internal_items lifted to api.py.** Phase 1 had `_filter_user_routers` + `_PROVIDER_SUFFIX_RE` private to binary_sensor.py. Phase 2 lifts the regex and helper to api.py so all four Phase 2 platforms (routers + services + middlewares + entrypoints) reuse one filter. The `name_key` keyword default (`"name"`) preserves Phase 1 caller ergonomics.
- **fetch_all drops entire payload on non-auth error.** CONTEXT.md D-07 gives the agent's discretion whether to filter null sections or return empty lists. We chose the strict path (drop the entire payload) so callers see a stale cycle rather than mixed fresh+stale data; this keeps entity state consistent with a single coordinator cycle.
- **reload_routers does NOT poll.** CONTEXT.md D-05 mandates the POST returns `None` on 2xx and raises on non-2xx. Verification (polling until router set changes) lives in the reload service handler (plan 02-04). Keeping the API client free of polling simplifies testing — the 9 Phase 1 test_api.py tests cover the contract without time-based mock complexity.
- **sensor.py + button.py stubs added now.** PLAN.md 02-01 extends PLATFORMS to include sensor + button before those platforms exist (plan 02-03 fills them in). Without stub modules, `hass.config_entries.async_forward_entry_setups` crashes the Phase 1 integration tests. Stubs are 30-line `async_setup_entry` no-ops; plan 02-03 replaces them with real implementations.
- **`_router_name` alias on TraefikEntity.** Phase 1 binary_sensor reads `self._router_name` for the router label. Phase 2 introduces the more general `description_key` keyword but keeps `self._router_name` as a thin alias so existing code paths work unchanged. Future platforms can ignore the attribute entirely.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Extended PLATFORMS crashed Phase 1 integration tests because sensor.py + button.py don't exist yet**
- **Found during:** Task 1 verification (`pytest tests/`)
- **Issue:** Task 1 extended `PLATFORMS = ["binary_sensor", "sensor", "button"]` per PLAN.md but the `sensor.py` + `button.py` modules arrive in plan 02-03. `hass.config_entries.async_forward_entry_setups` tries to `importlib.import_module("custom_components.traefik.sensor")` and fails with `ModuleNotFoundError`, leaving the config entry in `SETUP_RETRY` rather than `LOADED`.
- **Fix:** Created minimal stub modules with `async_setup_entry` no-ops. Plan 02-03 fills in the real implementations.
- **Files modified:** `custom_components/traefik/sensor.py` (new), `custom_components/traefik/button.py` (new)
- **Verification:** All 3 test_coordinator tests that exercise `async_setup` now land the entry in `LOADED` with the full fetch_all mock set.
- **Committed in:** `43f0a2f` (Task 1 commit)

**2. [Rule 3 - Blocking] `type TraefikData = TraefikData` self-referential alias is a ruff F811 + mypy no-redef violation**
- **Found during:** Task 3 ruff + mypy verification
- **Issue:** PLAN.md 02-01's literal snippet `type TraefikData = TraefikData` is a no-op self-referential alias. ruff's `F811` flags it as a redefinition; mypy's `no-redef` flags it as a name conflict, plus a downstream `Incompatible return value type` on `_async_update_data` because mypy cannot resolve the type through the alias.
- **Fix:** Dropped the alias line; the TypedDict class itself (`TraefikData`) is the canonical type identifier. `from .coordinator import TraefikData` resolves to the class. Documented in the class docstring + this deviation entry.
- **Files modified:** `custom_components/traefik/coordinator.py`
- **Verification:** `uv run ruff check custom_components/traefik/coordinator.py` exits 0; `uv run mypy --strict custom_components/traefik/coordinator.py` reports "Success: no issues found".
- **Committed in:** `5494739` (Task 3 commit)

**3. [Rule 3 - Blocking] Test fixtures in test_coordinator.py only mocked 2 of the 6 endpoints fetch_all now calls**
- **Found during:** Task 3 verification (`pytest tests/test_coordinator.py`)
- **Issue:** Phase 1 `test_coordinator.py` only mocked `/api/version` + `/api/http/routers`. Phase 2 `fetch_all()` fans out six endpoints; unmocked endpoints crashed the coordinator cycle with `ClientConnectorError`.
- **Fix:** Extracted a `_stub_all_endpoints(aioclient_mock, ...)` helper that mocks all six. Existing 6 tests now use the helper; happy-path defaults are sensible empty lists / empty overview.
- **Files modified:** `tests/test_coordinator.py`
- **Verification:** 6/6 test_coordinator tests pass; auth/error tests still hit `/api/version` with the failure mode (no stubs needed for those).
- **Committed in:** `5494739` (Task 3 commit)

**4. [Rule 1 - Bug] test_binary_sensor.py still imported `_filter_user_routers` from binary_sensor (no longer exported)**
- **Found during:** Task 3 test collection
- **Issue:** Phase 1's `binary_sensor._filter_user_routers` was deleted in Task 3. test_binary_sensor.py still imported it; test collection failed.
- **Fix:** Import `filter_internal_items` from `custom_components.traefik.api` (canonical home per CONTEXT.md D-06). Rename the two affected test functions (`test_filter_user_routers_drops_internal` → `test_filter_internal_items_drops_internal`; same for `test_filter_preserves_special_chars_in_user_names`).
- **Files modified:** `tests/test_binary_sensor.py`
- **Verification:** 10/10 test_binary_sensor tests pass.
- **Committed in:** `5494739` (Task 3 commit)

**5. [Rule 3 - Blocking] ruff format sweep needed across 9 touched files for the CI format gate**
- **Found during:** Final verification (`uv run ruff format --check`)
- **Issue:** CI mirror runs `ruff format --check` and fails on unformatted files. The 9 files touched by plan 02-01 had line-break mismatches against ruff 0.15's preferred wrap style.
- **Fix:** Ran `ruff format` across `custom_components/` and `tests/`. Pure whitespace + line-break cleanup; no semantic change.
- **Files modified:** 9 files (api.py, binary_sensor.py, button.py, const.py, coordinator.py, entity.py, sensor.py, test_binary_sensor.py, test_coordinator.py)
- **Verification:** `ruff format --check custom_components/ tests/` reports "14 files already formatted".
- **Committed in:** `abfb5de` (style commit)

---

**Total deviations:** 5 auto-fixed (1 bug + 4 blocking)
**Impact on plan:** All deviations resolved at execution time. The only structural deviation is dropping the literal `type TraefikData = TraefikData` alias (PLAN.md example is a self-referential no-op that lints reject); the TypedDict class form preserves the import contract. The new sensor.py + button.py stubs are explicitly forward-references for plan 02-03, not new behavior.

## Issues Encountered

- **PLATFORMS extension sequencing.** PLAN.md 02-01 extends PLATFORMS in Task 1; the actual `sensor.py` + `button.py` modules land in Task 02-03. The Task 1 commit had to ship stub modules so Phase 1 integration tests don't crash on `importlib.import_module`. Same pattern as Phase 1's `services.yaml` placeholder (SUMMARY 01-03 deviation).
- **TraefikData PEP-695 alias snippet.** PLAN.md 02-01's example `type TraefikData = TraefikData` is a no-op self-reference. Both ruff and mypy reject it; the class form (`class TraefikData(TypedDict, total=False)`) is the canonical Phase 2 type. PLAN.md needs an update for plan 02-03 onwards (drop the self-referential alias from the example).

## Known Stubs

- `custom_components/traefik/sensor.py` — forward-reference stub. Plan 02-03 fills in `TraefikEntrypointSensor`, `TraefikServiceSensor`, `TraefikRoutersCountSensor`, `TraefikServicesCountSensor`, `TraefikMiddlewaresCountSensor` (CONTEXT.md D-15/D-16/D-17).
- `custom_components/traefik/button.py` — forward-reference stub. Plan 02-03 fills in `TraefikReloadButton` (CONTEXT.md D-13).

## User Setup Required

None — all changes are internal to the integration; no external service configuration or credentials required.

## Next Phase Readiness

Phase 2-02 (Options Flow + Reauth + Reconfigure) is unblocked:

- `CONF_TLS_WARN_DAYS`, `MIN_SCAN_INTERVAL`, `MAX_SCAN_INTERVAL`, `MIN_TLS_WARN_DAYS`, `MAX_TLS_WARN_DAYS`, `DEFAULT_TLS_WARN_DAYS` are all defined in `const.py` and ready for `voluptuous` clamp wiring in Options Flow.
- `async_get_clientsession(hass)` + bearer-per-request patterns are stable from Phase 1; the reauth flow's `_validate_input` probe can call `client.get_overview()` without changes.
- The `traefik.reload_routers` service handler (plan 02-04) can call `client.reload_routers()` directly; the `Content-Length: 0` header is already handled.

Phase 2-03 (sensor + button + any-router-failing) is unblocked:

- All entity classes use `super().__init__(entry, category="<cat>", description_key=<key>)`. New categories: `http_entrypoints`, `http_services`, `overview`, `diagnostics`.
- `filter_internal_items(items, name_key="name")` is importable from `api.py` for services + middlewares filtering.
- `coordinator.data["http_routers"]`, `["http_services"]`, `["http_middlewares"]`, `["entrypoints"]` keys all flow through the TypedDict shape.

Outstanding Phase 2 work tracked (NOT plan 02-01 blockers):

- Plan 02-02: Options Flow (scan_interval + verify_ssl + tls_warn_days); `async_step_reauth`; `async_step_reconfigure`; `entry.add_update_listener`; translation bundle updates.
- Plan 02-03: `sensor.py` (5 entities), `button.py` (TraefikReloadButton), `binary_sensor.py` (TraefikAnyRouterFailingBinarySensor on Diagnostics device).
- Plan 02-04: `async_setup` (module-level) registering `traefik.reload_routers` service; polling verification loop; `coordinator.async_add_listener` stale entity cleanup; integration tests.
- Replace placeholder brand icons with the official Traefik Apache-2.0 logo (Phase 1 deviation, deferred to Phase 4).

---

*Phase: 02-core-entities-options-reauth-reload*
*Completed: 2026-07-05*

## Self-Check: PASSED

All committed files exist; all verification steps in the plan passed at execution time. Commit hashes referenced above (`43f0a2f`, `e525748`, `5494739`, `abfb5de`) are present in git history. 25/25 tests pass; ruff check + ruff format --check + mypy --strict all green.