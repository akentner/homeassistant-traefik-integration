---
name: spike-findings-homeassistant-traefik-integration
description: Implementation blueprint for Home Assistant Traefik integration Phase 3 (TLS Certificate Expiry). Stdlib TLS handshake patterns, SNI routing, format-string parsing, async wrapping. Auto-loaded during implementation work on `custom_components/traefik/`.
---

<context>
## Project: homeassistant-traefik-integration

Custom Home Assistant integration (HACS-distributable) that connects to a
Traefik reverse proxy and surfaces routers, services, entrypoints,
middleware, and TLS certificate health as HA entities. Phase 3 adds TLS
cert expiry sensors via out-of-band TLS handshakes to each router's
public hostname.

Spike session wrapped: 2026-07-06
Idea: validate stdlib `ssl` + `socket` TLS handshake approach before
committing to Phase 3 plan.
</context>

<requirements>
## Requirements

Locked from `.planning/spikes/03-tls-handshake/MANIFEST.md` and
re-confirmed by 4 VALIDATED spike questions:

- Python stdlib only — no `cryptography` import, no `manifest.json` `requirements` entries
- `ssl.SSLContext(PROTOCOL_TLS_CLIENT)` + `check_hostname=False` + `load_default_certs()`
- `server_hostname=host` parameter on `wrap_socket` for SNI routing
- Wrap blocking work in `asyncio.to_thread(...)` + `asyncio.timeout(5)`
- Primary parser: `ssl.cert_time_to_seconds()`; manual format-string fallback as defense in depth
- Every error path returns typed `CertError`; never propagate exceptions
- `asyncio.Semaphore(4)` for concurrent handshakes
- IPv6 must work (`socket.create_connection((host, port))` handles `[host]:port`)
- Honor `entry.options[CONF_TLS_WARN_DAYS]` for expiring threshold
- `CertCoordinator` as sibling on `entry.runtime_data` (no shape migration)
- 6h `update_interval`; in-memory `dict[str, CertInfo]` cache; no persistence
</requirements>

<findings_index>
## Feature Areas

| Area             | Reference                       | Key Finding |
|------------------|---------------------------------|-------------|
| TLS Handshake    | `references/tls-handshake.md`   | Stdlib `ssl` + `socket` validated against 18 real-world certs + local Traefik SNI simulator. All 4 spike questions VALIDATED. |

## Source Files

Original spike source files preserved in `sources/` for complete reference:

- `sources/shared/tls.py` — production-ready prototype (ruff + mypy-strict clean)
- `sources/001-stdlib-tls-handshake/` — foundation validation against real internet hosts
- `sources/002-notafter-format-strings/` — format-string loop coverage + catalogue
- `sources/003-sni-routing-multicert/` — Traefik simulator with `sni_callback` + cert generation
- `sources/004-error-handling-async-wrap/` — error scenarios (timeout, refused, IPv6, parse, sem)
- `run-all.sh` — single-command spike re-runnable for CI verification
</findings_index>

<metadata>
## Processed Spikes

- 001-stdlib-tls-handshake (VALIDATED) — foundation: stdlib TLS handshake populates cert dict
- 002-notafter-format-strings (VALIDATED) — `parse_not_after` handles all observed shapes
- 003-sni-routing-multicert (VALIDATED) — `ssl_sock.context = new_ctx` swap is the canonical SNI pattern
- 004-error-handling-async-wrap (VALIDATED) — every error path caught; concurrent sem=4 scales

## Confidence

HIGH. All 4 spike questions answered with concrete evidence:
- 18 real internet TLS hosts (Let's Encrypt, DigiCert, Sectigo, Cloudflare, Apple, Microsoft, etc.)
- Local Traefik simulator with 4 distinct self-signed certs (exact SNI, SAN-routed, wildcard fallback)
- 9 error scenarios (timeout, refused, IPv4/IPv6, parse failures, concurrent 8x sem=4)

Prototype at `sources/shared/tls.py` passes both `ruff check` and `mypy --strict`.
</metadata>