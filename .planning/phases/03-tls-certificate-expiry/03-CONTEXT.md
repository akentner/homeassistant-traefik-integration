# Phase 3: TLS Certificate Expiry - Context

**Gathered:** 2026-07-06
**Status:** Ready for spike (pre-phase activity) → planning

<domain>
## Phase Boundary

Surface Traefik TLS certificate health into Home Assistant: for every TLS-enabled
HTTP router, expose a `sensor.traefik_<host>_cert` (`device_class: timestamp`,
`notAfter` as native value, `days_until_expiry` attribute) and a paired
`binary_sensor.traefik_<host>_expiring` (`BinarySensorDeviceClass.PROBLEM`,
ON when `days_until_expiry <= CONF_TLS_WARN_DAYS`). All TLS handshakes run via
stdlib `ssl.SSLContext.getpeercert(binary_form=False)` wrapped in
`asyncio.to_thread` on a separate `CertCoordinator` with a 6-hour cadence,
bounded by a 4-concurrent semaphore. Per-host timeout 5s. In-memory cache. Pure
TTL re-handshake (no listener-based invalidation). Any TLS error marks the
respective entity `unavailable` and never crashes the integration.

Phase 3 covers 6 requirements: TLS-01, TLS-02, TLS-03, TLS-04, TLS-05, TEST-04.
Diagnostics dump and quality-scale metadata are out of scope (Phase 4). TCP/UDP
router TLS (Traefik v3 supports TCP routers with TLS) is out of scope (PROJECT.md
HTTP-only, deferred to v2).

## Pre-Phase Activity (must complete BEFORE planning)

The ROADMAP and PITFALLS #14 strongly flag a **`/gsd:spike`** before planning
Phase 3. Validate the stdlib `ssl` handshake against 3+ real Traefik v2/v3
deployments covering:

- **SNI routing** — multiple routers under different SNI hosts on the same Traefik
  process must each resolve to the right cert.
- **Multi-cert chains** — Traefik can serve different certs per route; verify
  the handshake hits the right leaf.
- **Wildcard certs** — `*.example.com` certs; routers with TLS but no per-host
  resolution.
- **IPv6** — AAAA-only hosts may need explicit `[host]:443` handling.
- **Hostname mismatch** — cert's CN doesn't match the SNI hostname we probed.
- **Format strings** — confirm `NOTAFTER_FORMATS` loop covers observed
  `notAfter` shapes (`Nov 15 12:00:00 2025 GMT`, with/without trailing space,
  locale variants, ISO-style).

Spike deliverable: a spike document + a working `tls.py` prototype + passing
tests. The Phase 3 plan consumes the spike output.

</domain>

<decisions>
## Implementation Decisions

### Sensor & binary_sensor shape

- **D-01 (entity model):** Every TLS-enabled HTTP router becomes one
  `sensor.traefik_<host>_cert` (TraefikCertTimestampSensor,
  `SensorDeviceClass.TIMESTAMP`) and one paired
  `binary_sensor.traefik_<host>_expiring` (TraefikCertExpiryBinarySensor,
  `BinarySensorDeviceClass.PROBLEM`). `unique_id` pattern:
  `f"{entry.entry_id}_tls_cert_{host}"` and `f"{entry.entry_id}_tls_expiring_{host}"`.

- **D-02 (expansion):** Surface one entity pair per distinct hostname. The set
  of hostnames = union of (a) every `tls.domains[].main` and SAN entry on every
  TLS-enabled router, (b) every `Host(\`x\`)` match in the rule that is NOT
  already covered by (a). Routers with TLS set but no `tls.domains[]` AND no
  `Host()` match in the rule are skipped entirely (wildcard / default-cert
  setups — Traefik owns those; out of Phase 3 scope). De-duped to one entity
  per unique hostname; if multiple routers terminate TLS for the same host,
  the `router_name` attribute is a list of all such router names.

- **D-03 (expiring binary_sensor state):**
  - `state == ON` when `days_until_expiry <= CONF_TLS_WARN_DAYS` (already-expired
    certs with `days_until_expiry < 0` treated as breach — strongly
    user-actionable).
  - `state == OFF` when `days_until_expiry > CONF_TLS_WARN_DAYS`.
  - `state == unavailable` (entity itself remains registered) when the last
    cert probe errored (timeout / SNI mismatch / format-string parse failure
    / IPv6 unreachable / etc.).
  - `_attr_entity_registry_enabled_default = True` (always present, dormant in
    OFF state when not actionable). The sensor is always registered so users
    can build automations on it without first visiting the entity registry.

- **D-04 (threshold attribute visibility):** Every expiring binary_sensor
  exposes a `days_until_expiry` attribute always — so when the user changes
  `tls_warn_days` from 14 to 7, the affected entities flip ON immediately and
  the `days_until_expiry` attribute proves the change took effect (no logbook
  event, no separate notification — attribute-level visibility only).

### Coordinator & cache

- **D-05 (coordinator):** Separate `CertCoordinator(DataUpdateCoordinator)` on
  the integration's `entry.runtime_data` (or a sibling peer on
  `entry.runtime_data` — planner picks the cleanest shape), 6-hour
  `update_interval`, single instance per config entry. Per handshake:
  - 5s `asyncio.timeout(5)` per host.
  - `asyncio.to_thread` wrap so the blocking `ssl.getpeercert()` does not stall
    the HA event loop.
  - `asyncio.Semaphore(4)` — at most 4 concurrent handshakes per coordinator
    cycle. Bounded so a 200-router Traefik doesn't hammer the host network all
    at once.

- **D-06 (cache):** In-memory `dict[str, CertInfo]` (host → parsed cert info)
  on the `CertCoordinator` instance. Parsed `CertInfo` shape:
  ```python
  @dataclass(frozen=True)
  class CertInfo:
      not_after: datetime  # UTC
      days_until_expiry: int
      last_error: str | None  # None on success
      fetched_at: datetime  # UTC, when this cert was probed
  ```
  Cache dies on unload (no persistence). Entities mark unavailable when their
  cached entry's `last_error` is non-None.

- **D-07 (re-handshake policy):** Pure 6h TTL — the main `TraefikCoordinator`'s
  router-list changes do NOT trigger a re-handshake. New TLS-enabled routers
  get sensors on the next 6h tick (Phase 2's existing stale-cleanup listener
  removes sensors for hosts no longer present). Simpler, predictable. The
  threshold re-evaluation path (D-08) is the only listener-driven edge case.

### Options Flow integration

- **D-08 (threshold live-re-eval):** Extend the existing
  `_async_options_updated` listener (`custom_components/traefik/__init__.py:149`)
  to ALSO push the new `CONF_TLS_WARN_DAYS` to `CertCoordinator.threshold`
  and call `coordinator.async_update_listeners()` so all cert entities
  re-render immediately. No re-handshake on threshold change — the cached
  cert data is unchanged; only the threshold applied to it shifts. Binary
  sensors flip ON/OFF within ~1 second of the user saving the option.

- **D-09 (options scope):** Phase 2 already registered `CONF_TLS_WARN_DAYS`
  (default 14, clamp 1..90) in `config_flow.py:92` and validated it. No new
  options field — Phase 3 wires the existing knob to CertCoordinator.

### Error handling

- **D-10 (never crash):** Every TLS error path (timeout, SNI mismatch, format
  string parse failure, IPv6 unreachable, OSError on socket, etc.) is caught
  inside the `tls.py` helper and recorded as `CertInfo(last_error=...)`.
  Exceptions DO NOT propagate to the CertCoordinator. The coordinator's
  `_async_update_data` returns the partial cache; the `ConfigEntryNotReady`
  recovery machinery is reserved for the main coordinator (Phase 1 D-15).
  Cert errors are local — the integration keeps polling, other entities stay
  healthy, the affected entity simply shows `unavailable`.

- **D-11 (parsing format strings):** `NOTAFTER_FORMATS` tuple iterates
  multiple known shapes (PITFALLS #14):
  - `"%b %d %H:%M:%S %Y %Z"` — `Nov 15 12:00:00 2025 GMT` (with timezone)
  - `"%b %d %H:%M:%S %Y"` — `Nov 15 12:00:00 2025` (locale fallback)
  - `"%b  %d %H:%M:%S %Y %Z"` — `Nov  15 12:00:00 2025 GMT` (double-digit
    day-of-month padding)
  - Any additional shape observed in the spike gets added.
  On full miss: log `_LOGGER.debug` once per host per 24h with the raw string,
  set `last_error="notAfter parse failed"`.

### Claude's Discretion

- Exact `CertCoordinator` storage location on `entry.runtime_data` (sibling
  vs. wrapped — e.g., a `CertRuntime(coordinator=CertCoordinator(...))` or
  `entry.runtime_data = TraefikRuntime(coordinator=..., cert=...)`). Phase 2
  established the bare-coordinator model; Phase 3 either extends the type
  alias or stores a sibling. Planner picks the version that minimises
  regression risk (PITFALLS #6 reminder).
- Whether the `tls_warn_days` clamping stays 1..90 (Phase 2 default) or
  extends to a wider range. Phase 2's clamps are valid for Phase 3.
- `tls.py` module layout — single function vs. small class. Single
  function is simpler; a `class TlsFetcher(hass, sem=4, timeout=5)` is more
  testable. Pick whichever unit-tests more cleanly.
- Exact ordering / wording in `strings.json` for any new state strings
  (unavailable-state explanations, etc.).
- Stale-cert cleanup of old `tls_cert_<host>` entities when the host is
  removed from all routers — Phase 2's `coordinator.async_add_listener`
  pattern generalises naturally (check `unique_id` prefix against cache
  contents each cycle).
- Optional: a small "TLS certs by issuer" attribute aggregation on the
  Overview device. Not in the ROADMAP; skip unless a clear UX value
  surfaces.
- Domain language: sensor icon choice (`mdi:certificate`, `mdi:lock-alert`,
  `mdi:shield-lock`); binary_sensor icon (`mdi:lock-alert` on, `mdi:lock`
  off, per HA UX conventions).

### Folded Todos

None — `todo match-phase 3` would be queried by the cross-reference step but
the project has no `todos/` queue at this time (matches prior phases 1 and 2).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project planning artifacts

- `.planning/PROJECT.md` — Vision, Core Value, TLS-01/02/03 listed in
  Active requirements, Out-of-Scope (acme.json, chain validation,
  per-middleware entities, etc.)
- `.planning/REQUIREMENTS.md` — TLS-01..05 + TEST-04 (6 requirements mapped
  to Phase 3 with phase traceability); CFG-05 already covers the
  `tls_warn_days` Options Flow knob (Phase 2)
- `.planning/ROADMAP.md` — Phase 3 success criteria 1-5, plans 03-01..03-03,
  pre-phase `gsd-spike` activity (REQUIRED before planning)
- `.planning/STATE.md` — "Phase 3 — pending" decision (stdlib ssl, sem=4,
  6h cadence, spike first)
- `.planning/research/SUMMARY.md` — Phase 3 implications: separate
  CertCoordinator with 6h cadence, two-coordinator split justification,
  `asyncio.to_thread` for blocking sockets, format-string loop per PITFALLS #14
- `.planning/research/PITFALLS.md` — **Pitfall #14 (TLS cert parse — format
  string loop, locale bugs, defense in depth)** is the central reference
  for the spike; **Pitfall #6 (runtime_data shape migration)** for the
  coordinator-storage decision; **Pitfall #12 (noisy default-enabled)**
  gives the precedent for `_attr_entity_registry_enabled_default` decisions;
  M-12 specifically mentioned cert-expiry-default-off in PITFALLS #12
- `.planning/research/STACK.md` — Python stdlib `ssl` + `socket.create_connection`
  + `asyncio.open_connection` + `asyncio.timeout` + `asyncio.to_thread`
  (HA bundles everything; no `cryptography` import)
- `.planning/phases/01-foundation/01-CONTEXT.md` — D-01 bare-coordinator
  runtime_data; D-14 bearer per-request header; D-15 exception dispatch
  (`ConfigEntryAuthFailed` / `UpdateFailed`)
- `.planning/phases/02-core-entities-options-reauth-reload/02-CONTEXT.md` —
  D-02 `TraefikEntity` per-category device model (HTTP Routers device
  identifier `(DOMAIN, f"{entry_id}_http_routers")`); D-06 `filter_internal_items`
  helper; D-08 `_async_options_updated` listener on
  `entry.options.get(...)`; D-09 existing `tls_warn_days` knob with default
  14; D-15/D-16/D-17 sensor platform patterns; D-18 `coordinator.async_add_listener`
  stale-cleanup pattern; D-20 extra_state_attributes discipline

### Spike output (consumed by Phase 3 plan)

- `.planning/spikes/03-tls-handshake/SPIKE.md` — the spike deliverable
  (planned artifact path; spike workflow creates it). MUST be read before
  plan-phase. Contains: validated scenarios, observed `notAfter` shape
  catalogue, recommended `NOTAFTER_FORMATS` tuple, IPv6 handshake notes,
  SNI-host selection policy, any protocol-level gotchas (e.g. Traefik
  returns 421 / no-cert-on-port cases).
- `custom_components/traefik/tls.py` — spike prototype (planned artifact).
  Useful as a starting point for plan-phase.

### Home Assistant Core docs (verified)

- `https://developers.home-assistant.io/docs/core/entity/sensor/` —
  `SensorDeviceClass.TIMESTAMP` + `SensorStateClass.MEASUREMENT` /
  `MEASUREMENT` semantics on attributes for `days_until_expiry`
- `https://developers.home-assistant.io/docs/core/entity/binary-sensor/` —
  `BinarySensorDeviceClass.PROBLEM` semantics
- `https://developers.home-assistant.io/docs/creating_integration_manifest/` —
  manifest schema (no changes needed; Phase 1 manifest is sufficient for
  Phase 3 entities)
- `https://developers.home-assistant.io/docs/integration_setup_failures/` —
  reminder that `ConfigEntryNotReady` is reserved for the main coordinator;
  TLS errors must NOT bubble through here (D-10)

### Home Assistant skill references (local)

- `~/.opencode/skills/integrations/SKILL.md` — primary HA integration
  reference (config flow, DataUpdateCoordinator, entity naming,
  CoordinatorEntity availability)

### User's reference integrations (local, sibling patterns)

- `/home/akentner/Projects/homeassistant-gatus-integration/` — bare
  coordinator-in-runtime_data pattern; PEP-695 `type` alias;
  `ConfigEntryAuthFailed` exception mapping; stale-entity cleanup via
  `coordinator.async_add_listener`
- `/home/akentner/Projects/homeassistant-kroki-integration/` —
  `after_dependencies: ["http"]` manifest pattern

### Traefik API docs (verified Jul 2026)

- `https://doc.traefik.io/traefik/reference/install-configuration/api-dashboard/`
  — confirms Traefik's HTTP API does NOT expose certificate `notAfter`;
  justifies out-of-band TLS handshake approach
- `https://doc.traefik.io/traefik/reference/routing-configuration/http/routing/router/`
  — router `tls` block schema (`tls.domains[].main`, `tls.domains[].sans`,
  `tls.certResolver`)
- `https://doc.traefik.io/traefik/reference/install-configuration/tls/`
  — TLS configuration docs (validated default-cert and wildcard handling)

### Python stdlib reference

- `https://docs.python.org/3/library/ssl.html#ssl.SSLContext.getpeercert` —
  `getpeercert(binary_form=False)` returns dict with `notAfter`, `notBefore`,
  `subject`, `issuer`, `serialNumber`, `version`
- `https://docs.python.org/3/library/socket.html` — `socket.create_connection`
  for the blocking-with-timeout wrapping inside `to_thread`

### HACS distribution docs

- `https://hacs.xyz/docs/publish/start` — `hacs.json` (Phase 1 schema;
  no changes for Phase 3)
- `https://hacs.xyz/docs/publish/integration` — repository layout
  (no changes; Phase 3 adds files inside `custom_components/traefik/`)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets

- **`TraefikEntity` base** (`custom_components/traefik/entity.py:38-97`) —
  Used by every entity platform. Phase 3 introduces a new category
  `"http_routers_tls"` (distinct from the Phase 2 `http_routers` device so
  TLS sensors live under a separate device card and per-category identifier
  `(DOMAIN, f"{entry.entry_id}_http_routers_tls")`). Device model label
  "HTTP Routers TLS" — added to `_CATEGORY_TO_MODEL` in `entity.py:20`.
  Inherits `_attr_has_entity_name`, `sw_version` from coordinator,
  per-category identifier, and `DeviceEntryType.SERVICE`.

- **`TraefikCoordinator` class** (`coordinator.py:61-102`) — already exposes
  `self.client` for direct access; `_async_update_data` already maps
  auth errors → `ConfigEntryAuthFailed` (Phase 1 D-15). Phase 3 leaves this
  untouched and adds a sibling `CertCoordinator` rather than wrapping the
  existing class (avoids `runtime_data` shape migration per PITFALLS #6).

- **`filter_internal_items`** (`api.py:29-45`) — Reused if Phase 3 needs to
  drop TLS-internal routers (`api@internal` is excluded by Phase 2 already;
  the helper continues to apply). No change needed.

- **`TraefikEntity.async_add_listener`-driven stale cleanup** (Phase 2 D-18,
  binary_sensor.py:67-92 / sensor.py:103-142) — Pattern reused for Phase 3
  TLS entities. The cleanup callback reads `coordinator.data['tls_certs']`
  (or whatever cache shape Phase 3 settles on) and removes registry entries
  whose host is no longer present.

- **`_async_options_updated` listener** (`custom_components/traefik/__init__.py:149-168`)
  — Already mutates `coordinator.update_interval` on Options change. Phase 3
  extends this single function to also push `CONF_TLS_WARN_DAYS` to
  CertCoordinator and trigger `cert_coordinator.async_update_listeners()`
  (D-08).

- **`_friendly_rule` regex** (`binary_sensor.py:24-32`) — Returns the first
  `Host(\`x\`)` match from a router's rule. Reused by Phase 3's hostname
  extraction for the fallback case (`tls.domains[]` empty; `Host()` in rule
  covers the host).

- **`entry.options[CONF_TLS_WARN_DAYS]` already wired** — Phase 2's
  `config_flow.py:92,165,191` already accepts and validates the knob (default
  14, clamp 1..90). No new options field.

- **Phase 2 sensor pattern** (`sensor.py:172-225` —
  `TraefikEntrypointSensor`/`TraefikServiceSensor`) — Template for
  Phase 3's `TraefikCertTimestampSensor`. Uses `_attr_unique_id = ...`,
  `entity_id = ...`, `_attr_name = ...` set explicitly per CONTEXT.md D-09
  prefix convention. `available = self.coordinator.last_update_success`
  baseline plus Phase 3-specific `host_present_in_cache` override.

- **Phase 2 binary_sensor any-failing pattern** (binary_sensor.py:142-209
  — `TraefikAnyRouterFailingBinarySensor`) — Template for Phase 3's
  `TraefikCertExpiryBinarySensor`. `BinarySensorDeviceClass.PROBLEM`,
  `is_on` returns boolean or None, `available = self.coordinator.last_update_success`.

- **`async_to_thread` import** — `from homeassistant.helpers.async_helpers`
  (or stdlib `asyncio.to_thread` since 3.9) — for wrapping `ssl.getpeercert`
  blocking call so the HA event loop stays responsive.

### Established Patterns

- **PEP-695 type aliases** — `type TraefikConfigEntry =
  ConfigEntry["TraefikCoordinator"]`. Phase 3 needs a parallel
  `CertConfigEntry` (or extends the alias with a typed `runtime_data`
  wrapper) — planner picks.
- **`async_get_clientsession(hass)`** — NOT used here (TLS uses stdlib
  `socket` + `ssl`, not aiohttp). The HA-shared-session rule is
  aiohttp-only.
- **Bearer per-request header** — NOT applicable (TLS handshake is
  unauthenticated by design).
- **Lazy log formatting** — `_LOGGER.debug("host=%s err=%s", host, err)`
  in the cert caching helper; never include the full `CertInfo` in a log
  message.
- **TypedDict (total=False)** — main `TraefikData` is a TypedDict. Phase 3
  decides whether cert data lives inside it (adds `'tls_certs'` key) or in
  CertCoordinator's own typed payload.
- **`coordinator.async_add_listener` for stale cleanup** — replicated
  verbatim from Phase 2 D-18.
- **`_attr_entity_registry_enabled_default`** — Phase 2 D-14 used `False`
  on `TraefikAnyRouterFailingBinarySensor` (PITFALLS M-12). Phase 3
  D-03 inverts this to `True` for `TraefikCertExpiryBinarySensor` per the
  user's explicit "auto-enable when threshold breached" decision (always
  registered; state maps to breach/no-breach).

### Integration Points

- `custom_components/traefik/__init__.py` —
  `async_setup_entry` extended to instantiate `CertCoordinator` alongside
  `TraefikCoordinator`; store both on `runtime_data`. `_async_options_updated`
  extended per D-08. `async_unload_entry` also unloads `CertCoordinator`.
- `custom_components/traefik/const.py` — Add
  `CONF_TLS_CERT_COOLDOWN = "tls_cert_cooldown"` constant (for possible
  future override; default 21600s) and `DEFAULT_TLS_CERT_COOLDOWN = 21600`
  (= 6h). Phase 3 may NOT add `CONF_TLS_WARN_DAYS` (already present from
  Phase 2). Optional: `TLS_SEMAPHORE = 4`, `TLS_HANDSHAKE_TIMEOUT = 5.0`.
  Add `"http_routers_tls"` to `_CATEGORY_TO_MODEL` (in entity.py).
- `custom_components/traefik/entity.py` — Add `"HTTP Routers TLS": "http_routers_tls"`
  mapping to `_CATEGORY_TO_MODEL` (line 20).
- `custom_components/traefik/coordinator.py` — Optionally extend
  `TraefikData` TypedDict with a `'tls_certs'` key for the cached payload
  shape, OR define a parallel `CertData` TypedDict for `CertCoordinator.data`.
- `custom_components/traefik/sensor.py` — Add
  `TraefikCertTimestampSensor(SensorDeviceClass.TIMESTAMP, native_value:
  datetime | None)`. One per cached host.
- `custom_components/traefik/binary_sensor.py` — Add
  `TraefikCertExpiryBinarySensor(BinarySensorDeviceClass.PROBLEM)`. One per
  cached host. `is_on` = `(days_until_expiry <= threshold)`.
- `custom_components/traefik/tls.py` — NEW. Hosts
  `fetch_cert_not_after(host: str, port: int, *, timeout: float = 5.0) ->
  CertInfo | CertError`. Wraps `ssl.SSLContext.wrap_socket` +
  `socket.create_connection`. Imports `asyncio.to_thread` for the blocking
  call.
- `custom_components/traefik/cert_coordinator.py` — NEW. Hosts
  `CertCoordinator(DataUpdateCoordinator)`. _async_update_data iterates
  the union-of-hosts set, fans out `asyncio.to_thread(fetch_cert_not_after,
  ...)` calls bounded by a `Semaphore(4)`, returns the merged cache dict.
  Hosts the `threshold` attribute that the options listener writes to.
- `custom_components/traefik/strings.json` — Add state strings for
  `'unavailable'` (e.g., `'Cert probe failed'`), threshold-related help text,
  cert sensor name. Phase 4 may add German translation.
- `tests/components/traefik/` — NEW files (TEST-04):
  `test_tls.py` (format-string parse, IPv6, SNI, hostname-mismatch),
  `test_cert_coordinator.py` (semaphore, timeout, threshold re-eval,
  cache shape), `test_sensor_tls.py` (timestamp sensor + attributes),
  `test_binary_sensor_tls_expiring.py` (state transitions, threshold
  live-re-eval).

</code_context>

<specifics>
## Specific Ideas

- **Domain device separation:** HTTP Routers device (Phase 1/2) hosts the
  `binary_sensor.traefik_http_router_<name>` running-or-not sensors. Phase
  3's cert sensors live on a **separate device** ("HTTP Routers TLS" /
  identifier `(DOMAIN, f"{entry_id}_http_routers_tls")`). Benefits:
  users can pin device-info permission scopes (the TLS device only talks
  to cert-expiry endpoints, network-wise — clearer security model), and
  the states panel groups cert-sensor noise separately from router-status.

- **Two-coordinator split rationale (mirrors Phase 1 D-08 spec
  rationale):** Putting TLS handshakes on the 30s main coordinator cycle
  is wasteful and slow (TLS handshake to cold-start routers can take 2-3s
  each; 50 routers × 3s serialized = 150s cycle). Splitting to a slow
  6h coordinator keeps the main loop lean and matches cert-expiry
  timescales naturally. The two cycles are independent — main coordinator
  failures don't block cert coordinator, vice versa.

- **`days_until_expiry` semantics:** Always reflects `(not_after - utcnow()).days`
  with a floor on `0` so a 1-hour-out cert still shows `0` rather than `-1`
  until the next cycle (avoids alarming users on hour-boundary UTC vs local
  time confusion). Negative values only when actual cert is past
  `not_after`. Already-expired certs keep the negative count so users can
  see "how expired" via the attribute.

- **Threshold re-eval UX rationale:** A user configuring Phase 3's
  whole reason for existing is "I want to be warned before my cert
  expires". If they lower the threshold from 14d to 7d to be safer,
  the response should be immediate — they want to know if any cert is
  within 7d right now. The 6h tick is for cert data freshness; the
  threshold change is a UX concern. D-08 captures this distinction.

- **`CONF_TLS_WARN_DAYS` already in Options Flow:** Phase 2 shipped it
  as a placeholder (CONTEXT.md D-09 / const.py:28 / config_flow.py:92).
  Phase 3 just wires it. No regression on existing setups that already
  configured a non-default value (Phase 2 listener already stores it).

- **Spike-first ordering is non-negotiable:** Per ROADMAP Phase 3
  pre-phase activity and PITFALLS #14, planning against the stdlib
  approach without spike validation risks choosing a broken approach.
  The 4-area decisions captured in this CONTEXT.md (entity shape,
  expiring state, threshold re-eval, cache strategy) are orthogonal
  to the handshake-mechanics decisions the spike will validate (SNI,
  format strings, IPv6, hostname mismatch). Spike output will be folded
  into the Phase 3 plan as inputs, not constraints.

- **Quality-scale Bronze alignment:** D-03 (always-enabled binary_sensor)
  matches HA quality-scale Bronze rule for "entity should be available
  whenever the device is set up" rather than the noise-reduction M-12
  precedent. Phase 3 explicitly diverges from M-12 here because the
  user wants the entity visible (dormant OFF state communicates
  "all certs are fine") rather than hidden behind an opt-in.

</specifics>

<deferred>
## Deferred Ideas

### Pre-phase spike (REQUIRED before planning)

- **`/gsd:spike`** validate stdlib TLS handshake against 3+ real Traefik v2/v3
  deployments before the Phase 3 plan. See Pre-Phase Activity section above.

### Reviewed Todos (not folded)

None — no todos match this phase (todo_count: 0).

### Other deferred items from discussion

- **TLS chain validation / SAN walk** — REQUIREMENTS.md TLS-V2-01..03
  (chain trust path, subject/issuer attributes). v2 territory; out of
  Phase 3 scope.
- **Cert renewal triggering / ACME actions** — explicitly out of scope
  per PROJECT.md Out-of-Scope ("Traefik owns cert lifecycle").
- **Qualys SSL Labs-style letter grade** — out of scope; the integration
  surfaces expiry only, not chain quality or vulnerability assessment.
- **Per-middleware entities** — explicitly out of PROJECT.md scope.
- **TCP/UDP router TLS** — PROJECT.md HTTP-only; v2.
- **HTTP/3 (QUIC) cert probing** — Traefik v3 supports HTTP/3 over QUIC
  on UDP; the stdlib TLS handshake path doesn't naturally extend to
  QUIC. v2 if/when Traefik exposes HTTP/3 in API responses.
- **Persistent cert cache across HA restarts** — D-06 in-memory only;
  storage to `hass.config.path()` deferred to v2 (avoid stale-data UX).

### Phase scope reminders (NOT to be added in Phase 3)

These would be scope creep — flagged explicitly so the planner does NOT add them:

- ❌ Repairs flow for expired certs (Phase 4 scope).
- ❌ `diagnostics.py` with credential redaction (Phase 4).
- ❌ Quality-scale metadata file (Phase 4 polish).
- ❌ Translations (`de.json` etc.; Phase 4).
- ❌ GitHub Actions release-enforcement CI (Phase 4).
- ❌ `CHANGELOG.md` / `info.md` (Phase 4).
- ❌ FAQ additions for cert-related locked-out features (Phase 4).

### Pending research for later phases (locked by ROADMAP / Phase 4)

- **Phase 4 (Quality):** diagnostics surface area for cert data — redact
  fingerprint / serial number but expose `notAfter` summary? Decide
  during Phase 4 discussion.

</deferred>

---

*Phase: 03-tls-certificate-expiry*
*Context gathered: 2026-07-06*
</content>
</invoke>