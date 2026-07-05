# Phase 2: Core Entities + Options + Reauth + Reload - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-07-06
**Phase:** 2 — Core Entities + Options + Reauth + Reload
**Areas discussed:** Device model, Options Flow scope, Reload verification, Aggregate sensor shape, Stale entity cleanup

---

## Device Model

| Option | Description | Selected |
|--------|-------------|----------|
| Single Traefik device — all entities together | All entities group under ONE device `<hostname> Traefik`. Matches current code (single identifier `(DOMAIN, entry.entry_id)`). Phase 1 CONTEXT.md D-07 dropped. | |
| **Multi-device per category** | HTTP Routers, HTTP Services, HTTP Entrypoints, Overview — one device per category, identifier `(DOMAIN, f"{entry.entry_id}_http_routers")` etc. Preserves Phase 1 CONTEXT.md D-07 plan. | ✓ |
| Hybrid | Traefik-level device + per-category sub-devices + Diagnostics device. Three devices. | |

**User's choice:** Multi-device per category — 9 devices (preserves Phase 1 plan)
**Notes:** Phase 1 code shipped single-device identifier. Phase 2 migrates `entity.py` to per-category identifier + name. Adds Diagnostics device for reload button + any-failing binary_sensor.

---

## Options Flow Scope & scan_interval Policy

| Option | Description | Selected |
|--------|-------------|----------|
| **Multi-knob: scan_interval + verify_ssl + warn threshold** | scan_interval (15-300s, default 15), verify_ssl (default True), tls_warn_days placeholder (1-90, default 14). Silver tier blocked; Bronze OK. URL change → Reconfigure only. | ✓ |
| No scan_interval — fixed 30s | scan_interval removed from user config. Only verify_ssl. Cleanest Bronze tier path. | |
| Options = scan_interval only | scan_interval + verify_ssl. tls_warn_days held back to Phase 3 Options. | |

**User's choice:** Multi-knob — scan_interval + verify_ssl + warn threshold
**Notes:** Phase 3 picks up `tls_warn_days` automatically when TLS-02 ships. URL change partition: Reconfigure only (not Options) — single source-of-truth for entry URL.

---

## Reload Verification Behavior

| Option | Description | Selected |
|--------|-------------|----------|
| **Poll router-name set with backoff, success bool from set diff** | POST refresh, then poll every 200ms → 5s exp backoff, max 10 attempts (≤5s budget). Compare name SET vs pre-POST snapshot. Return `{verified, elapsed_ms, attempts, name_diff}`. | ✓ |
| Poll router-count only | Compare count vs pre-POST. Fails on no-op refresh. Simpler. | |
| Trust the POST — always success | Single 1s delay, always return `verified=true`. Lighter but no signal. | |

**User's choice:** Poll router-name SET with backoff, returns structured dict.
**Notes:** Service description documents the `verified=false` semantics — refresh POSTed but no observable change in polling window. Exponential backoff bounded by 5s.

---

## Aggregate Sensor Shape

| Option | Description | Selected |
|--------|-------------|----------|
| Single sensor, state = total, attributes = breakdown | `sensor.traefik`. State = total count; attrs = {routers, services, middlewares, http_routers, tcp_routers, udp_routers}. Clean dashboard tile. | |
| **Three sensors — one per count** | `sensor.traefik_routers`, `sensor.traefik_services`, `sensor.traefik_middlewares`. Each can fire individual automations. | ✓ |
| One sensor with dictionary state | state = routers, attribute = {services, middlewares}. Less useful at a glance. | |

**User's choice:** Three sensors — one per count
**Notes:** Naming uses bare `traefik_<thing>` to avoid colliding with Phase 1's `traefik_http_router_<slug>` per-router sensor IDs.

---

## Stale Entity Cleanup Strategy

| Option | Description | Selected |
|--------|-------------|----------|
| **Delete immediately (gatus pattern)** | When router disappears, delete entity via `entity_registry.async_remove`. Cleanest state. Risk: dashboard refs go permanently unavailable. | ✓ |
| Mark unavailable indefinitely, never delete | Entity becomes `unavailable` and stays in registry. Reversible if router reappears. | |
| Unavailable → 3 cycles → delete | 45s grace period for Traefik provider-thrash. | |

**User's choice:** Delete immediately (gatus pattern)
**Notes:** Cleanup registered via `coordinator.async_add_listener(callback)` per-platform, paired with `entry.async_on_unload`. Aggregate sensors + any-failing never deleted (single instance per config entry).

---

## the agent's Discretion

- Exact wording in `strings.json` / `translations/en.json`
- Whether to log `name_diff` in reload responses (yes — adds signal)
- Aggregating entrypoint `transport` (tcp vs udp) into a single string
- Whether to strip leading `:` from `entrypoint.address` for nicer HA display
- Whether Phase 1's `routers` key in TraefikData stays as back-compat alias or migrates fully to `http_routers`

## Deferred Ideas

- "Entrypoint request count" — Traefik doesn't expose it via `/api/entrypoints`; would need Prometheus path. Deferred to v2.
- TCP/UDP router/service entities — out of scope (PROJECT.md HTTP-only; v2+).
- Per-router `using` chain visualization — v2.
- `traefik.reload_static_config` service — v2.
- YAML-mode scan_interval / verify_ssl override — needs `configuration.yaml` schema change; Phase 4 docs.
- TLS handshake spike (Phase 3 pre-requisite) — gsd-spike before Phase 3 planning, not Phase 2.
- `de.json` translation — Phase 4 polish.

## Phase Scope Reminders (NOT to add in Phase 2)

- ❌ TLS / certs / `tls.py` (Phase 3)
- ❌ `diagnostics.py` with redaction (Phase 4)
- ❌ `quality_scale.yaml` metadata (Phase 4)
- ❌ Button state attribute — fire-and-forget only
- ❌ Mid-phase HACS publication (wait until Phase 4)
- ❌ Multi-language translations (Phase 4)
