# Spike Wrap-Up Summary

**Date:** 2026-07-06
**Spikes processed:** 4
**Feature areas:** tls-handshake (1)
**Skill output:** `./.claude/skills/spike-findings-homeassistant-traefik-integration/`

## Processed Spikes

| #   | Name                      | Type     | Verdict      | Feature Area |
|-----|---------------------------|----------|--------------|--------------|
| 001 | stdlib-tls-handshake      | standard | ✓ VALIDATED  | tls-handshake |
| 002 | notafter-format-strings   | standard | ✓ VALIDATED  | tls-handshake |
| 003 | sni-routing-multicert     | standard | ✓ VALIDATED  | tls-handshake |
| 004 | error-handling-async-wrap | standard | ✓ VALIDATED  | tls-handshake |

## Key Findings

1. **`CERT_NONE` returns empty `getpeercert()` dict.** Per Python docs,
   the dict is empty when the cert chain is not validated. Use
   `PROTOCOL_TLS_CLIENT` (default `CERT_REQUIRED`) + `check_hostname=False`
   + `load_default_certs()`. Asymmetric: validate chain, skip strict hostname.

2. **`ssl.cert_time_to_seconds()` is the canonical `notAfter` parser.**
   All 18 sampled real-world certs (Let's Encrypt, DigiCert, Sectigo,
   Cloudflare, Apple, Microsoft, Google, etc.) use the canonical
   `"%b %d %H:%M:%S %Y %Z"` format with `GMT` tz. Single-digit days use
   double-space padding (`Aug  5` not `Aug 5`). Manual format-string
   fallback formats never fired in production sampling.

3. **SNI server pattern: pre-built SSLContext per cert + context swap.**
   The `SSLContext.sni_callback` must set `ssl_sock.context = new_ctx` to
   switch to the right cert mid-handshake. `load_cert_chain` inside the
   callback has off-by-one issues (cert meant for handshake N gets served
   in handshake N+1).

4. **IPv6 failures identical to IPv4 failures.** `ConnectionRefusedError`
   for `[::1]:1` (no listener) returns the same `error='refused'`
   TypedDict as IPv4. `socket.create_connection` handles `[host]:port`
   natively for both.

5. **`asyncio.to_thread` + `Semaphore(4)` scales.** 8 concurrent
   handshakes against localhost completed in 20ms; event loop stays
   responsive because all blocking work is in a worker thread.

## Deliverables

- **Skill:** `./.claude/skills/spike-findings-homeassistant-traefik-integration/`
  - `SKILL.md` — auto-load manifest with requirements
  - `references/tls-handshake.md` — implementation blueprint
  - `sources/` — original spike source code preserved
  - `run-all.sh` — single-command spike re-runner
- **Conventions:** `.planning/spikes/03-tls-handshake/CONVENTIONS.md` (per-spike)
- **Project CLAUDE.md:** routing line added

## Next Steps

Phase 3 plan can proceed directly. The implementation blueprint at
`references/tls-handshake.md` provides the recipes for:

- `custom_components/traefik/tls.py` — stdlib handshake helper
- `custom_components/traefik/cert_coordinator.py` — 6h cadence, sem=4, cache
- `custom_components/traefik/sensor.py` — `TraefikCertTimestampSensor(SensorDeviceClass.TIMESTAMP)`
- `custom_components/traefik/binary_sensor.py` — `TraefikCertExpiryBinarySensor(BinarySensorDeviceClass.PROBLEM)`
- Tests in `tests/components/traefik/test_tls.py` + `test_cert_coordinator.py`

Confidence: HIGH. The spike-findings skill will auto-load in future
build conversations.