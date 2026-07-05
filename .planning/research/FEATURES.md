# Feature Research — Home Assistant Traefik Integration

**Domain:** Home Assistant custom integration wrapping a local reverse-proxy (Traefik v2/v3) HTTP API.
**Researched:** 2026-07-05
**Confidence:** HIGH for table-stakes / differentiators; HIGH for anti-features (anchored in PROJECT.md explicit "Out of Scope" list).
**Existing artifact this feeds:** PROJECT.md `Active` requirements (CORE-01..06, TLS-01..02, DIAG-01..03, CFG-01, DOCS-01, TEST-01).

---

## Executive Summary

A reverse-proxy integration in HA has the same fundamental shape as any "polling
HTTP API" integration (it sits in the same family as the user's `gatus` and
`kroki` integrations): one **device**, many **entities**, a **coordinator** as
the single poll point, and a **config flow + options flow** for setup. What makes
Traefik *specific* is the shape of what it exposes — **routers, services,
middlewares, entrypoints, TLS** — and the fact that self-hosters expect to
monitor (a) which routes are actually healthy, (b) which TLS certs are about to
expire, and (c) whether the proxy itself is up.

The feature landscape for a **read-mostly** Traefik HA integration is
remarkably well-defined. Looking at the Traefik v3.7 API surface, the most
commonly-wired HA entities map cleanly to one Traefik endpoint each:

| HA entity type | Maps to Traefik endpoint | Why users want it |
|---|---|---|
| `binary_sensor` per router | `GET /api/http/routers` (`status` field) | "Is my service reachable?" |
| `binary_sensor` per TLS cert | TLS handshake to router host (not in API) | "Will my domain break next week?" |
| `sensor` per entrypoint | `GET /api/entrypoints` | "Is the listener bound?" |
| `sensor` per service | `GET /api/http/services` | "How many backends? Are they healthy?" |
| aggregate `sensor.traefik` | `GET /api/overview` | One-line health tile |
| `reload` HA service | `POST /api/http/routers/refresh` | Manual nudge after external config edits |

Things users explicitly **do not** want this integration to do (and the
ecosystem confirms it): mutate Traefik config files on disk, expose a UI
router editor (Traefik's own dashboard already does this), support Traefik v1
(EOL), or implement WebSocket streaming (polling is sufficient for
self-hosters).

---

## Traefik API → HA Entity Map (verified against Traefik v3.7 docs)

Each Traefik endpoint used, with the HA entity (or entities) it maps to.

| Traefik endpoint | Method | HA entity output | Field used | Notes |
|---|---|---|---|---|
| `/api/version` | GET | `device` sw_version + diagnostic attribute | `Version`, `Codename`, `StartDate` | Auth check lives here too |
| `/api/entrypoints` | GET | `sensor` per entrypoint | `name`, `address`, `http.redirections` (optional attr) | One entrypoint = one sensor |
| `/api/http/routers` | GET | `binary_sensor` per router | `name`, `status`, `rule`, `entryPoints`, `service`, `tls` | The flagship table-stakes entity |
| `/api/http/routers/{name}` | GET | (optional) `binary_sensor` rich attrs | adds `using`, `provider` | Useful for diagnostics only |
| `/api/http/services` | GET | `sensor` per service | `name`, `loadBalancer.servers`, `loadBalancer.strategy` (status attr) | Server URLs can leak; flag in docs |
| `/api/http/middlewares` | GET | count-only attribute | `name`, `type` | Used in DIAG-01 aggregate; full per-middleware entity is anti-feature (low value, many middlewares) |
| `/api/overview` | GET | aggregate `sensor.traefik` | `http.routers`, `http.services`, `http.middlewares`, `tcp.routers`, etc. | Single dashboard tile |
| `/api/http/routers/refresh` | POST | `traefik.reload_routers` HA service | none (mutate endpoint) | DIAG-03; user-triggered |

The detailed per-router/per-service `…/{name}` endpoints are intentionally
**not** polled in v1 — everything we need is in the list endpoints, and
batching them saves N×N round trips.

---

## Feature Landscape

### Table Stakes (Users Expect These)

Features users assume exist in any integration that wraps a polling HTTP API
for an infra component. Missing these = users leave and write a custom template
sensor.

| # | Feature | Why Expected | HA Requirement | Traefik API | Complexity | Dependencies | Notes |
|---|---|---|---|---|---|---|---|
| T-1 | **Config Flow (UI setup)** | HA's modern path; YAML-only is deprecated in user perception. Without it, the integration feels broken in 2026. | CORE-01 | n/a (entry form) | LOW | none | Two-step: URL → API key + connectivity test |
| T-2 | **YAML config support** | Required by PROJECT.md; matches user's other custom integrations; needed for pinned releases and Ansible deploys | CORE-02 | n/a | LOW | none | Adds `async_step_yaml` import path in `config_flow.py` |
| T-3 | **DataUpdateCoordinator polling pattern** | HA Bronze quality scale mandatory; without it, every entity polls independently (N×N calls) | all CORE / DIAG | every API call | LOW | T-1 | Single 10 s timeout per fetch, `asyncio.gather` for parallel endpoint fetches |
| T-4 | **Reconnect / `ConfigEntryNotReady` on failure** | Self-hoster's Traefik may be down when HA boots; integration must not be stuck in failed state | implicit in CORE-01 | n/a | LOW | T-1, T-3 | Catch `aiohttp.ClientError` → `UpdateFailed` → auto-retry by HA |
| T-5 | **Reauth flow (configurable API key)** | API key rotation; Traefik can also reject unknown keys. Without it, user must delete + re-add the integration. | implicit in CORE-01 (no separate ID; rolled into CFG-01) | `/api/version` for verification | LOW | T-1 | `async_step_reauth` + `async_set_unique_id` + `ConfigEntryAuthFailed` on 401 |
| T-6 | **Reconfigure flow (URL/options without re-add)** | Users occasionally move Traefik to a new host (VM migrate, IP change). Without it, manual delete+re-add is ugly. | CFG-01 | `/api/version` | LOW | T-1 | `async_step_reconfigure` blocks on unique_id mismatch |
| T-7 | **Options Flow (scan interval, TLS warn threshold, TLS verify)** | Users legitimately want different poll cadences for proxy state vs. cert expiry. Hardcoding = constant GitHub issue. | CFG-01 | n/a | LOW | T-1 | Bound via `entry.add_update_listener` to apply on save |
| T-8 | **`sensor.traefik` aggregate (counts)** | Top-level "is my proxy up and how big is it" tile. Same pattern as every HA infra integration (Pi-hole, NAS, etc.). | DIAG-01 | `/api/overview` | LOW | T-3 | `sensor` device_class=`enum` or `measurement`, attrs `routers/services/middlewares/tcp/udp` |
| T-9 | **Per-router `binary_sensor` (status)** | "At a glance, which of my 12 routers are failing?" — the Core Value from PROJECT.md | CORE-04 | `/api/http/routers` | LOW | T-3 | `_attr_device_class = BinarySensorDeviceClass.RUNNING` (or `CONNECTIVITY` — pick RUNNING for proxy/infra); name = `@router` rule identifier |
| T-10 | **Per-entrypoint `sensor` (address + state)** | Sanity check that listeners are bound; users map `entrypoint.websecure` → `sensor.traefik_entrypoint_websecure` in dashboards | CORE-05 | `/api/entrypoints` | LOW | T-3 | One sensor per entrypoint, `address` as attr |
| T-11 | **Per-service `sensor` (server count, LB strategy)** | "My service has 3 backends, are they all up?" — exposed as a count + attribute list | CORE-06 | `/api/http/services` | LOW–MED | T-3 | Severity bump MED because server URL list could leak to HA log; redact in `extra_state_attributes` or make opt-in |
| T-12 | **Device registry grouping** | Users expect a single "Traefik" device in the Integrations panel with all entities under it. Without it, entities feel orphaned. | implicit (HA Bronze) | n/a | LOW | T-1 | `_attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, manufacturer="Traefik", model="Reverse Proxy", sw_version=..., configuration_url=...)` |
| T-13 | **Stale entity cleanup** | When a router/service disappears, the entity should leave the registry, not become permanently "unavailable" | implicit (HA Bronze) | n/a | LOW | T-3, T-9/T-10/T-11 | `coordinator.async_add_listener(_remove_stale)` pattern (mirrors user's `gatus`) |
| T-14 | **Diagnostics dump** | When something breaks, users attach a diagnostics file rather than pasting logs. Gold-tier quality; matches PROJECT.md "Gold" plan. | implicit (HA Silver+ expectation) | n/a | MED | T-3 | `diagnostics.py` exporting sanitized API responses (token-stripped) |
| T-15 | **HACS distribution (HACS JSON + README + brand icons)** | Without HACS, the integration is invisible to the ~80% of HA users who install via HACS | DOCS-01 | n/a | LOW | T-1 | `hacs.json` + `custom_components/traefik/brand/icon.png` |

### Differentiators (Competitive Advantage)

Features that set this integration apart from "just template sensors". Most
of these map directly to specific Traefik concepts that no other simple HA
integration covers today (Traefik-specific niche).

| # | Feature | Value Proposition | HA Requirement | Traefik API | Complexity | Dependencies | Notes |
|---|---|---|---|---|---|---|---|
| D-1 | **TLS cert expiry sensor (`days_until_expiry`)** | **This is the killer feature.** Self-hosters burn themselves on expired LE certs every 90 days; surfacing this inside HA lets you wire a Notify automation. Nobody else does this for Traefik today. | TLS-01 | TLS handshake to router's host (stdlib `ssl`) — Traefik's HTTP API does **not** expose `notAfter` | MED–HIGH | T-3 | Parse `Host(\`hass.example.com\`)` from router rule; open `asyncio.open_connection(host, 443, ssl=True)` with `asyncio.timeout(5)`; read `getpeercert()['notAfter']` |
| D-2 | **`binary_sensor` "cert expiring soon"** | The boolean that powers automations: "notify me when this hits 14 days" | TLS-02 | derived from D-1 | MED | D-1 | `_attr_device_class = BinarySensorDeviceClass.PROBLEM`; threshold from options |
| D-3 | **Aggregate "any router failing" `binary_sensor`** | One binary on the dashboard that goes red when *any* router is non-enabled. Pattern of every infra integration (Pi-hole "blocking disabled"). | DIAG-02 | derived from `/api/http/routers` | LOW | T-3, T-9 | `_attr_device_class = BinarySensorDeviceClass.PROBLEM`; attrs list failing routers |
| D-4 | **`traefik.reload_routers` HA service** | Lets a user wire "after I edit dynamic config + restart the docker container, force Traefik to refresh routes" into one automation. Distinct from restarting HA. | DIAG-03 | `POST /api/http/routers/refresh` | LOW | T-1 | `services.yaml` + `platform.async_register_entity_service` pattern from HA docs |
| D-5 | **Router attribute `rule` exposed** | "Which HA entity is `Host(\`hass.example.com\`)` actually forwarding to?" — `extra_state_attributes: {rule, service, entry_points}` | implicit (part of T-9) | `/api/http/routers` | LOW | T-3 | Strips password-like strings from rule (defensive; HA rarely has them) |
| D-6 | **Traefik version sensor** | "What version is my proxy?" — `sensor.traefik_version` with `sw_version` on the device | implicit (diagnostics) | `/api/version` | LOW | T-3 | Two fields: `sensor` showing version string + `StartDate` as attr |
| D-7 | **Service-type middlewares listed per router** | Routers list their middleware chain; users may want to know "this router runs through auth + ratelimit". Useful but secondary. | implicit (part of T-9 attrs) | `/api/http/routers` → `middlewares` list | LOW | T-3 | Include as comma-separated attr; do **not** spawn per-middleware entities (anti-feature — see below) |
| D-8 | **Configurable TLS warn threshold (days)** | Different self-hosters use different LE windows (90d, 30d, custom). The threshold must be user-configurable. | CFG-01 | n/a | LOW | T-7, D-2 | `vol.Optional(CONF_TLS_WARN_DAYS, default=14)` in options flow |
| D-9 | **Configurable scan interval for proxy state vs. cert expiry** | Proxy state can change in seconds; cert expiry can wait hours. Two cadences prevents hammering the routers' TLS ports. | CFG-01 | n/a | MED | T-3, D-1 | Two coordinators: state (default 30 s) + certs (default 6 h). Pattern: extend `DataUpdateCoordinator` twice. |
| D-10 | **Differentiator: separate update interval for the proxy state vs. TLS handshake** | This is what makes D-9 actually achievable; users care about it because hammering their own router's TLS port every 30 s is wasteful. | (part of D-9) | n/a | LOW | D-9 | Single integration, two `DataUpdateCoordinator` subclasses |
| D-11 | **Service health (per-backend up/down via `/api/http/services`) attribute** | For a load-balancer service with multiple servers, expose `server_statuses` (list of `{url, status}`) — same shape nginx-proxy-manager-switches uses (though they use switches). | (part of T-11) | `/api/http/services` | MED | T-3 | Only useful if `loadBalancer.healthcheck` is configured server-side; otherwise attr is empty |

### Anti-Features (Commonly Requested, Often Problematic)

Features that seem good but create problems. **All five of these are
explicit "Out of Scope" in PROJECT.md.** Listed here so they're not
re-litigated in `/gsd-discuss-phase`.

| # | Anti-Feature | Why Requested | Why Problematic | What to Do Instead |
|---|---|---|---|---|
| A-1 | **Mutate Traefik dynamic config files on disk** | "Let me add a router from HA!" | (a) Traefik's config files are the source of truth; mutating from HA creates two writers → race conditions. (b) Path/access depends on deployment (file, Docker label, K8s CRD, Consul, etc.) — six different code paths. (c) The Traefik dashboard already exists and does this well. | Use the Traefik dashboard; the integration is read-only. |
| A-2 | **ACME / Let's Encrypt flow in HA** | "Auto-provision certs from HA!" | Traefik handles ACME natively (`certResolver`); duplicating it in HA = two systems fighting over the same `acme.json` file. | Trust Traefik; expose cert expiry via D-1/D-2 only. |
| A-3 | **Built-in route / middleware editor UI** | "Let me click to add a router!" | Traefik dashboard already does this in 100x more detail. Re-implementing in HA = years of UI work for a worse experience. | Link to Traefik dashboard from `DeviceInfo.configuration_url`. |
| A-4 | **Traefik v1 support** | "I have an old v1 still!" | v1 is EOL since 2021, different API shape (`/api/providers` etc.), ~5% of users. Doubles test surface, branches code, prevents using v2-only features. | Document v2.11+ / v3.x requirement; link to v1→v2 migration in README. |
| A-5 | **WebSocket streaming via `/api/websocket`** | "Real-time updates!" | (a) Traefik's WebSocket pushes events but not router config diffs; you'd still poll on connect. (b) Adds reconnect logic, message routing, JSON diff parsing. (c) 30 s polling is fast enough for human-perceivable "is my router up?". | Polling at 30 s; document why in FAQ. |
| A-6 | **Switch entities to enable/disable routers** | "Let me toggle routers!" | Traefik has no API endpoint to enable/disable a single router (it's all-or-nothing config reload). `npm_switches` does this for NPM because NPM exposes an `enable` API field; Traefik does not. | Skip; add to FAQ why this isn't supported. |
| A-7 | **Per-middleware entities (one entity per middleware)** | "I want to monitor each middleware!" | Middlewares are config-time constructs with no runtime state; turning them into entities creates 30+ "always-ok" entities per user. Use the count in DIAG-01 instead. | Use `/api/overview` count only. |
| A-8 | **TCP / UDP router + service entities** | "I run Traefik for Minecraft / DNS!" | PROJECT.md explicitly targets HTTP-only (CORE-03). TCP/UDP traffic is mostly game/media servers, not the homelab-services use case the user described. Doubles entity surface. | Out of scope per PROJECT.md; revisit only if user feedback demands it. |
| A-9 | **Reading `/api/rawdata` and parsing dependency graph** | "Show me the dependency tree!" | Raw data dumps the entire dynamic config (~MB for any non-trivial deployment); parsing dependency graphs is dashboard-level, not entity-level. | Skip; use Traefik dashboard for that. |
| A-10 | **Reading Traefik's `acme.json` from disk** | "Read the cert directly!" | Requires SSH / shared mount / supervisor permission; fails on remote Traefik (different host); couples to file format that has changed across versions. | TLS handshake (D-1) — works regardless of where Traefik lives. |
| A-11 | **Auto-discovery (`zeroconf` / `dhcp`)** | "Traefik should advertise itself!" | Traefik does not advertise via mDNS / SSDP by default; adding discovery = polling the network for nothing. | Manual config entry; standard HA pattern. |

---

## Feature Dependencies (build order)

```
T-1 Config Flow (UI)
  ├── T-2 YAML config support
  ├── T-3 DataUpdateCoordinator
  │     ├── T-9 Per-router binary_sensor       (CORE-04, the Core Value)
  │     ├── T-10 Per-entrypoint sensor         (CORE-05)
  │     ├── T-11 Per-service sensor            (CORE-06)
  │     ├── T-8  Aggregate sensor.traefik      (DIAG-01)
  │     ├── T-13 Stale entity cleanup
  │     └── D-6  Traefik version sensor
  ├── T-4 ConfigEntryNotReady handling
  ├── T-5 Reauth flow
  ├── T-6 Reconfigure flow
  ├── T-7 Options Flow                         (CFG-01)
  │     └── D-8 Configurable TLS warn days
  ├── T-12 Device registry grouping
  ├── T-14 Diagnostics dump
  ├── T-15 HACS distribution
  └── D-4 traefik.reload_routers service

D-1  TLS cert expiry sensor                   (TLS-01)
  ├── D-2 Cert expiring-soon binary_sensor    (TLS-02)
  └── D-9 Two-coordinator split               (state 30s + certs 6h)

D-3  Aggregate any-router-failing binary      (DIAG-02)
  └── requires T-9

D-5  Router `rule` attribute
  └── part of T-9

D-7  Service middlewares attr
  └── part of T-9

D-10 Two-cadence coordinator
  └── requires D-1, D-9

D-11 Backend server health attr
  └── part of T-11
```

**Critical ordering facts:**
- T-1 must precede everything else — no config entry = no setup.
- T-3 must precede all entity features (the coordinator is the single poll point).
- T-9 (per-router binary_sensor) is the **highest-leverage** single feature —
  it is the PROJECT.md Core Value and is the only one that must land in v1.
- D-1 (TLS expiry) can be a v1.1 stretch goal — it works without changing the
  table-stakes architecture but needs a separate coordinator cadence.
- D-3 (any-router-failing aggregate) requires T-9 to be in place.

---

## MVP Definition

### Launch With (v1.0.0)

The minimum to deliver the PROJECT.md **Core Value** ("see at a glance which
routers are enabled, which are failing"):

- [ ] **T-1** Config Flow (UI) — entry path
- [ ] **T-3** DataUpdateCoordinator — single poll point
- [ ] **T-4** `ConfigEntryNotReady` handling — HA-managed retry
- [ ] **T-9** Per-router `binary_sensor` (status) — **Core Value**
- [ ] **T-12** Device registry grouping — single "Traefik" device
- [ ] **T-13** Stale entity cleanup — for router deletion
- [ ] **T-15** HACS distribution — install path
- [ ] **T-2** YAML config support — PROJECT.md mandate
- [ ] **D-6** Traefik version sensor — surfaces `sw_version` on the device (tiny effort)
- [ ] **DOCS-01** README with install + config + example dashboards
- [ ] **TEST-01** Unit tests for API client + entity derivation

### Add at v1.1 (validate, then ship)

Features that are obviously valuable but each adds a non-trivial increment
of complexity (own coordination cadence, own host list, etc.):

- [ ] **T-5** Reauth flow (API key rotation) — almost always requested in GH issues
- [ ] **T-6** Reconfigure flow (URL change) — same reason
- [ ] **T-7** Options Flow (CFG-01 scan interval + TLS warn days) — without it, GitHub floods with "how do I change X"
- [ ] **T-8** Aggregate `sensor.traefik` (counts) — small extra endpoint, big dashboard value
- [ ] **T-10** Per-entrypoint sensor (CORE-05)
- [ ] **T-11** Per-service sensor (CORE-06)
- [ ] **D-3** Aggregate "any router failing" `binary_sensor` (DIAG-02) — direct payoff of T-9
- [ ] **D-4** `traefik.reload_routers` HA service (DIAG-03) — let users call it from automations
- [ ] **T-14** Diagnostics dump — quality-of-life for support

### Defer to v2+ (post validation)

Features that have a real cost (extra coordinator, separate host iteration,
TLS handshake complexity, etc.) and need user validation first:

- [ ] **D-1** TLS cert expiry sensor (TLS-01) — needs the TLS handshake helper
- [ ] **D-2** "Cert expiring soon" `binary_sensor` (TLS-02)
- [ ] **D-8** Configurable TLS warn threshold (depends on D-1)
- [ ] **D-9** / **D-10** Two-coordinator split (state vs. certs) — depends on D-1
- [ ] **D-5** Router `rule` attribute (low cost, ship in v1.1 if room)
- [ ] **D-7** Per-router middleware list (low cost, ship in v1.1 if room)
- [ ] **D-11** Per-backend server health (medium complexity, requires server-side `healthcheck` to be configured)

### Never Build (Anti-Features, locked out by PROJECT.md "Out of Scope")

- A-1 through A-11 from the anti-features table above. Document these in the
  README's FAQ so users find the answer instead of opening an issue.

---

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| T-9 Per-router binary_sensor (CORE-04) | HIGH | LOW | **P0 (Core Value)** |
| T-1 Config Flow | HIGH | LOW | P0 |
| T-3 DataUpdateCoordinator | HIGH | LOW | P0 |
| T-12 Device registry grouping | HIGH | LOW | P0 |
| T-13 Stale entity cleanup | MED | LOW | P0 |
| T-15 HACS distribution | HIGH | LOW | P0 |
| T-2 YAML config support | MED | LOW | P0 |
| D-6 Traefik version sensor | LOW | LOW | P0 |
| T-4 ConfigEntryNotReady | MED | LOW | P0 |
| D-3 Aggregate "any router failing" | HIGH | LOW | P1 |
| T-8 Aggregate `sensor.traefik` | HIGH | LOW | P1 |
| T-10 Per-entrypoint sensor | MED | LOW | P1 |
| T-11 Per-service sensor | MED | LOW–MED | P1 |
| D-4 `traefik.reload_routers` service | MED | LOW | P1 |
| T-5 Reauth flow | MED | LOW | P1 |
| T-6 Reconfigure flow | MED | LOW | P1 |
| T-7 Options Flow | HIGH | LOW | P1 |
| T-14 Diagnostics dump | MED | MED | P1 |
| D-1 TLS cert expiry sensor | HIGH | MED–HIGH | P2 |
| D-2 Cert expiring-soon binary_sensor | HIGH | MED | P2 |
| D-8 Configurable TLS warn threshold | HIGH | LOW | P2 |
| D-9 Two-cadence coordinator | MED | MED | P2 |
| D-11 Per-backend server health | LOW | MED | P2 |
| D-5 Router `rule` attribute | LOW | LOW | P1 (do in v1.1) |
| D-7 Per-router middleware list | LOW | LOW | P1 (do in v1.1) |

**Priority key:**
- **P0** — Must ship in v1.0; absence = product feels incomplete.
- **P1** — Should ship in v1.1; absence = common GitHub issue.
- **P2** — Future; ship when users validate the v1 architecture.

---

## Mapping back to PROJECT.md requirements

Every feature above traces to a PROJECT.md `Active` requirement ID:

| PROJECT.md ID | Features |
|---|---|
| CORE-01 | T-1 (Config Flow) |
| CORE-02 | T-2 (YAML support) |
| CORE-03 | T-3 (Coordinator + all listed endpoints in Traefik API map) |
| CORE-04 | T-9 (per-router binary_sensor) |
| CORE-05 | T-10 (per-entrypoint sensor) |
| CORE-06 | T-11 (per-service sensor) |
| TLS-01 | D-1 (TLS cert expiry sensor) |
| TLS-02 | D-2 (cert expiring-soon binary_sensor) |
| DIAG-01 | T-8 (aggregate `sensor.traefik`) |
| DIAG-02 | D-3 (any-router-failing aggregate) |
| DIAG-03 | D-4 (`traefik.reload_routers` HA service) |
| CFG-01 | T-7 (Options Flow), D-8 (TLS warn days), D-9 (state vs. cert cadence) |
| DOCS-01 | T-15 + README |
| TEST-01 | Unit tests for API client + entity derivation |

PROJECT.md `Out of Scope` items map directly to anti-features A-1 through A-5
in this document. A-6 through A-11 are *additional* anti-features this research
uncovered that PROJECT.md did not call out explicitly — should be added to
the Out of Scope section as part of `/gsd-discuss-phase` for phase 1.

---

## Competitor Feature Analysis

Reference: the only comparable existing integration in the wild is
`InTheDaylight14/nginx-proxy-manager-switches` (34 stars, HACS, MIT). It's
for NGINX Proxy Manager, not Traefik, but it's the closest feature peer.

| Feature | npm-switches | gatus (user's) | Traefik integration (this plan) | Rationale |
|---|---|---|---|---|
| Polling coordinator | ✅ | ✅ | ✅ (T-3) | HA Bronze mandatory |
| Config Flow | ✅ | ✅ | ✅ (T-1) | Standard |
| YAML config | ❌ | ❌ | ✅ (T-2) | PROJECT.md mandate + matches user's other integrations |
| Per-router/per-host binary_sensor | ❌ (uses switches) | ✅ | ✅ (T-9) | npm uses switches because NPM exposes enable/disable; Traefik doesn't, so binary_sensor is the right primitive |
| Switch per proxy | ✅ (flagship feature) | ❌ | ❌ (A-6 — Traefik has no enable endpoint) | Different domain |
| Aggregate counts sensor | ✅ | ✅ | ✅ (T-8) | Standard |
| Reconfigure / reauth | ❌ ("TODO") | ✅ | ✅ (T-5, T-6) | HA Bronze → Silver; npm-switches explicitly lists this as future work |
| TLS expiry monitoring | ❌ | ❌ | ✅ (D-1, D-2) | **Differentiator** — neither npm-switches nor gatus does this |
| Reload / refresh HA service | ❌ | ❌ | ✅ (D-4) | **Differentiator** — npm-switches has a "Renew certificate" button but no refresh |
| Diagnostics dump | ❌ | ❌ | ✅ (T-14) | **Differentiator** — quality scale target |
| Stale entity cleanup | ❌ | ✅ | ✅ (T-13) | Matches user's gatus pattern |
| Options flow | ❌ | ✅ | ✅ (T-7) | Standard for polling integrations |

**Gap npm-switches has that we don't:** Switches per proxy host (because
NPM's API supports enable/disable, Traefik's doesn't).

**Gap we have that npm-switches doesn't:** TLS monitoring, reauth, diagnostics.

**Verdict:** No existing HA integration covers the Traefik niche. There is
clear headroom for a small but well-built integration to be the canonical
"Traefik monitoring for HA" component in HACS.

---

## Sources

### HIGH confidence — Official Traefik docs (verified 2026-07-05)

- [doc.traefik.io/traefik/reference/install-configuration/api-dashboard](https://doc.traefik.io/traefik/reference/install-configuration/api-dashboard/) — endpoint list, response semantics, security guidance
- [doc.traefik.io/traefik/reference/install-configuration/entrypoints](https://doc.traefik.io/traefik/reference/install-configuration/entrypoints/) — entrypoint schema (`address`, `http.tls`, etc.)
- [doc.traefik.io/traefik/reference/routing-configuration/http/routing/router](https://doc.traefik.io/traefik/reference/routing-configuration/http/routing/router/) — router schema (`status`, `rule`, `entryPoints`, `tls`, `middlewares`)
- [doc.traefik.io/traefik/reference/routing-configuration/http/load-balancing/service](https://doc.traefik.io/traefik/reference/routing-configuration/http/load-balancing/service/) — service schema (`loadBalancer.servers`, `strategy`, `status`)
- [doc.traefik.io/traefik/reference/routing-configuration/http/middlewares/overview](https://doc.traefik.io/traefik/reference/routing-configuration/http/middlewares/overview/) — middleware types (28 built-in types; we expose only counts)

### HIGH confidence — Official Home Assistant docs

- [developers.home-assistant.io/docs/creating_integration_manifest](https://developers.home-assistant.io/docs/creating_integration_manifest/) — manifest schema, `iot_class` choices
- [developers.home-assistant.io/docs/config_entries_config_flow_handler](https://developers.home-assistant.io/docs/config_entries_config_flow_handler/) — ConfigFlow, reauth, reconfigure patterns
- [developers.home-assistant.io/docs/config_entries_options_flow_handler](https://developers.home-assistant.io/docs/config_entries_options_flow_handler/) — OptionsFlow binding pattern
- [developers.home-assistant.io/docs/integration_quality_scale](https://developers.home-assistant.io/docs/integration_quality_scale/) — Bronze/Silver/Gold/Platinum rule lists
- [developers.home-assistant.io/docs/core/entity](https://developers.home-assistant.io/docs/core/entity/) — entity naming, device class, state class conventions

### HIGH confidence — Local project artifacts

- `/home/akentner/Projects/homeassistant-traefik-integration/.planning/PROJECT.md` — requirement IDs CORE-01..06, TLS-01..02, DIAG-01..03, CFG-01; **Out of Scope** list anchors A-1..A-5
- `/home/akentner/Projects/homeassistant-traefik-integration/.planning/research/STACK.md` — stack choices verified; this features research depends on the coordinator / aiohttp / PEP-695 / `ConfigEntry.runtime_data` decisions there
- `/home/akentner/.opencode/skills/integrations/SKILL.md` — HA integration conventions, quality scale rules, config flow patterns
- `/home/akentner/.opencode/skills/home-assistant/SKILL.md` — HA API patterns for referencing entity states from automations
- `/home/akentner/Projects/homeassistant-gatus-integration/custom_components/gatus/` — user's prior similar integration; patterns reused (PEP-695 `type GatusConfigEntry`, stale entity cleanup via `coordinator.async_add_listener`, `DeviceInfo` grouping, `async_step_yaml`)

### MEDIUM confidence — Ecosystem reference (closest existing peer)

- [github.com/InTheDaylight14/nginx-proxy-manager-switches](https://github.com/InTheDaylight14/nginx-proxy-manager-switches) — only HA integration that wraps a reverse proxy with mutating access; 34 stars; gives a read on what users in this niche *actually* request (renamed certificates, unique IDs, reconfiguration — all gaps noted in its README "Features to be developed")

### LOW confidence / flagged

- **TLS handshake for cert expiry (D-1)** — `asyncio.open_connection(host, 443, ssl=True)` + `transport.get_extra_info("ssl_object").getpeercert()` is stdlib-only and well-documented Python behavior, but edge cases (SNI, multi-cert chains, wildcard certs) deserve a Phase 1 spike before locking in. Confidence: MED on the approach, LOW on the edge-case behavior until spike validates it.
- **`/api/http/routers/{name}` enriched attributes (D-5/D-7)** — field names `using`, `provider` confirmed in Traefik docs but the practical user value is uncertain; mark as nice-to-have until users request it.

---

*Feature research for: homeassistant-traefik-integration*
*Researched: 2026-07-05*
*Confidence: HIGH on table-stakes, differentiators, and MVP ordering; MED on TLS handshake approach pending spike.*