# Stack Research — Home Assistant Traefik Integration

**Domain:** HACS-distributable Home Assistant custom integration (local-polling HTTP client)
**Researched:** 2026-07-05
**Confidence:** HIGH

## Executive Summary

This stack is the **2025/2026 baseline for a polling-type HA custom integration that talks to a local JSON API over HTTP**. It matches the user's two sibling integrations (`homeassistant-gatus-integration`, `homeassistant-kroki-integration`) almost line-for-line, follows the current HA quality-scale conventions, and has **zero third-party pip dependencies** because everything is provided by Home Assistant Core itself.

The headline decisions:

1. **No `aiotraefik` library exists** on PyPI (verified 404). Build a thin custom aiohttp wrapper inside the integration, sharing HA's `aiohttp.ClientSession` via `async_get_clientsession(hass)`. ~150 LOC; this is what every other HA "hub" integration does.
2. **No extra HTTP library** — neither `requests` nor `httpx`. HA Core bundles `aiohttp==3.14.x` (verified at `home-assistant/core/dev/requirements.txt` → `aiohttp==3.14.1`) and provides `aiohttp_client.async_get_clientsession()` for shared, cookie-aware, configured sessions.
3. **HA Core minimum: `2025.4.0`** (PROJECT.md baseline). Safely targets modern `DataUpdateCoordinator`, `ConfigEntry.runtime_data`, PEP-695 `type` aliases, `asyncio.timeout()`, and `aiohttp` 3.13+.
4. **TLS cert expiry (TLS-01)** — Traefik's HTTP API does **not** expose certificate `notAfter` dates. It only exposes `tls: { certResolver: "letsencrypt" }` per router. To surface expiry, do a one-shot TLS handshake to the router's public hostname using Python stdlib `ssl` (no extra deps). All other entities come straight from the JSON API.

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| Python | `>=3.12` (HA 2025.4 ships 3.13) | Runtime | PROJECT.md mandate; HA 2025.4 bundles CPython 3.13 |
| Home Assistant Core | `>=2025.4.0` | Framework | PROJECT.md mandate; gives `ConfigEntry.runtime_data`, PEP-695 syntax, `asyncio.timeout` |
| `aiohttp` | `>=3.13` (HA bundles 3.14.x) | HTTP client | HA uses aiohttp everywhere; HA's `async_get_clientsession(hass)` is the Platinum-quality shared session |
| `voluptuous` | bundled by HA | Config-flow schema | Already required by HA — no manifest entry |
| `cryptography` (HA-bundled) | bundled by HA | (only if TLS parsing needs more than `ssl`) | HA ships `cryptography==46.0.x` already; no manifest entry unless explicitly imported |

> **Rule:** Custom integrations **must not** add to `manifest.json`'s `requirements` anything that HA Core already bundles. The HA developer docs are explicit: *"Custom integrations should only include requirements that are not required by the Core requirements.txt."* — [developers.home-assistant.io/docs/creating_integration_manifest](https://developers.home-assistant.io/docs/creating_integration_manifest/#custom-integration-requirements).

### Manifest (`custom_components/traefik/manifest.json`)

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

Confidence: **HIGH** — verified against `developers.home-assistant.io/docs/creating_integration_manifest`, `hacs.xyz/docs/publish/integration/`, and the user's own existing integrations.

### `hacs.json` (repository root)

```json
{
  "name": "Traefik",
  "homeassistant": "2025.4.0",
  "hacs": "2.0.5"
}
```

| Field | Value | Rationale |
|-------|-------|-----------|
| `name` | `"Traefik"` | Display name in HACS UI. Required. |
| `homeassistant` | `"2025.4.0"` | Matches PROJECT.md mandate; HACS will refuse older installs. |
| `hacs` | `"2.0.5"` | Current HACS minimum as of 2025; matches `gatus` integration. |
| **No `filename` / `zip_release`** | — | Standard folder-based distribution; HACS will read `custom_components/traefik/`. |
| **No `country`** | — | Not a regional integration. |

Confidence: **HIGH** — verified at `hacs.xyz/docs/publish/start` and `hacs.xyz/docs/publish/integration`.

### HACS Repository Layout

```
homeassistant-traefik-integration/   (repo root, becomes HACS default repo)
├── custom_components/
│   └── traefik/
│       ├── __init__.py
│       ├── manifest.json
│       ├── const.py
│       ├── coordinator.py
│       ├── api.py                    # Traefik HTTP client (aiohttp wrapper)
│       ├── config_flow.py
│       ├── services.yaml             # for "reload routers" action
│       ├── strings.json
│       ├── translations/
│       │   └── en.json
│       ├── binary_sensor.py          # router status, cert-expiring
│       ├── sensor.py                 # entrypoint/service/middleware aggregates
│       ├── diagnostics.py            # HA Gold-tier: diagnostic dump
│       ├── entity.py                 # shared base class
│       └── quality_scale.yaml        # rule tracking
├── tests/
│   ├── conftest.py
│   ├── test_config_flow.py
│   ├── test_coordinator.py
│   ├── test_api.py
│   ├── fixtures/
│   │   ├── traefik_entrypoints.json
│   │   ├── traefik_routers.json
│   │   ├── traefik_services.json
│   │   └── traefik_middlewares.json
│   └── ...
├── hacs.json
├── README.md
├── LICENSE
├── pyproject.toml                    # dev deps only
├── pytest.ini
├── ruff.toml
└── .github/workflows/
    ├── hassfest.yaml
    ├── hacs-action.yaml
    └── tests.yaml
```

The `OK example` from `hacs.xyz/docs/publish/integration` confirms this exact `custom_components/<domain>/` layout. Confidence: **HIGH**.

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

Confidence: **HIGH** — verified against the user's existing `gatus` integration and HA core (`home-assistant/core/dev/homeassistant/components/faa_delays/coordinator.py`, `home-assistant/core/dev/homeassistant/components/gios/coordinator.py`).

### Traefik API Client (built-in, not a PyPI package)

**Decision: build the client inside the integration, do not use `aiotraefik`.**

- `aiotraefik` on PyPI → **404 not found**. Verified directly.
- `traefik` on PyPI → also not found.
- The Traefik API surface used by this integration is small (~6 endpoints, all JSON) — a 100-LOC client is smaller than a dependency to maintain.

The Traefik API endpoints actually used:

| Endpoint | Returns | Used for |
|----------|---------|----------|
| `GET /api/version` | `{Version, Codename, StartDate}` | DIAG sensor; auth verification in config flow |
| `GET /api/entrypoints` | `[{name, address, ...}]` | CORE-05 entrypoint sensors |
| `GET /api/http/routers` | `[{name, rule, service, status, tls, ...}]` | CORE-04 router binary sensors; TLS-01 router selection |
| `GET /api/http/services` | `[{name, loadbalancer, status, ...}]` | CORE-06 service sensors |
| `GET /api/http/middlewares` | `[{name, type, ...}]` | DIAG-01 middleware count |
| `GET /api/overview` | `{http:{routers, services, middlewares}, ...}` | DIAG-01 top-level aggregator |
| `POST /api/http/routers/refresh` | `{}` | DIAG-03 reload service |

Authentication header: `Authorization: Bearer <api_key>`. Confidence: **HIGH** — verified at `doc.traefik.io/traefik/reference/install-configuration/api-dashboard/#endpoints`.

**TLS cert expiry (TLS-01)** is the one feature the API can't answer. Approach:

1. After the coordinator fetches routers, filter to those with `tls` set.
2. For each, extract the host from the router's `rule` (e.g. `Host(\`hass.example.com\`)` → `hass.example.com`).
3. Open a TLS connection (`asyncio.open_connection(host, 443, ssl=True)` wrapped in `asyncio.timeout(5)`).
4. Read `transport.get_extra_info('ssl_object').getpeercert()`.
5. Parse `notAfter` (e.g. `Nov 15 12:00:00 2025 GMT`) into a `datetime`.
6. Compute `days_until_expiry = (not_after - utcnow()).days`.

All of this uses **Python stdlib `ssl` + `asyncio`** — no extra dependencies. Confidence: **HIGH** (Python stdlib).

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

Confidence: **HIGH** — versions verified against `MatthewFlamm/pytest-homeassistant-custom-component/blob/master/requirements_test.txt` and the user's `gatus` `pyproject.toml`.

### `pyproject.toml` (dev dependencies only)

```toml
[project]
name = "homeassistant-traefik-integration"
version = "1.0.0"
description = "Home Assistant custom integration that exposes Traefik reverse-proxy state as entities"
requires-python = ">=3.13"

[dependency-groups]
dev = [
    "mypy>=2.1.0",
    "pytest>=9.0.0",
    "pytest-asyncio>=1.4.0",
    "pytest-cov>=7.0.0",
    "pytest-homeassistant-custom-component>=0.13.345",
    "ruff>=0.15.15",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
testpaths = ["tests"]
addopts = "--cov=custom_components.traefik --cov-report term-missing"

[tool.ruff]
line-length = 120
target-version = "py313"

[tool.ruff.lint]
select = ["B", "E", "F", "I", "UP", "ASYNC", "SIM", "RUF"]
ignore = ["E501"]

[tool.mypy]
python_version = "3.13"
strict = true
```

Confidence: **HIGH** — copied patterns from user's `gatus` integration, augmented with HA's current pytest-asyncio fixture-loop setting (from `MatthewFlamm/pytest-homeassistant-custom-component/setup.cfg`).

### Architecture Components (no pip install needed)

```python
# coordinator.py — pattern verified against FAADelays/GIOS core integrations
from datetime import timedelta
from typing import override
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from .api import TraefikApi, TraefikApiError

type TraefikConfigEntry = ConfigEntry["TraefikCoordinator"]

class TraefikCoordinator(DataUpdateCoordinator[TraefikData]):
    config_entry: TraefikConfigEntry

    def __init__(self, hass: HomeAssistant, entry: TraefikConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=entry.options.get(
                CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL,
            )),
        )
        self.api = TraefikApi(
            session=aiohttp_client.async_get_clientsession(hass),
            base_url=entry.data[CONF_URL],
            api_key=entry.data[CONF_API_KEY],
            verify_ssl=entry.options.get(CONF_VERIFY_SSL, True),
        )

    @override
    async def _async_update_data(self) -> TraefikData:
        try:
            async with asyncio.timeout(10):
                return await self.api.fetch_all()
        except TraefikApiAuthError as err:
            raise ConfigEntryAuthFailed from err
        except TraefikApiError as err:
            raise UpdateFailed(str(err)) from err
```

This is **directly copied** from the verified HA core pattern (`faa_delays`, `gios`). Confidence: **HIGH**.

---

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

---

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

---

## HACS / Distribution Specifics

### `hacs.json` schema (verified at hacs.xyz/docs/publish/start)

```json
{
  "name": "Traefik",
  "homeassistant": "2025.4.0",
  "hacs": "2.0.5"
}
```

- `homeassistant` uses [AwesomeVersion](https://github.com/ludeeus/awesomeversion) — accepts CalVer (`2025.4.0`) and SemVer (`1.0.0`). Use CalVer to align with HA's release cadence and match the user's `gatus` pattern.
- `hacs: "2.0.5"` is the current HACS minimum (verified from the user's existing `gatus` integration).
- Without `zip_release: true`, HACS reads from the default branch (or the latest GitHub release if releases are published).

### Brand assets (required by HACS)

```
custom_components/traefik/
└── brand/
    ├── icon.png       # 256×256 PNG, transparent background
    └── icon@2x.png    # 512×512 PNG
```

These are referenced by HACS to render the integration icon. Without them, HACS installation still works but the UI looks broken. Confidence: **HIGH** — verified at `developers.home-assistant.io/docs/creating_integration_file_structure`.

### GitHub releases (recommended)

Each release tag becomes a version in HACS. Tag format: `v2025.7.0` or `1.0.0` (HACS strips leading `v`). Release body can be auto-generated. No need for binary artifacts — HACS downloads the tarball of the matching tag.

### `services.yaml` (for DIAG-03 reload action)

```yaml
reload_routers:
  name: Reload Traefik routers
  description: >-
    Triggers a hot reload of Traefik routers via the /api/http/routers/refresh
    endpoint. Use after adding/removing routes externally.
  fields:
    entry_id:
      name: Config entry
      description: The Traefik config entry to reload.
      required: true
      selector:
        config_entry:
          integration: traefik
```

The actual service handler is registered in `async_setup` (NOT `async_setup_entry`) — verified against HA core docs.

---

## QA / Lint / CI Stack

| Tool | Where | Notes |
|------|-------|-------|
| `ruff check` + `ruff format` | pre-commit + GitHub Actions | The user's `gatus` config uses `select = ["B","E","F","I","UP","ASYNC","SIM","RUF"]` |
| `mypy --strict` | pre-commit | PEP-695 type aliases work natively on 3.13 |
| `hassfest` | GitHub Actions | Validates `manifest.json` schema; uses `python -m script.hassfest --integration-path custom_components/traefik` |
| HACS Action | GitHub Actions | `hacs/action@main` with category `integration` |
| `pytest` + coverage | GitHub Actions | Quality scale target: **95%+ coverage per module** |

Confidence: **HIGH** for ruff/pytest/hassfest; **HIGH** for HACS Action (verified at `hacs.xyz/docs/publish/action`).

---

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

---

## Roadmap Implications (downstream input)

Based on this stack, the natural phase structure is:

1. **Phase 1 — Foundation**
   - Scaffold `custom_components/traefik/` with `manifest.json`, `const.py`, `__init__.py`, `config_flow.py`.
   - Implement `TraefikApi` (aiohttp wrapper) and `TraefikCoordinator` (DataUpdateCoordinator).
   - Add one binary sensor (router status) end-to-end to prove the polling loop.
   - Sets up CI (hassfest + HACS + pytest).

2. **Phase 2 — Core entities**
   - Add the remaining entity types (sensor for entrypoints/services/middlewares, aggregate `sensor.traefik`).
   - Add `services.yaml` + reload action handler (DIAG-03).
   - Add Options Flow (CFG-01).

3. **Phase 3 — TLS**
   - Add TLS cert-expiry sensor and binary sensor.
   - Implements a separate update cadence (cert expiry changes slowly — once per hour is enough) so it doesn't hammer the routers every 30 s.

4. **Phase 4 — Quality scale**
   - Add `quality_scale.yaml` even though it's not enforced — it documents intent for downstream contributors.
   - Diagnostics support (`diagnostics.py`).
   - Translations cleanup, deprecation migrations.

**Dependencies that inform phase ordering:**
- Cannot ship TLS features until the TLS-handshake helper is built — keep it isolated.
- Config Flow must come **before** Options Flow (Options Flow requires an existing config entry).
- HACS brand assets can land any time but must be there before the first public release.

**Research flags for phases:**
- Phase 1 — **Standard patterns**, unlikely to need additional research. The DataUpdateCoordinator + config flow pattern is exhaustively documented.
- Phase 3 — **Flagged for deeper research.** TLS handshake + cert chain parsing has subtle pitfalls (SNI, cert chains with multiple certs, hostname mismatch). A `gsd-spike` is recommended before committing to the approach.
- Phase 4 — Light research needed on `homeassistant.components.diagnostics` schema (it's been moving).

---

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

---

*Stack research for: homeassistant-traefik-integration*
*Researched: 2026-07-05*
*Confidence: HIGH — verified against official HA + HACS + Traefik docs and the user's own two sibling custom integrations.*