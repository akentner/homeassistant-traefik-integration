# Spike Conventions

Patterns established across the `.planning/spikes/03-tls-handshake/`
session. New spikes in this project follow these unless the question
requires otherwise.

## Stack

- **Python 3.14** (matches `.python-version` + `pyproject.toml`).
- **stdlib-only** for the spike itself — no `cryptography`, no extra
  pip installs. The integration we're spiking also mandates stdlib-only.
- **pytest** for any persistent unit tests that survive into the
  integration codebase (spike scripts are throwaway; tests that ship
  live in `tests/components/traefik/`).

## Structure

```
.planning/spikes/03-tls-handshake/
├── MANIFEST.md                          # index, requirements, verdicts
├── CONVENTIONS.md                       # this file
├── shared/                              # code reused by all 4 spikes
│   └── tls.py                           # fetch_cert_info / parse_not_after prototype
├── 001-stdlib-tls-handshake/
│   ├── README.md
│   └── probe.py                         # standalone runnable
├── 002-notafter-format-strings/
│   ├── README.md
│   └── probe.py
├── 003-sni-routing-multicert/
│   ├── README.md
│   ├── server.py                        # Traefik simulator
│   └── certs/
│       ├── generate.sh                  # self-signed cert generation
│       ├── router-a.{crt,key,pem}
│       ├── router-b.{crt,key,pem}
│       ├── router-c.{crt,key,pem}
│       └── wildcard.{crt,key,pem}
└── 004-error-handling-async-wrap/
    ├── README.md
    └── probe.py                         # exercises scenarios + reuses spike 003 server
```

## Patterns

- **One `probe.py` per spike** that exits 0 on pass, 1 on fail. Lets the
  spike be CI-runnable. Spikes that need a server (like 003) co-locate
  the server in the same spike dir.
- **`shared/` for cross-spike code.** Spike 003 + 004 both use the
  `tls.py` prototype from `shared/`. The prototype will become
  `custom_components/traefik/tls.py` during Phase 3 plan execution.
- **Stdout-only observability.** This spike is a fact-validation exercise
  (does stdlib TLS work?), not a user-facing feature. No forensic log
  layer — printed test results are the artifact.
- **Markdown tables for results.** Each spike's `probe.py` prints
  `[PASS]/[FAIL]` lines that paste directly into the README.
- **Generate self-signed certs via `openssl` CLI**, not `cryptography`
  library. Avoids pip dep, matches the stdlib-only mandate.
- **`asyncio.to_thread` wrapper** for any blocking stdlib call
  (`socket`, `ssl`) — event-loop responsiveness is non-negotiable.

## Tools & Libraries

- **Python stdlib `ssl`** — `PROTOCOL_TLS_CLIENT`, `check_hostname=False`,
  `load_default_certs()`, `cert_time_to_seconds()`, `SSLContext.sni_callback`.
- **Python stdlib `socket`** — `create_connection((host, port))` works for
  IPv4 and IPv6 (`[::1]:port`).
- **Python stdlib `asyncio`** — `to_thread`, `timeout`, `Semaphore`,
  `start_server`, `open_connection`, `gather`.
- **`openssl` CLI** for cert generation (in spike 003's `generate.sh`).

## Anti-patterns to avoid

- `verify_mode=CERT_NONE` — returns empty `getpeercert()` dict.
- `load_cert_chain` inside `sni_callback` — off-by-one cert serving
  (cert meant for handshake N gets served in handshake N+1).
- `check_hostname=True` on the client probe — fails on Traefik default
  certs and wildcards.
- Forgetting `load_default_certs()` — `CERT_REQUIRED` fails without
  trusted CAs.
- Using `cryptography` library — adds manifest `requirements`; HA bundles
  it but project mandates stdlib-only.
- `asyncio.get_event_loop()` — deprecated; use `asyncio.run()`.
- Per-entity async update instead of `DataUpdateCoordinator` —
  defeats the 6h cadence + semaphore pattern.