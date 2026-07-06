# Phase 3: TLS Certificate Expiry - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-07-06
**Phase:** 3-tls-certificate-expiry
**Areas discussed:** Expiration binary_sensor default state, Multi-domain TLS expansion, Threshold live re-evaluation, Cache invalidation trigger, Wrap-up
**Wrap-up decision:** Run `/gsd:spike` to validate against real Traefik deployments before planning

---

## Expiration binary_sensor default state

| Option | Description | Selected |
|--------|-------------|----------|
| Default OFF, user opts in | `entity_registry_enabled_default=False`; matches Phase 2 `TraefikAnyRouterFailingBinarySensor` precedent | |
| Default ON | Surface immediately; simpler UX but pollutes States panel | |
| Default OFF, but auto-enable when threshold breached | Hybrid: opt-out of registry mutation; entity stays dormant until actionable | ✓ (refined below) |

**User's choice:** Default OFF, but auto-enable when `days_until_expiry <= threshold`.
**Refined through follow-ups (D-03):**
- Selected "always-enabled, state maps to breach" (not registry mutation).
- Selected `state == ON` for `days_until_expiry <= threshold`, `OFF` otherwise, `unavailable` on probe error.
- Already-expired certs (`days_until_expiry < 0`) treated as breach (`ON`).

**Notes:** PITFALLS M-12 references "TLS-expiry binary_sensor default-on at 89 days → floods activity stream" as a precedent against default-ON. The user accepted the noise-reduction logic but preferred always-registered with state-driven semantics over literal registry mutation. Final shape: registered always, dormant in OFF, flips ON at threshold breach, `unavailable` on probe failure.

---

## Multi-domain TLS expansion

| Option | Description | Selected |
|--------|-------------|----------|
| Collapse to one pair per router (named by first host) | Simpler entity count, attributes expose full host list | |
| One entity per `Host()` match in rule | Fanout; aligns with ROADMAP `sensor.<host>` syntax | |
| One per `tls.domains[]` entry (Traefik's authoritative list) | Closest to Traefik's own model | ✓ (refined below) |

**User's choice:** Hybrid: `tls.domains[]` entries first, then any extra `Host()` matches not already covered (D-02).

**Refined through follow-ups:**
- Confirmed `tls.domains[]` is the primary source of truth; `Host()` adds entries not already enumerated.
- Routers with `tls` set but empty `tls.domains[]` AND no `Host()` match are skipped entirely (wildcard / default-cert setups).
- Entity naming: `sensor.traefik_<host>_cert` + `binary_sensor.traefik_<host>_expiring` (per host).
- De-dup: one entity per distinct hostname; `router_name` attribute is a list of all such routers.

**Notes:** Matches ROADMAP Phase 3 Success Criterion 1 verbatim — "sensor.<host> reporting the certificate's notAfter".

---

## Threshold live re-evaluation

| Option | Description | Selected |
|--------|-------------|----------|
| Instant — extend `_async_options_updated` listener | Recompute state in-place via `coordinator.async_update_listeners()` | ✓ |
| Wait for next 6h cycle | Simplest but laggy UX | |
| Manual TLS refresh button | Third entity on diagnostics device | |

**User's choice:** Instant via extended `_async_options_updated` listener (D-08).

**Refined through follow-ups:**
- Threshold change does NOT trigger a re-handshake — cached cert data unchanged; just threshold application shifts.
- Re-eval wiring goes through the existing entry-level listener (not per-entity subscription) — minimal complexity addition on top of Phase 2.
- Visible signal = `days_until_expiry` attribute always exposed (no logbook event spam, no separate notification).

**Notes:** Mirrors Phase 1's `_async_options_updated` precedent (CONTEXT.md D-08) where scan_interval changes apply live. Threshold change is functionally identical — a coordination-layer change applied to existing data.

---

## Cache invalidation trigger

| Option | Description | Selected |
|--------|-------------|----------|
| Pure 6h TTL only | Independent coordinator; main coordinator's changes don't propagate | ✓ |
| 6h TTL + listener on router-list change | New TLS routers trigger re-handshake | |
| 6h TTL + manual refresh button | Listener + button for explicit invalidation | |

**User's choice:** Pure 6h TTL (D-07).

**Refined through follow-ups:**
- Concurrency: `asyncio.Semaphore(4)` — fixed 4-concurrent handshakes per cycle (TLS-05).
- Cache location: in-memory `dict[str, CertInfo]` on `CertCoordinator` instance (no persistence).
- Per-handshake timeout: `asyncio.timeout(5)` (PITFALLS #14 recommended).

**Notes:** Simplest possible design — CertCoordinator is independent of TraefikCoordinator's data flow. The threshold-change path (D-08) is the only out-of-band trigger; it doesn't re-handshake, only re-evaluates.

---

## Wrap-up

| Option | Description | Selected |
|--------|-------------|----------|
| I'm ready to write CONTEXT.md | Decisions captured are sufficient | |
| Run gsd-spike now to validate against real Traefik deployments first | Pre-phase activity per ROADMAP / PITFALLS #14 | ✓ |
| Explore additional gray areas | Attribute richness, host denylist, IPv6 fallback, etc. | |

**User's choice:** Run gsd-spike first.

**Notes:** CONTEXT.md was written with all 4-area decisions locked and an explicit "Pre-Phase Activity" section flagging the spike as REQUIRED before plan-phase. The spike validates stdlib TLS handshake against 3+ real Traefik v2/v3 deployments (SNI, multi-cert chains, wildcards, IPv6, format strings). After the spike completes, the Phase 3 plan can be built from the captured CONTEXT.md + spike output.

## Claude's Discretion

Items deferred to Claude during discussion (full list in CONTEXT.md Claude's Discretion):
- `CertCoordinator` storage location on `entry.runtime_data`
- `tls_warn_days` clamp range (1..90 retained from Phase 2)
- `tls.py` module layout (function vs class)
- `strings.json` wording
- Stale-cert cleanup of old entities when host removed
- Optional "TLS certs by issuer" aggregation
- Icon choices (`mdi:certificate`, `mdi:lock-alert`, `mdi:shield-lock`)

## Deferred Ideas

Captured in CONTEXT.md `<deferred>` section:
- Pre-phase `/gsd:spike` (required activity)
- TLS chain validation / SAN walk (v2)
- Cert renewal triggering / ACME actions (out of scope)
- Qualys-style letter grade (out of scope)
- TCP/UDP router TLS (PROJECT.md HTTP-only; v2)
- HTTP/3 QUIC cert probing (v2 if Traefik exposes)
- Persistent cert cache across restarts (v2)
- Diagnostics dump for cert data (Phase 4)
- Multi-language translations (Phase 4)
- Repairs flow for expired certs (Phase 4)
- GitHub Actions CI (Phase 4)
- FAQ cert-related entries (Phase 4)
- `CHANGELOG.md` / `info.md` (Phase 4)
- Quality-scale metadata (Phase 4)
</content>
</invoke>