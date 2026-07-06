---
spike: 007
name: dns-preflight
type: standard
validates: "Given an unresolvable hostname, when probed, then fast-fail with error='dns' in <100ms instead of waiting for socket.create_connection timeout."
verdict: PARTIAL
related: [004-error-handling-async-wrap, 001-stdlib-tls-handshake]
tags: [tls, dns, ux, fail-fast, optimization]
---

# Spike 007: DNS Preflight (Fail-Fast on Bad Hostnames)

## What This Validates

Whether doing a DNS lookup BEFORE the TCP+TLS handshake saves enough
time on bad hostnames to justify the extra `getaddrinfo` call on every
probe.

## Research

Approach: wrap `proto.fetch_cert_info()` with a `getaddrinfo()` call
first. If it fails, return `error='dns'` immediately. Otherwise, fall
through to the existing TLS handshake.

Comparison measured elapsed time for the same 5 hostnames under both
patterns.

## How to Run

```bash
cd .planning/spikes/03-tls-handshake/007-dns-preflight
python3 probe.py
```

## What to Expect

Both patterns work; preflight is faster on bad hostnames (~5-12ms
savings), but the absolute time is already low (~10ms for bad TLDs
without preflight, vs ~0.5ms with).

## Observability

Stdout only.

## Investigation Trail

**v1:** Initial hypothesis was that preflight would save ~1-2 seconds
(avoiding the socket timeout on bad hostnames). Reality: Python's
`socket.create_connection` already fails fast on DNS errors via
`getaddrinfo` — the savings are minimal.

**v2:** Measured on real bad-TLD hostnames (`.test`, `.invalid`):
- Without preflight: 5-12ms
- With preflight: 0.3-0.5ms

**v3:** On good hostnames, preflight is sometimes FASTER (DNS cache hit)
and sometimes the same (no measurable cost).

**v4:** Discovered the underlying reason: `socket.create_connection`
ALREADY calls `getaddrinfo` internally. The "preflight" just exposes
that call as a separate step that can be timed / classified differently.

## Results

**Verdict: PARTIAL ⚠** (functional but not worth the complexity)

| Host type | No preflight | With preflight | Savings |
|-----------|--------------|----------------|---------|
| `nonexistent.example.test` | 12.4ms | 0.5ms | 11.9ms |
| `.invalid` TLD | 5.5ms | 0.4ms | 5.1ms |
| `nx.example.invalid` | 8.1ms | 0.3ms | 7.8ms |
| `letsencrypt.org` | 76.5ms | 29.5ms | 47ms (DNS cache) |
| `github.com` | 36.7ms | 36.3ms | negligible |

**Why PARTIAL, not VALIDATED:**
- Saves only 5-12ms on bad hostnames (acceptable but not impactful)
- Adds 1 function call + error mapping on every probe (extra code surface)
- The error returned is functionally identical (`error='dns'` either way)
- Python's stdlib `socket.create_connection` already does the right thing

**RECOMMENDATION for Phase 3 plan:**
- **SKIP DNS preflight in Phase 3** — the savings don't justify the complexity
- The existing `proto.fetch_cert_info()` already returns `error='dns'`
  for unresolvable hostnames (verified by spike 004 IPv6 scenario)
- Only revisit if users report "stuck probe" issues in production
- If we DO need preflight later (e.g. for very long-tail timeouts on
  certain DNS misconfigurations), the spike code shows the pattern:
  `socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)` wrapped in
  try/except for `socket.gaierror` → return error dict early

**Watch for:**
- DNS-over-HTTPS (DoH) environments where `getaddrinfo` may behave
  differently; spike 007 used system resolver only
- IPv6 literal hosts (e.g. `[::1]`) — `getaddrinfo` accepts these, but
  preflight would still call DNS even though no lookup is needed
- Network configurations where `getaddrinfo` blocks longer than
  `socket.create_connection` itself (rare but possible)