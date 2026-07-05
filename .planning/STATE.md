---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: executing
stopped_at: Completed 02-03-PLAN.md
last_updated: "2026-07-05T23:22:57Z"
last_activity: 2026-07-05
progress:
  total_phases: 4
  completed_phases: 1
  total_plans: 8
  completed_plans: 6
  percent: 75
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-07-05)

**Core value:** If nothing else works, the user must be able to see — at a
glance inside Home Assistant — which Traefik routers are enabled, which are
failing, and which TLS certificates are expiring soon.
**Current focus:** Phase 02 — core-entities-options-reauth-reload
first router binary_sensor + HACS manifest)

## Current Position

Phase: 02 (core-entities-options-reauth-reload) — EXECUTING
Plan: 3 of 4 complete (next: 02-04 Service + Stale Cleanup + Tests)
Status: Ready to execute
Last activity: 2026-07-05

Progress: [████████░░] 75%

## Performance Metrics

**Velocity:**

- Total plans completed: 6
- Average duration: 12m
- Total execution time: 1.1 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1. Foundation | 4 | 4 | 12m |
| 2. Core Entities + Options + Reauth + Reload | 2 | 4 | 11m |
| 3. TLS Certificate Expiry | 0 | 3 | — |
| 4. Quality + Diagnostics + Polish + HACS | 0 | 2 | — |

**Recent Trend:**

- Last 5 plans: 4 from Phase 1 + Phase 02 P01 (17m, 3 tasks, 12 files)
- Trend: stable

*Updated after each plan completion*
| Phase 01 P01 | 10 | 2 tasks | 9 files |
| Phase 01 P02 | 7 | 3 tasks | 4 files |
| Phase 01 P03 | 9 | 2 tasks | 6 files |
| Phase 01 P04 | 25 | 2 tasks | 18 files |
| Phase 02-core-entities-options-reauth-reload P01 | 17m | 3 tasks | 12 files |
| Phase 02-core-entities-options-reauth-reload P03 | 4m | 3 tasks | 3 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Phase 1 — pending]: Polling, not WebSocket; v2/v3 Traefik API only;
  HACS-distributable; Config Flow + YAML; per-router/per-cert entities;
  `aiohttp` over `requests`. (All listed in PROJECT.md — to be moved to
  Validated after Phase 1 ships.)

- [Phase 3 — pending]: TLS handshake uses stdlib `ssl` (no `cryptography`
  import), `asyncio.to_thread` wrapper, separate `CertCoordinator` with
  6-hour cadence. Spike to validate against 3+ real Traefik deployments
  before Phase 3 planning.

- [Phase 2 — pending]: HA quality-scale rule "Polling intervals are NOT
  user-configurable" — scan-interval override is opt-in via Options Flow
  but quality-scale Bronze is targeted without it (decide during Phase 2
  discuss).

- [Phase 02-core-entities-options-reauth-reload]: Per-category multi-device model: TraefikEntity takes (entry, category, *, description_key=None); device identifier is (DOMAIN, f'{entry_id}_{category}') instead of Phase 1's single-device (DOMAIN, entry_id). Existing HA device-registry rows become orphans; new per-category devices appear on first restart after upgrade.
- [Phase 02-core-entities-options-reauth-reload]: TraefikData is now a TypedDict (PEP-589, total=False) with version/entrypoints/http_routers/http_services/http_middlewares/overview keys. fetch_all drops entire payload on non-auth error (CONTEXT.md D-07) so entities see a stale cycle rather than mixed fresh+stale data.
- [Phase 02-core-entities-options-reauth-reload]: filter_internal_items lifted from binary_sensor to api.py — canonical helper for @<provider> filtering across all Phase 2 platforms (routers/services/middlewares/entrypoints). Local _filter_user_routers / _PROVIDER_SUFFIX_RE removed from binary_sensor.
- [Phase 02-core-entities-options-reauth-reload]: reload_routers POSTs /api/http/routers/refresh with explicit Content-Length: 0 header (aiohttp requires it for empty-body POSTs). Does not poll — verification lives in the reload service handler (plan 02-04). Traefik returns 202 before reload completes (PITFALLS #15).
- [Phase 02-core-entities-options-reauth-reload]: TypedDict(total=False) safe access via _dict_or_empty / _list_or_empty helpers in sensor.py — keeps mypy --strict clean when reading partial coordinator payloads; pattern reusable across Phase 2+ platforms (Phase 3 TLS sensors).

### Pending Todos

None yet.

### Blockers/Concerns

- Phase 3 is strongly flagged in `research/SUMMARY.md` for a `gsd-spike`
  before planning: TLS handshake has subtle edge cases (SNI, multi-cert
  chains, wildcard certs, IPv6). Spike should validate 3+ real Traefik
  deployments and confirm format-string loop covers observed `notAfter`
  shapes.

- `requirementS.md` footer says `46 total` but the traceability table
  contains 49 rows (CFG:6 + API:6 + COORD:4 + ROUTER:4 + ENTRY:3 +
  DIAG:4 + TLS:5 + UX:4 + DIST:5 + DOCS:4 + TEST:4 = 49). Table itself
  is correct — only the footer is stale. Cosmetic; not a coverage gap.

- Phase 2-03 depends on the per-category device model landed in plan 02-01.
  The four new sensor platforms (TraefikEntrypointSensor, TraefikServiceSensor,
  three aggregate counters) all instantiate via
  `super().__init__(entry, category='http_entrypoints' | 'http_services' |
  'overview', description_key=...)` — every parameter is now in place.

## Session Continuity

Last session: 2026-07-05T23:22:57Z
Stopped at: Completed 02-03-PLAN.md
49/49 v1 requirements mapped.
Resume file: .planning/phases/02-core-entities-options-reauth-reload/02-04-PLAN.md
