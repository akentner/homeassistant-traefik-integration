---
phase: 03-tls-certificate-expiry
plan: 01
subsystem: tls
tags: [tls, ssl, asyncio, dataclass, semaphore, traefik, certificate-expiry]

# Dependency graph
requires:
  - phase: 02-multi-device
    provides: "Per-category device model, TypedDict coordinator, _CATEGORY_TO_MODEL"
provides:
  - "custom_components.traefik.tls — stdlib-only TLS handshake helper with CertInfo/CertError types and RFC 6125 §6.4.3 SAN matching"
  - "custom_components.traefik.cert_coordinator.CertCoordinator — 6h DataUpdateCoordinator with Semaphore(4), 5s timeout, in-memory cache"
  - "CertCoordinator sibling attach on entry.runtime_data (PITFALLS #6 — no runtime_data shape migration)"
  - "Threshold live re-evaluation via async_set_threshold + async_update_listeners in _async_options_updated"
  - "3 new translation keys (cert_unavailable, cert_timestamp_sensor, cert_expiring_binary_sensor) for Phase 3 03-02 entities"
affects:
  - "phase 03 plan 02 — entity platforms read cert_coordinator.data + cert_coordinator.threshold_days"
  - "phase 03 plan 03 — test surface exercises _collect_hosts_from_main_coordinator and async_set_threshold"

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "stdlib-only TLS handshake (ssl.PROTOCOL_TLS_CLIENT + check_hostname=False + load_default_certs trio)"
    - "defense-in-depth never-raise contract: tls.fetch_cert_info returns CertError + cert_coordinator._probe adds asyncio.TimeoutError catch"
    - "sibling attach on entry.runtime_data (NOT runtime_data shape migration) — preserves existing entry.runtime_data.client.reload_routers() accessor"
    - "config_entry=entry passed to super().__init__() to auto-populate self.config_entry for downstream coordinator introspection"
    - "24h parse-failure log throttle via time.monotonic() (resilient to host pause / wall-clock drift)"

key-files:
  created:
    - "custom_components/traefik/tls.py — CertInfo (frozen dataclass), CertError/CertDict TypedDicts, parse_not_after (ssl.cert_time_to_seconds + 3-format fallback), _hostname_matches_san (RFC 6125 wildcard), _fetch_cert_raw (host/sni split for tests), fetch_cert_info, fetch_cert_info_async (asyncio.to_thread wrapper)"
    - "custom_components/traefik/cert_coordinator.py — CertCoordinator (DataUpdateCoordinator[dict[str, CertInfo | CertError]]), _HOST_FROM_RULE regex, _collect_hosts_from_main_coordinator (tls.domains[].main + sans[] + Host(...) match union, filter_internal_items'd), _probe (Semaphore + timeout + asyncio.to_thread), async_set_threshold, get_threshold, _reset"
  modified:
    - "custom_components/traefik/const.py — added TLS_HANDSHAKE_TIMEOUT=5.0, TLS_SEMAPHORE=4, DEFAULT_TLS_CERT_COOLDOWN=21600"
    - "custom_components/traefik/entity.py — _CATEGORY_TO_MODEL gains 'http_routers_tls' -> 'HTTP Routers TLS'"
    - "custom_components/traefik/coordinator.py — TYPE_CHECKING-guarded CertCoordinator import; TraefikCoordinator.cert_coordinator: CertCoordinator | None = None (sibling attach)"
    - "custom_components/traefik/__init__.py — extend _async_options_updated with CONF_TLS_WARN_DAYS push via cert_coordinator.async_set_threshold (defensive try/except); extend async_setup_entry with CertCoordinator instantiation, sibling attach, and try/except-shielded first refresh with empty-cache fallback via async_set_updated_data({})"
    - "custom_components/traefik/strings.json — 3 new translation keys: exceptions.cert_unavailable, entity.cert_timestamp_sensor, entity.cert_expiring_binary_sensor"

key-decisions:
  - "Used _open_tls_connection helper to centralize the spike-validated SSLContext trio (PROTOCOL_TLS_CLIENT, check_hostname=False, load_default_certs) — single source of truth, testable seam"
  - "Sibling attach (PITFALLS #6) — entry.runtime_data stays the main TraefikCoordinator; cert_coordinator is an attribute on it. Preserves all existing entry.runtime_data.client.reload_routers() accessors"
  - "Cert-coordinator first refresh wrapped in try/except with async_set_updated_data({}) fallback — a TLS failure on first cycle must NOT raise ConfigEntryNotReady (CONTEXT.md D-10); the main coordinator's auth/not-ready path is the only entry-level failure surface"
  - "24h parse-failure log throttle via time.monotonic() (NOT wall clock) — user can pause the host and wall-clock drift would log too often or too rarely"
  - "ConnectionRefusedError catch comes BEFORE OSError (subclass-of ordering matters on every supported platform)"
  - "_probe uses TimeoutError (stdlib) which since 3.11 aliases asyncio.TimeoutError — single catch covers both"
  - "Stage 2 const additions in Task 2 commit (deviation from plan order) — cert_coordinator.py's mypy --strict gate requires the consts to be defined for the file to typecheck in isolation"

patterns-established:
  - "Never-raise contract: every TLS error path in tls.fetch_cert_info returns a typed CertError TypedDict; cert_coordinator._probe adds asyncio.TimeoutError + Exception catch on top"
  - "Sibling coordinator pattern: DataUpdateCoordinator stored as an attribute on another coordinator's instance; entry.runtime_data shape unchanged"
  - "TYPE_CHECKING-guarded forward refs for sibling coordinator imports (coordinator.py imports CertCoordinator for type annotations only)"

requirements-completed: [TLS-03, TLS-04, TLS-05]

# Metrics
duration: 25min
completed: 2026-07-06
---

# Phase 3 Plan 1: TLS Handshake Foundation Summary

**stdlib-only TLS handshake helper with CertInfo/CertError types, RFC 6125 §6.4.3 SAN matching, and a sibling CertCoordinator with 6h cadence + Semaphore(4) + 5s timeout — wired into the integration with no runtime_data shape migration (PITFALLS #6).**

## Performance

- **Duration:** 25 min
- **Started:** 2026-07-06T07:36:07Z
- **Completed:** 2026-07-06T08:01:00Z
- **Tasks:** 3 of 3 complete
- **Files modified:** 7 (2 created, 5 modified)

## Accomplishments

- **`tls.py`** — stdlib-only handshake helper. `CertInfo` (frozen dataclass) with `not_after`, `days_until_expiry`, `subject`, `issuer`, `san`, `san_mismatch` (spike 006), `fetched_at`; `CertError` TypedDict with closed-set error classification (`timeout | dns | refused | unreachable | oserror | ssl | parse | empty | unknown`); `parse_not_after` with `ssl.cert_time_to_seconds()` primary + 3-format fallback (no unused `%Y%m%d%H%M%SZ`); `_hostname_matches_san` with RFC 6125 wildcard (label-count guard, case-insensitive, trailing-dot tolerant); 24h per-host parse-failure log throttle via `time.monotonic()`; `_fetch_cert_raw` test seam with separate `sni` parameter.
- **`cert_coordinator.py`** — `CertCoordinator(DataUpdateCoordinator[dict[str, CertInfo | CertError]])` with `update_interval=21600s` (6h), `Semaphore(4)` + `asyncio.timeout(5)`, in-memory cache keeping both `CertInfo` AND `CertError`; `_collect_hosts_from_main_coordinator` reads `entry.runtime_data.data["http_routers"]` and dedupes the union of `tls.domains[].main` + `tls.domains[].sans[]` + `Host(...)` rule matches (after `filter_internal_items`); `async_set_threshold` mutates `threshold_days` and calls `async_update_listeners` (no re-handshake).
- **Wiring** — `coordinator.py` adds `cert_coordinator: CertCoordinator | None = None` on `TraefikCoordinator` (sibling attach, PITFALLS #6); `__init__.py` extends `async_setup_entry` to instantiate the cert coordinator, attach as sibling, and `try/except`-shield the first refresh (with `async_set_updated_data({})` empty-cache fallback so a TLS failure does NOT raise `ConfigEntryNotReady`); `_async_options_updated` extended to push `CONF_TLS_WARN_DAYS` via `async_set_threshold` (defensive try/except); `entity.py` adds `"http_routers_tls" → "HTTP Routers TLS"`; `strings.json` gains 3 new translation keys; `const.py` adds 3 cert-cycle knobs.

## Task Commits

1. **Task 1: Create tls.py** — `74e7c02` (feat)
2. **Task 2: Create cert_coordinator.py** — `b4f9332` (feat)
3. **Task 3: Wire CertCoordinator into integration** — `f7adcf4` (feat)
4. **Plan metadata:** `THIS_COMMIT` (docs)

## Files Created/Modified

- `custom_components/traefik/tls.py` *(created)* — 399 LOC. CertInfo, CertError, CertDict, is_error, _format_rdn, parse_not_after, _hostname_matches_san, _parse_log_cooldown + _log_parse_failure_once (24h throttle), _build_error, _open_tls_connection, _fetch_cert_raw (sni seam), fetch_cert_info, fetch_cert_info_async. No `cryptography` import.
- `custom_components/traefik/cert_coordinator.py` *(created)* — 257 LOC. CertCoordinator class, _HOST_FROM_RULE regex, _collect_hosts_from_main_coordinator, _probe (Semaphore + timeout + never-raise), async_set_threshold, get_threshold, _reset (test seam).
- `custom_components/traefik/const.py` *(modified)* — added `TLS_HANDSHAKE_TIMEOUT=5.0`, `TLS_SEMAPHORE=4`, `DEFAULT_TLS_CERT_COOLDOWN=21600` with CONTEXT.md D-05 comment.
- `custom_components/traefik/entity.py` *(modified)* — `_CATEGORY_TO_MODEL` gains `"http_routers_tls": "HTTP Routers TLS"`.
- `custom_components/traefik/coordinator.py` *(modified)* — TYPE_CHECKING-guarded `CertCoordinator` import; `cert_coordinator: CertCoordinator | None = None` on `TraefikCoordinator`; docstring updated to "Phase 2 + Phase 3 sibling attach".
- `custom_components/traefik/__init__.py` *(modified)* — extend imports, extend `_async_options_updated` with CONF_TLS_WARN_DAYS push (defensive try/except), extend `async_setup_entry` with cert coordinator instantiation + sibling attach + try/except-shielded first refresh.
- `custom_components/traefik/strings.json` *(modified)* — added `exceptions.cert_unavailable`, `entity.cert_timestamp_sensor`, `entity.cert_expiring_binary_sensor`.

## Decisions Made

- **Stage const additions in Task 2** rather than Task 3 — `cert_coordinator.py`'s `mypy --strict` gate requires the 3 consts to be defined for the file to typecheck in isolation. Documented as a deviation.
- **No `# noqa: ASYNC109` on sync functions** — `fetch_cert_info` and `_fetch_cert_raw` are sync; ASYNC109 only fires on async functions, so the noqa would be flagged as unused (RUF100). Kept noqa only on `fetch_cert_info_async` (the only async function), where the rule actually fires. Plan said "on both" but ruff 0.15.20 flags the sync-function noqa as unused — deviation documented.
- **No `# noqa: BLE001`** — the project's `select = ["B", "E", "F", "I", "UP", "ASYNC", "SIM", "RUF"]` does not include `BLE` (flake8-blind-except), so `# noqa: BLE001` is flagged as `non-enabled` by RUF100. Removed; the comment about "last-resort catch-all by design" remains.
- **Centralized SSLContext construction in `_open_tls_connection`** — the spike-validated trio (PROTOCOL_TLS_CLIENT + check_hostname=False + load_default_certs) is the single source of truth; `_fetch_cert_raw` and `fetch_cert_info` both go through it.
- **TimeoutError (stdlib) catch, not asyncio.TimeoutError** — since Python 3.11 the latter is an alias for the former; the stdlib name covers both.

## Deviations from Plan

### Auto-fixed Issues

**1. [Plan order] Staged const.py additions in Task 2 commit (not Task 3)**
- **Found during:** Task 2 (cert_coordinator.py mypy --strict gate)
- **Issue:** Plan said consts land in Task 3 step 3a, but Task 2's mypy --strict acceptance criterion requires cert_coordinator.py to typecheck in isolation, which needs `DEFAULT_TLS_CERT_COOLDOWN` / `TLS_HANDSHAKE_TIMEOUT` / `TLS_SEMAPHORE` to be defined.
- **Fix:** Added the three consts to const.py as part of Task 2's commit (alongside cert_coordinator.py). Task 3's commit then wires the integration against the now-present consts.
- **Files modified:** `custom_components/traefik/const.py`
- **Verification:** `uv run mypy --strict custom_components/` clean; Task 2 acceptance criteria pass.
- **Committed in:** `b4f9332` (Task 2 commit)

**2. [Plan directive] Dropped `# noqa: ASYNC109` from sync functions**
- **Found during:** Task 1 (ruff RUF100 on initial tls.py)
- **Issue:** Plan step 14 said to add `# noqa: ASYNC109` to the `timeout` parameter on BOTH `fetch_cert_info` (sync) and `fetch_cert_info_async` (async). Ruff 0.15.20's RUF100 flags the sync-function noqa as "unused" because ASYNC109 only fires on async functions.
- **Fix:** Kept the noqa only on `fetch_cert_info_async` (the only async function, where the rule actually fires); removed from the two sync functions.
- **Files modified:** `custom_components/traefik/tls.py`
- **Verification:** `uv run ruff check custom_components/traefik/tls.py` clean.
- **Committed in:** `74e7c02` (Task 1 commit)

**3. [Plan directive] Dropped `# noqa: BLE001` from cert_coordinator.py and __init__.py**
- **Found during:** Task 2 + Task 3 (ruff RUF100 on initial cert_coordinator.py and __init__.py)
- **Issue:** Plan didn't explicitly ask for BLE001 noqa, but it was carried over from the spike prototype. Project's `select = ["B", "E", "F", "I", "UP", "ASYNC", "SIM", "RUF"]` does not include the BLE prefix, so `# noqa: BLE001` is flagged as "non-enabled" by RUF100.
- **Fix:** Removed the noqa; the explanatory comment about "final-resort catch-all" remains.
- **Files modified:** `custom_components/traefik/cert_coordinator.py`, `custom_components/traefik/__init__.py`
- **Verification:** `uv run ruff check custom_components/` clean.
- **Committed in:** `b4f9332` (Task 2) and `f7adcf4` (Task 3)

### Out-of-scope (not modified)

- **`custom_components/traefik/config_flow.py`** — has pre-existing ruff format diff that exists in the codebase prior to this plan. The orchestrator's `ruff format --check` reports it but it is not introduced by this plan. Left untouched per scope boundary (auto-fix only directly-caused issues).

---

**Total deviations:** 3 auto-fixed (1 plan order, 2 plan-directive removals for unused/non-enabled noqa)
**Impact on plan:** All deviations necessary to satisfy ruff RUF100 with the project's actual lint configuration. No scope creep; the const additions are the pre-condition for Task 2's mypy gate.

## Issues Encountered

- **Pre-existing `config_flow.py` format diff** — `ruff format --check` reports a would-reformat diff in `config_flow.py` that pre-dates this plan (verified via `git stash`). Not in scope; documented in the deviations section.
- **`is_error` and `Any` imports unused** — initial cert_coordinator.py had `is_error` (was only used in spike prototype; `_probe` builds the error dict directly) and `Any` (not actually referenced). Ruff F401 flagged them. Removed.
- **`zip()` needed `strict=False`** — B905 requires explicit `strict=` on `zip()`. Added `strict=False` (intentional, well-bounded by `_async_update_data`).
- **`asyncio.TimeoutError` redundant** — UP041 flagged `except (TimeoutError, asyncio.TimeoutError)` as redundant since 3.11 makes them aliases. Reduced to single `except TimeoutError`.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Phase 3 03-02 (entity platforms) is unblocked. The plan can:
  - Read `coordinator.cert_coordinator.data` for per-host `CertInfo | CertError` entries
  - Read `coordinator.cert_coordinator.threshold_days` for the `is_on` predicate
  - Use the `http_routers_tls` category in `_CATEGORY_TO_MODEL` for the per-device model label
  - Read `entity.cert_timestamp_sensor` / `entity.cert_expiring_binary_sensor` / `exceptions.cert_unavailable` translation keys
- Phase 3 03-03 (test surface) is unblocked. The plan can:
  - Drive the hostname-collect path via `_collect_hosts_from_main_coordinator` against a `MockConfigEntry` with synthetic `http_routers`
  - Probe `_probe` against a real `127.0.0.1:1` to assert the `refused`/`oserror` error path
  - Assert `async_set_threshold` flips `threshold_days` and triggers `async_update_listeners` without re-handshaking
- The cert-coordinator first refresh shield (`try/except` + `async_set_updated_data({})` fallback) means the integration can be enabled with no TLS-traffic-bearing Traefik and still come up cleanly.

## Self-Check

- [x] `custom_components/traefik/tls.py` exists, exports all 10 names, ruff + mypy --strict clean
- [x] `custom_components/traefik/cert_coordinator.py` exists, exports `CertCoordinator`, ruff + mypy --strict clean
- [x] `grep -c cryptography custom_components/traefik/tls.py` = 0
- [x] `uv run pytest tests/ -q` — 40 tests pass (no regressions)
- [x] `uv run ruff check custom_components/` — clean
- [x] `uv run mypy --strict custom_components/` — Success: no issues found in 11 source files
- [x] `python -c "import json; json.load(open('custom_components/traefik/strings.json'))"` — valid JSON, 3 new keys present
- [x] `manifest.json` `requirements` still `[]` (no cryptography)
- [x] `entry.runtime_data` still points at the main `TraefikCoordinator`; `cert_coordinator` is a sibling attribute (PITFALLS #6)

---

*Phase: 03-tls-certificate-expiry*
*Completed: 2026-07-06*
