<!-- GSD:project-start source:PROJECT.md -->
## Project

**Home Assistant Traefik Integration**

A custom Home Assistant integration that connects to a Traefik reverse proxy
and surfaces its operational state inside Home Assistant — routers, services,
entrypoints, middleware, and TLS certificate health are exposed as entities
the user can monitor, automate against, and visualize in dashboards.

Built for self-hosters running Traefik in front of their Home Assistant
and other homelab services who want a single pane of glass for reverse-proxy
health instead of having to log into the Traefik dashboard separately.

**Core Value:** If nothing else works, the user must be able to see — at a glance inside
Home Assistant — which Traefik routers are enabled, which are failing,
and which TLS certificates are expiring soon.

### Constraints

- **Tech stack**: Python 3.12+, Home Assistant Core (min version 2025.4.x),
  `aiohttp` for HTTP, HACS-compatible.
- **Distribution**: HACS default repository structure (`hacs.json`,
  `info.md`, `README.md`, version tags via releases).
- **Compatibility**: Must work against Traefik v2.11+ and v3.x.
- **Performance**: API polling must not exceed one call-set per scan
  interval (default 30s) per integration instance; multiple endpoints
  fetched in parallel via `asyncio.gather`.
- **Security**: API tokens never logged; integration must support
  self-signed certificates via user option.
- **No external service dependencies**: integration talks only to the
  Traefik API the user points it at — no SaaS, no telemetry.
<!-- GSD:project-end -->

<!-- GSD:stack-start source:research/STACK.md -->
## Technology Stack

## Executive Summary
## Recommended Stack
### Core Technologies
| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| Python | `>=3.12` (HA 2025.4 ships 3.13) | Runtime | PROJECT.md mandate; HA 2025.4 bundles CPython 3.13 |
| Home Assistant Core | `>=2025.4.0` | Framework | PROJECT.md mandate; gives `ConfigEntry.runtime_data`, PEP-695 syntax, `asyncio.timeout` |
| `aiohttp` | `>=3.13` (HA bundles 3.14.x) | HTTP client | HA uses aiohttp everywhere; HA's `async_get_clientsession(hass)` is the Platinum-quality shared session |
| `voluptuous` | bundled by HA | Config-flow schema | Already required by HA — no manifest entry |
| `cryptography` (HA-bundled) | bundled by HA | (only if TLS parsing needs more than `ssl`) | HA ships `cryptography==46.0.x` already; no manifest entry unless explicitly imported |
### Manifest (`custom_components/traefik/manifest.json`)
| Field | Value | Rationale |
|-------|-------|-----------|
| `domain` | `traefik` | Matches folder name. Underscore-style only (no hyphens). |
| `integration_type` | `service` | One Traefik instance per config entry. `hub` is for "many devices behind one gateway" (Hue); here the gateway itself *is* the integration. The user's `gatus` integration (multi-endpoint polling) is correctly `hub`; this is correctly `service`. |
| `iot_class` | `local_polling` | Traefik runs on the user's homelab; integration talks to it directly. **Not** `cloud_polling` (no third-party SaaS). If a user does point at a remote Traefik, the same `local_polling` label still fits — it's about who owns the proxy, not the network hop. |
| `after_dependencies` | `["http"]` | Ensures HA's shared `aiohttp.ClientSession` is set up before our `async_setup_entry`. Mirrors `kroki` integration. |
| `requirements` | `[]` | Everything we need is bundled in HA. |
| `version` | CalVer like `2025.7.0` or `1.0.0` | HACS validates this is a valid [AwesomeVersion](https://github.com/ludeeus/awesomeversion) string. |
| `codeowners` | `["@akentner"]` | GitHub usernames; required by HACS. |
| `documentation` | GitHub URL | Required by HACS. **Do NOT point at home-assistant.io** — that's for core integrations. |
| `issue_tracker` | GitHub issues URL | Required by HACS. |
| **No `quality_scale`** | — | Custom integrations trigger a `hassfest` warning if this is set. The user's `gatus` and `kroki` correctly omit it. |
### `hacs.json` (repository root)
| Field | Value | Rationale |
|-------|-------|-----------|
| `name` | `"Traefik"` | Display name in HACS UI. Required. |
| `homeassistant` | `"2025.4.0"` | Matches PROJECT.md mandate; HACS will refuse older installs. |
| `hacs` | `"2.0.5"` | Current HACS minimum as of 2025; matches `gatus` integration. |
| **No `filename` / `zip_release`** | — | Standard folder-based distribution; HACS will read `custom_components/traefik/`. |
| **No `country`** | — | Not a regional integration. |
### HACS Repository Layout
### Supporting Libraries (all from `homeassistant.*` — no `pip install`)
| Library | Import | Purpose | Notes |
|---------|--------|---------|-------|
| `DataUpdateCoordinator` | `homeassistant.helpers.update_coordinator` | Single polling point; fans updates to entities | Required by quality-scale **Bronze**. |
| `CoordinatorEntity` | `homeassistant.helpers.update_coordinator` | Base class for entities reading from coordinator | Sets `should_poll=False`, wires availability. |
| `ConfigFlow` | `homeassistant.config_entries` | UI setup wizard (CORE-01) | Subclass; `domain=` class keyword. |
| `OptionsFlow` | `homeassistant.config_entries` | Re-configure interval & TLS warning threshold (CFG-01) | Bound via `entry.add_update_listener`. |
| `SensorEntity` / `BinarySensorEntity` | `homeassistant.components.{sensor,binary_sensor}` | Entity base classes | Standard. |
| `DeviceInfo` | `homeassistant.helpers.device_registry` | Group entities under one "Traefik" device | Traefik is the device; routers/entrypoints are entities. |
| `DeviceEntryType.SERVICE` | `homeassistant.helpers.device_registry` | Marker for service-type devices | Recommended since `integration_type: "service"`. |
| `async_get_clientsession` | `homeassistant.helpers.aiohttp_client` | Shared aiohttp session | **Always** use this; never create `aiohttp.ClientSession()` yourself. |
| `ConfigEntryNotReady` | `homeassistant.exceptions` | First-refresh failure → HA auto-retries | For transient errors only. |
| `ConfigEntryAuthFailed` | `homeassistant.exceptions` | 401/403 → triggers reauth flow | For bad API keys. |
| `UpdateFailed` | `homeassistant.helpers.update_coordinator` | Coordinator update error | Wraps any exception from `_async_update_data()`. |
| `PLATFORMS` | n/a (constant) | Tuple `Platform.SENSOR, Platform.BINARY_SENSOR` | Forwarded to platform setup in `__init__.py`. |
| `selector` | `homeassistant.helpers.selector` | Type-safe config-flow form fields | Prefer `TextSelector`, `NumberSelector`, `BooleanSelector`. |
| `voluptuous` | re-exported via `homeassistant.helpers.config_validation` as `cv` | Schema validation | Use `cv.string`, `cv.port`, `cv.url` etc. |
### Traefik API Client (built-in, not a PyPI package)
- `aiotraefik` on PyPI → **404 not found**. Verified directly.
- `traefik` on PyPI → also not found.
- The Traefik API surface used by this integration is small (~6 endpoints, all JSON) — a 100-LOC client is smaller than a dependency to maintain.
| Endpoint | Returns | Used for |
|----------|---------|----------|
| `GET /api/version` | `{Version, Codename, StartDate}` | DIAG sensor; auth verification in config flow |
| `GET /api/entrypoints` | `[{name, address, ...}]` | CORE-05 entrypoint sensors |
| `GET /api/http/routers` | `[{name, rule, service, status, tls, ...}]` | CORE-04 router binary sensors; TLS-01 router selection |
| `GET /api/http/services` | `[{name, loadbalancer, status, ...}]` | CORE-06 service sensors |
| `GET /api/http/middlewares` | `[{name, type, ...}]` | DIAG-01 middleware count |
| `GET /api/overview` | `{http:{routers, services, middlewares}, ...}` | DIAG-01 top-level aggregator |
| `POST /api/http/routers/refresh` | `{}` | DIAG-03 reload service |
### Dev / Test Toolchain
| Tool | Version | Purpose | Notes |
|------|---------|---------|-------|
| `pytest` | `>=9.0.0` | Test runner | Matches what `pytest-homeassistant-custom-component` 0.13.x pulls in. |
| `pytest-asyncio` | `>=1.4.0` | Async tests | `asyncio_mode = "auto"` (see config below). |
| `pytest-homeassistant-custom-component` | `>=0.13.345` (latest 2026-07-04) | HA-specific fixtures: `hass`, `MockConfigEntry`, `aioclient_mock`, `snapshot` | Tracks HA Core daily. **Pin a floor, not a ceiling** — let it follow HA's release cadence. |
| `pytest-cov` | `>=7.0.0` | Coverage | HA quality-scale target is **95%+** per module. |
| `aioresponses` (optional) | `>=0.7` | Mock aiohttp requests in unit tests | Alternative to `aioclient_mock`. Pick one. |
| `ruff` | `>=0.15.0` | Lint + format | Replaces `flake8`/`isort`/`black`. |
| `mypy` | `>=2.1.0` | Strict type checking | `strict = true` in pyproject. |
| `pre-commit` | latest | Run ruff + hassfest locally | |
| `uv` | latest | Python dep manager (matches user's `~/.local/bin/uv`) | Replaces `pip`/`venv`. |
### `pyproject.toml` (dev dependencies only)
### Architecture Components (no pip install needed)
# coordinator.py — pattern verified against FAADelays/GIOS core integrations
## Alternatives Considered
| Layer | Recommended | Alternative | Why Not |
|-------|-------------|-------------|---------|
| HTTP client | `aiohttp` (via HA shared session) | `httpx` | HA core standardizes on aiohttp; Platinum rule requires web-session injection (only `aiohttp_client.async_get_clientsession` for aiohttp; `create_async_httpx_client` for httpx — but aiohttp is the convention and aiohttp is already bundled). |
| HTTP client | `aiohttp` (via HA shared session) | `requests` | Synchronous; blocks the event loop; explicitly banned by HA Platinum quality scale. |
| Traefik client | **Custom ~150 LOC wrapper** | `aiotraefik` PyPI package | **Does not exist** (404 verified). Even if it did, a thin custom client beats a maintained-dependency tax for 6 JSON endpoints. |
| TLS cert retrieval | Python stdlib `ssl` + `asyncio.open_connection` | `cryptography` library | Stdlib `ssl.SSLContext.get_ca_certs` is enough for `notAfter`; `cryptography` is only needed if we want to validate chains. HA bundles it anyway but we don't import it. |
| Async timeout | `asyncio.timeout()` (stdlib, py3.11+) | `async_timeout` PyPI package | HA Core deprecated `async_timeout` in 2024.x; replaced with stdlib. |
| Runtime data storage | `entry.runtime_data` | `hass.data[DOMAIN][entry_id]` | Deprecated since HA 2024; runtime_data is type-safe with PEP-695 aliases. |
| Quality scale | **Omit** | Set `quality_scale: "silver"` | hassfest **warns** if a custom integration sets this. Quality scale is a core-integration concept. |
| Min HA version | `2025.4.0` | `2025.1.0` or `2024.x` | PROJECT.md mandate. Going lower excludes users on HA 2025.4+ who have `runtime_data` available; going higher fragments the install base. |
| Discovery | **None** | `zeroconf` / `dhcp` | Traefik doesn't advertise `_traefik._tcp.local.` by default; users configure manually. Adding zeroconf is a Phase-2 stretch. |
| HACS channel | Default (latest) repo | "Beta" via `hacs.json` `country`/tags | No need for beta channel at v1.0. |
| Architecture (no TLS file access) | TLS handshake to router hostname | Read `acme.json` from disk | Requires SSH / shared mount / supervisor permissions — out of scope per PROJECT.md ("integration talks only to the Traefik API"). |
## What NOT to Use
| Avoid | Why | Use Instead |
|-------|-----|-------------|
| `requests` / `requests-cache` | Sync; blocks the event loop; banned by HA Platinum scale | `async_get_clientsession(hass)` + aiohttp |
| `httpx` (standalone) | Not the HA integration pattern; requires separate session injection | aiohttp |
| `aiotraefik` | Does not exist on PyPI | Custom aiohttp wrapper in `api.py` |
| `async_timeout` (PyPI) | Deprecated since HA 2024; replaced by stdlib | `asyncio.timeout()` (Python 3.11+) |
| `hass.data[DOMAIN][entry_id]` | Deprecated since HA 2024.x | `entry.runtime_data` (typed via PEP-695 alias) |
| Per-entity `async_update()` | N×N HTTP calls, defeats DataUpdateCoordinator pattern | `DataUpdateCoordinator` + `CoordinatorEntity` |
| `"quality_scale": "silver"` in manifest | hassfest warns for custom integrations | Omit the key |
| `"iot_class": "cloud_polling"` for Traefik | Traefik is the user's own homelab proxy — not a SaaS | `"iot_class": "local_polling"` |
| Creating `aiohttp.ClientSession()` in our code | Breaks HA's cookie/SSL/configured shared session; Platinum rule violation | `async_get_clientsession(hass)` |
| Reading Traefik's `acme.json` from disk | Requires filesystem access the integration does not have; fragile; out of scope | TLS handshake to each router hostname using stdlib `ssl` |
| `_LOGGER.info("…token…")` or similar | Leaks secrets | Use `_LOGGER.debug("…")` only with redacted tokens; better still, never log the token |
| Pinning `requirements: ["aiohttp==3.13.5"]` | HA bundles aiohttp; pinning causes dep conflicts; hassfest may reject duplicate | `requirements: []` |
| Python 3.10 / 3.11 syntax (e.g. `Optional[int]`) | HA 2025.4+ ships Python 3.13; PEP-695 type aliases (`type X = ...`) are available | Modern syntax: `int | None`, `type MyConfigEntry = ConfigEntry[...]` |
| `asyncio.get_event_loop()` | Deprecated in 3.12+; coordinator already has a loop | `asyncio.timeout()` context manager |
| `homeassistant.helpers.aiohttp_client.async_create_clientsession` | Used for **per-integration** sessions with custom SSL/cookies; we want the shared session | `async_get_clientsession(hass)` |
## HACS / Distribution Specifics
### `hacs.json` schema (verified at hacs.xyz/docs/publish/start)
- `homeassistant` uses [AwesomeVersion](https://github.com/ludeeus/awesomeversion) — accepts CalVer (`2025.4.0`) and SemVer (`1.0.0`). Use CalVer to align with HA's release cadence and match the user's `gatus` pattern.
- `hacs: "2.0.5"` is the current HACS minimum (verified from the user's existing `gatus` integration).
- Without `zip_release: true`, HACS reads from the default branch (or the latest GitHub release if releases are published).
### Brand assets (required by HACS)
### GitHub releases (recommended)
### `services.yaml` (for DIAG-03 reload action)
## QA / Lint / CI Stack
| Tool | Where | Notes |
|------|-------|-------|
| `ruff check` + `ruff format` | pre-commit + GitHub Actions | The user's `gatus` config uses `select = ["B","E","F","I","UP","ASYNC","SIM","RUF"]` |
| `mypy --strict` | pre-commit | PEP-695 type aliases work natively on 3.13 |
| `hassfest` | GitHub Actions | Validates `manifest.json` schema; uses `python -m script.hassfest --integration-path custom_components/traefik` |
| HACS Action | GitHub Actions | `hacs/action@main` with category `integration` |
| `pytest` + coverage | GitHub Actions | Quality scale target: **95%+ coverage per module** |
## Version Compatibility
| Component | Compatible With | Notes |
|-----------|-----------------|-------|
| `homeassistant>=2025.4.0` | Python 3.13+ (HA 2025.4 ships 3.13.3) | Use PEP-695 syntax (`type X = ...`, `int | None`) |
| `aiohttp>=3.13` (bundled) | HA Core's pinned `aiohttp==3.13.x` | Don't pin in manifest |
| `pytest-homeassistant-custom-component>=0.13.345` | HA Core 2026.7.1 (latest at fetch time) | Tracks HA daily; pin minimum, let it roll |
| `pytest-asyncio>=1.4.0` | `asyncio_mode = "auto"` | `asyncio_default_fixture_loop_scope = "function"` matches HA's own test config |
| Traefik API client | Traefik v2.11+ and v3.x | PROJECT.md mandate; v1.x is EOL, not targeted |
| `awesomeversion` (HACS validates) | `2025.4.0`, `1.0.0`, `2025.7.0` etc. | Both CalVer and SemVer accepted |
| `ruff>=0.15.15` | Python 3.13 | Enables `UP` rules targeting modern syntax |
## Roadmap Implications (downstream input)
- Cannot ship TLS features until the TLS-handshake helper is built — keep it isolated.
- Config Flow must come **before** Options Flow (Options Flow requires an existing config entry).
- HACS brand assets can land any time but must be there before the first public release.
- Phase 1 — **Standard patterns**, unlikely to need additional research. The DataUpdateCoordinator + config flow pattern is exhaustively documented.
- Phase 3 — **Flagged for deeper research.** TLS handshake + cert chain parsing has subtle pitfalls (SNI, cert chains with multiple certs, hostname mismatch). A `gsd-spike` is recommended before committing to the approach.
- Phase 4 — Light research needed on `homeassistant.components.diagnostics` schema (it's been moving).
## Sources
### HIGH confidence (official documentation)
- [developers.home-assistant.io/docs/creating_integration_manifest](https://developers.home-assistant.io/docs/creating_integration_manifest) — manifest schema (verified Jul 2026)
- [developers.home-assistant.io/docs/config_entries_config_flow_handler](https://developers.home-assistant.io/docs/config_entries_config_flow_handler) — ConfigFlow API (verified Jul 2026)
- [hacs.xyz/docs/publish/start](https://hacs.xyz/docs/publish/start) — hacs.json schema (verified)
- [hacs.xyz/docs/publish/integration](https://hacs.xyz/docs/publish/integration) — repository layout (verified)
- [doc.traefik.io/traefik/reference/install-configuration/api-dashboard](https://doc.traefik.io/traefik/reference/install-configuration/api-dashboard/) — API endpoints (verified Jul 2026)
- [doc.traefik.io/traefik/reference/install-configuration/entrypoints](https://doc.traefik.io/traefik/reference/install-configuration/entrypoints/) — Entrypoint schema (verified)
- [github.com/home-assistant/core/blob/dev/requirements.txt](https://github.com/home-assistant/core/blob/dev/requirements.txt) — HA Core pinned deps (`aiohttp==3.14.1`, `httpx==0.28.1`, `voluptuous==0.15.2`) — verified
- [github.com/MatthewFlamm/pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component) — testing helper; current version `0.13.345` (2026-07-04) — verified
- [github.com/MatthewFlamm/pytest-homeassistant-custom-component/blob/master/requirements_test.txt](https://raw.githubusercontent.com/MatthewFlamm/pytest-homeassistant-custom-component/master/requirements_test.txt) — pinned test deps — verified
- [github.com/MatthewFlamm/pytest-homeassistant-custom-component/blob/master/setup.cfg](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component/blob/master/setup.cfg) — `asyncio_mode = auto`, `asyncio_default_fixture_loop_scope = function` — verified
### HIGH confidence (HA core reference code)
- `homeassistant/components/faa_delays/coordinator.py` — minimal DataUpdateCoordinator pattern with `aiohttp_client.async_get_clientsession` and `asyncio.timeout`
- `homeassistant/components/gios/coordinator.py` — same pattern with `aiohttp.client_exceptions` handling and `UpdateFailed` with translation keys
- `homeassistant/components/frontend/__init__.py` — `aiohttp` + `yarl.URL` usage in HA core
### HIGH confidence (user's own prior integrations — local filesystem)
- `/home/akentner/Projects/homeassistant-gatus-integration/` — full pattern reference; manifest.json, hacs.json, pyproject.toml, CLAUDE.md
- `/home/akentner/Projects/homeassistant-kroki-integration/` — kroki_client.py custom aiohttp wrapper pattern; `after_dependencies: ["http"]` pattern
- `/home/akentner/Projects/homeassistant-gatus-integration/hacs.json` — `homeassistant: 2025.1.0`, `hacs: 2.0.5` baseline (verified current)
### NEGATIVE results (404 verified)
- `pypi.org/pypi/aiotraefik` — **does not exist** (404). Use custom aiohttp wrapper.
- `doc.traefik.io/traefik/routing-configuration/http/routing/observability/` — 404; route changed.
### MEDIUM confidence (verified pattern, no current docs link)
- `quality_scale.yaml` schema — based on user's `gatus`/`kroki` integrations; HA quality scale docs do exist at `developers.home-assistant.io/docs/core/integration-quality-scale` but were not fetched in this research pass. Flag for Phase 4 spike.
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

Conventions not yet established. Will populate as patterns emerge during development.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

Architecture not yet mapped. Follow existing patterns found in the codebase.
<!-- GSD:architecture-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd:quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd:debug` for investigation and bug fixing
- `/gsd:execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->



<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd:profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
