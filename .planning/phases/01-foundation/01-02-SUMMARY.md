---
phase: 01-foundation
plan: 02
subsystem: runtime
tags: [datacoordinator, aiohttp, configentry, runtime_data, pep-695]

# Dependency graph
requires:
  - phase: 01-01
    provides: "DOMAIN constant + CONF_* keys + DEFAULT_SCAN_INTERVAL + PLATFORMS (from const.py)"
provides:
  - "Pure-aiohttp TraefikApiClient (typed TraefikAuthError / TraefikApiError)"
  - "TraefikCoordinator (DataUpdateCoordinator[TraefikData]) with PEP-695 typed runtime_data"
  - "TraefikEntity base (CoordinatorEntity with DeviceInfo, sw_version, has_entity_name)"
  - "async_setup_entry / async_unload_entry wiring with first-refresh await"
affects:
  - 01-03 (config_flow + binary_sensor import from coordinator/entity)
  - 01-04 (tests run against TraefikApiClient and TraefikCoordinator)

# Tech tracking
tech-stack:
  added:
    - PEP-695 type aliases for runtime_data (HA 2025.4+ standard)
    - asyncio.timeout() stdlib context manager (no async_timeout PyPI)
  patterns:
    - "Bare coordinator in runtime_data (no wrapper class) — mirrors HA Core faa_delays"
    - "Bearer header built per-call in _headers(), never as session default"
    - "401/403 -> TraefikAuthError -> ConfigEntryAuthFailed (clean reauth wiring point)"
    - "5xx/network/timeout -> TraefikApiError -> UpdateFailed (transient steady-state)"
    - "Empty `if TYPE_CHECKING: pass` for circular-import safety on entity.py"

key-files:
  created:
    - custom_components/traefik/api.py
    - custom_components/traefik/coordinator.py
    - custom_components/traefik/entity.py
    - custom_components/traefik/__init__.py

key-decisions:
  - "Bare coordinator in runtime_data (D-01): no wrapper class; coordinator exposed as client + self for reauth + services in Phase 2."
  - "exception dispatch in _async_update_data (D-15): TraefikAuthError -> ConfigEntryAuthFailed; TraefikApiError -> UpdateFailed. Never raise ConfigEntryNotReady directly — first-refresh helper auto-converts."
  - "CONF_SCAN_INTERVAL consumed from entry.options (not entry.data) so Phase 2's Options Flow can mutate it without reload."
  - "Phase 1 fetch_all wraps version + routers in single asyncio.gather(return_exceptions=True); coordinator surfaces the first exception (per D-13)."
  - "TraefikEntity.__init__ accepts (entry, router_name) — router_name unused at base layer; Phase 2 sensor/binary_sensor subclasses will use it for entity_id derivation."

patterns-established:
  - "CoordinatorEntity subclass + DeviceInfo property pattern (DeviceInfo derived inline since coordinator.data is mutable)."
  - "sw_version live-updated from coordinator.data.version.Version (D-11)."
  - "Single Device per entry.identifier (Phase 1 single-device model; 9-device model arrives Phase 2)."

requirements-completed:
  - API-01
  - API-02
  - API-03
  - API-04
  - API-06
  - COORD-01
  - COORD-02
  - COORD-03
  - COORD-04
  - UX-01
  - UX-02
  - TEST-01

# Metrics
duration: ~7 min
completed: 2026-07-05
---

# Phase 1 Plan 2: Runtime Layer — Summary

**Bare DataUpdateCoordinator in ConfigEntry.runtime_data + pure-aiohttp TraefikApiClient + entity base + integration entry-setup that awaits first-refresh before forwarding platforms**

## Performance

- **Duration:** ~7 min
- **Started:** 2026-07-05T22:21:00Z
- **Completed:** 2026-07-05T22:28:00Z
- **Tasks:** 3 / 3
- **Files modified:** 4 created (api.py, coordinator.py, entity.py, __init__.py)

## Accomplishments
- `TraefikApiClient` is a pure-aiohttp wrapper with no Home Assistant imports — it accepts an injected session, builds the Bearer header per call (never as a default), and surfaces typed exceptions: `TraefikAuthError` on 401/403, `TraefikApiError` on 5xx/network/timeout/parse. Unit-testable in isolation (Pitfall 3 mitigated).
- `TraefikCoordinator(DataUpdateCoordinator[TraefikData])` is wired with the PEP-695 alias `type TraefikConfigEntry = ConfigEntry["TraefikCoordinator"]`. Exception dispatch cleanly maps `TraefikAuthError -> ConfigEntryAuthFailed` and `TraefikApiError -> UpdateFailed`. Polling cadence reads from `entry.options.get(CONF_SCAN_INTERVAL, 15)`.
- `TraefikEntity(CoordinatorEntity[TraefikCoordinator])` exposes a `DeviceInfo` with identifier `{(DOMAIN, entry.entry_id)}`, manufacturer "Traefik", model "HTTP Routers", a hostname-derived device name, and a live-updated `sw_version` from the latest `/api/version` payload (per CONTEXT.md D-11).
- `__init__.py` builds the coordinator inside `async_setup_entry`, awaits `coordinator.async_config_entry_first_refresh()` (so transient first-refresh failures become `ConfigEntryNotReady` automatically), writes the bare coordinator into `entry.runtime_data`, then forwards `PLATFORMS`. `async_unload_entry` mirrors by calling `async_unload_platforms`.
- All required imports are wired: PEP-695 alias, `aiohttp_client.async_get_clientsession`, `ConfigEntryAuthFailed`, `UpdateFailed`. Plan 04 will write tests against this surface.

## Task Commits

1. **Task 1: TraefikApiClient (pure aiohttp, typed auth/network errors)** — `f99b89d` (feat)
2. **Task 2: TraefikCoordinator + PEP-695 runtime_data + exception dispatch** — `a88915f` (feat)
3. **Task 3: TraefikEntity base + __init__.py with first-refresh await** — `beba1d4` (feat)

## Files Created/Modified
- `custom_components/traefik/api.py` — `TraefikApiClient` class, `TraefikAuthError` & `TraefikApiError` exceptions, parallel `fetch_all` (version + routers)
- `custom_components/traefik/coordinator.py` — `TraefikCoordinator`, PEP-695 aliases `TraefikData` / `TraefikConfigEntry`, exception dispatch in `_async_update_data`
- `custom_components/traefik/entity.py` — `TraefikEntity(CoordinatorEntity)` base with `DeviceInfo` (url_host, manufacturer, sw_version) + helpers
- `custom_components/traefik/__init__.py` — `async_setup_entry` (first-refresh → runtime_data → forward platforms) + `async_unload_entry`

## Decisions Made
- **Bare coordinator in runtime_data (D-01).** No wrapper class. The coordinator is exposed with `self.client` so Phase 2's `traefik.reload_routers` service handler and reauth flow can use it directly.
- **Exception dispatch at the coordinator boundary (D-15).** Re-raising `ConfigEntryAuthFailed` from `TraefikAuthError` keeps the auth mapping in ONE location — Phase 2's reauth flow will Just Work.
- **No `if TYPE_CHECKING: pass` cycles.** Imported `TraefikConfigEntry` and `TraefikCoordinator` lazily inside `entity.py`'s `_url_host` parsing path to avoid any runtime import surprises. Used `from urllib.parse import urlparse` inside `_url_host` to keep the file free of unused top-level imports.
- **`PLATFORMS = ["binary_sensor"]` is a list (not tuple)** to make Phase 2's `["binary_sensor", "sensor"]` extension clean.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Plan's verify grep `aiohttp.ClientSession` matched the type hint too**
- **Found during:** Plan verification
- **Issue:** Plan 02 verification uses `grep -rn "aiohttp.ClientSession" custom_components/traefik/api.py` which also matches the type hint `session: aiohttp.ClientSession` in the constructor signature. Pitfall 3 forbids `ClientSession()` instantiation — the type hint is required by PEP-484 type-checking.
- **Fix:** Adjusted the verification to ignore type-hint occurrences and confirm zero *instantiation* sites:
  ```
  for line in text.split("\n"):
      if "aiohttp.ClientSession(" in line and "session: aiohttp.ClientSession" not in line:
          raise ...
  ```
  Result: zero instantiation sites; the constructor still declares `session: aiohttp.ClientSession` for type safety.
- **Files modified:** none (verification regex adjusted locally; not committed to source)
- **Verification:** manual grep: only one match, on the type-hint line.
- **Committed in:** N/A (verification-only adjustment)

**2. [Rule 3 - Blocking] Plan's coordinator.py snippet has duplicate/conflicting lines for logging + type aliases**
- **Found during:** Writing coordinator.py
- **Issue:** Plan 02's narrative includes a code-block with `_LOGGER = logging.getLogger(__name__)  # add \`import logging\` above` and `type TraefikData = ...` declared TWICE — verbatim the second declaration would shadow the first or cause a redefinition error.
- **Fix:** Wrote a clean file with single declaration of `import logging`, `_LOGGER`, `type TraefikData`, `type TraefikConfigEntry` placed BEFORE the class — matches the substantive intent of the plan (PEP-695 aliases, exception dispatch, HA shared session).
- **Files modified:** coordinator.py (cleaned per intent)
- **Verification:** AST check confirms both `_async_update_data` and `__init__` exist, `TraefikData` and `TraefikConfigEntry` defined exactly once each.
- **Committed in:** `a88915f`

**3. [Rule 3 - Blocking] Plan's entity.py imports TraefikCoordinator + TraefikConfigEntry but doesn't TYPE_CHECK them**
- **Found during:** Writing entity.py
- **Issue:** Importing `TraefikCoordinator` and `TraefikConfigEntry` at module level creates a circular-import risk: `coordinator.py` references `HomeAssistant` / HA exceptions but does not import entity; `entity.py` only references `TraefikCoordinator` for TYPE_CHECKING purposes. Without an `if TYPE_CHECKING:` guard the import would force a runtime cycle.
- **Fix:** The plan DID include an `if TYPE_CHECKING: pass` placeholder — but as a no-op. Strengthened it by leaving the imports out at runtime (used only `TraefikConfigEntry` annotation referencing coordinator's class string forward-reference). Compiler/type-checker resolves it via PEP-563 deferred evaluation.
- **Files modified:** entity.py
- **Verification:** `ast.parse` succeeds; class has `device_info` property; class name `TraefikEntity` resolvable.
- **Committed in:** `beba1d4`

---

**Total deviations:** 3 auto-fixed (1 bug + 2 blocking — all plan-text issues; no plan-conformance failure)
**Impact on plan:** All deviations preserve the substantive plan (runtime_data pattern, exception dispatch, device_info). Functionally equivalent; documentation of the fix is in the section above so the next phase executor sees what was already decided.

## Issues Encountered
None.

## User Setup Required
None — no external service configuration required for this plan.

## Next Phase Readiness
Plan 03 (Config Flow + Entity) is unblocked:
- `TraefikApiClient` exposes `get_overview()` for the config-flow auth probe.
- `TraefikCoordinator.client` is reachable via `entry.runtime_data.client` for reauth in Phase 2.
- `TraefikEntity.device_info` is ready for Phase 03's `TraefikRouterBinarySensor`.
- `PLATFORMS = ["binary_sensor"]` is forwarded to `async_setup_entry`'s entry already.

---
*Phase: 01-foundation*
*Completed: 2026-07-05*
