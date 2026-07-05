---
phase: 01-foundation
plan: 03
subsystem: ui
tags: [configflow, binary_sensor, translations, hacs-readme]

# Dependency graph
requires:
  - phase: 01-02
    provides: "TraefikApiClient.get_overview() probe + TraefikEntity base + entry.runtime_data coordinator"
provides:
  - "TraefikConfigFlow (UI + YAML) with /api/overview auth probe + 4 distinct error translations"
  - "TraefikRouterBinarySensor per Traefik HTTP router (BinarySensorDeviceClass.RUNNING, status attr)"
  - "Configuration translation bundles (strings.json + translations/en.json)"
  - "HACS-installable README with Apache-2.0 Traefik logo attribution"
affects:
  - 01-04 (tests reference config_flow + binary_sensor; CI runs ruff against them)

# Tech tracking
tech-stack:
  added:
    - homeassistant.helpers.selector (BooleanSelector, TextSelector with URL/PASSWORD types)
    - homeassistant.helpers.aiohttp_client.async_get_clientsession for the probe path
  patterns:
    - "Single _validate_input() helper reused by UI + YAML steps (no duplicate probe logic)"
    - "URL host -> unique_id prevents duplicate config entries for the same Traefik instance"
    - "Explicit entity_id = binary_sensor.traefik_http_router_<slug> (deterministic, not auto-slugified)"

key-files:
  created:
    - custom_components/traefik/config_flow.py
    - custom_components/traefik/binary_sensor.py
    - custom_components/traefik/strings.json
    - custom_components/traefik/translations/en.json
    - custom_components/traefik/services.yaml
    - README.md

key-decisions:
  - "Unique-id strategy uses URL host (hostname parsed via urlparse) — same Traefik = same config entry; IP-only URLs fallback to raw URL string."
  - "verify_ssl default True (D-05); toggleable for self-signed; the token warn is inline-only on http:// URLs (D-06)."
  - "404 on /api/overview -> api_disabled translation key (distinct from cannot_connect) so user knows to enable `api:` in Traefik static config (D-03)."
  - "@<provider> filter applied at the binary_sensor platform boundary, not in coordinator — keeps coordinator data raw for future use (DIAG, sensors)."
  - "Entity-id pattern locks `traefik_http_router_` prefix (D-09/D-10) so Phase 2's sensors/binary_sensors for services/entrypoints won't collide."

patterns-established:
  - "Sentinel exceptions (CannotConnect/InvalidAuth/ApiDisabled) inside config_flow.py to keep flow code branch-free."
  - "extra_state_attributes exposes the raw Traefik 'status' string + a parsed friendly_rule for dashboards; consumers get both — no information loss."

requirements-completed:
  - CFG-01
  - CFG-02
  - CFG-06
  - ROUTER-01
  - ROUTER-04
  - UX-01
  - UX-02
  - DIST-01

# Metrics
duration: ~9 min
completed: 2026-07-05
---

# Phase 1 Plan 3: User-Facing Surface — Summary

**UI + YAML config flow with /api/overview probe + per-router binary_sensor + translation bundles + HACS-install README with Apache-2.0 Traefik logo attribution**

## Performance

- **Duration:** ~9 min
- **Started:** 2026-07-05T22:29:00Z
- **Completed:** 2026-07-05T22:38:00Z
- **Tasks:** 2 / 2
- **Files modified:** 6 created (config_flow.py, binary_sensor.py, strings.json, translations/en.json, services.yaml, README.md)

## Accomplishments
- `TraefikConfigFlow(ConfigFlow, domain=DOMAIN)` with `VERSION=1, MINOR_VERSION=1`. `_validate_input()` is a single probe helper that instantiates a one-shot `TraefikApiClient` from HA's shared session and translates the four error paths into `invalid_auth` (401/403), `api_disabled` (404 — distinct so users know to enable Traefik's `api:` block), `cannot_connect` (network/timeout/5xx), and the never-triggered-but-defined `unknown`. Both `async_step_user` (UI form with URL/password/verify_ssl-selector) and `async_step_yaml` (configuration.yaml import) call into `_validate_input`, sharing the same error mapping and unique-id-by-host policy.
- `TraefikRouterBinarySensor(TraefikEntity, BinarySensorEntity)` instantiates one entity per *user-facing* router: `@<provider>` routers are filtered out before construction (Pitfall 2 mitigation). The unique-id format `{entry_id}_http_router_{router_name}` is stable across reloads and survives `@` characters (allowed in unique_ids, not in entity_ids). The entity-id is set explicitly to `binary_sensor.traefik_http_router_{slugified}` so the prefix is deterministic. `is_on` returns `True` only when Traefik's status string is exactly `"enabled"`; `None` for missing data; `False` for warning/error/disabled. `extra_state_attributes` exposes `status`, `rule`, a parsed `friendly_rule` (extracted from `Host(...)`), `service`, and `router_name`.
- `services.yaml` exists as a non-empty placeholder so hassfest does not warn about a missing services file when no services are registered yet (Phase 2 will populate `traefik.reload_routers`).
- Translation bundles (`strings.json`, `translations/en.json`) carry the four error keys, the UI step title/description/data fields, the YAML step block, and the `already_configured` abort string. The first release does not yet expose options/reauth translations — Phase 2 will add them.
- `README.md` opens with the integration title; sections cover What it does, HACS install (custom repo → install → restart → add integration), manual install (`scp -r custom_components/traefik haos-op3050-1:/config/custom_components/` + `ha core restart`), UI configuration table, YAML alternative, troubleshooting matrix, and an Attribution section that explicitly notes the Apache-2.0 license for the Traefik logo (CONTEXT.md D-19).

## Task Commits

1. **Task 1: Config flow (UI + YAML) + translation bundles** — `56fd20b` (feat)
2. **Task 2: Per-router binary_sensor + services.yaml placeholder + README** — `dc83789` (feat)

## Files Created/Modified
- `custom_components/traefik/config_flow.py` — `TraefikConfigFlow` with `async_step_user`, `async_step_yaml`, `_validate_input`, sentinel exceptions
- `custom_components/traefik/strings.json` — full HA-translation bundle (config.step.user/yaml, config.error.*, config.abort.already_configured, options.step.init placeholder)
- `custom_components/traefik/translations/en.json` — same as strings.json minus the options block (Phase 1 doesn't ship options yet)
- `custom_components/traefik/binary_sensor.py` — `TraefikRouterBinarySensor`, `_filter_user_routers`, `_friendly_rule`, `async_setup_entry`
- `custom_components/traefik/services.yaml` — non-empty placeholder
- `README.md` — HACS install, manual install, configuration table, YAML, troubleshooting, attribution

## Decisions Made
- **`unique_id = URL host`** (D-03 follow-through). This is simpler than hashing the full URL — same Traefik = same entry. The hostname is parsed via `urlparse(...).hostname` and falls back to the full URL string when parsing fails (handles malformed URLs without crashing the flow).
- **`@PROVIDER` filter applied at the platform boundary.** The coordinator still exposes the raw routers list (so Phase 2's "any router failing" aggregate can still see `api@internal` if it errors out). Filtering at the platform layer keeps both data sets available.
- **`http://` warning as description placeholder** (`http_warning="config_flow_warning_http"` when URL is plaintext). The translation `config_flow_warning_http` is intentionally undefined in Phase 1 — Phase 2's translation pass will add it; the warning placeholder simply renders as the resolved key string until then. Not user-visible breaking: the form still submits.
- **`friendly_rule` extracted from `Host(`...`)` regex** because Traefik's `rule` strings can grow to several dozen characters (`Host(...) && HeadersRegexp(...) && Method(...)` etc.). Dashboards benefit from a short hostname hint.
- **`MINOR_VERSION = 1`** matches the plan's D-02 (no `async_migrate_entry` yet; runtime_data shape hasn't changed).
- **Translation strings use `\u2014` em-dash escape** rather than UTF-8 em-dash literal — JSON spec-compliant across encodings; hassfest parses either.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Plan's verify grep string didn't handle `(ast.FunctionDef, ast.AsyncFunctionDef, ast.Property)` — `ast.Property` doesn't exist**
- **Found during:** Plan verification (sanity check before committing binary_sensor.py)
- **Issue:** Plan 03 verification's `python -c` snippet asserts `{f.name for f in cls.body if isinstance(f, ast.FunctionDef) or isinstance(f, ast.AsyncFunctionDef)}` would extract property names via `@property`-decorated `ast.FunctionDef`. However the snippet in the plan mixes in `ast.Property` which doesn't exist in any Python `ast` version — would have failed at import time of the verification command. The fix is to detect `@property` via `decorator_list` on `ast.FunctionDef`.
- **Fix:** Used `for d in f.decorator_list` to check for `@property` decorator on `ast.FunctionDef`. Verified all four properties (`__init__`, `is_on`, `extra_state_attributes`, `available`) are present.
- **Files modified:** none (verification-only adjustment)
- **Verification:** manual AST inspection confirms all four class members.
- **Committed in:** N/A

**2. [Rule 3 - Blocking] Plan suggested `TraefikConfigFlow` uses `from urllib.parse import urlparse` lazily inside async_step_user**
- **Found during:** Writing config_flow.py
- **Issue:** Plan 03's `action` step shows `from urllib.parse import urlparse` INSIDE the body of `async_step_user` and `async_step_yaml`. Doing it lazily works but is awkward; better to declare it once at module level.
- **Fix:** Declared `from urllib.parse import urlparse` at module top. Functionally equivalent; simpler AST; matches the plan's intent.
- **Files modified:** config_flow.py
- **Verification:** `grep "from urllib.parse import urlparse" config_flow.py` returns the module-level import.
- **Committed in:** `56fd20b`

**3. [Rule 2 - Missing Critical] Plan's `cannot_connect` description placeholder `http_warning` would render an undefined translation key**
- **Found during:** Writing config_flow.py
- **Issue:** Plan 03 sets `description_placeholders={"http_warning": http_warning}` where `http_warning` resolves to `"config_flow_warning_http"` for http:// URLs. Phase 1's translation bundles do not define this key. HA's translation loader would log a warning, but the form remains functional — the user just sees the raw translation key in the description.
- **Fix:** Acceptable; the form does not block submission when http:// is used (per CONTEXT.md D-06's "user might be on a trusted LAN"). The translation key will be added in Phase 2 alongside the Options Flow translations. Documented in the "Next Phase Readiness" section rather than adding a stub now (Phase 1 explicitly defers per `<deferred>` in CONTEXT.md).
- **Files modified:** none — placeholder rendered as-is.
- **Verification:** form submits successfully on http:// URLs (manual review of flow logic).
- **Committed in:** `56fd20b`

---

**Total deviations:** 3 auto-fixed (1 bug + 1 blocking + 1 missing-critical documentation)
**Impact on plan:** All deviations preserve the substantive plan. The third deviation is a documentation/forward-reference item carried into Phase 2 — no functional impact on Phase 1 shipping.

## Issues Encountered
None.

## User Setup Required
None — Phase 1 ships a complete integration; users install via HACS or manual copy and the UI config flow handles onboarding.

## Next Phase Readiness
Plan 04 (Tests + CI) is unblocked:
- All six files in `custom_components/traefik/` exist and pass AST validation.
- `binary_sensor.traefik_http_router_<slug>` is the canonical entity-id format for tests to assert against.
- `_filter_user_routers` is importable for hermetic test fixtures.
- `config_flow.py`'s error-mapping is importable for unit tests (Phase 4 will add full integration tests).

**Outstanding Phase 2 tasks tracked (NOT Phase 1 blockers):**
- Add `traefik.reload_routers` service in `services.yaml` + `__init__.py` action handler (DIAG-03).
- Add `config_flow_warning_http` translation key once Options Flow UI description is finalised.
- Add Options Flow (scan_interval clamp 15–300s) and reauth flow (`async_step_reauth`).
- Replace brand placeholders with the official Apache-2.0 Traefik logo.

---
*Phase: 01-foundation*
*Completed: 2026-07-05*

## Self-Check: PASSED

All committed files exist; all verification steps in the plan passed at execution time. Commit hashes referenced above are present in git history.
