---
phase: 02-core-entities-options-reauth-reload
plan: 03
subsystem: entities
tags: [sensor, button, binary-sensor, per-category-device, diagnostics, count-aggregate, reload-button, ruff, mypy, pytest]

# Dependency graph
requires:
  - phase: 02-core-entities-options-reauth-reload
    plan: 01
    provides: "TraefikEntity(entry, category, *, description_key=None) per-category device model; filter_internal_items helper; TraefikData TypedDict with http_routers/http_services/http_middlewares/entrypoints/overview keys; sensor.py + button.py stub modules"
provides:
  - "sensor.py with 5 entity classes — TraefikEntrypointSensor (HTTP Entrypoints), TraefikServiceSensor (HTTP Services), TraefikRoutersCountSensor + TraefikServicesCountSensor + TraefikMiddlewaresCountSensor (Overview)"
  - "button.py with TraefikReloadButton (ButtonDeviceClass.RESTART) on Diagnostics device"
  - "binary_sensor.py extended with TraefikAnyRouterFailingBinarySensor (BinarySensorDeviceClass.PROBLEM, entity_registry_enabled_default=False) on Diagnostics device"
  - "All 5 Phase 2 categories now exercised — http_routers, http_services, http_entrypoints, overview, diagnostics (CONTEXT.md D-01)"
  - "DIAG-01 + DIAG-02 + ENTRY-01 + ENTRY-02 + ENTRY-03 satisfied at the entity layer (registration contract met; runtime verification lives in plan 02-04)"
affects:
  - Phase 02-04 (services.yaml + reload handler + integration tests covering the new entities + stale-entity cleanup registered via coordinator.async_add_listener)
  - Phase 3 (TLS sensor entities will instantiate via the same TraefikEntity(category=...) pattern + can reuse the _dict_or_empty/_list_or_empty TypedDict-safe helpers from sensor.py)
  - Phase 4 (diagnostics.py will round-trip the per-category device model + extra_state_attributes)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Per-category device model: every entity calls super().__init__(entry, category='<cat>', description_key=<key>) and the device identifier is derived in TraefikEntity.device_info"
    - "TypedDict(total=False) safe access via _dict_or_empty / _list_or_empty helpers — keeps mypy --strict clean when coordinator.data is a partial payload between cycles"
    - "Single-instance aggregate sensors share a `_TraefikAggregateCountSensor` base class so the count + breakdown attribute shape stays uniform across routers / services / middlewares"
    - "ButtonEntity routes through hass.services.async_call(DOMAIN, 'reload_routers', blocking=True) so the verification loop in __init__.py is the single source of truth (no client.reload_routers() call site drift)"

key-files:
  created: []
  modified:
    - custom_components/traefik/sensor.py (replaced 26-line stub with 362-line implementation: 5 entity classes + 2 TypedDict-safe helpers + aggregate base class)
    - custom_components/traefik/button.py (replaced 25-line stub with 62-line implementation: TraefikReloadButton)
    - custom_components/traefik/binary_sensor.py (extended async_setup_entry + added TraefikAnyRouterFailingBinarySensor)

key-decisions:
  - "Defensive TypedDict(total=False) reads via _dict_or_empty / _list_or_empty helpers in sensor.py. Chaining .get() on a TypedDict key directly triggers mypy strict-mode union-attr errors because the key is optional; the helpers keep the call sites clean without sprinkling type: ignore[union-attr]."
  - "TraefikAnyRouterFailingBinarySensor reads the *raw* http_routers list (not filter_internal_items-filtered). Per-router entities can't show api@internal because HA's entity-id regex rejects '@', but the aggregate is a normal HA entity so a failing Traefik-internal router should still surface the alarm."
  - "TraefikReloadButton.async_press uses hass.services.async_call(DOMAIN, 'reload_routers', blocking=True) — NOT client.reload_routers() directly. Going through the service keeps the verification loop in __init__.py as the single source of truth and surfaces the {verified, elapsed_ms, attempts, name_diff} response in HA's trace log for both call sites."
  - "Aggregate count sensors carry state_class=MEASUREMENT so HA can graph trends over time — useful for tracking how many routers / services / middlewares are configured across deploys."
  - "Aggregate sensors split http_count / tcp_count / udp_count across /api/overview even though PROJECT.md is HTTP-only. Surfacing TCP/UDP counts in attributes (not state) gives operators a heads-up when they add TCP services without us committing to TCP entities (CONTEXT.md deferred section)."

patterns-established:
  - "_dict_or_empty / _list_or_empty are the canonical TypedDict(total=False) read helpers for Phase 2+ platforms. New platforms should import from .sensor (or move them to a shared module in Phase 4) rather than chaining .get() directly."
  - "entity_id pattern family: traefik_<entity-class-suffix>_<slug> for per-instance entities (entrypoint / service / router), traefik_<bare-name> for aggregates (routers / services / middlewares). Mirrors Phase 1 D-09/D-10."
  - "Diagnostic binary sensors use _attr_entity_registry_enabled_default = False per PITFALLS M-12 — users opt in consciously rather than having noisy alarm entities in the States panel by default."

requirements-completed:
  - ENTRY-01
  - ENTRY-02
  - ENTRY-03
  - DIAG-01
  - DIAG-02
  - UX-04

# Metrics
duration: ~4 min
completed: 2026-07-05
---
# Phase 2 Plan 3: Core Sensor + Button + Aggregate Entities Summary

**5 sensor entities (per-entrypoint, per-service, 3 aggregates on Overview) + TraefikReloadButton + TraefikAnyRouterFailingBinarySensor — 7 new entity classes on the per-category device model, 25/25 tests still green.**

## Performance

- **Duration:** ~4 min
- **Started:** 2026-07-05T23:19:07Z
- **Completed:** 2026-07-05T23:22:57Z
- **Tasks:** 3 / 3
- **Files modified:** 3 (sensor.py +362/-8, button.py +62/-9, binary_sensor.py +79/-3)
- **Pytest runtime:** 0.71s wall-clock for the full suite (unchanged from Phase 1)

## Accomplishments

- **`sensor.py`** filled out: 5 entity classes per CONTEXT.md D-15/D-16/D-17. `TraefikEntrypointSensor` reports the listening address per Traefik entrypoint; `TraefikServiceSensor` reports `loadbalancer.status` (falling back to `service.status`) with backend server count + servers list in attributes; three `_TraefikAggregateCountSensor` subclasses on the Overview device report filtered counts with HTTP/TCP/UDP breakdowns from `/api/overview`. All 5 categories from the Phase 2 device model are now exercised (`http_routers` from Phase 1, `http_services`/`http_entrypoints`/`overview`/`diagnostics` from this plan).
- **`button.py`** filled out: `TraefikReloadButton` on the Diagnostics device with `ButtonEntityDeviceClass.RESTART` (DIAG-02). Press action routes through `hass.services.async_call(DOMAIN, "reload_routers", blocking=True)` so the verification loop registered in `__init__.py` (plan 02-04) is the single source of truth — no client-side drift between button-press and direct service-call verification paths.
- **`binary_sensor.py`** extended: `TraefikAnyRouterFailingBinarySensor` aggregate on the Diagnostics device (DIAG-01). `BinarySensorDeviceClass.PROBLEM` + `entity_registry_enabled_default=False` (PITFALLS M-12) so it doesn't pollute the States panel by default. Reads the raw `http_routers` list so a failing `api@internal` can still surface the alarm even though per-router entities can't show that name (HA entity-id regex rejects `@`). Attributes expose `{failing_router_count, failing_router_names}` for dashboards / automations (UX-04).
- **UX-04 (raw Traefik data via extra_state_attributes)** satisfied across all 5 sensors + 2 diagnostics entities: every entity exposes the raw Traefik JSON fields (`name`, `rule`, `service`, `transport`, `servers`, `status`, etc.) so dashboards can drill in without re-fetching from Traefik.

## Task Commits

Each task was committed atomically:

1. **Task 1: Create sensor.py with per-entrypoint, per-service, and three aggregate sensors** — `a52fa7f` (feat) — sensor.py
2. **Task 2: Create button.py with TraefikReloadButton on Diagnostics device** — `4b58105` (feat) — button.py
3. **Task 3: Extend binary_sensor.py with TraefikAnyRouterFailingBinarySensor on Diagnostics device** — `d8d9a7e` (feat) — binary_sensor.py

## Files Created/Modified

- `custom_components/traefik/sensor.py` — **REPLACED stub** with 362-line implementation: 5 entity classes (`TraefikEntrypointSensor`, `TraefikServiceSensor`, `_TraefikAggregateCountSensor` base, `TraefikRoutersCountSensor`, `TraefikServicesCountSensor`, `TraefikMiddlewaresCountSensor`) + 2 TypedDict-safe helpers (`_dict_or_empty`, `_list_or_empty`) + 1 coercion helper (`_safe_int`)
- `custom_components/traefik/button.py` — **REPLACED stub** with 62-line implementation: `TraefikReloadButton` (ButtonDeviceClass.RESTART on Diagnostics device)
- `custom_components/traefik/binary_sensor.py` — **EXTENDED** (preserved all existing Phase 1 + 02-01 code): `async_setup_entry` now also instantiates `TraefikAnyRouterFailingBinarySensor`; new class added at the bottom with category="diagnostics", description_key="any_router_failing", device_class=PROBLEM, entity_registry_enabled_default=False

## Decisions Made

- **TypedDict(total=False) safe reads via helpers.** `TraefikData` is `total=False` (every key optional). Chaining `.get()` on the raw value triggers mypy strict-mode `union-attr` errors because the result type is `... | None`. Two helpers (`_dict_or_empty`, `_list_or_empty`) coerce to `dict[str, Any]` / `list[dict[str, Any]]` (`{}` / `[]` on absence or wrong type), keeping the call sites clean without sprinkling `type: ignore[union-attr]`. This is a deliberate pattern for Phase 2+ platforms reading partial payloads between coordinator cycles.
- **Aggregate reads raw `http_routers` (not filtered).** `TraefikAnyRouterFailingBinarySensor.is_on` checks the raw list because per-router entities can't surface `api@internal` (HA entity-id regex rejects `@`), but the aggregate is a normal HA entity — the attribute dict can hold any name. Trade-off: operators see "1 failing" when an internal Traefik router goes down, even though no per-router entity flips off. Net win: clearer alarm signal.
- **Button routes through service, not client.** `TraefikReloadButton.async_press` calls `hass.services.async_call(DOMAIN, "reload_routers", blocking=True)` rather than `client.reload_routers()` directly. Reason: plan 02-04 wires the polling verification loop (`{verified, elapsed_ms, attempts, name_diff}` response) into the service handler in `__init__.py`'s module-level `async_setup`. Going through the service means the button and the direct service call share one verification path; the trace log captures the response dict for both call sites.
- **`state_class=MEASUREMENT` on aggregate counters.** HA can graph trends over time — useful for tracking how many routers / services / middlewares are configured across deploys (e.g., a chart that alerts when a deployment removes more than 3 routers at once).
- **Aggregate sensors split HTTP/TCP/UDP counts.** Even though PROJECT.md is HTTP-only and TCP/UDP entities are deferred, surfacing `tcp_count` / `udp_count` in attributes gives operators a heads-up when they add TCP services — useful debugging signal, zero cost.
- **`_safe_int` rejects bool.** `bool` is a subclass of `int` in Python; the helper explicitly returns 0 for booleans to avoid `True → 1` accidentally inflating overview counts when Traefik's API ever returns a boolean counter.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `_safe_int` accepted bool as int (would inflate overview counts)**
- **Found during:** Task 1 final verification
- **Issue:** First `_safe_int` implementation used `isinstance(value, (int, float))`. Python's `bool` is a subclass of `int`, so `True` would silently coerce to `1` if Traefik ever returned a boolean counter in `/api/overview`. Not currently a Traefik API shape, but defensive coding costs nothing.
- **Fix:** Added an explicit `isinstance(value, bool)` early-return returning `0` before the int/float branch.
- **Files modified:** `custom_components/traefik/sensor.py`
- **Verification:** mypy --strict clean; ruff check clean
- **Committed in:** `a52fa7f` (Task 1 commit)

**2. [Rule 3 - Blocking] TypedDict(total=False) strict-mode `union-attr` errors on `.get()` chains**
- **Found during:** Task 1 mypy --strict verification
- **Issue:** PLAN.md 02-03's literal snippet does `coordinator.data.get("overview")` then `overview.get("http")`. Under `mypy --strict`, `TraefikData` keys are all `... | None`, so chained `.get()` triggers 9 `union-attr` errors (the optional-key access pattern). The plan's key_link verification pattern `coordinator\.data\.get\(["'](entrypoints|http_services|http_routers|http_middlewares|overview)["']\)` is the literal grep target, but direct chained access fails strict mode.
- **Fix:** Extracted `_dict_or_empty` and `_list_or_empty` helpers in `sensor.py` that coerce `Any` to `dict[str, Any]` / `list[dict[str, Any]]` via `isinstance` checks. Sensor call sites do `data = _dict_or_empty(coordinator.data); entrypoints = _list_or_empty(data.get("entrypoints"))` — semantically identical (still reads `coordinator.data['entrypoints']`) but strict-mode clean. The pattern key_link grep `coordinator\.data\.get\([\"']...[\"']\)` won't match the literal pattern; documented here as a structural deviation. The DATA FLOW is unchanged — every key the plan calls out (`entrypoints`, `http_services`, `http_routers`, `http_middlewares`, `overview`) is still read from `coordinator.data`.
- **Files modified:** `custom_components/traefik/sensor.py`
- **Verification:** `uv run mypy --strict custom_components/traefik/sensor.py` exits 0 with "Success: no issues found in 1 source file". All 25 existing tests still pass.
- **Committed in:** `a52fa7f` (Task 1 commit)

**3. [Rule 3 - Blocking] `RUF005` lint warning on `router_entities + [any_failing_entity]` list concat**
- **Found during:** Task 3 ruff check
- **Issue:** `async_add_entities(router_entities + [any_failing_entity])` triggers `RUF005` ("Consider `[*router_entities, any_failing_entity]` instead of concatenation") — Phase 1 + 02-01 hadn't hit this because the original `binary_sensor.py` did `async_add_entities(entities)` where `entities` was already a list. Adding one more entity via concat was a new pattern.
- **Fix:** Changed to the recommended `[*router_entities, any_failing_entity]` spread form.
- **Files modified:** `custom_components/traefik/binary_sensor.py`
- **Verification:** `uv run ruff check` clean; all 10 test_binary_sensor tests pass.
- **Committed in:** `d8d9a7e` (Task 3 commit)

---

**Total deviations:** 3 auto-fixed (1 bug + 2 blocking)

**Impact on plan:** All deviations are local lint/type corrections. The structural change (deviation #2) keeps the data-flow key_link wiring identical — `coordinator.data` is still the source of truth for every read; only the access pattern differs. No new entities, no changed entity semantics, no scope creep.

## Issues Encountered

- **Mypy strict + TypedDict(total=False) is a known friction point** in HA integrations — every key access requires either `type: ignore` or a defensive coercion. The `_dict_or_empty` / `_list_or_empty` helper pattern is the cleanest fix and is reusable across all Phase 2+ platforms (Phase 3 TLS sensors will benefit). Consider moving these to `api.py` (next to `filter_internal_items`) in Phase 4 cleanup so all platforms share one source of truth.
- **`RUF002` flagged the multiplication sign `×` in docstrings** — I initially used `1×` per entity in the module docstring but ruff 0.15's `RUF002` warns on visually-ambiguous Unicode characters in docstrings. Replaced with plain `1 per` form to avoid the lint hit.

## Known Stubs

None — all entities, helpers, and platform modules are fully implemented.

## User Setup Required

None — all changes are internal to the integration; no external service configuration or credentials required.

## Next Phase Readiness

Phase 02-04 (Reload Service + Stale Cleanup + Integration Tests) is unblocked:

- `TraefikReloadButton.async_press` already routes through `hass.services.async_call(DOMAIN, "reload_routers", blocking=True)` — the service handler can be registered in `__init__.py`'s module-level `async_setup` (CONTEXT.md D-12) and both call sites get the verification response.
- `TraefikAnyRouterFailingBinarySensor` is a single-instance entity per config entry (CONTEXT.md D-19) — never deleted by the cleanup loop.
- All 7 new entity classes share the per-category device identifier pattern (`(DOMAIN, f"{entry_id}_{category}")`) — the cleanup hook in `coordinator.async_add_listener` can match on the `category` portion of the unique_id to know which entities are eligible for stale removal (routers/services/entrypoints only — never the aggregate sensors or the diagnostics binary_sensor).
- `_dict_or_empty` / `_list_or_empty` TypedDict helpers are reusable for any Phase 2+ platform reading `coordinator.data`.

Phase 3 (TLS Certificate Expiry) is unblocked:

- New TLS sensor / binary_sensor classes can follow the exact `super().__init__(entry, category="<cat>", description_key=<key>)` pattern from this plan.
- Per-router TLS sensors will land on the same `http_routers` device category (one entity per router that terminates TLS), or a separate `http_routers_tls` category if the user prefers to group TLS sensors separately (CONTEXT.md specifics deferred).

Outstanding Phase 2 work tracked (NOT plan 02-03 blockers):

- Plan 02-04: `services.yaml` schema for `reload_routers`; `__init__.py`'s module-level `async_setup` registering the service handler with the polling verification loop (`{verified, elapsed_ms, attempts, name_diff}`); `coordinator.async_add_listener` stale-entity cleanup hook for routers/services/entrypoints.
- Integration tests for the 7 new entities using `pytest-homeassistant-custom-component` fixtures (TEST-02 partial).

---

*Phase: 02-core-entities-options-reauth-reload*
*Completed: 2026-07-05*

## Self-Check: PASSED

All 3 task commits present (`a52fa7f`, `4b58105`, `d8d9a7e`); all 5 required entity classes (`TraefikEntrypointSensor`, `TraefikServiceSensor`, `TraefikRoutersCountSensor`, `TraefikServicesCountSensor`, `TraefikMiddlewaresCountSensor`) defined in `sensor.py`; `TraefikReloadButton` defined in `button.py`; `TraefikAnyRouterFailingBinarySensor` defined in `binary_sensor.py`. All 25 existing tests pass; ruff check + ruff format + mypy --strict all clean.