# Pitfalls Research — Home Assistant Traefik Integration

**Domain:** HACS-distributable Home Assistant custom integration that polls an external HTTP API (Traefik's `/api/...`)
**Researched:** 2026-07-05
**Confidence:** HIGH (most pitfalls cross-verified against HA Core source, Traefik v3 docs, and HACS docs)

## Executive Summary

This integration sits at three fault lines simultaneously:

1. **HACS submission gate** — manifest schema is enforced by hassfest; a single missing or misnamed key blocks the whole PR.
2. **HA entity ID constraint** — entities have an unforgiving regex (`^(?!.+__)(?!_)[\da-z_]+(?<!_)\.(?!_)[\da-z_]+(?<!_)$`) that rejects `@`, `:`, `.`, uppercase, and trailing/leading underscores. Traefik routinely generates names that contain `@<provider>` (e.g. `api@internal`, `strip@docker`).
3. **Traefik API quirks** — cert metadata is *not* exposed via `/api/http/routers`; dynamic-config writes are asynchronous; the `/api/http/routers/refresh` POST often returns 200 before the new config is actually loaded.

Most pitfalls are neither subtle nor mysterious — they are **well-documented landmines that custom integrations step on every release cycle**. The cure is to follow a small set of opinionated patterns rather than reinvent each one.

## Critical Pitfalls

### Pitfall 1: API token leaks via `_LOGGER.exception()` / diagnostics / repr

**What goes wrong:**
Logging an exception while the API client is in scope can dump the `Authorization: Bearer <token>` header. The diagnostics endpoint (if implemented naively) dumps the whole client including headers. A `repr(api)` containing the session/headers is the classic leak path.

**Why it happens:**
Developers pass `self` or the whole `TraefikApi` client into `_LOGGER.exception("oh no", exc_info=True)` for context. Python's exception chaining walks the locals — and `aiohttp.ClientSession` stores default headers. `_LOGGER.debug("data=%s", api_data)` similarly embeds secrets when `api_data` is a dict that includes the token.

**How to avoid:**
1. Pass the API client as `Authorization` middleware / request header that is built **per request** from `entry.data[CONF_API_KEY]`. Never store a `ClientSession` with default `Authorization` headers set.
2. Never log the `TraefikApi` instance. Log only short, redacted strings (`f"GET {url} -> {status}"`).
3. In `diagnostics.py`, return a **redacted dict** — substitute `CONF_API_KEY` with `"***"`; list reachable endpoints; omit headers.
4. Use lazy formatting: `_LOGGER.debug("path=%s status=%s", path, status)`. Never `_LOGGER.debug(f"path={path}")` with the full client in scope as a captured variable.

**Warning signs:**
- Grep the codebase: `grep -rn "api_key\|CONF_API_KEY\|Bearer" custom_components/traefik/`. Anything in a logging path is suspect.
- `python -m homeassistant --script check_config` won't catch this; only a code review will.

**Phase to address:**
**Phase 1 (Foundation)** — credential storage and request-construction pattern must be locked in before any logging is added.

---

### Pitfall 2: Traefik service/middleware names containing `@<provider>` → illegal entity IDs

**What goes wrong:**
HA entity IDs must match `^(?!_+)[a-z0-9_]+(?<!_)\.(?!_+)[a-z0-9_]+(?<!_)$` (verified at `homeassistant/core.py` `_OBJECT_ID` / `VALID_ENTITY_ID`). Traefik routinely returns names like:

- `api@internal` (the dashboard service — referenced by every dashboard router)
- `strip@docker`, `auth@docker`, `ratelimit@file` (provider-suffixed services/middlewares)
- `default-auth@kubernetescrd` (Kubernetes CRD middleware)

If you naïvely pass `api@internal` as the entity `name`, HA slugifies (`@` → `_`) and the `:` situation doesn't arise — but **`api@internal` slugified becomes `api_internal`**, which collides with a *different* Traefik service that was author-named `api-internal`. Worse, the dashboard `api@internal` is **special**: the Traefik API returns it under the same `/api/http/services` endpoint but it's the internal router service, not a real backend. Treating it as a normal service produces a phantom "all healthy" sensor.

**Why it happens:**
Developers assume "router/service/middleware name → entity name" is 1:1. It isn't — Traefik encodes the **provider namespace** as `<name>@<provider>` (`traefik.http.services.<name>@<provider>` style labels) to disambiguate same-named objects from different providers. HA has no concept of namespaces in entity IDs.

**How to avoid:**
1. **Use Traefik `name` as `unique_id`, not as the entity object ID.** Compute `_attr_unique_id = f"{entry.entry_id}_{router_name}_status"` directly. This is immutable and contains the unmodified `@`.
2. **Let HA slugify the human-readable name** by setting `_attr_has_entity_name = True` and `_attr_name = <traefik_name>`. Slugify in `homeassistant.util.slugify` converts `@`, `:`, `.` to `_` and strips leading/trailing underscores.
3. For the dashboard `api@internal` specifically — **filter it out** at the coordinator level (it's not a user-managed service). Pattern: `if "@internal" in svc["name"]: continue`.
4. For ambiguous humans, set `_attr_name = None` (device-only entity) for diagnostic services; surface only the routers and entrypoints as entities, not services.

**Warning signs:**
- A test fixture with the user's actual `file provider` output: `traefik.http.routers.*@file`, `traefik.http.services.*@file`. If your tests only mock provider-less names, you won't see this until a real install.
- A bug report like "I have `entity_id` empty for service `…@…`" — meaning `async_get_available_entity_id` returned a name that clashed with another and was suffixed `_2`.

**Phase to address:**
**Phase 2 (Core entities)** — when introducing per-router, per-service, per-middleware entities; **explicit filter helper** in `api.py`.

---

### Pitfall 3: Traefik router `rule` field accidentally used as entity name

**What goes wrong:**
The Traefik API's `/api/http/routers` payload includes a `rule` field like `Host(\`hass.example.com\`) && PathPrefix(\`/api\`)`. Junior devs sometimes pick the first descriptive-looking string field ("oh, `name` is short — let me use `rule` for the display!"). The rule is then rendered as `Host(\`hass.example.com\`) && PathPrefix(\`/api\`)` in the UI, which is unreadable and breaks automations (backticks, parentheses, pipes, ampersands, double quotes — none of which survive `slugify` cleanly).

**Why it happens:**
Both fields exist, both are strings, `rule` is "more descriptive". The naming has *nothing* in common with how humans think about Traefik routers — the `rule` is the matcher expression, the `name` is the identifier.

**How to avoid:**
1. Always derive entity display name from `name` (Traefik's identifier) plus an optional `rule`-derived hint (e.g. first `Host(`...`)` match from the rule).
2. **Store the rule as an entity `extra_state_attribute`**, never as `name` / never in `_attr_unique_id`.
3. Helper function: `first_host_from_rule(rule: str) -> str | None` — uses `re.search(r"Host\(`([^`]+)`\)", rule)` to extract the hostname. Returns None on no match. This becomes the friendly label for the router entity; falls back to `name` otherwise.

**Warning signs:**
- An entity showing backticks / `Host(`...`)` in the HA States panel.
- A snapshot test with `binary_sensor.traefik_router_host_hass_example_com_...` instead of `binary_sensor.traefik_router_my_router`.

**Phase to address:**
**Phase 2 (Core entities)** — entity platform design.

---

### Pitfall 4: Creating your own `aiohttp.ClientSession()` instead of HA's shared session

**What goes wrong:**
`session = aiohttp.ClientSession()` in `TraefikApi.__init__`. Violates HA's Platinum quality-scale rule ("WebSession Injection"). Loses HA's connector pooling, cookie persistence, DNS cache, custom SSL context, and the `_async_create_clientsession_for_event_loop` lifecycle. Worse: the integration **owns** the session and must close it on unload — easy to leak, easy to corrupt SSL state during reload.

**Why it happens:**
It's the natural first instinct when writing standalone Python. The HA-specific helper `async_get_clientsession(hass)` is hidden in `homeassistant.helpers.aiohttp_client`.

**How to avoid:**
```python
# api.py
from homeassistant.helpers.aiohttp_client import async_get_clientsession

def build_api(hass: HomeAssistant, base_url: str, api_key: str) -> TraefikApi:
    return TraefikApi(
        session=async_get_clientsession(hass),  # ← always this
        base_url=base_url,
        api_key=api_key,
    )
```
Cookie-jar and connector pooling come for free; HA shuts the session down at `stop`; you never call `.close()`.

**Warning signs:**
- `grep -rn "aiohttp.ClientSession\|ClientSession(" custom_components/traefik/` — should produce zero hits.
- `ruff` `B904` rules won't catch this specifically — but `mypy --strict` will (the helper's return type is annotated; `aiohttp.ClientSession` is not in HA's public API).

**Phase to address:**
**Phase 1 (Foundation)** — establish in the very first commit.

---

### Pitfall 5: HACS release-tag drift (manifest `version` ≠ git tag)

**What goes wrong:**
`manifest.json` says `"version": "1.2.0"`, but the latest GitHub release is `v1.1.0`. Or vice versa: code is at `1.2.0` but the latest release tag is `1.1.0` and HACS installs the older tag. HACS validates via [AwesomeVersion](https://github.com/ludeeus/awesomeversion) and **silently picks what matches**. The user sees "HACS says 1.2.0 is available but I'm on 1.1.0; download does nothing".

**Why it happens:**
Manual bumping of one without the other. CI that doesn't pin both. PRs that bump `manifest.json` without cutting a release.

**How to avoid:**
1. **Single source of truth.** Pick either `git describe` or a single Python constant like `__version__` and **generate** `manifest.json` + tag from it in CI.
2. Make the CI workflow `.github/workflows/release.yml` do exactly:
   ```yaml
   - run: python -c "import json,pathlib; m=json.loads(pathlib.Path('custom_components/traefik/manifest.json').read_text()); assert m['version']=='${TAG_NAME}', (m['version'], '${TAG_NAME}')"
   ```
   Fail the release if they don't match.
3. Add a pre-commit hook: `python script/check_manifest_version.py` reads git tag + manifest `version` and refuses to commit a mismatch.

**Warning signs:**
- HACS UI shows different version than `manifest.json`.
- GitHub releases page shows tags that don't appear in HACS's "Available updates".

**Phase to address:**
**Phase 1 (Foundation)** — set up the CI gates before cutting the first release.

---

### Pitfall 6: `ConfigEntry.runtime_data` shape change without `async_migrate_entry`

**What goes wrong:**
v1 sets `entry.runtime_data = TraefikApi(...)`. v1.1 wraps it: `entry.runtime_data = TraefikRuntime(api=..., coordinator=...)`. Existing user upgrades → `entry.runtime_data.api` doesn't exist → `AttributeError: 'TraefikRuntime' object has no attribute 'fetch_all'`. The exception happens on every platform update because entities read `coordinator.api` lazily.

**Why it happens:**
Developers change `runtime_data`'s type without bumping `VERSION` / `MINOR_VERSION` or implementing `async_migrate_entry`. The config entry just keeps its old `runtime_data` blob (or no blob at all) and breaks on the next reload.

**How to avoid:**
1. **Bump `VERSION` whenever `runtime_data` shape changes.** Use HA's standard `2.x` semantics.
2. Implement `async_migrate_entry(hass, entry)` in `__init__.py`:
   ```python
   async def async_migrate_entry(hass, entry):
       if entry.version == 1:
           # upgrade: build the new runtime_data, call async_update_entry
           new_runtime = TraefikRuntime.from_legacy(entry.runtime_data)
           hass.config_entries.async_update_entry(
               entry, runtime_data=new_runtime, version=2, minor_version=1
           )
       return True
   ```
3. **Decide runtime_data shape on day 1 and stick to it.** A two-field wrapper (`{api, coordinator}`) is the right forward-looking choice — don't start with `{api}` and "extend later".

**Warning signs:**
- GitHub issue: "after upgrading, integration shows as failed" — first thing to check: is `entry.runtime_data` an instance of the expected type?
- Test: load v1 fixture, simulate upgrade, call `async_setup_entry`, expect success.

**Phase to address:**
**Phase 1 (Foundation)** — choose the shape, write the test, ship it.

---

### Pitfall 7: `UpdateFailed` vs `ConfigEntryNotReady` — using the wrong one breaks re-auth

**What goes wrong:**
On a 401 from the Traefik API, the coordinator raises `UpdateFailed("401 Unauthorized")`. The entities go `unavailable`. The user has no recovery path; they have to manually delete + re-add the integration. **HA never offers the reauth flow** because that's wired to `ConfigEntryAuthFailed`, not `UpdateFailed`.

Conversely: on a transient network blip during **first** setup, the developer raises `ConfigEntryNotReady("timeout")` (correct), but on a **steady-state** poll they raise `UpdateFailed("timeout")` (also correct) — but they forget to *also* raise `ConfigEntryNotReady` if they've moved to a "first refresh failed" path. Status of `ConfigEntry` gets stuck in `SETUP_RETRY` for hours.

**Why it happens:**
The two exceptions look like cousins — both "something's wrong" — but they trigger **completely different** state machines in HA. `UpdateFailed` is a coordinator-level "this update failed; the next one might work". `ConfigEntryNotReady` is "this entry can't be set up right now; retry setup later". `ConfigEntryAuthFailed` is "creds are bad; prompt the user".

The mapping:

| Failure | Where raised | Exception |
|---|---|---|
| Auth (401/403) on **first** setup | `async_setup_entry` or first `coordinator.async_config_entry_first_refresh()` | `ConfigEntryAuthFailed` |
| Auth (401/403) on **steady-state** poll | `coordinator._async_update_data()` | `ConfigEntryAuthFailed` ← both paths must raise this |
| Device offline on **first** setup | `async_setup_entry` or first refresh | `ConfigEntryNotReady` |
| Device offline on **steady-state** poll | `coordinator._async_update_data()` | `UpdateFailed` |
| Malformed response, schema mismatch | coordinator | `UpdateFailed` |

**How to avoid:**
1. Raise `ConfigEntryAuthFailed` *both* in `async_setup_entry` *and* in `_async_update_data` — never let a `401` become an `UpdateFailed`.
2. Catch `aiohttp.ClientResponseError` and dispatch on `.status`:
   ```python
   if err.status in (401, 403):
       raise ConfigEntryAuthFailed from err
   raise UpdateFailed(...) from err
   ```
3. Define `TraefikAuthError` in `api.py` and re-raise it inside the coordinator; map exception types centrally.

**Warning signs:**
- Bug report: "HACS integration says `Updating Traefik… failed`, but the API key is fine, I just regenerated it." → it's stuck in unavailable land with no reauth prompt.
- After a successful reauth, entity IDs flap `unavailable` ↔ `ok` because the entry was deleted & re-added (HA created a *new* config entry since the reauth path was never wired).

**Phase to address:**
**Phase 1 (Foundation)** — coordinator implementation; **Phase 2 (Reauth flow)** — `async_step_reauth`.

---

### Pitfall 8: Polling too aggressively vs Traefik restart storms

**What goes wrong:**
`update_interval = timedelta(seconds=5)`. Traefik rebuilds the dynamic config every time any provider (Docker labels, file watcher, Consul) changes. If Docker flips a label every minute (e.g. a "is this container healthy" label), Traefik regenerates internal config, and the API scan returns mid-flight → JSON parse error → `UpdateFailed` spam in logs.

Worse: 4 endpoints × 4 parallel instances (one per integration instance) × 5s = **320 req/min**. Traefik's API isn't rated for that. Logs get spammed. The dashboard becomes laggy.

**Why it happens:**
"Polling is cheap, why not 5 seconds?" — the answer is Traefik has its own providers emitting constant micro-changes.

**How to avoid:**
1. **Default interval: `30 s`** (matches PROJECT.md). Clamp to `[15 s, 5 min]` in Options.
2. Add a Cooldown / Consecutive-Failure-Backoff: after 3 consecutive `UpdateFailed`s, double the interval up to a cap of 5 minutes. Use `DataUpdateCoordinator`'s built-in pattern.
3. **Suppress log spam**: `_LOGGER.debug` for individual failures. Let `_LOGGER.warning` fire once per hour, not once per failure.
4. Per-endpoint request coalescing: one `asyncio.gather(routers, services, middlewares, entrypoints)` per cycle, not per-entity refresh.

**Warning signs:**
- Traefik dashboard shows incoming API request rate > 10/s.
- `ha logs` shows `Updating Traefik failed: …` every 5 seconds.

**Phase to address:**
**Phase 1 (Foundation)** — coordinator `update_interval`. **Phase 2 (Options flow)** — user-tunable interval.

---

### Pitfall 9: Polling too slowly → missed state during HA restart

**What goes wrong:**
`update_interval = timedelta(minutes=5)`. User restarts HA. HA comes back, polls Traefik — but a critical router went down 30 seconds ago and you don't know for 4 minutes 30 seconds. For a "is this reverse proxy up?" sensor that's a useless lag.

**Why it happens:**
Developers bias toward "don't hammer the API", forgetting the integration's value proposition: **near-realtime visibility** into Traefik.

**How to avoid:**
1. On `async_setup_entry`, call `await coordinator.async_config_entry_first_refresh()` so the initial state appears immediately.
2. On HA restart, prefer the lower end (15s) so the dashboard shows current state quickly.
3. Combine with #8: 15s after restart, back off to 30s after one successful refresh, back off to 60s after 5 minutes of stability. The user-configurable scan interval is the upper bound; the backoff logic is internal.

**Warning signs:**
- "All my routers show `enabled` even though I know one is broken" — usually paired with a 5-minute interval.

**Phase to address:**
**Phase 1 (Foundation)** — first-refresh + adaptive backoff.

---

### Pitfall 10: `_attr_available = False` not set on coordinator failure → stale state

**What goes wrong:**
Coordinator raises `UpdateFailed`. The state machine retains the *last known good* state. Entities still appear `enabled` in the dashboard. User looks at the integration page and thinks everything is fine.

**Why it happens:**
`CoordinatorEntity` sets `available = self.coordinator.last_update_success` *only* if you let it. If you don't, you have to manage `_attr_available` yourself. Many developers don't realize this is automatic — they think they have to write `available()` like in the legacy `Entity` class.

**How to avoid:**
1. Always subclass `CoordinatorEntity[<YourCoordinator>]`. The base class already exposes `available` correctly when `coordinator.last_update_success` flips.
2. **Don't override `available`** unless you have additional logic. If a specific entity's underlying object (e.g. a router) disappeared from the API but the coordinator is healthy, override `available` to also check `router_name in self.coordinator.data["routers"]`.
3. Watch for the failure mode: write test where `coordinator.async_set_update_error(ValueError("x"))` then assert `entity.available is False`.

**Warning signs:**
- Snapshot test shows entity state still `on` after coordinator raised `UpdateFailed`.
- User reports: "routers dashboard says everything is fine, but Traefik log is full of 503s."

**Phase to address:**
**Phase 1 (Foundation)** — coordinator + base entity; **Phase 2 (entity platforms)** — per-platform overrides if needed.

---

### Pitfall 11: Forgetting `unique_id` → duplicate entities after re-setup

**What goes wrong:**
User adds the integration, removes it (perhaps to switch URLs), re-adds it. Entity registry has stale entries with no `unique_id` match — so HA creates **new** entities. After 3 re-adds, the dashboard shows three sets of the same routers with no way to consolidate (UI rename doesn't merge across unique_id space).

**Why it happens:**
The integration uses only `entity_id = f"sensor.traefik_{name}"`. No `_attr_unique_id`.

**How to avoid:**
1. **Every entity has `_attr_unique_id`.** Pattern from the user's `gatus` integration (`entity.py:46`):
   ```python
   self._attr_unique_id = f"{entry.entry_id}_{endpoint_key}_{sensor_type}"
   ```
2. For Traefik: `_attr_unique_id = f"{entry.entry_id}_router_{router_name}_status"` — incorporates the entry_id *and* the Traefik name (which may contain `@`).
3. The `entity_id` (slug-form display name) is derived automatically by HA from `name` + `has_entity_name`. Don't set `entity_id` manually.

**Warning signs:**
- `ha core entity-registry list` shows two `binary_sensor.traefik_router_my_router` entries.
- After re-setup, automations break because old entity_ids still exist with stale state.

**Phase to address:**
**Phase 1 (Foundation)** — base entity in `entity.py`. **Phase 2 (per-platform)** — entities added during this phase must copy the pattern.

---

### Pitfall 12: Missing `entity_registry_enabled_default = False` for noisy diagnostic entities

**What goes wrong:**
Every router has both:
- `binary_sensor.traefik_router_<name>_status` (the "is it OK" sensor)
- `sensor.traefik_router_<name>_last_checked` (the "when did we last poll" sensor)

By default, **both** are enabled. The "last checked" sensor changes on every poll (every 30s). It floods the activity stream, the logbook, and the recorder database. The user disables the integration because "it generates too many events".

**Why it happens:**
Default `entity_registry_enabled_default = True` is the safe choice for "real" sensors, but for **diagnostic / per-poll metadata** it's wrong.

**How to avoid:**
1. Per-entity `_attr_entity_registry_enabled_default = False` for:
   - TLS-expiry `binary_sensor` with threshold > 30 days (low signal)
   - Internal counter sensors (HTTP request count, error count)
   - "Last updated" timestamp sensors
2. Set `_attr_entity_category = EntityCategory.DIAGNOSTIC` on these too — moves them to a separate UI section so the user actively opts in.
3. Reserve `EntityCategory.DIAGNOSTIC` for: TLS raw days countdown (kept), but **not** the user-facing cert-expiry binary sensor (that's a real automation trigger).

**Warning signs:**
- `ha logbook` shows `sensor.traefik_router_*_last_checked` updating every 30s.
- Recorder DB blows up in size.

**Phase to address:**
**Phase 2 (Entity platforms)** — when adding the per-router and per-service sensors.

---

### Pitfall 13: `quality_scale` set in custom-integration manifest → hassfest warning + HACS rejected

**What goes wrong:**
Dev puts `"quality_scale": "silver"` in `manifest.json` to "be thorough". hassfest emits: *"Integration X declares a quality scale, but is not a core integration"*. The PR / HACS submission is blocked until it's removed.

**Why it happens:**
The quality scale is a **core-integration** governance tool. Custom integrations are not in the scoreboard. The latest hassfest explicitly enforces this. The user's existing `gatus` and `kroki` correctly omit it.

**How to avoid:**
- **Do not** add `quality_scale` to `manifest.json` of a custom integration.
- If you want to self-track your own quality, **optionally** add a `quality_scale.yaml` in the integration folder — that's just metadata, not enforced by hassfest. Match the schema at `developers.home-assistant.io/docs/core/integration-quality-scale`.
- For HACS submission: pass `python -m homeassistant --script hassfest --integration-path custom_components/traefik` and confirm zero output (clean).

**Warning signs:**
- `hassfest` action in CI has a red ❌ on `manifest.json`.

**Phase to address:**
**Phase 1 (Foundation)** — manifest.json template.

---

### Pitfall 14: TLS cert parsing — `ssl.SSLObject.getpeercert()` returns a tuple, not the cert PEM

**What goes wrong:**
The Traefik HTTP API **does not** expose certificate `notAfter` dates (verified at `doc.traefik.io/traefik/reference/install-configuration/api-dashboard/`). To get expiry, the integration must do an out-of-band TLS handshake to each router's public hostname: `asyncio.open_connection(host, 443, ssl=True)` → `transport.get_extra_info("ssl_object").getpeercert()`.

Developers then expect `getpeercert()` to return a parsed dict (legacy Python 2 behavior). **In Python 3, it returns a `bytes` (the DER-encoded cert)** unless `binary_form=False` is passed. And even with the dict form, the keys are `notAfter` / `notBefore` (the legacy nested dict), not always present, and `ssl` doesn't give a parsed X.509 object.

**Why it happens:**
The recipe from a 2018 StackOverflow answer. The stdlib `ssl` module's `getpeercert(binary_form=False)` returns a dict containing `subject`, `issuer`, `version`, `serialNumber`, `notBefore`, `notAfter`, plus the leaf cert only — no chain. Parsing the `notAfter` string `Nov 15 12:00:00 2025 GMT` requires `datetime.strptime` with the right format string. **Most failures: format string mismatch** (locale-dependent on some platforms, case-sensitive on others).

**How to avoid:**
1. Use `binary_form=False` to get the dict, then parse `notAfter`.
2. **Defense in depth**: check the format string against multiple known shapes:
   ```python
   NOTAFTER_FORMATS = (
       "%b %d %H:%M:%S %Y %Z",  # "Nov 15 12:00:00 2025 GMT"
       "%b %d %H:%M:%S %Y",     # locale-dependent fallback
   )
   for fmt in NOTAFTER_FORMATS:
       try:
           return datetime.strptime(raw, fmt)
       except ValueError:
           continue
   raise ValueError(f"Unknown notAfter format: {raw!r}")
   ```
3. **For chain validation (renewals, SAN walk)**, import `cryptography.x509` — HA bundles it. **Don't add it to `manifest.json`'s `requirements`** unless you actually use it (it bloats user installs).
4. **Cache the cert result** for at least the duration of one scan interval — never hammer the host.
5. **Wrap in `asyncio.timeout(5)`** — a hung TLS handshake on a broken router shouldn't kill the coordinator.

**Warning signs:**
- `_LOGGER.exception` showing `unknown time string 'Nov 15 12:00:00 2025 '` (trailing space variant).
- The expiry sensor spikes from `25 days` to `-100 days` then unavailable (clock skew on the cert parsing server).

**Phase to address:**
**Phase 3 (TLS)** — explicitly carved out per PROJECT.md as needing a spike first (`gsd-spike`).

---

### Pitfall 15: Traefik dynamic config is async — `POST /api/http/routers/refresh` returns 200 before reload completes

**What goes wrong:**
HA automation calls `traefik.refresh_routers` (the action). The handler POSTs to `/api/http/routers/refresh` and returns immediately. The provider (Docker labels watcher) reads Traefik's `dynamic-config.yml` from disk, parses it, applies it — *asynchronously*. The user polls a router status 2 seconds later and gets the *old* state. They assume the refresh didn't work.

**Why it happens:**
Traefik's `/refresh` endpoint only triggers the reload; it doesn't synchronously wait for the new configuration to be hot. This is by design (refresh is fire-and-forget so providers can take seconds).

**How to avoid:**
1. After the POST, **poll** `/api/http/routers` with a small backoff (200ms → 5s with exponential backoff, max 10 attempts) and compare router count / version. When the count changes from the pre-refresh value, the refresh is "done".
2. Or — fetch Traefik's `/api/version`'s `StartDate`: if it hasn't changed in the last hour, the reload is in-process and not done yet. Use this only as a smoke test, not a completion signal.
3. Service's response should include a `success: bool` field indicating whether verification confirmed reload completion. Document this in `services.yaml`.

**Warning signs:**
- Forum post: "Why does my refresh button do nothing?"
- The next poll after a manual refresh shows the same router count as before.

**Phase to address:**
**Phase 2 (DIAG-03 reload service)** — when implementing the `traefik.refresh_routers` action.

---

## Moderate Pitfalls

### Pitfall M1: Storing `CONF_API_KEY` in `ConfigEntry.options` instead of `.data`

**What goes wrong:**
User "edits" the config and clears the API key. The token comes back, then dies unexpectedly later. Or: credentials show up in the YAML options dump.

**Prevention:** API keys, URLs, secrets, usernames → `entry.data`. Tuning knobs (interval, threshold) → `entry.options`. See `integrations/SKILL.md` line 196.

**Phase:** Phase 1.

---

### Pitfall M2: Using `hass.data[DOMAIN][entry_id]` for runtime storage

**What goes wrong:**
No type safety, deprecated as of HA 2024.4. Developers then need a `cast()` everywhere.

**Prevention:** Use `entry.runtime_data = TraefikRuntime(...)`. Type it with `type TraefikConfigEntry = ConfigEntry[TraefikRuntime]` at module level.

**Phase:** Phase 1.

---

### Pitfall M3: Not implementing `async_unload_entry` → integration can't reload

**What goes wrong:**
After a code change requiring a reload (e.g. discovery info updated), "Reload" hangs, then errors. Config entry stays in `loaded` but platforms don't re-setup.

**Prevention:** Standard pattern (use the user's `gatus` integration as reference):
```python
async def async_unload_entry(hass: HomeAssistant, entry: TraefikConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
```

**Phase:** Phase 1.

---

### Pitfall M4: Polling during `ConfigEntryState.NOT_READY` / `SETUP_RETRY`

**What goes wrong:**
Coordinator starts ticking before the first refresh succeeds. `coordinator.data` is `None`. Entities read `None` and crash.

**Prevention:** `await coordinator.async_config_entry_first_refresh()` in `async_setup_entry`. Let the `ConfigEntryNotReady` exception propagate from the failed first refresh.

**Phase:** Phase 1.

---

### Pitfall M5: Service registration in `async_setup_entry` instead of `async_setup`

**What goes wrong:**
Multiple config entries overwrite each other's service handlers. Only the last registration wins.

**Prevention:** Services registered in the **module-level `async_setup`**, not per-entry. See `integrations/SKILL.md` line 309.

**Phase:** Phase 2 (DIAG-03).

---

### Pitfall M6: Forgetting `ConfigEntry.version` / `MINOR_VERSION` constants

**What goes wrong:**
`async_migrate_entry` is silently skipped because `VERSION = 1` (default) — but the data shape already changed. Tests pass because they go through `async_setup_entry`, which checks `if entry.version > 1` — never reached.

**Prevention:** Always set `VERSION = 1; MINOR_VERSION = 1` explicitly in `ConfigFlow`. Bump on changes.

**Phase:** Phase 1.

---

### Pitfall M7: Diagnostics dump includes ConfigEntry data → token leaks

**What goes wrong:**
`async_get_device_diagnostics` returns `dict(entry.data)`. The API key is in `entry.data[CONF_API_KEY]`. Bug tracker leak.

**Prevention:** Implement `diagnostics.py` with explicit **redaction**:
```python
async def async_get_config_entry_diagnostics(hass, entry):
    return {"entry": {**entry.data, CONF_API_KEY: "***"}, "data": safe_summary(coordinator.data)}
```

**Phase:** Phase 4 (Quality scale).

---

## Minor Pitfalls

### Pitfall m1: `update_interval` accidentally set to `None`

**What goes wrong:** Entity never refreshes. Diagnose-then-panic in GitHub.

**Prevention:** Use `timedelta(seconds=30)` constant. No `None` unless the integration has a push subscription.

### Pitfall m2: Using `parse_datetime` (HA util) for Traefik's `notAfter`

**What goes wrong:** Traefik's format `Nov 15 12:00:00 2025 GMT` doesn't match HA's helpers.

**Prevention:** Use stdlib `datetime.strptime` with the format strings from Pitfall 14.

### Pitfall m3: `aiohttp` TrustEnv enabled inadvertently

**What goes wrong:** `aiohttp.ClientSession(trust_env=True)` picks up `HTTP_PROXY` env var → user's corporate proxy intercepts HA → token leaks.

**Prevention:** The HA-provided `async_get_clientsession(hass)` sets `trust_env=False` by default. Use it.

### Pitfall m4: Quoting hostnames from `Host(\`example.com\`)` rule parse

**What goes wrong:** First regex grep catches the wrong paren depth, host comes back as `example.com) && PathPrefix(\`/api`.

**Prevention:** Use a tokenizer, not a regex. Limit to one match per rule; if multiple, take the first or comma-list them.

### Pitfall m5: Annotating return type as `dict` instead of `Mapping` for `coordinator.data`

**What goes wrong:** `mypy --strict` fails on the coordinator contract (HA wraps data in `ReadOnlyDict`).

**Prevention:** `DataUpdateCoordinator[TraefikData]` with `TraefikData = Mapping[str, Any]`.

### Pitfall m6: Forgetting `__init__.py` re-export tests

**What goes wrong:** pytest from CI can't find fixtures because they're in `conftest.py` not auto-imported.

**Prevention:** Keep `custom_components/traefik/` self-contained; tests live in `tests/` outside, importing via `custom_components.traefik`.

### Pitfall m7: `services.yaml` describes a field that the service doesn't accept (or vice versa)

**What goes wrong:** Developer adds a field to the service schema, forgets `services.yaml` → documentation is wrong. Or adds field to `services.yaml` with no schema → service raises at runtime.

**Prevention:** Schema + `services.yaml` in same PR; `ruff` enforces YAML schema if you `pip install voluptuous-serialize`.

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| `_attr_unique_id = router_name` (without `entry_id`) | Simpler code | Re-setup duplicates; entity registry de-sync | **Never** |
| `Polling 5s instead of 30s` | Real-time feel | Traefik provider thrash; API rate | **Never** |
| `entry.runtime_data = api_dict` (dict, not class) | No class boilerplate | Lost type safety; refactor for v1.1 is painful | Only for 1-week PoC |
| `entity_id = f"sensor.traefik_{name}"` set manually | Predictable ID | HA's auto-slugify is smarter (avoids collisions); breaks on `:` and `@` | **Never** |
| `raise Exception("traefik down")` from coordinator | One-line "fix" | HA treats generic `Exception` as bug, not transient; surfaces in HA logs not in integration panel | **Never** |
| `skip async_step_reauth` | Smaller code | Token rotation (required for security ops) blows up the integration. User emails asking how to fix | **Never** |
| Pass `aiohttp.ClientSession` through service handlers | Self-contained | Session lifecycle gets confused on reload | **Never** |

---

## Integration Gotchas (Traefik Specific)

| Gotcha | Common Mistake | Correct Approach |
|---|---|---|
| Router `name` contains `@` | Treat router names as flat strings | Traefik *forbids* `@` in router name; only service/middleware use `<name>@<provider>` format. Filter service names with `@internal` |
| Service `api@internal` | Treat as normal service | Filter out — it's Traefik's dashboard service, always "healthy", useless to monitor |
| Router `rule` with backticks | Use as entity name | Extract first `Host(...)` match or use raw router `name`. Store full rule as attribute |
| HTTP 401 from `/api/http/routers` | Treat as `UpdateFailed` | Map to `ConfigEntryAuthFailed` to trigger reauth |
| `/api/http/routers/refresh` returns 200 fast | Treat as "refresh done" | Poll `/api/http/routers` for version/count change; up to 10 attempts with backoff |
| TLS cert notAfter | Read from Traefik API | Traefik doesn't expose it. Out-of-band TLS handshake to each router host |
| `certResolver: "@letsencrypt"` (provider-style annotation) | Use in entity name | Strip the `@...` suffix; it's a resolver reference, not a property |
| Traefik dashboard disabled | Polling works; router/service list is empty | Don't fail setup; surface as diagnostic sensor "API enabled? X" (counts may be zero) |

---

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Per-entity `async_update()` (no coordinator) | N entities × M requests per interval | `DataUpdateCoordinator` + one parallel `asyncio.gather` per interval | 5+ entities |
| Sequential `await client.fetch_routers(); await client.fetch_services()` | Latency = sum of all endpoints | `asyncio.gather(*[fetch_endpoints])` → latency = max | 4+ endpoints |
| Scanning every 5 seconds | Log spam, Traefik dashboard lag | Default 30s; backoff to 60s after stable | Always |
| Loading all fixtures in test setup | Test suite slow (>30s) | Mock at the coordinator boundary, share fixtures via `conftest.py` | >100 entities |
| Logging the full response payload | Disk fill | Log at `debug` only; cap to first 1KB | First deploy |
| Per-router TLS handshake every scan | Slow polls + cert fingerprint blacklisted by host | Cache cert for at least one scan interval (24h reasonable) | When you have >10 TLS routers |
| Resolving `Host(...)` from every router `rule` per refresh | Slow even with cache | Cache the parsed rule by `(entry_id, router_name)` | >20 routers |

---

## Security Mistakes (Domain-specific)

| Mistake | Risk | Prevention |
|---------|------|------------|
| Token in `logger.exception("…", exc_info=True)` | Plaintext token in `home-assistant.log`; may be uploaded to error reporting | `logger.exception(…)` only with `exc_info=False`; never include client object |
| `services.yaml` accepting raw bearer token | Phishable: a malicious flow could pre-fill | Use `TextSelector` with `password: true` mask (HACS hides the field) |
| `verify_ssl=False` hardcoded | MITM easy | User option (default `True`); warn if disabled with `issue_id` Repairs flow |
| `_LOGGER.info(f"Connecting to {api_key}@{url}")` | Token printed on every integration load | Never embed credentials in log messages; use redacted form |
| Diagnostics dump with raw ConfigEntry data | Token disclosed via support attach | `diagnostics.py` must redact `CONF_API_KEY` |
| `_LOGGER.debug(api_data)` where `api_data` includes the request headers | Token leak via session dump | Don't pass client/dict; pass only summarized string |
| Storing token in `entry.options` (which is shown in UI by default) | UX disclosure; HACS sync | `CONF_API_KEY` MUST be in `.data`, not `.options` |

---

## UX Pitfalls

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| All sensor states use generic icon | Hard to spot "router down" in dashboard | `mdi:router-network` for routers, `mdi:lock-alert` for cert-expiring, `mdi:check-circle`/`mdi:alert-circle` for status |
| Long entity IDs (`binary_sensor.traefik_router_hass_example_com_status`) | UI overflow | Use router `name`, not hostname from rule, for entity name (display). Set `_attr_has_entity_name = True` |
| TLS-expiry binary sensor default-on even at 89 days | Floods activity stream | `entity_registry_enabled_default = False`; user opts in once they have alerts |
| Show "Last updated: 2025-01-01 03:00:00" as a sensor | Boring; state never changes once fetched | Set `state_class = SensorStateClass.TIMESTAMP` so history is useful for "is the integration dead?" |
| No status text on binary sensor | Automations can only check `on`/`off` | Set `binary_sensor._attr_extra_state_attributes = {"router_status_raw": "warning"}` to capture full Traefik status string |
| Config flow fails on empty API key with "Unknown error" | User has to guess | Map `vol.Invalid` → `"missing_api_key"` translation key |
| "Reload" doesn't restore last scan interval | User tunes interval, reloads, loses it | Use `entry.add_update_listener(_async_update_listener)` to re-create the coordinator |

---

## "Looks Done But Isn't" Checklist

- [ ] **Manifest:** `hassfest` passes locally (`python -m homeassistant --script hassfest --integration-path custom_components/traefik`). Confirm CI's `.github/workflows/hassfest.yaml` does the same. *Often missing*: `iot_class`, `codeowners`, `config_flow: true`, valid `version`.
- [ ] **HACS brand assets:** `custom_components/traefik/brand/icon.png` and `icon@2x.png` (256x256 and 512x512 PNG). HACS renders a broken UI without them.
- [ ] **`has_entity_name = True` everywhere** — not just on most platforms; sweep `grep -L "_attr_has_entity_name" custom_components/traefik/`.
- [ ] **`unique_id` on every entity** — `pytest-homeassistant-custom-component`'s `entity_registry` test will catch this with `entry_id_uniqueness` check.
- [ ] **`async_unload_entry` returns True on all platforms** — set `PARALLEL_UPDATES = 0` and don't forget `await async_unload_platforms(...)`.
- [ ] **Options Flow registered** with `entry.add_update_listener(_async_update_listener)` to recreate the coordinator on interval change.
- [ ] **Reauth path** verified end-to-end with a test that simulates a 401 and walks through `async_step_reauth_confirm`.
- [ ] **Diagnostics** with redacted credentials — `pytest` against `hass.data["diagnostics"]` to assert no `api_key` substring in dump.
- [ ] **`.github/CODEOWNERS`** matches `manifest.json` `codeowners` (HACS validates).
- [ ] **README** has HACS install badge, manual install instructions, and at least one example automation.
- [ ] **Tests pass with `asyncio_mode = "auto"`** — older `pytest-asyncio` requires `@pytest.mark.asyncio` on every test; missing one produces a "coroutine was never awaited" warning that CI won't catch.

---

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Token leaked in logs | MEDIUM (audit + rotate) | (1) grep the git history for the leak (use `gitleaks` or `trufflehog`). (2) Roll the token in Traefik. (3) Update secrets doc; add pre-commit hook (`detect-secrets`). (4) Add `LOGGER.exception(…)` lint rule to deny-list in ruff config. |
| Entities duplicated after re-setup | LOW (user-initiated) | (1) Identify the unique_id format. (2) Ask user to delete entities with `entity_registry.async_remove`. (3) Re-add. (4) Write a test that asserts unique_id is stable. |
| HACS rejects the submission | LOW | (1) Run `hacs/action` locally via `docker run --rm -v $(pwd):/repo ghcr.io/hacs/action`. (2) Fix reported manifest issue. (3) Re-submit. |
| Polling causes Traefik to slow down | LOW–MEDIUM | (1) Increase interval. (2) Add consecutive-failure backoff. (3) Gate some endpoints (TLS) to a slower cadence. (4) Document in README. |
| `runtime_data` migration breaks existing users | HIGH | (1) Bump `VERSION`. (2) Write `async_migrate_entry` that transforms old to new. (3) Add a test that loads old fixtures. (4) Users auto-migrate on HA restart; no manual action needed once fixed. |
| Cert-parser format mismatch | LOW | (1) Catch `ValueError` per format string. (2) Iterate over multiple known formats. (3) Log a `debug` with the raw string once per host per 24h. (4) Gracefully mark entity `unavailable` rather than crashing. |
| Refresh service returns 200 but config didn't reload | LOW | (1) Poll for count change after the POST. (2) Return service response with `verified: bool`. (3) Document the eventual-consistency window in `services.yaml`. |

---

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| #1 Token leakage | Phase 1 (Foundation) | `grep -rn "api_key\|CONF_API_KEY" custom_components/traefik/` returns nothing in `*.py` logger paths; `tests/test_diagnostics.py` asserts no `Bearer` substring in dump |
| #2 `@<provider>` in names | Phase 2 (Core entities) | Unit test with fixture `traefik_services_with_internal.json`; assert `api@internal` filtered; assert `strip@docker` slugifies to `strip_docker` (no leading underscore) |
| #3 Router `rule` as name | Phase 2 (Entity platforms) | Snapshot test names entities by `name`, not `rule`; snapshot attributes contain full `rule` |
| #4 `ClientSession()` | Phase 1 (Foundation) | `grep -rn "aiohttp.ClientSession\|ClientSession(" custom_components/traefik/` returns zero hits |
| #5 HACS tag drift | Phase 1 (CI setup) | GitHub Action fails if `${TAG_NAME}` ≠ `manifest.version` |
| #6 `runtime_data` migration | Phase 1 (Foundation) | Test: load v1 fixture → `async_migrate_entry` → assert new `runtime_data` shape |
| #7 `UpdateFailed` vs `ConfigEntryAuthFailed` | Phase 1 + Phase 4 (Reauth) | Test: mock 401 response → assert reauth flow triggered (not `UpdateFailed`) |
| #8 Aggressive polling | Phase 1 (Foundation) | Default `update_interval = 30s` constant; clamp in Options |
| #9 Slow polling restart | Phase 1 (Foundation) | `await coordinator.async_config_entry_first_refresh()` in `async_setup_entry` |
| #10 Stale-state on failure | Phase 2 (Entity platforms) | Test: trigger `UpdateFailed` → assert entity `available` flips False |
| #11 No `unique_id` | Phase 1 (Base entity) | `pytest-homeassistant-custom-component` registry test fails if duplicate unique_id per config entry |
| #12 Noisy default-enabled | Phase 2 (Entity platforms) | Snapshot tests with `entity_registry_enabled_default = False` on diagnostic sensors |
| #13 `quality_scale` rejected | Phase 1 (Foundation) | `manifest.json` has no `quality_scale` key; CI's hassfest step passes |
| #14 TLS cert parse | Phase 3 (TLS) | Unit tests for the format-string loop with at least 3 valid Traefik date strings and 2 invalid |
| #15 Refresh is async | Phase 2 (DIAG-03) | Test: mock refresh endpoint, mock subsequent `/api/http/routers`; assert polling loop detects version change |
| M1 `entry.options` for secrets | Phase 1 (Foundation) | Schema separates data/options; test asserts `entry.data[CONF_API_KEY]` exists, not `entry.options` |
| M2 `hass.data[DOMAIN]` | Phase 1 (Foundation) | `grep -rn "hass.data\[DOMAIN" custom_components/traefik/` returns zero hits |
| M3 Missing `async_unload_entry` | Phase 1 (Foundation) | Test: setup → unload → assert coordinator is no longer in `_tasks` |
| M5 Service in `async_setup_entry` | Phase 2 (DIAG-03) | Test: two config entries → both can call the service, handlers don't clobber |
| M7 Diagnostics leak | Phase 4 (Quality scale) | Test: `await diagnostics.async_get_config_entry_diagnostics(...)` then assert dump has no token |

---

## Sources

### HIGH confidence (official docs, current as of 2026-07-05)

- **HA integration manifest schema** — https://developers.home-assistant.io/docs/creating_integration_manifest/ (`domain`, `version`, `integration_type`, `iot_class`, `codeowners`, `quality_scale` semantics verified).
- **HA config flow** — https://developers.home-assistant.io/docs/config_entries_config_flow_handler/ (`async_step_reauth`, `async_step_reconfigure`, `async_migrate_entry`, `VERSION/MINOR_VERSION` semantics).
- **HA setup failures** — https://developers.home-assistant.io/docs/integration_setup_failures/ (`ConfigEntryNotReady` vs `ConfigEntryAuthFailed` vs `UpdateFailed` semantics; first-refresh behaviour).
- **HA core entity ID regex** — `https://raw.githubusercontent.com/home-assistant/core/dev/homeassistant/core.py` lines around `_OBJECT_ID = r"(?!_)[\da-z_]+(?<!_)"` and `VALID_ENTITY_ID = re.compile(r"^" + _DOMAIN + r"\." + _OBJECT_ID + r"$")` (verified).
- **Traefik v3 Router naming** — https://doc.traefik.io/traefik/reference/routing-configuration/http/routing/router/ (`@` forbidden in router name; verified).
- **Traefik v3 API & Dashboard endpoints** — https://doc.traefik.io/traefik/reference/install-configuration/api-dashboard/ (no cert-notAfter exposure verified; `/api/http/routers/refresh` POST documented as async).
- **Traefik v3 Entrypoints middleware reference** — https://doc.traefik.io/traefik/reference/install-configuration/entrypoints/ (`<middleware-name>@<provider-name>` format verified at multiple provider examples: `default-auth@kubernetescrd`, `strip@docker`, `api@internal`).
- **HACS publish docs** — https://hacs.xyz/docs/publish/integration/ (repo structure, manifest required keys, brand assets verified).

### MEDIUM confidence (verified by code inspection of user's local integrations)

- **`gatus` integration's `entity.py:46` pattern** — `unique_id = f"{entry.entry_id}_{endpoint_key}_{sensor_type}"`. This is the canonical template for the Traefik integration.
- **`gatus` integration's coordinator exception hierarchy** — `ConfigEntryAuthFailed` separate from generic `UpdateFailed`.

### LOW confidence (single source, no official HA check yet)

- **`availability` auto-wiring on `CoordinatorEntity`** — verified against the user's `gatus` entity.py:60 (`super().available and …`) but should be confirmed in upstream HA docs before final implementation.

### Negative results (verified 404 / NOT applicable)

- **No `quality_scale` for custom integrations** — hassfest blocks this (verified against user's `gatus` & `kroki` integrations which correctly omit).
- **No `aiotraefik` PyPI package** — would have replaced the in-line `api.py` wrapper but doesn't exist.

---

*Pitfalls research for: homeassistant-traefik-integration*
*Researched: 2026-07-05*
*Confidence: HIGH — cross-verified against HA Core source (entity ID regex), Traefik v3 docs (router naming, refresh endpoint semantics), and the user's two sibling custom integrations (`gatus`, `kroki`).*
