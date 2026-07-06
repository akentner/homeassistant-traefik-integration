---
spike: 003
name: sni-routing-multicert
type: standard
validates: "Given a TLS server with multiple certs (SNI-routed), when connecting with server_hostname=X, then the cert returned matches X."
verdict: VALIDATED
related: [001-stdlib-tls-handshake, 004-error-handling-async-wrap]
tags: [tls, sni, multi-cert, traefik, simulator]
---

# Spike 003: SNI Routing & Multi-Cert Selection

## What This Validates

A TLS server (Traefik) can route to **different certs per SNI hostname**,
and the stdlib `ssl.wrap_socket(sock, server_hostname=X)` correctly
honors the SNI so we get the right leaf cert for the host we probed.

This is the killer test: Traefik's whole point is SNI routing per
Host() rule. If we got the wrong cert, the integration would surface
expired dates for the wrong host.

## Research

Approach: build a local TLS server that simulates Traefik's SNI
routing by serving different leaf certs based on the SNI in the
ClientHello. This is the canonical pattern (`SSLContext.sni_callback`
+ swap to a per-cert `SSLContext`).

Server cert map:

| SNI hostname                | Served cert CN             | SAN                              |
|-----------------------------|----------------------------|----------------------------------|
| `router-a.example.test`     | `router-a.example.test`    | `router-a.example.test`          |
| `router-b.example.test`     | `router-b.example.test`    | `router-b.example.test`          |
| `router-c.example.test`     | `router-c.example.test`    | `router-c.example.test`, `router-c-alt.example.test` |
| `router-c-alt.example.test` | `router-c.example.test`    | (same as above)                  |
| `unknown.example.test`      | `*.example.test` (default) | `*.example.test`, `example.test` |

The `router-c-alt` case simulates a Traefik config where one cert is
shared across two SANs (common pattern — single cert, multiple routes).

## How to Run

```bash
# Generate self-signed certs (one-time)
cd .planning/spikes/03-tls-handshake/003-sni-routing-multicert/certs
bash generate.sh

# Run the SNI server + probe
cd ..
python3 server.py
```

## What to Expect

All 5 SNI cases return the expected cert (CN matches):
- 3 exact-name SNIs → exact-match certs
- 1 SAN-routed SNI (`router-c-alt`) → cert with that SAN
- 1 unknown SNI → wildcard fallback cert (Traefik's default-cert behavior)

## Observability

Stdout only. The test server is bound to `127.0.0.1:0` (ephemeral port);
no network exposure.

## Investigation Trail

**v1 (BUG):** First attempt used `initial_context.load_cert_chain(...)`
inside the SNI callback. Result: cert rotation was off-by-one — request
N got the cert meant for request N-1. Root cause: `load_cert_chain`
modifies the SSL_CTX but doesn't reliably affect the *current* handshake
that triggered the callback.

**v2 (FIX):** Built one `SSLContext` per cert with the cert already
loaded. Inside the SNI callback, swapped `ssl_sock.context = new_ctx`.
This is the canonical Python SNI pattern. Result: all 5 cases pass.

**v3 (SAN routing):** Added `router-c-alt.example.test → router-c
context` mapping to mirror Traefik's Host() rule on SAN entries.
Validated that SAN-based SNI works end-to-end.

**v4 (CA trust):** Initial cert generation used
`basicConstraints=critical,CA:FALSE` which made Python refuse to trust
them as root CAs via `load_verify_locations`. Regenerated without that
extension — now trusted as self-signed roots.

## Results

**Verdict: VALIDATED ✓**

```
[PASS] SNI=router-a.example.test       → CN='router-a.example.test'    SAN=('router-a.example.test',)
[PASS] SNI=router-b.example.test       → CN='router-b.example.test'    SAN=('router-b.example.test',)
[PASS] SNI=router-c.example.test       → CN='router-c.example.test'    SAN=('router-c.example.test', 'router-c-alt.example.test')
[PASS] SNI=router-c-alt.example.test   → CN='router-c.example.test'    SAN=('router-c.example.test', 'router-c-alt.example.test')
[PASS] SNI=unknown.example.test        → CN='*.example.test'           SAN=('*.example.test', 'example.test')
```

**Signal for Phase 3 plan:**
- `server_hostname=X` is the right SNI parameter. NOT optional.
- Traefik's Host() rule semantics are preserved by the stdlib callback.
- Wildcard certs are the standard fallback for unmatched SNIs —
  `check_hostname=False` is required so we don't error on wildcard
  mismatches.
- For Phase 3's `tls.domains[].sans[]` handling: probe the SAN directly
  (Traefik routes SAN-based SNI to the cert owning that SAN).
- One `SSLContext` per cert, pre-built, swapped via `ssl_sock.context`
  is the cleanest pattern (don't try `load_cert_chain` mid-handshake).