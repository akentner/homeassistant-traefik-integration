---
phase: 03-tls-certificate-expiry
plan: 03
subsystem: testing
tags: [pytest, mypy, ruff, tls, openssl, asyncio, threading, monkeypatch, fixture]

# Dependency graph
requires:
  - phase: 03-01
    provides: "tls.py (parse_not_after, fetch_cert_info, fetch_cert_info_async, _fetch_cert_raw, CertInfo, CertError, _hostname_matches_san, log throttle) + cert_coordinator.py (CertCoordinator Semaphore(4) + timeout(5s) + async_set_threshold + _collect_hosts_from_main_coordinator)"
  - phase: 03-02
    provides: "TraefikCertTimestampSensor + TraefikCertExpiryBinarySensor + shared _cert_cache_availability helper + D-04 always-on attributes + D-08 live re-eval + D-03 default=True inversion pin"
provides:
  - "TEST-04 contract: 6 known notAfter formats (canonical / no-tz / double-space / Python docs example / leap day / year boundary) + 5 invalid formats (empty / ISO-8601 / garbage / dash / slash) covered by parametrized tests in test_tls.py"
  - "Real mock TLS server fixture (mock_certificate_server) — openssl-generated CA + server cert, asyncio TLS server on 127.0.0.1:0, stdlib SSLContext.load_cert_chain chain, monkey-patched SSLContext.load_verify_locations into the production _open_tls_connection so the handshake succeeds against the throwaway CA"
  - "CertCoordinator lifecycle coverage: Semaphore(4) + timeout(5s) defaults, _probe classifies TimeoutError vs Exception correctly, async_set_threshold mutates + calls async_update_listeners exactly once, _async_update_data fans out via asyncio.gather, _collect_hosts_from_main_coordinator pulls union of tls.domains[].main + sans[] + Host() rule matches (BLOCKER #1 fix pin)"
  - "Entity state-derivation coverage: TraefikCertTimestampSensor (13 tests) + TraefikCertExpiryBinarySensor (18 tests) covering native_value / is_on / available / D-04 days_until_expiry always-on / D-08 live re-eval / D-03 default=True inversion pin (Phase 2 M-12 default=False inverted)"
  - "Total test count 40 → 123 (83 new tests, 207% growth)"
affects:
  - Phase 4 (CI gates run on the extended suite; new tests must match the existing MagicMock-coordinator pattern for entity tests, asyncio.to_thread pattern for blocking I/O tests, and the socket_enabled fixture transitively for real-network tests)

# Tech tracking
tech-stack:
  added:
    - pytest_asyncio.fixture (mock_certificate_server async fixture decorated)
    - asyncio.to_thread (test seam to drive blocking fetch_cert_info from async tests without starving the asyncio TLS server's accept path)
    - openssl CLI (per-test CA + leaf cert generation via subprocess.run)
    - tempfile.TemporaryDirectory (cert + key isolation; auto-cleanup at fixture exit)
    - pathlib.Path (cert chain composition: leaf.pem + ca.crt → chain.pem)
  patterns:
    - "asyncio.to_thread wrapping of sync fetch_cert_info in async tests — preserves the production pattern (which wraps sync fetch_cert_info for the same reason: HA event loop responsiveness)"
    - "monkeypatch.setattr on _open_tls_connection to inject the test CA into the trust store — keeps the production code untouched while enabling a real TLS handshake against an openssl-generated cert"
    - "module-level pre-import of custom_components.traefik.tls in conftest.py — sidesteps the pytest-homeassistant-custom-component namespace hijack of 'custom_components' (its testing_config stub) that would otherwise shadow the production module for in-fixture imports"
    - "socket_enabled transitive fixture dependency (no default value) — declares required pytest-socket enable_socket so the mock_certificate_server fixture + real-network graceful-error tests bypass pytest_socket.GuardedSocket"
    - "HA CachedProperties metaclass: _attr_<name> → __attr_<name> access pattern for class-level boolean attribute assertions (entity_registry_enabled_default)"
    - "MagicMock with .data dict + .threshold_days + .get_threshold = MagicMock(return_value=…) wiring for entity tests — exercises the production code's exact access pattern without HA's DataUpdateCoordinator lifecycle"

key-files:
  created:
    - tests/test_tls.py (33 tests)
    - tests/test_cert_coordinator.py (19 tests)
    - tests/test_sensor_tls.py (13 tests)
    - tests/test_binary_sensor_tls_expiring.py (18 tests)
  modified:
    - tests/conftest.py (added mock_certificate_server async fixture + CA-signed cert chain generator + custom_components.traefik.tls module-level pre-import)

key-decisions:
  - "Generated CA-signed server cert (not plain self-signed) because production _open_tls_connection uses PROTOCOL_TLS_CLIENT + load_default_certs which rejects self-signed chains. The per-test CA is monkey-patched into the production SSLContext via _open_tls_connection override so the handshake completes successfully."
  - "Used asyncio.to_thread to drive sync fetch_cert_info from async tests — passing through the event loop would block the asyncio.start_server's accept path, causing the handshake to hang (event-loop starvation)."
  - "Pre-imported custom_components.traefik.tls at module level in conftest.py because pytest-homeassistant-custom-component's namespace package for 'custom_components' would otherwise shadow the production module for any in-fixture import. Module-level pre-import binds the production tls module before the namespace hijack takes effect."
  - "DNS failure test (test_unresolvable_host_returns_dns) monkey-patches socket.getaddrinfo directly because the HA plugin replaces getaddrinfo with a RuntimeError-raising stub for non-IP hostnames (DNS restriction in tests). The monkey-patched version raises the real socket.gaierror so the production 'except socket.gaierror' branch is exercised, not the catch-all 'unknown'."
  - "Refused/oserror graceful-error tests use the socket_enabled fixture (not pytest_socket default) — the test framework's pytest_socket.GuardedSocket blocks all AF_INET socket creation by default; socket_enabled calls enable_socket() to restore the real socket module so connection attempts to 127.0.0.1 actually fire."
  - "Pinned TraefikCertExpiryBinarySensor._attr_entity_registry_enabled_default = True via the private __attr_<name> name (HA's CachedProperties metaclass renames class-level _attr_* attributes to __attr_*); the public class-level read returns a property descriptor, not the underlying value."
  - "Patched _open_tls_connection (not _fetch_cert_raw or fetch_cert_info) so the test exercises the production entry point's connection logic and CA injection happens at exactly the right layer — _open_tls_connection is the only function that creates the SSLContext where load_default_certs is called."

patterns-established:
  - "Pattern 1: Async TLS server fixture. Use pytest_asyncio.fixture with socket_enabled (no default) as transitive dependency; bind to ('127.0.0.1', 0) for ephemeral port assignment; the asyncio.start_server handler may be a no-op (the test only needs the handshake). Cleanup: server.close() + await server.wait_closed() in the generator's finally block."
  - "Pattern 2: asynio.to_thread for sync-only test seams. When a sync production function blocks on I/O (socket create, file read), wrap the call in asyncio.to_thread inside an async test to avoid event-loop starvation. Mirrors the production code's own use of asyncio.to_thread."
  - "Pattern 3: MagicMock coordinator with explicit return_value wiring. For entity tests, MagicMock with .data dict + .threshold_days + .get_threshold = MagicMock(return_value=threshold_days) exercises the production access pattern exactly. For test-specific method overrides (e.g., c.async_update_listeners = MagicMock()), assign at the instance level — class-level patching is fragile."
  - "Pattern 4: Monkeypatch.setattr on module-private names for trusted CA injection. Production code calls ssl.SSLContext.load_default_certs(); tests need to also load a test CA. Override _open_tls_connection (or similar module-private seam) via monkeypatch.setattr; revert at fixture teardown is automatic."

requirements-completed: [TEST-04]

# Metrics
duration: 25min
completed: 2026-07-06
---

# Phase 3 Plan 03: TEST-04 Test Surface Summary

**Adds 83 hermetic tests covering TLS format-string parsing, graceful error paths, bounded-concurrency probe lifecycle, threshold live re-eval, hostname union extraction, and entity state derivation for the new per-host `sensor.<host>_cert` and `binary_sensor.<host>_expiring` pairs — Phase 3 closes at 123 tests passing (3× growth).**

## What was built

### `tests/conftest.py` extension
- New imports: `asyncio`, `socket`, `ssl`, `subprocess`, `tempfile`, `pytest_asyncio` (test-only seam; production fixtures remain unchanged).
- **Module-level pre-import of `custom_components.traefik.tls`** — required to bind the production module before pytest-homeassistant-custom-component's testing_config namespace hijacks the `custom_components` name.
- New `mock_certificate_server` async fixture that spins up a stdlib TLS server on 127.0.0.1:0 with an openssl-generated CA-signed cert chain (CA → server leaf with `subjectAltName=DNS:<sni_hostname>`). Monkey-patches the production `_open_tls_connection` to also `load_verify_locations(cafile=<test CA>)` so the handshake validates against the throwaway CA. Yields `(host, port, sni_hostname)` so tests can both happy-path probe AND probe with a mismatched SNI to exercise the `san_mismatch=True` spike-006 detection.

### `tests/test_tls.py` — TEST-04 contract (33 tests)
- **6 parametrized known shapes** (canonical `Nov 15 12:00:00 2025 GMT`, no-tz variant, double-space-day, Python-docs example, leap day, year boundary) — all parse to UTC tzinfo.
- **5 parametrized invalid shapes** (empty, garbage, ISO-8601, dash-words, US slash) — all raise `ValueError("Unknown notAfter")`.
- **24h parse-failure log throttle** — three calls to `_log_parse_failure_once` for the same host produce only one `_LOGGER.debug` call; per-host independence (host-a and host-b each get their own first-call log line).
- **8 SAN match cases** — exact, wildcard, multi-label wildcards, narrow-wildcard, no-match, multi-SAN hit, case-insensitive, plus **adversarial suffix** (`*.example.com` MUST NOT match `foo.example.com.evil.org`) and **empty-SAN** rejection.
- **3 `is_error` edges** — `CertError` dict, `CertInfo` dataclass, empty dict.
- **3 graceful-error paths** — port 1 → `refused`/`oserror`, unresolvable `.invalid` TLD → `dns` (via monkey-patched `socket.getaddrinfo` raising `gaierror` to bypass HA's DNS-restriction stub), wrong-port → `refused`/`oserror`.
- **2 mock-server handshake tests** — happy path cert fetch via real TLS handshake (90 days_until_expiry → ~365), and SNI-mismatch detection via the test-only `_fetch_cert_raw(host="127.0.0.1", sni="wrong.example.test", …)` seam (cert's SAN does not include `wrong.example.test` → `san_mismatch=True`).

### `tests/test_cert_coordinator.py` — CertCoordinator lifecycle (19 tests)
- Defaults: `update_interval=6h`, `Semaphore(4)`, `timeout=5s`, `config_entry` wired via `super().__init__(config_entry=entry)` (BLOCKER #1 fix pin), `threshold_days` sourced from `entry.options` with `DEFAULT_TLS_WARN_DAYS=14` fallback.
- Probe: `TimeoutError` → `CertError(error="timeout")`, generic `Exception` → `CertError(error="unreachable")` — never raises (D-10 contract).
- Threshold: `async_set_threshold(7)` mutates AND calls `async_update_listeners()` exactly once (D-08 live re-eval pin); `get_threshold()` reader mirrors the stored value.
- Data path: `_async_update_data` fans out via `asyncio.gather`, success+error rows merge (D-06), empty routers + missing runtime_data both return `{}` without raising.
- Hostname extraction (BLOCKER #1 fix validation): union of `tls.domains[].main` + `sans[]` (string OR list per Traefik v3) + `Host(\`x\`)` rule matches, lowercased + deduped, skips `@<provider>` items (via `filter_internal_items`), skips TLS-but-no-host (CONTEXT.md out-of-scope wildcard certs).
- `_HOST_FROM_RULE` regex extraction edge cases.

### `tests/test_sensor_tls.py` — TraefikCertTimestampSensor (13 tests)
- `native_value` reads `not_after` from cache; None on cold start (CertError rows do NOT get a timestamp sensor per production `async_setup_entry`).
- `available` delegates to `_cert_cache_availability` (cold start → False; CertInfo → True).
- `extra_state_attributes` ALWAYS surfaces `days_until_expiry` (D-04 contract) — None on cold-start, integer on success, `last_error` flows through verbatim.
- `subject`, `issuer`, `san`, `san_mismatch`, `host`, `port`, `fetched_at` attributes populated.
- `unique_id` / `entity_id` format pin.
- `device_info` clusters on the new `http_routers_tls` per-category device.

### `tests/test_binary_sensor_tls_expiring.py` — TraefikCertExpiryBinarySensor (18 tests)
- `is_on` signed-int semantics: 4 parametrized cases (30/False, 14/True, 10/True, -1/True) — boundary, under, breach all return True.
- `is_on` returns `None` (NOT False) on `CertError` rows and cold start — HA renders "unknown", not a misleading "off".
- `available` delegated to `_cert_cache_availability` (CertInfo → True; CertError + cold start → False).
- `days_until_expiry` + `threshold_days` always present (D-04 + D-08).
- **D-08 live re-eval** — mutating `coordinator.threshold_days` flips `is_on` without a re-handshake; BOTH raise-then-lower AND lower-then-raise scenarios exercised.
- **D-03 inversion pin** — `TraefikCertExpiryBinarySensor._attr_entity_registry_enabled_default` is True (Phase 3 cert alarm always-on) vs `TraefikAnyRouterFailingBinarySensor` default False (Phase 2 router-failure alarm opt-in). Intentional divergence from CONTEXT.md `<specifics>`.
- `device_class == PROBLEM` (D-14).

## Deviations from Plan

### Auto-fixed Issues (Rules 1-3)

**1. [Rule 3 — blocking] HA plugin DNS restriction routed around `CertError(error="dns")` test**
- **Found during:** Task 1
- **Issue:** `pytest-homeassistant-custom-component` patches `socket.getaddrinfo` to raise a generic `RuntimeError("DNS resolution disabled in tests")` for any non-IP hostname, which would land in the production `except Exception` catch-all and surface as `CertError(error="unknown")` — not the `error="dns"` the plan expected.
- **Fix:** Test now monkey-patches `socket.getaddrinfo` directly with a `socket.gaierror`-raising function, ensuring the production `except socket.gaierror` branch is exercised.
- **Files modified:** `tests/test_tls.py`
- **Commit:** cb8308e

**2. [Rule 3 — blocking] pytest_socket.GuardedSocket blocks AF_INET socket creation**
- **Found during:** Task 1 — refused-port + wrong-port tests failing
- **Issue:** `pytest-homeassistant-custom-component` calls `pytest_socket.disable_socket(allow_unix_socket=True)` which replaces `socket.socket` with a guard that raises `SocketBlockedError` for any non-Unix family. Tests connecting to `127.0.0.1:1` or `127.0.0.1:99999` to verify refused/wrong-port error classification fail because the socket itself can't be created.
- **Fix:** Tests now request the `socket_enabled` pytest_socket fixture (which calls `enable_socket()` to restore the real socket module). The teardown assertion `assert not HASocketBlockedError.instances` is satisfied because successful connections don't raise.
- **Files modified:** `tests/test_tls.py`
- **Commit:** cb8308e

**3. [Rule 1 — bug] sync `fetch_cert_info` blocks asyncio TLS server accept path**
- **Found during:** Task 1 — mock-server handshake tests timing out after 5s
- **Issue:** `fetch_cert_info` is a sync function that calls `socket.create_connection` which blocks the event loop. When called from an async test directly, the asyncio.start_server's accept loop can't fire because the loop is stuck in the blocking call. Result: handshake times out.
- **Fix:** Mock-server tests now drive `fetch_cert_info` / `_fetch_cert_raw` via `asyncio.to_thread` — the same pattern production uses internally via `fetch_cert_info_async`. Threading is what the production code anticipates.
- **Files modified:** `tests/test_tls.py`
- **Commit:** cb8308e

**4. [Rule 3 — blocking] production SSLContext rejects self-signed test cert**
- **Found during:** Task 1 — mock-server happy path returns `oserror("CERTIFICATE_VERIFY_FAILED")` instead of successful handshake
- **Issue:** Production `_open_tls_connection` uses `PROTOCOL_TLS_CLIENT` + `ctx.load_default_certs()` which trusts only public CAs. The openssl-generated self-signed test cert isn't in the default CA bundle, so the handshake fails at cert verification.
- **Fix:** Fixture generates a throwaway CA, signs the server leaf with it, AND monkey-patches production `_open_tls_connection` to additionally call `ctx.load_verify_locations(cafile=<test CA>)` so the test CA is in the trust store. The handshake then completes normally.
- **Files modified:** `tests/conftest.py`
- **Commit:** cb8308e

**5. [Rule 3 — blocking] pytest_homeassistant_custom_component namespace hijacks `custom_components`**
- **Found during:** Task 1 — in-fixture `from custom_components.traefik import tls` resolves to the HA testing_config stub, not the production module
- **Issue:** pytest-homeassistant-custom-component ships a namespace package at `testing_config/custom_components/` which takes precedence over our project's `custom_components/` directory for in-fixture imports (Python loads the first `custom_components/__init__.py` it finds).
- **Fix:** conftest.py pre-imports `from custom_components.traefik import tls as _tls_module` at module level — this binds the production module to `sys.modules` before any in-fixture import resolves. In-fixture references then use `_tls_module._open_tls_connection` instead of re-importing.
- **Files modified:** `tests/conftest.py`
- **Commit:** cb8308e

### Other Adjustments

- **Test 1 (`async_set_threshold` notification)** — the plan suggested `c.async_update_listeners = AsyncMock()` but `DataUpdateCoordinator.async_update_listeners` is a SYNC method (despite its `async_` prefix — verified via `inspect.getsource`) that schedules callbacks via `self._listeners`. Switched to `MagicMock()` and asserted on `call_count` instead of `await_count`.
- **Test 2 (`fetch_cert_info_async` mock signature)** — production calls `fetch_cert_info_async(host, port, timeout=self._timeout, …)`; the original mock `fake_probe(host)` only took `host`, raising `TypeError("unexpected keyword argument 'timeout'")` surfaced as `CertError(error="unreachable")`. Updated the mock signature to `(host, port=443, **_kwargs)` to accept the production call shape.
- **Test 3 (`MagicMock.get_threshold` mock)** — production `extra_state_attributes` reads via `self._coordinator.get_threshold()`, not directly from `threshold_days`. The mock coordinator's default `MagicMock()` returns a `MagicMock` instance for `get_threshold()` which doesn't compare equal to an int. Added explicit `coord.get_threshold = MagicMock(return_value=threshold_days)` wiring.
- **Threshold state-flip test comments** — original comments had operator precedence inverted (`5 < 14` is False but `info.days_until_expiry (=5) <= threshold (=14)` is True → alarm ON, not OFF). Rewrote the comments and removed the contradictory assertions.
- **TraefikCertExpiryBinarySensor `_attr_entity_registry_enabled_default` access** — HA's `CachedProperties` metaclass moves `_attr_*` class attributes to `__attr_*` private names and wraps them in `property`. The class-level read returns a property descriptor, not the underlying boolean. Tests read `cls.__dict__.get("__attr_entity_registry_enabled_default")` to pin the actual value.
- **TraefikCertTimestampSensor error-row scenario** — production `async_setup_entry` (`sensor.py`:142-144) only creates timestamp sensors for `CertInfo` cache rows. Error rows get a `binary_sensor` only. Removed error-row assertion paths from `test_sensor_tls.py`; the analog "cold-start" path tests the same `days_until_expiry=None` D-04 contract. The error-row attribute coverage lives in `test_binary_sensor_tls_expiring.py`.

### Out-of-scope (NOT addressed per deviation scope rules)

- `custom_components/traefik/config_flow.py` ruff format diff (pre-existing from Phase 2-02 — `git stash` test confirmed the file would also need reformatting before this plan started). Not introduced by 03-03; out of scope.

## Files

| Path | Status | Purpose |
|------|--------|---------|
| `tests/conftest.py` | modified | +mock_certificate_server async fixture, +CA-signed cert chain generator, +module-level custom_components.traefik.tls pre-import |
| `tests/test_tls.py` | new | 33 TEST-04 contract tests: format parser (6 good + 5 bad), log throttle, hostname match, is_error, graceful errors, mock-server handshake |
| `tests/test_cert_coordinator.py` | new | 19 CertCoordinator lifecycle tests: defaults, probe error classification, async_set_threshold, _async_update_data, _collect_hosts_from_main_coordinator |
| `tests/test_sensor_tls.py` | new | 13 TraefikCertTimestampSensor state-derivation + D-04 contract pin |
| `tests/test_binary_sensor_tls_expiring.py` | new | 18 TraefikCertExpiryBinarySensor state-derivation + D-08 live re-eval + D-03 inversion pin |

## Verification

- `uv run pytest tests/ -q --no-header` → 123 passed
- `uv run pytest tests/test_tls.py -v --no-header` → 33 passed
- `uv run pytest tests/test_cert_coordinator.py -v --no-header` → 19 passed
- `uv run pytest tests/test_sensor_tls.py tests/test_binary_sensor_tls_expiring.py -v --no-header` → 31 passed
- `uv run ruff check custom_components/ tests/` → all checks passed
- `uv run ruff format --check tests/` → all formatted
- `uv run mypy --strict custom_components/` → success, no issues
- `uv run mypy --strict tests/test_tls.py` → success, no issues
- `uv run mypy --strict tests/test_cert_coordinator.py` → success, no issues
- `uv run mypy --strict tests/test_sensor_tls.py tests/test_binary_sensor_tls_expiring.py` → success, no issues

## Self-Check: PASSED

All claimed files exist on disk (verified via `ls` post-write). All claimed commits exist in `git log --oneline` (verified via `git log`). REQUIREMENTS.md update left to STATE.md commit step.
