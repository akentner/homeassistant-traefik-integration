# Roadmap: Home Assistant Traefik Integration

## Overview

A HACS-distributable custom HA integration that polls the Traefik v2/v3 HTTP API
and surfaces reverse-proxy state (routers, services, entrypoints, TLS cert expiry)
as native HA entities. The journey walks a strict architectural dependency chain
(`const → api → coordinator → entity → config_flow → init → platforms`), proves
the polling loop with the Core Value (per-router binary_sensor) first, layers in
the remaining table-stakes surfaces (entrypoints, services, options, reauth,
reload), then attacks the killer differentiator (TLS cert expiry via out-of-band
stdlib TLS handshake), and finishes with quality polish, diagnostics redaction,
and HACS publication.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Foundation** — Scaffold `custom_components/traefik/`, Config Flow (completed 2026-07-05)
      + YAML, Coordinator, first router binary_sensor, HACS manifest
- [ ] **Phase 2: Core Entities + Options + Reauth + Reload** — Entrypoint /
      Service / Overview sensors, Options Flow, reauth + reconfigure, reload
      service + button, stale entity cleanup
- [ ] **Phase 3: TLS Certificate Expiry** — Spike → `tls.py` + `CertCoordinator`
      → per-router cert timestamp sensor + expiring binary_sensor (6-hour cadence)
- [ ] **Phase 4: Quality + Diagnostics + Polish + HACS** — Diagnostics dump with
      redaction, CI workflows (hassfest / HACS / pytest / release), `info.md` /
      `CHANGELOG.md` / FAQ, Bronze quality-scale metadata

## Phase Details

### Phase 1: Foundation
**Goal**: Users can install the integration from HACS (or by copying
`custom_components/traefik/`), complete the config flow, and see one
`binary_sensor` per Traefik router under a single "Traefik" device.
**Depends on**: Nothing (first phase)
**Requirements**: CFG-01, CFG-02, CFG-06, API-01, API-02, API-03, API-04, API-06,
COORD-01, COORD-02, COORD-03, COORD-04, ROUTER-01, ROUTER-04, UX-01, UX-02,
DIST-01, DIST-02, DIST-03, DOCS-01, TEST-01, TEST-03 (22 requirements)
**Success Criteria** (what must be TRUE):
  1. User can install the integration via HACS (or manually), restart HA, and
     complete the UI config flow by entering the Traefik base URL, bearer token,
     and `verify_ssl` option; alternatively place a YAML entry in
     `configuration.yaml`. The integration registers under a single "Traefik"
     device in HA's device registry.
  2. The integration exposes one `binary_sensor.<router_name>` per Traefik
     router with `_attr_has_entity_name=True` so the UI displays
     `Traefik <Router Name>`; state is `on` when the router is `enabled` and
     `off` when `disabled` or errored; `unique_id` is stable so re-setup does
     not duplicate entries.
  3. Initial router state appears immediately after HA restart because
     `coordinator.async_config_entry_first_refresh()` is awaited inside
     `async_setup_entry`; polling completes inside one event-loop tick without
     blocking, with a configured scan interval (default 30s) and
     `asyncio.timeout(10)` wrapping every cycle.
  4. Transient API outages surface as `ConfigEntryNotReady` with backoff (no
     log spam); an invalid bearer token (HTTP 401) raises
     `ConfigEntryAuthFailed` so HA marks the entry for reauth instead of
     showing endless "Updating Traefik failed" messages.
  5. The package ships with `manifest.json` (no `quality_scale` key —
     hassfest blocks it for custom integrations), `hacs.json`, brand icons
     (`brand/icon.png` + `icon@2x.png`), a `pyproject.toml`, and a `README.md`
     documenting the HACS install path; `hassfest` validation passes.
**Plans**: 4 plans (scaffold → coordinator+entity+__init__ → config_flow+router entity+docs → tests+CI)

Plans:
- [x] 01-01: Project scaffold (manifest.json, hacs.json, const.py, brand/, pyproject.toml, .gitignore, LICENSE, ruff config)
- [x] 01-02: api.py + coordinator.py + entity.py + __init__.py (DataUpdateCoordinator, runtime_data, async_setup_entry, first_refresh)
- [x] 01-03: config_flow.py (UI config + YAML step) + binary_sensor platform for first router entity + README skeleton
- [x] 01-04: Unit tests (TraefikApiClient parsing + error paths), fixture capture, hassfest validation in CI

### Phase 2: Core Entities + Options + Reauth + Reload
**Goal**: Users can configure integration options without re-adding the entry,
reauthenticate when the bearer token changes, and see per-entrypoint,
per-service, aggregate-overview, any-router-failing, and reload entities in
addition to the per-router binary_sensor from Phase 1.
**Depends on**: Phase 1
**Requirements**: CFG-03, CFG-04, CFG-05, API-05, ROUTER-02, ROUTER-03,
ENTRY-01, ENTRY-02, ENTRY-03, DIAG-01, DIAG-02, DIAG-03, UX-03, UX-04,
TEST-02 (15 requirements)
**Success Criteria** (what must be TRUE):
  1. User can open integration Options after setup to change scan interval
     (clamped 15s–5min), TLS verification, certificate warning threshold, and
     the Traefik base URL — without removing and re-adding the entry; the
     coordinator restarts on option change via `entry.add_update_listener`.
  2. When the bearer token rotates, HA presents the reauth flow automatically;
     when the user needs to point the integration at a new Traefik host, the
     reconfigure flow updates the entry in place (no delete+re-add).
  3. The integration exposes one `sensor` per Traefik entrypoint (reporting
     listening address + current request count), one `sensor` per Traefik
     service (load-balancer status + backend server health when a healthcheck
     is configured), and an aggregate `sensor.traefik` reporting the total
     counts of routers, services, and middlewares.
  4. The integration exposes a "Reload" `button` entity and a
     `traefik.reload_routers` HA service that posts
     `/api/http/routers/refresh` and waits for the reload to actually complete
     (Traefik returns 200 before providers finish reloading, so the service
     polls `/api/http/routers` to verify before returning).
  5. Each router binary_sensor exposes the Traefik router `name`, the first
     `Host(...)` match as a friendly rule hint, and the full `rule` as
     extra-state attributes; Traefik names containing `@<provider>` (e.g.
     `api@internal`, `strip@docker`) are filtered out at coordinator level
     (HA's entity-ID regex rejects `@`); routers removed in Traefik are
     pruned from HA via `coordinator.async_add_listener` cleanup hook.
**Plans**: 4 plans in 3 waves

Plans:
- [ ] 02-01: Foundation — TraefikEntity multi-device refactor + new api.py endpoints + filter_internal_items + TraefikData TypedDict + const.py extension
- [ ] 02-02: Config Flow — OptionsFlow + Reauth + Reconfigure + entry.add_update_listener + translation bundle updates
- [ ] 02-03: Entities — sensor.py (entrypoint + service + 3 aggregates) + button.py (reload) + binary_sensor.py (any-router-failing)
- [ ] 02-04: Service + Stale Cleanup + Tests — module-level async_setup + reload handler with polling verification + stale entity cleanup listeners + integration tests
- [ ] 02-04: Stale entity cleanup via coordinator.async_add_listener + @<provider> filtering + integration tests with pytest-homeassistant-custom-component

### Phase 3: TLS Certificate Expiry
**Goal**: Users can see — for every Traefik router terminating TLS — a Home
Assistant timestamp sensor for the certificate's `notAfter` and a
`binary_sensor` that turns `on` when the cert is within the configured warning
threshold (default 14 days).
**Depends on**: Phase 2
**Pre-phase activity**: Run `gsd-spike` against 3+ real Traefik v2/v3
deployments before planning Phase 3 — validate stdlib `ssl` handshake handles
SNI routing, multi-cert chains, wildcard certs, and that the format-string
loop covers all observed `notAfter` shapes. Spike deliverable: spike document
+ `tls.py` prototype with tests. (Strongly flagged in
`research/SUMMARY.md`.)
**Requirements**: TLS-01, TLS-02, TLS-03, TLS-04, TLS-05, TEST-04
(6 requirements)
**Success Criteria** (what must be TRUE):
  1. For every Traefik router with `tls` set, the integration exposes a
     `sensor.<host>` reporting the certificate's `notAfter` timestamp
     (`device_class: timestamp`) and a `days_until_expiry` attribute that
     decreases monotonically as the cert approaches expiry.
  2. For every TLS-enabled router, the integration exposes a
     `binary_sensor.<host>_expiring` that turns `on` when
     `days_until_expiry ≤ user-configurable warning threshold (default 14)`
     with `BinarySensorDeviceClass.PROBLEM`; turning the threshold in
     integration options immediately re-evaluates the entities.
  3. TLS handshakes run on a separate `CertCoordinator` (not the main
     state coordinator) with a 6-hour cadence, bounded by a semaphore so
     large router counts do not hammer the host network all at once;
     results are cached per cycle so a slow router does not stall others.
  4. Any TLS error — unreachable host, timeout, format-string mismatch,
     SNI mismatch, IPv6 failure — marks the corresponding entity
     `unavailable` and lets the rest of the integration keep working; the
     integration never crashes from a TLS failure. The actual TLS socket
     work runs via `asyncio.to_thread` so the HA event loop stays
     responsive.
  5. Unit tests cover ≥3 known `notAfter` format strings (e.g.
     `Nov 15 12:00:00 2025 GMT`, `Nov 15 12:00:00 2025+00:00`, ISO-style)
     and ≥2 invalid format strings (graceful `unavailable` mapping); a mock
     TLS handshake exercises the cache + semaphore + timeout paths.
**Plans**: TBD (likely 2-3 plans after spike)

Plans:
- [ ] 03-01: tls.py + CertCoordinator (6h cadence, semaphore, to_thread wrapper, format-string parse loop) + Options Flow additions for CONF_TLS_WARN_DAYS
- [ ] 03-02: TLS-01 timestamp sensor + TLS-02 expiry binary_sensor platforms + cache wiring into main coordinator
- [ ] 03-03: TLS tests (format strings, invalid inputs, cache + semaphore + timeout)

### Phase 4: Quality + Diagnostics + Polish + HACS
**Goal**: v1.0 release-ready — diagnostics dump with credential redaction,
full HACS publication asset set, GitHub Actions for CI, Bronze quality-scale
metadata, and a FAQ that preempts the locked-out anti-features so users do
not file duplicate issues.
**Depends on**: Phase 3
**Requirements**: DIAG-04, DIST-04, DIST-05, DOCS-02, DOCS-03, DOCS-04
(6 requirements)
**Success Criteria** (what must be TRUE):
  1. A downloaded diagnostics dump from HA's Developer Tools contains the
     integration's config + coordinator data with all credential-shaped
     fields — `api_key`, `token`, `password`, `basic_auth` — redacted via
     `async_redact_data` (`TO_REDACT` whitelist); no plaintext token ever
     appears in the export.
  2. The repository ships working GitHub Actions: `hassfest` validation,
     HACS Action publish workflow, `pytest` workflow running on Python 3.13,
     and a release workflow that enforces `git tag` equals
     `manifest.json` `version` so a tag mismatch fails CI.
  3. The repository ships `info.md` (HACS store card summary), `CHANGELOG.md`
     (versioned history with the v1.0.0 entry), and a README `## FAQ`
     section covering the locked-out anti-features (config-file mutation,
     ACME provisioning in HA, Traefik v1, Traefik Enterprise, WebSocket
     streaming, per-middleware entities, etc.) so users do not file
     duplicate issues against these by design.
  4. The repository ships a `quality_scale.yaml` self-tracking metadata file
     declaring the **Bronze** target tier for v1.0 (Silver as a stretch
     goal for v1.2); `manifest.json` deliberately omits the `quality_scale`
     key because hassfest blocks it for custom integrations (verified per
     `research/SUMMARY.md` pitfall P7).
  5. The chosen v1.0.0 release tag passes `hassfest` validation, HACS
     Action, and the pytest workflow; `CHANGELOG.md` is updated for the
     tag; the integration is publishable via HACS default repository
     flow.
**Plans**: TBD (likely 2 plans)

Plans:
- [ ] 04-01: diagnostics.py with async_redact_data + repairs (optional) + quality_scale.yaml metadata
- [ ] 04-02: GitHub Actions (hassfest / HACS / pytest / release-tag-enforcement) + info.md + CHANGELOG.md + FAQ + brand asset finalization

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation | 4/4 | Complete    | 2026-07-05 |
| 2. Core Entities + Options + Reauth + Reload | 0/TBD | Not started | - |
| 3. TLS Certificate Expiry | 0/TBD | Not started | - |
| 4. Quality + Diagnostics + Polish + HACS | 0/TBD | Not started | - |

**Coverage:**

| Phase | Requirements | Count |
|-------|--------------|-------|
| 1 | CFG-01,02,06 / API-01,02,03,04,06 / COORD-01..04 / ROUTER-01,04 / UX-01,02 / DIST-01,02,03 / DOCS-01 / TEST-01,03 | 22 |
| 2 | CFG-03,04,05 / API-05 / ROUTER-02,03 / ENTRY-01,02,03 / DIAG-01,02,03 / UX-03,04 / TEST-02 | 15 |
| 3 | TLS-01..05 / TEST-04 | 6 |
| 4 | DIAG-04 / DIST-04,05 / DOCS-02,03,04 | 6 |
| **Total** | | **49 / 49** ✓ |
