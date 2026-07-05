# Phase 1: Foundation - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-07-05
**Phase:** 1 - Foundation
**Areas discussed:** runtime_data shape day 1, Config flow validation probe, UX defaults & branding, Device & entity model (added late in discussion)

---

## runtime_data shape day 1

| Option | Description | Selected |
|--------|-------------|----------|
| Two-field `TraefikRuntime(api, coordinator)` wrapper | PEP-695 typed; forward-looking per PITFALLS #6; service handlers access `entry.runtime_data.api` directly |  |
| **Bare coordinator in runtime_data** (HA Core pattern) | PEP-695 `type TraefikConfigEntry = ConfigEntry[TraefikCoordinator]`; matches `faa_delays/__init__.py`, `gatus/__init__.py`; api client as `coordinator.client` | ✓ |
| Bare API client only | Coordinator recreated; loses type safety; PITFALLS #6 explicitly warns |  |

**User's choice:** Initially asked for a check against current HA Core dev team best practices, then a decision between the wrapper and bare-coordinator patterns.

**Research outcome:** Verified against `home-assistant/core/dev/homeassistant/components/faa_delays/__init__.py` (the canonical HA Core reference for a polling integration) — uses bare coordinator. The user's `gatus` integration also uses this pattern. PITFALLS #6's wrapper recommendation was over-engineering relative to current HA Core practice.

**Decision:** Bare coordinator in `entry.runtime_data`. PEP-695 typed. API client as `coordinator.client`.

---

## Config flow validation probe

| Option | Description | Selected |
|--------|-------------|----------|
| `/api/http/routers` | Matches Phase 1's actual usage; proves token + read scope; semantically aligned |  |
| `/api/version` | Cheapest; same auth boundary as everything else; minimal value-add over routers probe |  |
| **`/api/overview`** | Aggregates stats; broadest API surface; 200 confirms URL + token + API enabled | ✓ |
| All endpoints in parallel | Overkill; causes false negatives; coordinator already does this in steady-state |  |

**User's choice:** `/api/overview`.

**Notes:** Traefik's API contract (verified via Traefik docs) puts all `/api/*` endpoints on the same auth boundary — there is no public `/api/version`. A 200 on `/api/overview` is the strongest single-call proof that URL + token + API enabled all work. The coordinator's steady-state fetch will still call `/api/http/routers` (Phase 1 data) and `/api/version` (device sw_version).

**Error mapping decision:**
- 401/403 → `invalid_auth`
- 404 → `api_disabled` (distinct translation key — Traefik's `api: {}` not enabled)
- Timeout / 5xx → `cannot_connect`
- Other → `unknown`

---

## UX defaults & branding

### Polling cadence

| Option | Description | Selected |
|--------|-------------|----------|
| **15s default** | Aggressive; near-realtime; PITFALLS #8 warns of provider thrash but user accepts | ✓ |
| 30s default | Balanced; matches PROJECT.md |  |
| 60s default | Conservative; stale-state risk per PITFALLS #9 |  |

**User's choice:** 15s default. User accepts the PITFALLS #8 risk. Phase 2 Options Flow clamps `[15s, 5min]` so users can tune up if 15s causes thrash.

### First binary_sensor enable default

| Option | Description | Selected |
|--------|-------------|----------|
| **Enabled by default** | Core Value visibility; user sees routers immediately | ✓ |
| Disabled by default | Opt-in; defeats Core Value |  |

**User's choice:** Enabled by default.

### verify_ssl UX

| Option | Description | Selected |
|--------|-------------|----------|
| **Top-level, default True** | Secure-by-default; user flips for self-signed | ✓ |
| Top-level, default False | Optimizes for homelab self-signed; less secure default |  |
| Behind 'Advanced' toggle | More friction for self-signed homelab users |  |

**User's choice:** Top-level, default True.

### Brand icons

| Option | Description | Selected |
|--------|-------------|----------|
| **Official Traefik logo (Apache 2.0, attributed)** | Honest representation; Apache 2.0; attribution in README | ✓ |
| Custom Material Design Icon derivative | mdi:router-network; no attribution; less Traefik-specific |  |
| Custom-designed hybrid | Higher design effort; deferred to Phase 4 if preferred |  |

**User's choice:** Official Traefik logo.

### Device sw_version

| Option | Description | Selected |
|--------|-------------|----------|
| **Live-updating** | Coordinator fetches `/api/version` each cycle; device card shows current after upgrade | ✓ |
| One-shot at first refresh | Frozen; user can't tell from HA when Traefik upgraded |  |
| No sw_version at all | Deviates from UX-01 requirement |  |

**User's choice:** Live-updating.

### HTTP+token warning

| Option | Description | Selected |
|--------|-------------|----------|
| **Inline form warning, still allow** | Lightest-touch mitigation; user might be on trusted LAN | ✓ |
| Refuse http://, force https:// | Breaks homelab LAN workflow |  |
| Silent — no warning | Matches gatus behavior; not recommended per PITFALLS |  |

**User's choice:** Inline form warning, allow submission.

---

## Device & entity model (added late in discussion)

**Trigger:** After discussing the three initially-selected areas, the user provided additional architectural direction:

> "Es sollen für die 9 Kategorien Devices angelegt werden: HTTP Routers, HTTP Services, HTTP Middlewares, TCP Routers, TCP Services, TCP Middlewares, UDP Routers, UDP Services und Certificates. Für jeden Eintrag innerhalb der Kategorien sollen Sensor Entitäten anglegt werden. EntityId: sensor.traefik_{http|tcp|udp|cert}_{router|service|middleware}_{name}. Status: device_class: enum, Option in Attributes: success|warning|error. Restliche Informationen in Attributes."

### Device model

| Option | Description | Selected |
|--------|-------------|----------|
| **9 devices, one per category** | Each category registers as its own device; Phase 1 ships only "HTTP Routers" | ✓ |
| One device with sub-devices per category | `via_device` linking; more setup work for Phase 1 |  |
| Single Traefik device, 9 sensor groups (ROADMAP original) | Loses per-category grouping in HA UI |  |

**User's choice:** 9 devices, one per category.

### Entity type

| Option | Description | Selected |
|--------|-------------|----------|
| Sensor with `device_class=enum`, state IS enum string | Maps to SensorDeviceClass.ENUM |  |
| Sensor + binary_sensor (both) | Redundant |  |
| **Binary sensor only, status enum in attributes** | State true = success, false = warning/error; raw status string in attributes | ✓ |

**User's choice:** Binary sensor only, raw status enum in attributes. Initial statement said "Sensor Entitäten" but follow-up clarified to "binary_sensor mit true=success und false=warning|error, den nativen State success|warning|error in die Attributes".

### Status location

| Option | Description | Selected |
|--------|-------------|----------|
| State IS the enum | Native value IS success/warning/error; matches ENUM device_class |  |
| **State in attributes, separate main value** | Binary sensor's true/false is main; raw enum in attributes | ✓ |

**User's choice:** Status string in attributes; binary sensor's true/false is the main state.

**Architectural shift:** This deviates from the ROADMAP's "single Traefik device + binary_sensor per router" model. The ROADMAP.md should be updated when moving to plan-phase, but for Phase 1 CONTEXT.md we capture the new model.

---

## the agent's Discretion

- Exact error message wording in `strings.json` / `translations/en.json`.
- Whether to bundle a `services.yaml` placeholder in Phase 1.
- Test fixture sourcing (capture from `haos-op3050-1` vs hand-craft).
- Specific hostname extraction edge cases (malformed URL fallback).

## Deferred Ideas

- **YAML configuration scope** (CFG-02): Skipped from explicit discussion; minimal
  schema (URL + token + verify_ssl) implied. Phase 4 may expand.
- **Phase 3 spike for TLS:** Already locked in ROADMAP; not discussed here.
- **Phase 2 scan-interval knob vs quality-scale rule:** Decision deferred to
  Phase 2 discuss-phase. The 15s default in Phase 1 works for both paths
  (knob-or-not).
- **Phase 4 dark icon, services.yaml polish, diagnostics, repairs:** All
  Phase 4 polish per ROADMAP, not Phase 1.