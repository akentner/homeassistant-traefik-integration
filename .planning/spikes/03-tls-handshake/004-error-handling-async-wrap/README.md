---
spike: 004
name: error-handling-async-wrap
type: standard
validates: "Given timeout/unreachable/SNI-mismatch/IPv6-fail conditions, when wrapped in asyncio.to_thread+timeout+Semaphore, then no crash."
verdict: VALIDATED
related: [001-stdlib-tls-handshake, 003-sni-routing-multicert]
tags: [tls, async, error-handling, semaphore, ipv6]
---

# Spike 004: Error Handling & Async Wrapper

## What This Validates

Every TLS error path is caught locally and surfaced as a typed
`CertError` — never propagated to the CertCoordinator (CONTEXT.md
D-10). The `asyncio.to_thread` + `Semaphore(4)` pattern works
correctly for concurrent handshakes. IPv6 failures are handled
identically to IPv4.

## Research

The blocking `socket.create_connection` + `ssl.wrap_socket` chain must
be wrapped:

1. **`asyncio.to_thread(...)`** — offload to a worker thread so the HA
   event loop stays responsive (CONTEXT.md D-05)
2. **`asyncio.timeout(5)`** per host — hung handshake can't stall the
   coordinator (CONTEXT.md D-05)
3. **`asyncio.Semaphore(4)`** — bound concurrent handshakes per cycle
   (CONTEXT.md D-05)

Error paths to handle:
- `socket.timeout` — handshake exceeded per-host budget
- `socket.gaierror` — DNS resolution failed
- `ConnectionRefusedError` — TCP port closed
- `OSError` — generic socket error (e.g., UNEXPECTED_EOF, network down)
- `ssl.SSLError` — handshake-level error (cert verify, protocol, etc.)
- `ValueError` from `parse_not_after` — format-string miss
- Any other `Exception` — last-resort catch (never propagate)

## How to Run

```bash
cd .planning/spikes/03-tls-handshake/004-error-handling-async-wrap
python3 probe.py
```

Spins up the spike 003 SNI server on `127.0.0.1:0`, then runs all 9
scenarios against it.

## What to Expect

```
── Scenario: timeout (hangs on accept) ──────────────
  [PASS] elapsed=2.01s  result={'error': 'timeout', 'detail': 'timeout after 2.0s'}

── Scenario: connection refused ────────────────────
  [PASS] result={'error': 'refused', 'detail': '[Errno 111] Connection refused'}

── Scenario: IPv6 unreachable ─────────────────────
  [PASS] result={'error': 'refused', 'detail': '[Errno 111] Connection refused'}

── Scenario: parse failure (sync) ─────────────────
  [PASS] all 5 malformed inputs correctly rejected (ValueError)

── Scenario: concurrent (8x, sem=4) ───────────────
  [PASS] completed 8/8 in 0.02s; first 3: ['router-a=cert', 'router-a=cert', 'router-a=cert']

── Scenario: SNI routing (host=127.0.0.1, SNI=…) ─
  [PASS] [PASS] [PASS] [PASS]   ← all 4 SNI cases
```

## Observability

Stdout only. All errors include `host`, `port`, `error` classification,
and a `detail` string with the underlying exception message.

## Investigation Trail

**v1 (BUG):** Initial `fetch_cert_info_async` used host as both
TCP connect address AND SNI. Test hostnames like `router-a.example.test`
don't resolve via DNS → all concurrent probes got `gaierror`. Fix:
in spike tests, use `127.0.0.1` for the connect address and pass the
hostname separately as SNI. The production `fetch_cert_info` API
correctly uses host for both (real Traefik routers ARE reachable via
DNS) — only the test pattern needed adjustment.

**v2 (BUG):** Hanging TCP server pattern used `_accept_loop.connections`
as a function-attribute trick which failed at import time. Fix: use a
plain local variable `accepted_conns: list[socket.socket]`.

**v3 (cert trust):** Self-signed certs with `basicConstraints=CA:FALSE`
were rejected by `load_verify_locations` — they can't serve as trust
anchors when they declare themselves non-CAs. Fix: regenerate without
the `basicConstraints` extension.

## Results

**Verdict: VALIDATED ✓ (9/9 scenarios)**

| # | Scenario                    | Result                                                    |
|---|-----------------------------|-----------------------------------------------------------|
| 1 | Timeout (hangs on accept)   | PASS — `error='timeout'` after 2.0s                       |
| 2 | Connection refused          | PASS — `error='refused'`                                  |
| 3 | IPv6 unreachable            | PASS — `error='refused'` (same as v4)                     |
| 4 | Parse failure (5 inputs)    | PASS — all rejected with `ValueError`                     |
| 5 | Concurrent 8x sem=4         | PASS — 8/8 in 20ms                                        |
| 6 | SNI router-a.example.test   | PASS — CN=router-a.example.test                           |
| 7 | SNI router-b.example.test   | PASS — CN=router-b.example.test                           |
| 8 | SNI router-c-alt (SAN)      | PASS — CN=router-c.example.test (SAN-routed)              |
| 9 | SNI unknown (wildcard)      | PASS — CN=*.example.test (default cert fallback)          |

**Signal for Phase 3 plan:**
- The `fetch_cert_info` / `fetch_cert_info_async` API shape works:
  - Returns `CertInfo` on success, `CertError` (dict) on failure
  - NEVER raises — all paths caught
  - Wrapped via `asyncio.to_thread` + `asyncio.timeout(N)` for non-blocking
    + bounded execution
- IPv6 and IPv4 failures are indistinguishable (good — Traefik hostnames
  resolve to either, and the integration should just handle both)
- Concurrent handshakes at sem=4 scale: 8 certs in 20ms (~2.5ms/cert)
  against localhost. Network latency will dominate in production but
  the HA event loop stays responsive because of `to_thread`.
- The `(host, port, sni)` separation in tests exposed a real design
  question for Phase 3: should `fetch_cert_info` accept SNI as a
  separate param? **Recommend:** keep host-as-SNI for production
  (real routers are reachable), but expose a low-level `_fetch_cert`
  that takes (host, port, sni) for tests + advanced cases.