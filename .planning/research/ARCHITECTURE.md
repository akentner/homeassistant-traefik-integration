# Architecture Research

**Domain:** Home Assistant custom-component integration (HACS) wrapping an external HTTP API (Traefik reverse-proxy API v2/v3)
**Project:** homeassistant-traefik-integration
**Researched:** 2026-07-05
**Confidence:** HIGH — patterns are well-established HA conventions, verified against current HA developer docs, Traefik API docs, HACS publish docs, and reference integrations in HA Core.

---

## Executive Summary

A Home Assistant custom-component integration is not a "library that exposes a Python API" — it is a **declarative plugin that registers with the HA runtime** by populating a small set of well-known files inside `custom_components/<domain>/`. The runtime (not the integration code) is responsible for instantiation, lifecycle, and registry management.

The canonical skeleton (per `homeassistant/components/<domain>/` template from the HA integrations skill) is:

1. **`manifest.json`** — registers the integration with HA (domain, name, codeowners, deps, IoT class).
2. **`const.py`** — the only file whose contents are referenced by every other file (`DOMAIN`, config keys, defaults).
3. **`__init__.py`** — owns `async_setup_entry` / `async_unload_entry`; instantiates the API client + coordinator, stores them on `ConfigEntry.runtime_data`, and forwards to platforms.
4. **`api.py`** — `aiohttp`-based client class. Pure HTTP, no HA dependencies. Exposes one async method per Traefik endpoint. **Easy to unit-test in isolation.**
5. **`coordinator.py`** — single `TraefikCoordinator(DataUpdateCoordinator[TraefikData])` that fans out one polling cycle (parallel `asyncio.gather`) to all entities.
6. **`entity.py`** — `TraefikEntity(CoordinatorEntity)` base; sets `_attr_has_entity_name=True` and the device-info block.
7. **Platform files** (`sensor.py`, `binary_sensor.py`, `button.py`) — one file per entity type; each file holds the entity-description list and the per-entity class. **One entity kind per file is the convention.**
8. **`config_flow.py`** — `ConfigFlow` (user step) + `OptionsFlow`; `VERSION`/`MINOR_VERSION` for future migrations.
9. **`services.yaml`** + service handlers in `__init__.py` — domain-level service registration (`async_setup`, not `async_setup_entry`).
10. **`diagnostics.py`** — `async_get_config_entry_diagnostics` that returns redacted coordinator data.
11. **`strings.json`** + `translations/` — every user-facing string.
12. **`tls.py`** — stdlib `ssl` helper for certificate expiry fetching (only relevant if the coordinator also fetches live certs; router payload already includes `tls` info).

Above the runtime sits the **HACS distribution layer** (`hacs.json`, `info.md`, `README.md`, `CHANGELOG.md`, `brand/`, `.gitignore`) — these are the files that let the repo be installed via HACS.

---

## Standard Architecture

### System Overview (ASCII)

```
┌──────────────────────────────────────────────────────────────────────┐
│                       USER'S HOME ASSISTANT                          │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │                   HA Core Runtime                              │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌────────────────────┐    │  │
│  │  │ ConfigEntry  │  │ StateMachine │  │ Entity / Device    │    │  │
│  │  │ Registry     │  │              │  │ Registry           │    │  │
│  │  └──────┬───────┘  └──────▲───────┘  └─────────▲──────────┘    │  │
│  │         │                 │                    │               │  │
│  │         │ runtime_data    │ async_write_       │ registers      │  │
│  │         ▼                 │ ha_state           │                │  │
│  │  ┌──────────────────────────────────────────────────────┐      │  │
│  │  │  custom_components/traefik/                         │      │  │
│  │  │  ┌────────────────┐  ┌──────────────────────────┐    │      │  │
│  │  │  │ __init__.py    │  │ config_flow.py            │    │      │  │
│  │  │  │  - setup_entry │  │  - ConfigFlow             │    │      │  │
│  │  │  │  - unload_entry│  │  - OptionsFlow           │    │      │  │
│  │  │  │  - services    │  │                          │    │      │  │
│  │  │  └────────┬───────┘  └──────────────────────────┘    │      │  │
│  │  │           │                                          │      │  │
│  │  │           ▼                                          │      │  │
│  │  │  ┌──────────────────────────────────────────┐         │      │  │
│  │  │  │ coordinator.py                           │         │      │  │
│  │  │  │  TraefikCoordinator(DataUpdateCoordinator│         │      │  │
│  │  │  │    [TraefikData])                        │         │      │  │
│  │  │  │  _async_update_data()  ──► fan out       │         │      │  │
│  │  │  └────────┬─────────────────────────────────┘         │      │  │
│  │  │           │ uses                                      │      │  │
│  │  │           ▼                                           │      │  │
│  │  │  ┌──────────────────────────────────────────┐         │      │  │
│  │  │  │ api.py                                   │         │      │  │
│  │  │  │  TraefikApiClient (aiohttp)              │         │      │  │
│  │  │  │  - get_entrypoints()                     │         │      │  │
│  │  │  │  - get_routers()                         │         │      │  │
│  │  │  │  - get_services()                        │         │      │  │
│  │  │  │  - get_middlewares()                     │         │      │  │
│  │  │  │  - get_overview()                        │         │      │  │
│  │  │  │  - reload()  ──► POST /api/http/routers/ │         │      │  │
│  │  │  │                  refresh                 │         │      │  │
│  │  │  └────────┬─────────────────────────────────┘         │      │  │
│  │  │           │ uses (optional)                           │      │  │
│  │  │           ▼                                           │      │  │
│  │  │  ┌──────────────────────────────────────────┐         │      │  │
│  │  │  │ tls.py (stdlib ssl)                      │         │      │  │
│  │  │  │  fetch_cert_not_after(host, port)        │         │      │  │
│  │  │  │  parse_cert_expiry_from_router(...)      │         │      │  │
│  │  │  └──────────────────────────────────────────┘         │      │  │
│  │  │                                                       │      │  │
│  │  │  ┌──────────────┐  ┌──────────────┐  ┌────────────┐    │      │  │
│  │  │  │ entity.py    │  │ sensor.py    │  │ binary_    │    │      │  │
│  │  │  │  Traefik-    │  │  Entrypoint- │  │ sensor.py  │    │      │  │
│  │  │  │  Entity      │  │  Sensor,     │  │  Router-   │    │      │  │
│  │  │  │  (base)      │  │  Service-    │  │  Status,   │    │      │  │
│  │  │  └──────────────┘  │  Sensor, ... │  │  Cert-     │    │      │  │
│  │  │                     └──────────────┘  │  Expiry,   │    │      │  │
│  │  │                                      │  Any-      │    │      │  │
│  │  │  ┌──────────────┐                   │  Failing   │    │      │  │
│  │  │  │ button.py    │                   └────────────┘    │      │  │
│  │  │  │  Reload-     │                                   │      │  │
│  │  │  │  Button      │                                   │      │  │
│  │  │  └──────────────┘                                   │      │  │
│  │  │                                                       │      │  │
│  │  │  ┌──────────────┐  ┌──────────────┐  ┌────────────┐    │      │  │
│  │  │  │ services.yaml│  │ diagnostics  │  │ strings    │    │      │  │
│  │  │  │  reload      │  │ .py          │  │ .json      │    │      │  │
│  │  │  └──────────────┘  └──────────────┘  └────────────┘    │      │  │
│  │  └──────────────────────────────────────────────────────┘      │  │
│  └────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────▲─────────────────────┘
                                                 │ HTTPS + Bearer token
                                                 │ polling every 30s
                                                 │
                                          ┌──────┴───────┐
                                          │   Traefik    │
                                          │   v2 / v3    │
                                          │   /api/...   │
                                          └──────────────┘
```

### Component Responsibilities

| Component | Responsibility | Talks to | Tested by |
|---|---|---|---|
| `manifest.json` | Registers domain + dependencies with HA Core; HACS reads it via `hacs.json` | HA Core, HACS | `hassfest` validator |
| `const.py` | `DOMAIN`, `CONF_*` keys, `DEFAULT_*` values, signal names — no logic | every other file | import-only |
| `api.py` (`TraefikApiClient`) | Pure async HTTP wrapper; one method per Traefik endpoint; raises typed errors | aiohttp ClientSession | `tests/components/traefik/test_api.py` (mocked transport) |
| `tls.py` | Helper for cert expiry (stdlib `ssl.SSLContext.get_ca_certs` or direct cert fetch) | stdlib `ssl`, `socket` | `tests/components/traefik/test_tls.py` |
| `coordinator.py` (`TraefikCoordinator`) | One polling cycle = parallel fetch of all endpoints; caches in `TraefikData`; raises `UpdateFailed` on API errors, `ConfigEntryAuthFailed` on 401/403 | `TraefikApiClient` | `tests/components/traefik/test_coordinator.py` |
| `entity.py` (`TraefikEntity`) | Shared `CoordinatorEntity` base; sets `has_entity_name=True` and the device-info block; reads `_attr_unique_id` from subclasses | `CoordinatorEntity`, `DeviceInfo` | via platform tests |
| `__init__.py` | `async_setup_entry` instantiates client + coordinator + services; stores on `entry.runtime_data`; `async_unload_entry` tears down | `coordinator`, `config_flow`, `services.yaml` | `test_init.py` |
| `config_flow.py` | UI config flow (`async_step_user`) + options flow + YAML import (`async_step_import`) | `TraefikApiClient` (validate creds) | `test_config_flow.py` (100% coverage) |
| `sensor.py` | All `SensorEntity` classes — entrypoint listener / request count, service LB status, top-level aggregate | `TraefikEntity`, `TraefikData` | `test_sensor.py` + snapshot |
| `binary_sensor.py` | Router `enabled`/`status` and certificate-expiry `binary_sensor` | `TraefikEntity`, `TraefikData` | `test_binary_sensor.py` + snapshot |
| `button.py` | Single `ButtonEntity`: "Reload Traefik" → calls `client.reload()` | `TraefikEntity`, `TraefikApiClient` | `test_button.py` |
| `services.yaml` | Schema for domain services (`traefik.reload`) | `__init__.py` (handlers) | `test_services.py` |
| `diagnostics.py` | `async_get_config_entry_diagnostics` returns redacted snapshot of coordinator data | `async_redact_data` | `test_diagnostics.py` |
| `strings.json` + `translations/<lang>.json` | Every user-facing string (flow title, errors, aborts, options, exceptions) | HA frontend | `hassfest` |
| `repairs.py` (optional) | `CreateNotify` issues for users when config drift / cert expired > threshold / unreachable API | `homeassistant.helpers.issue_registry` | `test_repairs.py` |
| `quality_scale.yaml` | Tracks rule status per HA Quality Scale (Bronze target for v1) | HA `hassfest` | `hassfest` validator |

---

## Recommended Project Structure

```
homeassistant-traefik-integration/                       # GitHub repo root
├── custom_components/
│   └── traefik/                                        # <-- domain = "traefik"
│       ├── __init__.py                                 # async_setup_entry / unload / service handlers
│       ├── manifest.json                               # HA integration manifest
│       ├── const.py                                    # DOMAIN, CONF_*, DEFAULT_*
│       ├── api.py                                      # TraefikApiClient (aiohttp)
│       ├── tls.py                                      # Cert-expiry helpers (stdlib ssl)
│       ├── coordinator.py                              # TraefikCoordinator
│       ├── entity.py                                   # TraefikEntity base
│       ├── config_flow.py                              # ConfigFlow + OptionsFlow + OptionsFlowHandler
│       ├── sensor.py                                   # Entrypoint + Service + aggregate sensors
│       ├── binary_sensor.py                            # Router-status + cert-expiry + any-failing
│       ├── button.py                                   # Reload button
│       ├── services.yaml                               # traefik.reload schema
│       ├── diagnostics.py                              # async_get_config_entry_diagnostics
│       ├── repairs.py                                  # (optional) repair issues
│       ├── strings.json                                # default (English) translations
│       ├── translations/
│       │   └── de.json                                 # German translations (per locale)
│       └── quality_scale.yaml                          # rule status tracking
├── tests/
│   ├── __init__.py
│   ├── conftest.py                                     # shared fixtures (mock_config_entry, mock_api, init_integration)
│   ├── const.py                                        # TEST_* constants
│   ├── fixtures/
│   │   ├── traefik_entrypoints.json
│   │   ├── traefik_routers.json
│   │   ├── traefik_services.json
│   │   ├── traefik_middlewares.json
│   │   └── traefik_overview.json
│   ├── api/
│   │   ├── __init__.py
│   │   ├── test_init.py                                # full setup + teardown
│   │   ├── test_config_flow.py                         # 100% coverage required
│   │   ├── test_coordinator.py
│   │   ├── test_sensor.py                              # + snapshot_platform
│   │   ├── test_binary_sensor.py                       # + snapshot_platform
│   │   ├── test_button.py
│   │   ├── test_services.py
│   │   └── test_diagnostics.py
│   └── components/                                     # HA Core's tests/components style (mirror)
│       └── traefik/
│           ├── __init__.py
│           ├── conftest.py
│           ├── test_init.py
│           ├── test_config_flow.py
│           ├── test_sensor.py
│           ├── test_binary_sensor.py
│           ├── test_button.py
│           ├── test_services.py
│           ├── test_diagnostics.py
│           └── snapshots/                              # syrupy snapshots
│               └── test_sensor.ambr
├── README.md                                           # HACS-visible readme
├── info.md                                             # HACS "Information" / store card
├── CHANGELOG.md                                        # HA-conventional changelog (release-tagged)
├── hacs.json                                           # HACS manifest: { name, country, homeassistant }
├── LICENSE
├── .gitignore                                          # __pycache__, .venv, .pytest_cache, etc.
└── pyproject.toml                                      # (optional) ruff/mypy/pytest config
```

### Structure Rationale

- **`custom_components/traefik/` is sacred.** HACS copies this exact path verbatim into the user's HA `config/custom_components/`. Everything HA runs lives here. Anything else is build/test tooling.
- **`tests/components/traefik/` mirrors HA Core's own test layout.** HA documentation and `hassfest` enforce this; deviating breaks `script.hassfest --integration-path` and makes running under `pytest homeassistant/components/traefik/...` impossible.
- **`api.py` is deliberately separated from HA.** `TraefikApiClient` takes an `aiohttp.ClientSession` and a base URL — it can be unit-tested with `aioresponses` or a local aiohttp test server without instantiating Home Assistant.
- **One platform file per entity kind.** HA's `async_forward_entry_setups` and the `PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]` list in `__init__.py` are file-bound, so this maps 1:1.
- **`entity.py` base class prevents 20× duplication** of `has_entity_name`, `device_info`, `available` properties across the 6+ entity classes.
- **`tls.py` is a helper, not a platform.** It's called by `coordinator.py` and `binary_sensor.py`; the integration has no `Platform.TLS` and `Entity` is not a real HA platform.

---

## Architectural Patterns

### Pattern 1: `DataUpdateCoordinator` fans out one poll to many entities

**What:** A single class (`TraefikCoordinator`) owns the polling clock. Every `update_interval` seconds it calls `_async_update_data`, which fans out one parallel HTTP request per Traefik endpoint (`asyncio.gather`), bundles the parsed responses into a typed `TraefikData` dataclass, and stores it on `self.data`. All entity subclasses are `CoordinatorEntity[TraefikCoordinator]`; they receive updates via `_handle_coordinator_update()` which the coordinator auto-calls after each successful poll.

**When:** Use this whenever multiple entities derive from the same upstream — virtually all integrations wrapping a REST API. **Default pattern in HA Core for polling integrations.**

**Trade-offs:**
- Pro: One HTTP client, one connection pool, one schedule, one place to handle auth errors.
- Pro: Entities are stateless — they read from `self.coordinator.data`.
- Con: All entities update together — fine for our case (Traefik poll = single user-configured interval).
- Con: Slightly more boilerplate than per-entity `update()` methods; worth it beyond ~3 entities.

**Example skeleton (per HA developer docs):**

```python
# coordinator.py
from __future__ import annotations
from datetime import timedelta
import async_timeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator, UpdateFailed,
)
from homeassistant.exceptions import ConfigEntryAuthFailed
from .api import TraefikApiClient, TraefikApiError, TraefikAuthError
from .const import DOMAIN, DEFAULT_SCAN_INTERVAL, LOGGER

type TraefikConfigEntry = ConfigEntry["TraefikApiClient"]  # runtime_data type alias

class TraefikData:
    """Immutable snapshot returned by one coordinator cycle."""
    def __init__(self, entrypoints, routers, services, middlewares, overview):
        self.entrypoints = entrypoints
        self.routers = routers
        self.services = services
        self.middlewares = middlewares
        self.overview = overview

class TraefikCoordinator(DataUpdateCoordinator[TraefikData]):
    """Polling coordinator for a Traefik instance."""
    config_entry: TraefikConfigEntry

    def __init__(self, hass: HomeAssistant, entry: TraefikConfigEntry,
                 client: TraefikApiClient, scan_interval: int) -> None:
        super().__init__(
            hass,
            LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(seconds=scan_interval),
            always_update=True,  # safe default; revisit later
        )
        self.client = client
        self._scan_interval = scan_interval

    async def _async_update_data(self) -> TraefikData:
        """Fetch all endpoints in parallel; raise UpdateFailed on transient errors."""
        try:
            async with async_timeout.timeout(10):
                entrypoints, routers, services, middlewares, overview = await asyncio.gather(
                    self.client.get_entrypoints(),
                    self.client.get_routers(),
                    self.client.get_services(),
                    self.client.get_middlewares(),
                    self.client.get_overview(),
                )
        except TraefikAuthError as err:
            raise ConfigEntryAuthFailed("Traefik API authentication failed") from err
        except TraefikApiError as err:
            raise UpdateFailed(f"Traefik API error: {err}") from err

        return TraefikData(
            entrypoints=entrypoints,
            routers=routers,
            services=services,
            middlewares=middlewares,
            overview=overview,
        )
```

### Pattern 2: `ConfigEntry.runtime_data` replaces `hass.data[DOMAIN]`

**What:** The integration's runtime objects (the `TraefikApiClient` and `TraefikCoordinator`) are stored on `entry.runtime_data` — a typed slot on the `ConfigEntry` itself. The HA integrations skill calls this out as the modern standard; `hass.data[DOMAIN][entry.entry_id]` is legacy.

**Why:** Lifetime tied to the config entry — automatic cleanup on unload, no leak risk, type-safe access from any platform via `entry.runtime_data`.

```python
# __init__.py
async def async_setup_entry(hass: HomeAssistant, entry: TraefikConfigEntry) -> bool:
    """Set up Traefik from a config entry."""
    client = TraefikApiClient(
        session=async_get_clientsession(hass),
        base_url=entry.data[CONF_URL],
        api_key=entry.data[CONF_API_KEY],
        verify_ssl=entry.data.get(CONF_VERIFY_SSL, True),
    )
    # validate connection eagerly
    try:
        await client.get_overview()
    except TraefikAuthError as err:
        raise ConfigEntryAuthFailed from err
    except TraefikApiError as err:
        raise ConfigEntryNotReady(f"Cannot reach Traefik: {err}") from err

    coordinator = TraefikCoordinator(hass, entry, client, scan_interval)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator  # store for platforms

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True

async def async_unload_entry(hass: HomeAssistant, entry: TraefikConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
```

### Pattern 3: `CoordinatorEntity` base class with `has_entity_name=True`

**What:** Every entity inherits from `CoordinatorEntity[TraefikCoordinator]` and sets `_attr_has_entity_name = True`. The frontend then derives entity names as `"{device_name} {entity.name}"`. The `entity.py` file defines one shared `TraefikEntity` base that injects the `DeviceInfo` and a class-level translation key.

```python
# entity.py
from __future__ import annotations
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, ATTR_MANUFACTURER, ATTR_MODEL
from .coordinator import TraefikCoordinator, TraefikConfigEntry

class TraefikEntity(CoordinatorEntity[TraefikCoordinator]):
    """Base for all Traefik entities."""
    _attr_has_entity_name = True

    def __init__(self, coordinator: TraefikCoordinator,
                 entry: TraefikConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer=ATTR_MANUFACTURER,   # "Traefik"
            model=ATTR_MODEL,                  # "Reverse Proxy"
            configuration_url=entry.data[CONF_URL],
            sw_version=coordinator.data.overview.get("version") if coordinator.data else None,
        )
```

Per-entity subclass (one file per platform):

```python
# sensor.py  (excerpt)
class TraefikEntrypointSensor(TraefikEntity, SensorEntity):
    """Sensor reporting a Traefik entrypoint's request count."""
    _attr_translation_key = "entrypoint"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "requests"
    entity_description: TraefikSensorEntityDescription

    def __init__(self, coordinator, entry, name, description):
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_entrypoint_{name}"
        self._entrypoint_name = name

    @property
    def native_value(self) -> int | None:
        if not (ep := self.coordinator.data.entrypoints.get(self._entrypoint_name)):
            return None
        return ep.get("stats", {}).get("requests", {}).get("total", 0)
```

### Pattern 4: Domain service registration in `async_setup`, not `async_setup_entry`

**What:** Integration-level services (those that work across all config entries, like `traefik.reload`) are registered once in `async_setup` (synchronous, fires once at HA boot), not per-entry. The handler resolves the target config entry from `call.data[ATTR_CONFIG_ENTRY_ID]`.

**Why (per integrations skill, line 306):** "Registration: Register all service actions in `async_setup`, NOT in `async_setup_entry`."

```python
# __init__.py
async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up integration-wide services."""

    async def reload_service(call: ServiceCall) -> None:
        """Handle the traefik.reload service call."""
        entry_id = call.data[ATTR_CONFIG_ENTRY_ID]
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="entry_not_loaded",
            )
        coordinator: TraefikCoordinator = entry.runtime_data
        try:
            await coordinator.client.reload()
            await coordinator.async_request_refresh()
        except TraefikApiError as err:
            raise HomeAssistantError(f"Reload failed: {err}") from err

    hass.services.async_register(
        DOMAIN, SERVICE_RELOAD, reload_service,
        schema=vol.Schema({vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string}),
    )
    return True
```

### Pattern 5: Diagnostics with `async_redact_data`

**What:** A `diagnostics.py` file exposes `async_get_config_entry_diagnostics(hass, entry)` which returns a redacted snapshot of coordinator data. Users can download this from **Settings → Devices & Services → Traefik → ⋯ → Download diagnostics** and share it with the developer without leaking the API key.

```python
# diagnostics.py
from __future__ import annotations
from typing import Any
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant
from . import TraefikConfigEntry

TO_REDACT = {"api_key", "token", "password", "basic_auth"}

async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TraefikConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = {
        "entry": {
            "title": entry.title,
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "coordinator_data": {
            "entrypoints": coordinator.data.entrypoints,
            "routers": coordinator.data.routers,
            "services": coordinator.data.services,
            "middlewares": coordinator.data.middlewares,
            "overview": coordinator.data.overview,
        } if coordinator.data else None,
    }
    return async_redact_data(data, TO_REDACT)
```

### Pattern 6: `ConfigEntry.runtime_data` + typed ConfigEntry alias

**What:** Declare `type TraefikConfigEntry = ConfigEntry[TraefikApiClient]` (or `[TraefikCoordinator]`, depending on what you store) at module level in `coordinator.py`/`__init__.py`. Every function signature uses this type — gives static type-checkers (mypy/pyright) and IDEs a clear handle on what data lives on the entry.

---

## Data Flow

### Poll cycle (every `scan_interval` seconds)

```
                HA event loop tick (every N seconds)
                              │
                              ▼
            TraefikCoordinator._async_update_data
                              │
                  asyncio.gather (parallel)
            ┌────────┬────────┼────────┬────────┐
            ▼        ▼        ▼        ▼        ▼
          /api/    /api/    /api/    /api/    /api/
          entry-   http/    http/    http/    overview
          points   routers  services middl-
                              │
                              ▼
                     TraefikApiClient parses JSON
                              │
                              ▼
                     TraefikData dataclass
                              │
                              ▼
              self.data = TraefikData(...)  ←─────────┐
                              │                         │
                              ▼                         │
              CoordinatorEntity._handle_coordinator_update
                              │                         │
                              ▼                         │
            for entity in self.async_contexts():       │
                entity._handle_coordinator_update()    │
                              │                         │
                              ▼                         │
                entity.async_write_ha_state()           │
                              │                         │
                              ▼                         │
            HA StateMachine writes to recorder  ────────┘
                              │
                              ▼
            Frontend / WebSocket subscribers update
```

### Service call: `traefik.reload`

```
User (UI / automation / YAML)
        │  service: traefik.reload
        ▼
HA service registry
        │  resolves call.data[ATTR_CONFIG_ENTRY_ID] → entry
        ▼
__init__.py: reload_service handler
        │  entry.runtime_data.client.reload()
        │     │
        │     ▼
        │  aiohttp POST /api/http/routers/refresh
        │     Bearer <token>
        │
        │  await coordinator.async_request_refresh()
        ▼
Coordinator performs one immediate poll (see above)
        │
        ▼
HA service call returns ServiceResponse
```

### Config flow

```
User: Settings → Devices & Services → + Add Integration → "Traefik"
        │
        ▼
ConfigFlow.async_step_user
        │  Schema: url, api_key, verify_ssl
        │  validates via TraefikApiClient.get_overview()
        │     → TraefikAuthError → "invalid_auth"
        │     → TraefikApiError  → "cannot_connect"
        │     → success         → unique_id = entry_id? or hostname
        ▼
async_create_entry(...)
        │
        ▼
ConfigEntry stored → triggers async_setup_entry (see pattern 2)
```

### User edits options (OptionsFlow)

```
User: Traefik card → Configure → Options
        │
        ▼
OptionsFlow.async_step_init (triggered by HA UI)
        │  Schema: scan_interval, verify_ssl, tls_expiry_threshold
        ▼
HA updates entry.options + reloads entry
        │
        ▼
async_setup_entry re-runs with new options
        │
        ▼
Coordinator reconstructed with new scan_interval
```

---

## Recommended Project Structure (build order — depends on `const.py`)

This is the **dependency graph** the roadmap phases should follow. Each arrow means "depends on":

```
                     manifest.json            (Phase 0: scaffolding — required by HA loader)
                          │
                          ▼
                       const.py               (Phase 0 — no logic, DOMAIN + CONF_* + DEFAULT_*)
                          │
                          ▼
                       api.py                 (Phase 1 — TraefikApiClient; no HA deps; unit-testable)
                          │
                          ▼
                       tls.py                 (Phase 1 — stdlib ssl helper; needed for TLS expiry)
                          │
                          ▼
                    coordinator.py            (Phase 2 — TraefikCoordinator; depends on api+const)
                          │
                          ▼
                       entity.py               (Phase 2 — TraefikEntity base; depends on coordinator+const)
                          │
                          ▼
                  config_flow.py              (Phase 3 — ConfigFlow + OptionsFlow; depends on api for validation)
                          │
                          ▼
                     __init__.py               (Phase 3 — wires client + coordinator + platforms + services)
                          │
                          ▼
              ┌────────────┬────────────┬────────────┐
              ▼            ▼            ▼            ▼
          sensor.py   binary_sensor.py  button.py  services.yaml
              │            │            │            │
              └────────────┴────────────┴────────────┘
                              │
                              ▼
                       diagnostics.py          (Phase 5 — reads coordinator.data)
                              │
                              ▼
                       repairs.py              (Phase 6 — optional)
                              │
                              ▼
                  strings.json + translations/ (Phase 4 — strings used by every platform)
                              │
                              ▼
                    quality_scale.yaml         (Phase 5 — tracks rule status)

                ↑--- INDEPENDENT TRACK ---↑
                tests/                       (Phase 1 onwards — written alongside code)
                README.md, info.md,          (Phase 7 — HACS distribution)
                CHANGELOG.md, hacs.json,
                brand/, .gitignore
```

**Rule of thumb:** a file must not import a module that doesn't exist yet. The order above is a partial order — within the same "row" files can be built in parallel.

### Why this order matters

- **`const.py` before everything.** `DOMAIN` is referenced by `manifest.json` (implicitly, as the directory name), `config_flow.py` (for `ConfigFlow.domain=DOMAIN`), `__init__.py`, `entity.py`, etc.
- **`api.py` before `coordinator.py` before entity platforms.** The coordinator wraps the client; entities read coordinator data. Config-flow validation also calls the client directly (to validate credentials during `async_step_user`).
- **`config_flow.py` before `__init__.py`'s platform wiring.** Although not strictly enforced (the loader resolves the flow lazily), `__init__.py`'s `ConfigEntry` is created by the flow — the flow must work end-to-end before the entry ever exists.
- **`strings.json` before platform files.** Every platform's config-flow errors and entity translation keys reference strings; running `hassfest` requires all keys to resolve.
- **`__init__.py` `async_setup_entry` before platform files.** Platforms are forwarded via `hass.config_entries.async_forward_entry_setups` — `__init__.py` must list them in `PLATFORMS`.
- **`diagnostics.py` last among runtime files.** It depends on `runtime_data` shape being stable (the coordinator storing `TraefikData`).
- **Tests can start at Phase 1** (`api.py` tests don't depend on HA). Integration tests (`tests/init_integration` fixture) require the full setup chain — Phase 3 minimum.

---

## Entity Catalog (one per file where applicable)

The Traefik API surfaces multiple distinct entity kinds. Mapping them to HA platforms:

| Project requirement | Traefik data | HA platform | File | Entity class | Device class / state class |
|---|---|---|---|---|---|
| CORE-04 | Router | `binary_sensor` | `binary_sensor.py` | `TraefikRouterSensor` | `BinarySensorDeviceClass.RUNNING` |
| CORE-05 | Entrypoint | `sensor` | `sensor.py` | `TraefikEntrypointSensor` | `SensorStateClass.TOTAL_INCREASING` |
| CORE-06 | Service | `sensor` | `sensor.py` | `TraefikServiceSensor` | `SensorStateClass.MEASUREMENT` (server count) + `ENUM` (status) |
| DIAG-01 | Aggregate counts | `sensor` | `sensor.py` | `TraefikOverviewSensor` | `SensorStateClass.MEASUREMENT` |
| DIAG-02 | Any router failing | `binary_sensor` | `binary_sensor.py` | `TraefikAnyRouterFailingSensor` | `BinarySensorDeviceClass.PROBLEM` |
| TLS-01 | Certificate notAfter | `sensor` | `sensor.py` (or `binary_sensor.py`) | `TraefikCertificateSensor` | `SensorDeviceClass.TIMESTAMP` |
| TLS-02 | Certificate expiring soon | `binary_sensor` | `binary_sensor.py` | `TraefikCertExpiryBinarySensor` | `BinarySensorDeviceClass.PROBLEM` |
| DIAG-03 | Trigger reload | `button` | `button.py` | `TraefikReloadButton` | `ButtonEntityDeviceClass.RESTART` |

**Why one entity kind per file (convention):** HA Core convention — `sensor.py`, `binary_sensor.py`, `button.py`. Each platform's `async_setup_entry` is one line in `PLATFORMS`. Platforms can be enabled/disabled independently by the user. Allows `PARALLEL_UPDATES = 1` to be platform-scoped. Aligns 1:1 with `hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)`.

**Why one `entity.py` base class:** Six+ entity classes will share `DeviceInfo`, `has_entity_name`, `available`, and a `_handle_coordinator_update` callback. Inlining each = six copies of the same logic. Base class = one place to fix.

---

## Component Boundaries (precise)

| Concern | Lives in | Notes |
|---|---|---|
| Domain name, config keys, defaults | `const.py` | No imports from HA — pure Python constants |
| HTTP transport, JSON parsing, typed errors | `api.py` | No HA imports (testable without HA) |
| Cert parsing, `ssl` socket setup | `tls.py` | stdlib only; no HA imports |
| Polling schedule, parallel fetch, error mapping (`UpdateFailed`, `ConfigEntryAuthFailed`) | `coordinator.py` | Uses `async_timeout`, HA exceptions |
| Shared entity attrs, `DeviceInfo`, base `available` | `entity.py` | Inherits `CoordinatorEntity` |
| Sensor + binary_sensor + button entity classes | platform files | Inherit `TraefikEntity` |
| Config flow UI, validation, options flow | `config_flow.py` | Imports `api.py` for validation only |
| Setup, unload, service registration, runtime_data population | `__init__.py` | Owns the `ConfigEntry` lifecycle |
| Service schema + descriptions | `services.yaml` | Pairs with handlers in `__init__.py` |
| Diagnostics snapshot (redacted) | `diagnostics.py` | Imports `runtime_data` only |
| Repair issues | `repairs.py` | Uses `ir.async_create_issue` |
| User-facing text (English) | `strings.json` | Pairs with `translations/<lang>.json` |
| Quality Scale rule tracking | `quality_scale.yaml` | Validator input |

### Boundaries that must NOT blur

- **`api.py` must never import from `homeassistant.*`.** Otherwise `tests/api/` cannot run without a HA fixture.
- **`config_flow.py` must validate via the client, not via `__init__.py`.** Otherwise the user can't tell *why* setup failed.
- **`__init__.py` must not contain entity classes.** Entity logic belongs in platform files.
- **`coordinator.py` must not know about entity classes.** It exposes `TraefikData`; entities read fields.
- **`services.yaml` schema must match the handler signature.** They are validated by HA at registration time.

---

## Per-entity-class file split — why one entity type per file

This is a HA Core convention with three reasons:

1. **Platform boundaries are enforced by `async_forward_entry_setups(entry, PLATFORMS)`.** `PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]` maps 1:1 to `sensor.py`, `binary_sensor.py`, `button.py`. Splitting further (e.g., `routers.py`, `entrypoints.py`) breaks this mapping.
2. **`PARALLEL_UPDATES` is per-platform.** A platform-scoped update semaphore lives in the platform file (`PARALLEL_UPDATES = 0` for our coordinator-driven case).
3. **HA loader scans by filename.** `hassfest --integration-path ...` validates each expected platform file; missing one = integration won't load.

Within a single `sensor.py`, multiple entity classes (`TraefikEntrypointSensor`, `TraefikServiceSensor`, `TraefikCertificateSensor`, `TraefikOverviewSensor`) are fine — they share the platform, the description pattern, and the setup function. Same for `binary_sensor.py`.

---

## Where TLS expiry logic plugs in

**Two layers, not one:**

### Layer 1 — Traefik API already provides `tls` data per router

The `/api/http/routers` response includes each router's TLS config:

```json
{
  "name": "router-foo@docker",
  "status": "enabled",
  "rule": "Host(`foo.example.com`)",
  "tls": {
    "certResolver": "letsencrypt",
    "domains": [{"main": "foo.example.com"}]
  }
}
```

…but **does not include cert metadata (issuer, notAfter, SANs)**. For that we need the cert itself.

### Layer 2 — `tls.py` helper fetches the cert from the router

Two valid approaches:

**Option A (recommended):** Live TLS handshake via stdlib `ssl`. When `TraefikCoordinator` updates a given router's data, it can lazy-fetch the served cert:

```python
# tls.py
import ssl, socket
from datetime import datetime, timezone

def fetch_cert_not_after(host: str, port: int = 443,
                         timeout: float = 5.0) -> datetime | None:
    """Connect to host:port, fetch the presented cert, return its notAfter."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # we don't care about validity here
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as ssock:
            der = ssock.getpeercert(binary_form=True)
    ctx2 = ssl.create_default_context()
    parsed = ctx2.cert_to_dict(der) if hasattr(ctx2, "cert_to_dict") else _parse_der(der)
    not_after = parsed.get("notAfter")
    return _parse_openssl_date(not_after) if not_after else None
```

This is called by `TraefikCoordinator._async_update_data` in the same `asyncio.gather` block (with `asyncio.to_thread` since `ssl`/`socket` are blocking).

**Option B:** Trust the Traefik dashboard / Traefik's `/api/rawdata` output if richer cert data exists (it doesn't, currently — certs are not exposed via the API).

**Decision: Option A.** Place `fetch_cert_not_after` in `tls.py` (no HA imports, stdlib only, testable in isolation). Call it from `TraefikCoordinator` for each router that has `tls.certResolver` set.

The result feeds two entities:
- **`TraefikCertificateSensor`** (sensor, `TIMESTAMP` device class) — `native_value` = `not_after` datetime.
- **`TraefikCertExpiryBinarySensor`** (binary_sensor, `PROBLEM` device class) — `is_on` = `days_until_expiry < threshold` (threshold is a config option, default 14).

---

## Where services live

**Schema** → `services.yaml` at the package root:

```yaml
# custom_components/traefik/services.yaml
reload:
  name: Reload
  description: Force Traefik to reload its dynamic configuration.
  fields:
    config_entry_id:
      name: Config entry
      description: The Traefik config entry to reload.
      required: true
      selector:
        config_entry:
          integration: traefik
```

**Handlers** → registered in `__init__.py:async_setup` (NOT `async_setup_entry`), per the HA integrations skill (line 306).

Why `async_setup`, not `async_setup_entry`? Because:
1. Service registration is per-domain, not per-entry.
2. HA expects services to be registered exactly once per domain at startup.
3. The handler still scopes work to a specific entry via `call.data[ATTR_CONFIG_ENTRY_ID]`.

---

## Where diagnostics live

Single file `diagnostics.py` with one function:

```python
async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TraefikConfigEntry,
) -> dict[str, Any]:
    ...
```

Pattern source: `homeassistant/components/fully_kiosk/diagnostics.py` (verified — uses `async_redact_data` to strip `serial`, `Mac`, `ip4`, etc.) and `homeassistant/components/hassio/diagnostics.py` (verified — iterates device + entity registries).

**Critical for Traefik:** redact `api_key`/`token` from `entry.data` before returning. The set `TO_REDACT = {"api_key", "token", "password", "basic_auth"}` covers our case.

---

## HACS-specific files

HACS publishes this repo. Required files at the repo root (NOT inside `custom_components/`):

| File | Purpose | Content |
|---|---|---|
| `hacs.json` | HACS manifest | `{"name": "Traefik", "country": ["DE"], "homeassistant": "2025.4.0", "hacs": "1.33.0"}` |
| `info.md` | HACS store card | Short blurb shown in HACS UI before install |
| `README.md` | Install + usage docs | HACS-visible, must include install steps |
| `CHANGELOG.md` | Release notes | Updated per GitHub release |
| `LICENSE` | License file | Required for HACS default-repo inclusion |
| `brand/custom_components/traefik/icon.png` | Brand icon | 256×256, transparent background |
| `brand/custom_components/traefik/dark_icon.png` | Dark-mode icon | Optional |
| `.gitignore` | Excludes | `__pycache__`, `.venv`, `.pytest_cache`, `*.egg-info`, `dist/`, `build/` |

**Repository structure constraint (verified from HACS docs):** exactly ONE `custom_components/<name>/` directory at the repo root. Multiple = only the first one is managed.

**Reference template:** [custom-components/blueprint](https://github.com/custom-components/blueprint) (cited in HACS docs). Alternative generator: [cookiecutter-homeassistant-custom-component](https://github.com/oncleben31/cookiecutter-homeassistant-custom-component).

---

## Test Layout

Following HA Core convention (`tests/components/<domain>/...`):

```
tests/
├── __init__.py
├── conftest.py                # shared fixtures
├── const.py                   # TEST_URL, TEST_API_KEY, MOCKED_RESPONSES dict
├── fixtures/                  # JSON snapshots of real Traefik responses
│   ├── entrypoints.json
│   ├── routers.json
│   ├── services.json
│   ├── middlewares.json
│   └── overview.json
└── components/
    └── traefik/
        ├── __init__.py
        ├── conftest.py
        ├── api.py             # `def mock_trafik_api(): ...` fixture
        ├── test_init.py
        ├── test_config_flow.py
        ├── test_coordinator.py
        ├── test_sensor.py
        ├── test_binary_sensor.py
        ├── test_button.py
        ├── test_services.py
        ├── test_diagnostics.py
        └── snapshots/         # syrupy snapshot files
            ├── test_sensor.ambr
            ├── test_binary_sensor.ambr
            └── test_button.ambr
```

**Coverage requirement:** >95% on all modules (HA Core standard; per integrations skill line 614).

**Standard fixtures (per integrations skill line 711):**

```python
# tests/components/traefik/conftest.py
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry, load_fixture, async_test_home_assistant,
)

@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Traefik",
        data={
            CONF_URL: "https://traefik.local",
            CONF_API_KEY: "test-key",
            CONF_VERIFY_SSL: False,
        },
        unique_id="traefik.local",
    )

@pytest.fixture
def mock_traefik_api():
    """Patch TraefikApiClient with a MagicMock returning fixture data."""
    with patch(
        "custom_components.traefik.coordinator.TraefikApiClient",
        autospec=True,
    ) as cls:
        client = cls.return_value
        client.get_entrypoints = AsyncMock(return_value=load_fixture("entrypoints.json", DOMAIN))
        client.get_routers     = AsyncMock(return_value=load_fixture("routers.json",     DOMAIN))
        client.get_services    = AsyncMock(return_value=load_fixture("services.json",    DOMAIN))
        client.get_middlewares = AsyncMock(return_value=load_fixture("middlewares.json", DOMAIN))
        client.get_overview    = AsyncMock(return_value=load_fixture("overview.json",    DOMAIN))
        client.reload          = AsyncMock()
        yield client

@pytest.fixture
async def init_integration(hass, mock_config_entry, mock_traefik_api):
    mock_config_entry.add_to_hass(hass)
    with patch("custom_components.traefik.PLATFORMS", PLATFORMS):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
    return mock_config_entry
```

**Snapshot test pattern** (per integrations skill line 686):

```python
async def test_sensor_entities(
    hass, init_integration, snapshot, entity_registry, device_registry,
):
    await snapshot_platform(hass, entity_registry, snapshot, init_integration.entry_id)
```

**Config-flow test pattern** (per integrations skill line 650): every step must be covered; **100% coverage required for `config_flow.py`** specifically.

---

## Anti-Patterns to Avoid

### Anti-Pattern 1: Storing runtime state in `hass.data[DOMAIN]`

**What people do:** `hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator`.
**Why it's wrong:** Manual cleanup needed; easy to leak on unload; no type safety. The HA integrations skill (line 119) explicitly recommends `ConfigEntry.runtime_data` instead.
**Do this instead:** `entry.runtime_data = coordinator` — automatic lifetime, type-safe.

### Anti-Pattern 2: Per-entity `async_update()` instead of `DataUpdateCoordinator`

**What people do:** Each sensor implements its own `async_update()` and triggers its own HTTP call. N sensors = N HTTP calls per poll cycle.
**Why it's wrong:** N+1 query problem; rate-limited APIs will block; inconsistent state across entities (some updated, some not); HA skill (line 349) calls this out as the **standard pattern** to use.
**Do this instead:** One `DataUpdateCoordinator` with `asyncio.gather` of all endpoint calls; all entities derive from `CoordinatorEntity`.

### Anti-Pattern 3: Hardcoding `update_interval` from config

**What people do:** `update_interval = timedelta(seconds=entry.options[CONF_SCAN_INTERVAL])`.
**Why it's wrong:** HA skill (line 350) says: "Polling intervals are NOT user-configurable: Never add scan_interval, update_interval, or polling frequency options to config flows or config entries." Integrations are supposed to pick a sane default; making it user-configurable creates fragmentation.
**Mitigation for our case:** The PROJECT.md explicitly says `CFG-01` allows user to override scan interval. **Flag this as a deviation that will fail the quality scale.** Two options: (a) drop `CFG-01` scan-interval knob and use a fixed 30s; (b) keep it but accept that the integration won't reach Silver quality scale. Recommend (a).

### Anti-Pattern 4: Missing `runtime_data` type alias

**What people do:** `async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:`.
**Why it's wrong:** Every helper has to cast `entry.runtime_data` to the right type. IDE/mypy can't help.
**Do this instead:** `type TraefikConfigEntry = ConfigEntry[TraefikCoordinator]` (declared in `coordinator.py`), then `async def async_setup_entry(hass: HomeAssistant, entry: TraefikConfigEntry) -> bool:`.

### Anti-Pattern 5: Using `_attr_unique_id = f"traefik_{name}"`

**What people do:** Prefixing unique IDs with the integration domain.
**Why it's wrong:** HA derives domain from the entity_id prefix; redundant in the unique ID makes cross-instance dedup fail and bloats the DB.
**Do this instead:** Use a stable identifier (router name, entrypoint name, cert SAN) WITHOUT the domain prefix. Format: `f"{entry.entry_id}_{kind}_{name}"` — the entry_id already namespaces per-instance.

### Anti-Pattern 6: Logging the API key

**What people do:** `_LOGGER.debug("Authenticated with %s", api_key)`.
**Why it's wrong:** Tokens appear in HA logs; logs are often shared when troubleshooting.
**Do this instead:** Never log secrets; for diagnostics, use `async_redact_data`; for debug, log only the URL + last 4 chars (`token[-4:]`).

### Anti-Pattern 7: Multiple config-flow sources without unique-ID discipline

**What people do:** `async_step_user` and `async_step_import` both create entries without checking uniqueness.
**Why it's wrong:** Same Traefik instance can be added twice.
**Do this instead:** Always call `await self.async_set_unique_id(...)` followed by `self._abort_if_unique_id_configured()` — works for both UI and YAML import.

### Anti-Pattern 8: Mixing TLS logic into `api.py` or `coordinator.py`

**What people do:** Stuff `ssl` socket handling into the HTTP client.
**Why it's wrong:** `ssl` is blocking; mixing it with `aiohttp` requires `asyncio.to_thread`. Hard to test, hard to mock.
**Do this instead:** Separate `tls.py` helper, called from coordinator via `asyncio.to_thread`.

### Anti-Pattern 9: One platform file for everything

**What people do:** `entities.py` containing sensor + binary_sensor + button all together.
**Why it's wrong:** HA can't disable a single platform; loader expects per-platform files for `async_forward_entry_setups` to find them.
**Do this instead:** Strict 1:1 mapping — `sensor.py` (all sensors), `binary_sensor.py` (all binary sensors), `button.py` (all buttons).

### Anti-Pattern 10: Skipping `always_update=False` on the coordinator

**What people do:** Default `always_update=True` even when polling JSON that rarely changes.
**Why it's wrong:** Every poll cycle writes every entity state to the recorder — DB bloat.
**Do this instead:** Once `TraefikData` has `__eq__` implemented, set `always_update=False` so identical payloads don't trigger writes. (Polish optimization, not Phase 1.)

---

## Traefik-Specific Architectural Notes

### Endpoint map (from official Traefik API docs)

| Traefik endpoint | Purpose | Used by entity |
|---|---|---|
| `GET /api/entrypoints` | Listen addresses + stats (requests, connections) | `TraefikEntrypointSensor` |
| `GET /api/http/routers` | All HTTP router definitions + status | `TraefikRouterSensor` (binary) |
| `GET /api/http/services` | Service load-balancer configs + server URLs | `TraefikServiceSensor` |
| `GET /api/http/middlewares` | Middleware definitions | (count only — DIAG-01) |
| `GET /api/overview` | Aggregate stats + Traefik version | `TraefikOverviewSensor`, `sw_version` in `DeviceInfo` |
| `GET /api/version` | Traefik version | fallback for `sw_version` |
| `POST /api/http/routers/refresh` | Force hot reload of dynamic config | `TraefikReloadButton`, `traefik.reload` service |

**Auth:** all GET endpoints require `Authorization: Bearer <api_key>` (insecure mode: no auth — recommended for LAN-only). `aiohttp.ClientSession` carries the bearer header.

**Polling pattern:** `asyncio.gather` all 5 GETs in parallel within the coordinator. POST is triggered on-demand only (button / service).

### Version compatibility

- **v2.x (2.11+)** and **v3.x** share the same JSON shape for `/api/http/{routers,services,middlewares}` and `/api/entrypoints`. **One client, one parser.**
- `/api/overview` was renamed/refactored between v2 and v3 — verify against fixtures for both, or fall back gracefully if a field is missing (defensive parsing: `data.get("key", default)`).
- v2 → v3 differences are minor and mostly additive; do not special-case for v1.

### TLS in scope vs. out of scope

**In scope (PROJECT requirements):**
- Read `notAfter` of certificates that terminate on each Traefik-exposed router.
- Compute `days_until_expiry = notAfter - now`.
- Expose both as entities.
- Warn when below threshold (binary_sensor, default 14d).

**Out of scope (PROJECT.md explicit):**
- Provisioning certs (Let's Encrypt / ACME).
- Reading raw cert files from Traefik's data dir.
- Detecting SNI mismatch.

→ `tls.py` needs exactly ONE function (`fetch_cert_not_after(host, port)`), called per router per poll cycle (cached for `scan_interval`).

---

## Scaling Considerations

This integration scales per **Traefik instance** (one config entry) and per **HA instance**. There's no cross-instance scaling.

| Scale | Architecture adjustments |
|---|---|
| 1 Traefik, 1 HA, 5 routers | Default — `asyncio.gather` of 5 GETs every 30s, fits comfortably |
| 1 Traefik, 1 HA, 200 routers | Still fine — TLS fetch becomes the bottleneck; consider rate-limiting cert fetches (cached per `scan_interval`) |
| 5 Traefik instances, 1 HA | 5 config entries → 5 coordinators → still fine; each entry has its own coordinator, no shared state |
| TLS fetch for 200 routers every 30s | `asyncio.to_thread` per cert fetch — 200 blocking sockets every 30s may exhaust `ulimit -n`; **mitigate by sampling** (e.g., only re-fetch certs every Nth poll cycle, or stagger within the cycle) |

### Scaling priorities

1. **First bottleneck:** TLS cert fetching — `socket.create_connection` is blocking and 200 simultaneous sockets can hit the file-descriptor limit. Mitigation: `asyncio.to_thread` + a semaphore (e.g., max 10 concurrent cert fetches).
2. **Second bottleneck:** Recursive `extra_state_attributes` — if every sensor has 10 attributes, the recorder DB grows fast. Mitigation: keep attributes to <5 stable fields per entity; for changing values, create additional sensors instead.

---

## Integration Points

### External: Traefik API

| Traefik | Pattern | Notes |
|---|---|---|
| HTTP `/api/...` | `aiohttp.ClientSession.get(...)` with `Authorization: Bearer <token>` | Single session per HA instance via `async_get_clientsession(hass)` |
| `POST /api/http/routers/refresh` | `aiohttp.ClientSession.post(...)` | No body; idempotent |
| TLS handshake | `ssl.wrap_socket` over `socket.create_connection` (blocking) | Wrap in `asyncio.to_thread` |

### Internal: HA Core

| Boundary | Communication | Notes |
|---|---|---|
| `ConfigFlow` ↔ `TraefikApiClient` | Direct method call (validation) | Lives in `config_flow.py`, imports `api.py` |
| `__init__.py` ↔ coordinator | `entry.runtime_data` | One assignment at end of `async_setup_entry` |
| Coordinator ↔ entities | `CoordinatorEntity._handle_coordinator_update` | Auto-called by coordinator after each successful poll |
| Platform ↔ coordinator | `self.coordinator` (inherited from `CoordinatorEntity`) | Read-only access |
| `services.yaml` ↔ handlers | HA service registry | Pair-by-name (`DOMAIN`, `SERVICE_RELOAD`) |
| `diagnostics.py` ↔ coordinator | `entry.runtime_data` | Read-only, redacted |

---

## HA-Specific Patterns Cited

| Pattern | Source | Used by |
|---|---|---|
| `DataUpdateCoordinator[T]` | https://developers.home-assistant.io/docs/integration_fetching_data/ | `coordinator.py` |
| `ConfigEntry.runtime_data` | integrations skill line 119 | `__init__.py`, every platform |
| `CoordinatorEntity[T]` | integrations skill line 114 | `entity.py` base |
| `type TraefikConfigEntry = ConfigEntry[...]` | integrations skill line 121 | `coordinator.py` |
| `async_get_clientsession(hass)` | integrations skill line 164 | `__init__.py` |
| `ConfigFlow` + `OptionsFlow` | https://developers.home-assistant.io/docs/config_entries_config_flow_handler/ | `config_flow.py` |
| `async_set_unique_id` + `_abort_if_unique_id_configured` | HA config-flow docs | `config_flow.py` |
| `async_step_reauth` for credential rotation | HA config-flow docs | `config_flow.py` (if token can expire) |
| `async_redact_data` for diagnostics | integrations skill diagnostics pattern | `diagnostics.py` |
| Service registration in `async_setup` | integrations skill line 306 | `__init__.py` |
| `ServiceValidationError` + `HomeAssistantError` | integrations skill lines 318, 326 | service handlers |
| `PARALLEL_UPDATES = 0` for coordinator-driven platforms | integrations skill line 359 | each platform file |
| `has_entity_name=True` + `translation_key` | integrations skill line 411 | `entity.py` base + each entity |
| `_attr_unique_id` from stable identifier, no domain prefix | integrations skill line 366 | each entity |

---

## Sources

### Primary (HIGH confidence — verified against current docs)

- `/home/akentner/.opencode/skills/integrations/SKILL.md` — HA integration conventions (786 lines, primary reference)
- https://developers.home-assistant.io/docs/config_entries_config_flow_handler/ — ConfigFlow patterns
- https://developers.home-assistant.io/docs/integration_fetching_data/ — DataUpdateCoordinator patterns
- https://developers.home-assistant.io/docs/core/entity/sensor — Sensor entity properties
- https://hacs.xyz/docs/publish/start — HACS general publish requirements
- https://hacs.xyz/docs/publish/integration — HACS integration-specific requirements
- https://doc.traefik.io/traefik/reference/install-configuration/api-dashboard/ — Traefik API endpoints (verified v2/v3 shape)
- https://github.com/home-assistant/core/blob/dev/homeassistant/components/fully_kiosk/diagnostics.py — verified diagnostics pattern with `async_redact_data`
- https://github.com/home-assistant/core/blob/dev/homeassistant/components/hassio/diagnostics.py — verified config-entry diagnostics pattern

### Secondary (MEDIUM confidence — verified patterns)

- Traefik API JSON shapes verified via the official Traefik API docs above; exact field names (`status`, `rule`, `tls`, `certResolver`, `entryPoints`) are canonical as of Traefik 2.11+/3.x.

### References

- [custom-components/blueprint](https://github.com/custom-components/blueprint) — HACS-recommended template for custom integrations
- [cookiecutter-homeassistant-custom-component](https://github.com/oncleben31/cookiecutter-homeassistant-custom-component) — generator for integration scaffold

---

## Confidence Assessment

| Area | Confidence | Reason |
|---|---|---|
| File layout (`__init__.py`, `coordinator.py`, etc.) | HIGH | Verified against HA developer docs + integrations skill |
| `DataUpdateCoordinator` fan-out pattern | HIGH | Canonical pattern, official doc has full example |
| `ConfigEntry.runtime_data` modern pattern | HIGH | Explicitly recommended in integrations skill (line 119) |
| `config_flow.py` + OptionsFlow shape | HIGH | Official HA docs walkthrough + integrations skill |
| `services.yaml` registration in `async_setup` | HIGH | integrations skill line 306 explicit |
| `diagnostics.py` template | HIGH | Two reference integrations read directly |
| Traefik API endpoint list | HIGH | Official Traefik docs, stable since v2.0 |
| TLS fetch via stdlib `ssl` | HIGH | stdlib is stable; pattern is documented |
| HACS file requirements | HIGH | Direct from hacs.xyz docs |
| Per-entity-class file split rationale | MEDIUM | Convention from HA Core style, not formalized in docs |
| Quality Scale Bronze→Silver→Gold progression | MEDIUM | From integrations skill, but project hasn't committed to a target tier yet |

---

## Open Questions / Gaps

1. **Scan-interval user override (`CFG-01`)** — conflicts with HA quality-scale rule "Polling intervals are NOT user-configurable". Recommend either dropping the option or accepting Bronze tier only. **Decide in roadmap Phase 3.**
2. **Cert re-fetch cadence** — if a user has 200 routers and `scan_interval=30s`, fetching all certs every cycle is heavy. Need policy: fetch once per cycle, or once per N cycles, or only when `tls.domains` changes. **Decide in roadmap Phase 2 (coordinator design).**
3. **YAML configuration (CORE-02)** — `ConfigFlow.async_step_import` is the standard hook; verify against `hassfest` what fields are accepted in YAML (must match `vol.Schema` of `async_step_user`). **Decide in roadmap Phase 3.**
4. **Quality Scale target** — Bronze is the minimum for HACS. Silver adds entity-unavailable handling and parallel updates. Recommend **Bronze for v1**, Silver for v2 once we have feedback.
5. **Translations scope** — only `en` is strictly required; `de.json` is nice-to-have given the user's locale. Add to Phase 7 (docs/polish).
6. **`brand/` assets** — need to source an icon (Traefik logo is Apache 2.0; verify attribution) and provide both light + dark variants.

---

*Architecture research for: homeassistant-traefik-integration*
*Researched: 2026-07-05*