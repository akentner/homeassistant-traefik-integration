---
phase: 03-tls-certificate-expiry
plan: 02
subsystem: tls-entities
tags: [tls, certificate-expiry, sensor, binary_sensor, datetime, coordinator-listener, entity-cleanup]

# Dependency graph
requires:
  - phase: 03-tls-certificate-expiry
    plan: 01
    provides: "CertCoordinator (6h sibling coordinator) wired on entry.runtime_data.cert_coordinator with threshold_days + get_threshold() + async_set_threshold + async_add_listener; CertInfo/CertError TypedDict cache shape; CertCoordinator._collect_hosts_from_main_coordinator hostname union (tls.domains[].main + sans[] + Host(...) matches)"
  - phase: 02-multi-device
    plan: 01
    provides: "Per-category device model with _CATEGORY_TO_MODEL['http_routers_tls'] = 'HTTP Routers TLS'; TraefikEntity base class"
  - phase: 02-core-entities-options-reauth-reload
    plan: 03
    provides: "sensor.py + binary_sensor.py Phase 2 entity patterns + stale-cleanup listener pattern (D-18)"
provides:
  - "TraefikCertTimestampSensor — SensorDeviceClass.TIMESTAMP entity surfacing CertInfo.not_after UTC datetime + days_until_expiry/subject/issuer/san/san_mismatch always-on attributes (TLS-01)"
  - "TraefikCertExpiryBinarySensor — BinarySensorDeviceClass.PROBLEM entity with is_on=days_until_expiry<=threshold_days, _attr_entity_registry_enabled_default=True (D-03 explicit divergence from Phase 2 M-12), shared _cert_cache_availability helper (TLS-02)"
  - "sensor.py + binary_sensor.py async_setup_entry extended with cert-cycle entity-creation closure (_create_pending_*_entities) + stale-cleanup callback (_remove_stale_cert_hosts / _remove_stale_cert_expiring) wired via single combined cert_coordinator.async_add_listener per platform"
  - "Module-level _cert_cache_availability(coordinator, host) helper in sensor.py — single source of truth for cache availability across both platforms (SUGGESTION #1 fix)"
  - "WARNING #1 split: _remove_stale_cert_hosts registered ONLY in sensor.py; _remove_stale_cert_expiring registered ONLY in binary_sensor.py — no duplicate registration"
  - "BLOCKER #2 fix: entity-creation closure fires once on initial setup AND on every cert cycle (6h) so newly-discovered hosts get their entities after the cold-start empty-cache fallback in plan 03-01 Task 3 step 3d(iii)"
affects:
  - "phase 03 plan 03 — test surface (test_sensor_tls.py + test_binary_sensor_tls_expiring.py) exercises both entity classes + cleanup listener + cert-cache-availability helper"
  - "phase 04 — diagnostics integration may want to surface the new threshold + days_until_expiry attributes"

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Single combined cert-cycle listener per platform: one cert_coordinator.async_add_listener registration covers both entity creation (BLOCKER #2) AND stale-cleanup (WARNING #1) — folds two concerns into one callback to keep the listener registration count minimal"
    - "Cross-platform helper import: sensor.py defines _cert_cache_availability(coordinator, host) and binary_sensor.py imports it directly — single source of truth, no per-platform drift (SUGGESTION #1 fix)"
    - "Defensive getattr cert_coordinator access in BOTH platform async_setup_entry — None-tolerant for partial installs / test harnesses without Phase 3 wiring"
    - "D-03 explicit _attr_entity_registry_enabled_default = True on cert-expiring binary_sensor (diverges from Phase 2 M-12 on TraefikAnyRouterFailingBinarySensor) — cert expiry is a security-impacting alarm that warrants always-on visibility"
    - "Signed-int threshold semantics: days_until_expiry <= threshold_days returns True for negative days (already-expired certs surface as breach)"
    - "Host normalisation at __init__ top: host = host.lower() before unique_id assignment — defensive against cache rows populated with mixed casing"

key-files:
  created: []
  modified:
    - "custom_components/traefik/sensor.py — added UTC/datetime + SensorDeviceClass imports; CertInfo/CertError/is_error imports; CertCoordinator TYPE_CHECKING import; cert-sensor block in async_setup_entry (cert_coordinator=None-tolerant guard, _create_pending_cert_sensor_entities closure, _remove_stale_cert_hosts cleanup, _cert_update_listener combined listener); TraefikCertTimestampSensor class (TIMESTAMP device class, mdi:certificate icon, native_value=CertInfo.not_after, days_until_expiry/subject/issuer/san/san_mismatch/host/port/fetched_at/last_error attributes, available delegates to shared helper); _cert_cache_availability module-level helper + cert_cache_availability public alias"
    - "custom_components/traefik/binary_sensor.py — added cast + CertCoordinator TYPE_CHECKING import + _cert_cache_availability + CertInfo/CertError/is_error imports; cert-expiring block in async_setup_entry (cert_coordinator=None-tolerant guard, _create_pending_cert_binary_sensor_entities closure, _remove_stale_cert_expiring cleanup, _on_cert_update combined listener); TraefikCertExpiryBinarySensor class (PROBLEM device class, _attr_entity_registry_enabled_default=True D-03, mdi:lock-alert icon, is_on=days_until_expiry<=threshold_days, available delegates to shared helper, days_until_expiry/threshold_days/not_after/last_error/san_mismatch/host/fetched_at always-on attributes)"

key-decisions:
  - "Combined cert-cycle listener per platform — single async_add_listener registration covers both BLOCKER #2 entity creation AND WARNING #1 cleanup (folded into one _cert_update_listener / _on_cert_update callback) instead of two separate registrations. Keeps the listener count minimal and ensures both paths fire atomically on every cert cycle"
  - "Shared _cert_cache_availability helper in sensor.py + import in binary_sensor.py — chosen over per-platform duplicates to enforce cross-platform consistency (the SUGGESTION #1 problem class). The earlier per-platform helper duplication proposal was deleted because two near-identical sources of truth would inevitably drift"
  - "Public cert_cache_availability alias added at sensor.py module level alongside the underscore-prefixed _cert_cache_availability — both names point at the same function object. The underscore form is canonical for per-platform imports (binary_sensor.py imports it); the public alias is for tests / future cross-module callers"
  - "Host normalised to lowercase at __init__ top of BOTH TraefikCertTimestampSensor AND TraefikCertExpiryBinarySensor — defensive against cache rows populated with mixed casing (the cert coordinator already lowercases in production, but a test harness could inject mixed casing; threat-model hardening)"
  - "Distinct description_key per platform (timestamp sensor: host, binary_sensor: f'{host}_expiring') so each entity has a unique name in the States panel even though they live on the same device"

patterns-established:
  - "Two-platform entity pair reading from sibling coordinator cache: one TraefikEntity-derived SensorEntity + one TraefikEntity-derived BinarySensorEntity reading from entry.runtime_data.cert_coordinator.data (NOT entry.runtime_data.data — proves Phase 3 sibling coordinator pattern). Both share the same _cert_cache_availability helper for cross-platform availability consistency"
  - "Cert-cycle entity-creation closure pattern: _create_pending_*_entities callable on initial setup AND on every cert cycle (BLOCKER #2). Materialises one entity per cached host not already in the entity registry; idempotent across repeated cycle ticks"

requirements-completed: [TLS-01, TLS-02]

# Metrics
duration: 14min
completed: 2026-07-06
---

# Phase 3 Plan 2: TLS Entity Platforms Summary

**TraefikCertTimestampSensor + TraefikCertExpiryBinarySensor on the new "HTTP Routers TLS" device, with cert-cycle entity-creation closures (BLOCKER #2) and split stale-cleanup listeners (WARNING #1) sharing a single cross-platform `_cert_cache_availability` helper (SUGGESTION #1) — TLS-01 + TLS-02 delivered, threshold re-evaluates within ~1s on Options change.**

## Performance

- **Duration:** 14 min
- **Started:** 2026-07-06T07:50:40Z
- **Completed:** 2026-07-06T08:04:27Z
- **Tasks:** 2 of 2 complete
- **Files modified:** 2 (sensor.py +268 lines, binary_sensor.py +218 lines)

## Accomplishments

- **`TraefikCertTimestampSensor` (TLS-01)** — `SensorDeviceClass.TIMESTAMP` entity per TLS-probed hostname. `native_value` returns `CertInfo.not_after` UTC `datetime | None` (None on error/unknown). Always-on attributes: `days_until_expiry`, `subject`, `issuer`, `san` (sorted tuple), `san_mismatch` (spike 006), `host`, `port`, `fetched_at` (ISO 8601), `last_error` (CONTEXT.md D-04 contract). `unique_id = f"{entry_id}_tls_cert_{host}"`, `entity_id = f"sensor.traefik_{slug}_cert"`, icon `mdi:certificate`, category `http_routers_tls`.
- **`TraefikCertExpiryBinarySensor` (TLS-02)** — `BinarySensorDeviceClass.PROBLEM` entity per TLS-probed hostname. `is_on = days_until_expiry <= threshold_days` (signed-int: negative days = breach → True). `_attr_entity_registry_enabled_default = True` (D-03 explicit divergence from Phase 2 M-12 — cert expiry is a security-impacting alarm that warrants always-on visibility). `available` delegates to the shared `sensor.py._cert_cache_availability` helper. Always-on attributes: `days_until_expiry`, `threshold_days`, `not_after` (ISO 8601), `last_error`, `san_mismatch`, `host`, `fetched_at` (D-04 + D-08 contract). `unique_id = f"{entry_id}_tls_expiring_{host}"`, `entity_id = f"binary_sensor.traefik_{slug}_expiring"`, icon `mdi:lock-alert`, category `http_routers_tls`.
- **Cross-platform `_cert_cache_availability` helper (SUGGESTION #1 fix)** — defined at module level in `sensor.py`, imported by `binary_sensor.py`. Returns `False` on `not last_update_success`, missing/empty cache, missing host row, or `CertError`. Single source of truth — both platforms consult the same function so a TLS-host pair can never show "timestamp sensor unavailable + binary_sensor stale ON". Public `cert_cache_availability = _cert_cache_availability` alias added for tests / future cross-module callers.
- **BLOCKER #2 fix — cert-cycle entity creation** — each platform defines a `_create_pending_*_entities` closure fired ONCE on initial setup AND on every cert cycle (6h) via `cert_coordinator.async_add_listener`. After the cold-start empty-cache fallback in plan 03-01 Task 3 step 3d(iii) seeds `async_set_updated_data({})`, the next 6h cycle fills the cache and the closure materialises entities for the newly-discovered hosts. Idempotent across repeated cycle ticks (skips hosts already in the entity registry).
- **WARNING #1 fix — split stale-cleanup per platform** — `_remove_stale_cert_hosts` defined + registered ONLY in `sensor.py` (for `tls_cert_` unique_id prefix); `_remove_stale_cert_expiring` defined + registered ONLY in `binary_sensor.py` (for `tls_expiring_` prefix). No duplicate registration. Both gated on `cert_coordinator.last_update_success` so transient cycle failure cannot mass-delete entities (Phase 2 D-18 pattern replicated verbatim).
- **Combined cert-cycle listener per platform** — single `cert_coordinator.async_add_listener(_cert_update_listener)` in sensor.py and `cert_coordinator.async_add_listener(_on_cert_update)` in binary_sensor.py fold entity creation + cleanup into one callback. Listener registration count stays minimal; both paths fire atomically on every cert cycle.
- **Defensive `getattr` for `cert_coordinator`** — both `async_setup_entry` bodies use `getattr(entry.runtime_data, "cert_coordinator", None)` and skip the TLS setup cleanly when None (degraded-install tolerance: partial install / test harness without Phase 3 wiring).
- **Phase 2 entities preserved** — `TraefikAnyRouterFailingBinarySensor._attr_entity_registry_enabled_default = False` retained at line 251 (M-12 phase-2 contract unchanged). `TraefikRouterBinarySensor`, `TraefikEntrypointSensor`, `TraefikServiceSensor`, `_TraefikAggregateCountSensor` and the three aggregate counters unchanged.
- **Strings.json translation bundle verified** — `entity.cert_timestamp_sensor`, `entity.cert_expiring_binary_sensor`, `exceptions.cert_unavailable` all present (added in plan 03-01).
- **Domain separation verified** — `entity._CATEGORY_TO_MODEL['http_routers_tls'] == 'HTTP Routers TLS'` while `entity._CATEGORY_TO_MODEL['http_routers'] == 'HTTP Routers'` — distinct device identifier `(DOMAIN, f"{entry_id}_{category}")` for the two domains.

## Task Commits

1. **Task 1: sensor.py — TraefikCertTimestampSensor + extend async_setup_entry to materialize one sensor per cached hostname** — `800b644` (feat)
2. **Task 2: binary_sensor.py — TraefikCertExpiryBinarySensor + stale-cleanup listener wired to cert_coordinator** — `600a0d7` (feat)
3. **Plan metadata:** `THIS_COMMIT` (docs)

## Files Created/Modified

- `custom_components/traefik/sensor.py` *(modified)* — +268 lines. New imports: `datetime.datetime`, `SensorDeviceClass`, `CertInfo`, `CertError`, `is_error`, `CertCoordinator` (TYPE_CHECKING); `cast` from `typing`. Cert-sensor block in `async_setup_entry` (cert_coordinator None-tolerant guard, `_create_pending_cert_sensor_entities` closure, `_remove_stale_cert_hosts` cleanup, `_cert_update_listener` combined listener). New `TraefikCertTimestampSensor` class at end-of-file. New module-level `_cert_cache_availability` helper + public `cert_cache_availability` alias.
- `custom_components/traefik/binary_sensor.py` *(modified)* — +218 lines. New imports: `cast`, `_cert_cache_availability` from `.sensor`, `CertInfo`, `CertError`, `is_error`, `CertCoordinator` (TYPE_CHECKING). Cert-expiring block in `async_setup_entry` (cert_coordinator None-tolerant guard, `_create_pending_cert_binary_sensor_entities` closure, `_remove_stale_cert_expiring` cleanup, `_on_cert_update` combined listener). New `TraefikCertExpiryBinarySensor` class at end-of-file (after `TraefikAnyRouterFailingBinarySensor`).

## Decisions Made

- **Combined cert-cycle listener per platform (not two separate listeners)** — single `cert_coordinator.async_add_listener(...)` registration per platform covers both BLOCKER #2 entity creation AND WARNING #1 cleanup. Folding into one callback keeps the listener count minimal and ensures both paths fire atomically. Alternative — two separate listeners per platform — would double the listener registration overhead with no behavioural benefit.
- **`cert_cache_availability` public alias added alongside `_cert_cache_availability`** — both names point at the same function object. The underscore form is canonical for the per-platform imports (binary_sensor.py imports it as `_cert_cache_availability`); the public alias is for tests / future cross-module callers that prefer a non-private name.
- **Host lowercase normalisation at `__init__` top of BOTH entity classes** — defensive against cache rows populated with mixed casing. The cert coordinator already lowercases in production (`_collect_hosts_from_main_coordinator` does `hosts.add(main.lower())`), but a test harness could inject mixed casing; the `host = host.lower()` at the top of `__init__` makes the unique_id idempotent regardless of the cache value's casing.
- **Distinct `description_key` per platform** — `TraefikCertTimestampSensor` uses `host` as description_key; `TraefikCertExpiryBinarySensor` uses `f"{host}_expiring"`. Both entities live on the same `http_routers_tls` device but get distinct names in the States panel.

## Deviations from Plan

### Pre-existing issues (out of scope, not fixed)

**1. [Out of scope] `custom_components/traefik/config_flow.py` ruff format non-conformance**
- **Status:** Pre-existing — confirmed via `git stash` that the file failed `uv run ruff format --check` BEFORE my changes (last commit on this file was Phase 2).
- **Impact:** `uv run ruff format --check custom_components/` reports 1 file would be reformatted (config_flow.py). The plan's verification block expected ruff format clean across the directory.
- **Decision:** Per scope-boundary rule (only auto-fix issues DIRECTLY caused by the current task's changes), did NOT modify config_flow.py. Both `uv run ruff check custom_components/` and `uv run mypy --strict custom_components/` are clean across all 11 files; only the auto-format diff is non-conforming and only in the unrelated config_flow.py.
- **Resolution:** Tracked under "Deferred Issues" for a future Phase 4 cleanup pass.

### Auto-fixed Issues

**1. [Rule 1 - Bug] mypy --strict union-attr narrowing in TraefikCertTimestampSensor**
- **Found during:** Task 1 (initial mypy --strict run after first implementation)
- **Issue:** After `if cache is None or is_error(cache): return None` guard, mypy could not narrow `cache` from `CertInfo | CertError | None` to `CertInfo` for `cache.not_after` access. Same pattern in `extra_state_attributes`.
- **Fix:** Added explicit `cast("CertInfo", cache)` after the guard in both `native_value` and `extra_state_attributes` to document the post-is_error narrowing for mypy --strict.
- **Files modified:** `custom_components/traefik/sensor.py`
- **Verification:** `uv run mypy --strict custom_components/traefik/sensor.py` reports "Success: no issues found".

**2. [Rule 1 - Bug] mypy --strict union-attr narrowing in TraefikCertExpiryBinarySensor**
- **Found during:** Task 2 (initial mypy --strict run after first implementation)
- **Issue:** Same as sensor.py — after `if cache is None or is_error(cache): return None` guard, mypy could not narrow `cache` for `cache.days_until_expiry` access.
- **Fix:** Added explicit `cast("CertInfo", cache)` after the guard in both `is_on` and `extra_state_attributes`.
- **Files modified:** `custom_components/traefik/binary_sensor.py`
- **Verification:** `uv run mypy --strict custom_components/traefik/binary_sensor.py` reports "Success: no issues found".

**3. [Rule 1 - Bug] ruff SIM118 — `.keys()` redundant in set comprehension**
- **Found during:** Task 1 (ruff check)
- **Issue:** `{h.lower() for h in cache.keys()}` triggers SIM118 (use `key in dict` not `key in dict.keys()`).
- **Fix:** Changed to `{h.lower() for h in cache}` (drops redundant `.keys()` call).
- **Files modified:** `custom_components/traefik/sensor.py`
- **Verification:** `uv run ruff check custom_components/traefik/sensor.py` clean.

**4. [Rule 1 - Bug] ruff SIM103 — return-the-condition refactor in `_cert_cache_availability`**
- **Found during:** Task 1 (ruff check)
- **Issue:** `if is_error(row): return False; return True` triggers SIM103 (return `not is_error(row)` directly).
- **Fix:** Collapsed to `return not is_error(row)` after the `row is None` early return.
- **Files modified:** `custom_components/traefik/sensor.py`
- **Verification:** `uv run ruff check custom_components/traefik/sensor.py` clean.

**5. [Rule 1 - Bug] ruff format auto-format on long line in `_create_pending_cert_binary_sensor_entities`**
- **Found during:** Task 2 (ruff format --check)
- **Issue:** `new_entities.append(TraefikCertExpiryBinarySensor(entry, cert_coordinator, host, cache_value))` was over the line-length threshold (or ruff format preferred the multi-line collapse).
- **Fix:** Ran `uv run ruff format` — formatter collapsed to single line within budget.
- **Files modified:** `custom_components/traefik/binary_sensor.py`
- **Verification:** `uv run ruff format --check custom_components/traefik/binary_sensor.py` reports "1 file already formatted".

## Verification

- [x] `uv run ruff check custom_components/traefik/sensor.py` — All checks passed
- [x] `uv run ruff format --check custom_components/traefik/sensor.py` — 1 file already formatted
- [x] `uv run mypy --strict custom_components/traefik/sensor.py` — Success: no issues found
- [x] `uv run ruff check custom_components/traefik/binary_sensor.py` — All checks passed
- [x] `uv run ruff format --check custom_components/traefik/binary_sensor.py` — 1 file already formatted
- [x] `uv run mypy --strict custom_components/traefik/binary_sensor.py` — Success: no issues found
- [x] `uv run ruff check custom_components/` — All checks passed (11 files)
- [x] `uv run mypy --strict custom_components/` — Success: no issues found in 11 source files
- [x] `uv run pytest tests/ -q` — 40 passed in 13.63s (no regressions)
- [x] Both new entity classes importable + correct device classes (`TIMESTAMP`, `PROBLEM`)
- [x] Domain separation: `_CATEGORY_TO_MODEL['http_routers_tls'] == 'HTTP Routers TLS'`, `_CATEGORY_TO_MODEL['http_routers'] == 'HTTP Routers'`
- [x] `strings.json` parses + 3 new translation keys present
- [x] Phase 2 invariant: `TraefikAnyRouterFailingBinarySensor._attr_entity_registry_enabled_default = False` retained at line 251
- [ ] `uv run ruff format --check custom_components/` — 1 pre-existing diff in `config_flow.py` (out of scope, see Deviations)

## Deferred Issues

- **Pre-existing `config_flow.py` ruff format non-conformance** — `uv run ruff format --check custom_flow.py` would auto-format some multi-line `vol.Schema({...})` calls + an `async def async_step_init` signature. Last touched in Phase 2; not modified by this plan. Track for a Phase 4 cleanup pass or as a stand-alone `gsd-quick` task. Does not block Phase 3 plan 03-03 (test surface) or any subsequent work — only impacts the strict-format-check verification step.

## Known Stubs

None. Both entity classes are fully wired (closure-based creation + cleanup listener + cross-platform helper + always-on attributes). All Phase 3 plan 03-02 success criteria met:

- Both entity classes read from `entry.runtime_data.cert_coordinator.data` (NOT `entry.runtime_data.data`) — confirmed by `cert_coordinator: CertCoordinator | None = getattr(...)` access pattern in both `async_setup_entry` blocks and the explicit `CertCoordinator` type annotation in `__init__`.
- `_remove_stale_cert_hosts` registered ONLY in sensor.py (function exists at exactly 2 locations in sensor.py: definition + caller inside `_cert_update_listener`).
- `_remove_stale_cert_expiring` registered ONLY in binary_sensor.py (function exists at exactly 2 locations in binary_sensor.py: definition + caller inside `_on_cert_update`).
- `_cert_cache_availability` defined in sensor.py AND imported by binary_sensor.py (no per-platform duplicate). Public `cert_cache_availability` alias added.
- Cleanup listeners gated on `cert_coordinator.last_update_success` (Phase 2 D-18 pattern replicated).
- `entry.runtime_data.cert_coordinator` accessed via `getattr(..., None)` defensively in BOTH platforms.
- Host normalised to lowercase at the top of each `__init__`.
- Phase 2 entities unchanged: `TraefikEntrypointSensor`, `TraefikServiceSensor`, `_TraefikAggregateCountSensor`, `TraefikRoutersCountSensor`, `TraefikServicesCountSensor`, `TraefikMiddlewaresCountSensor`, `TraefikRouterBinarySensor`, `TraefikAnyRouterFailingBinarySensor` all intact.