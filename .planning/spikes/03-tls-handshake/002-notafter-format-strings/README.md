---
spike: 002
name: notafter-format-strings
type: standard
validates: "Given a notAfter string from getpeercert, when parsed by a format-string loop, then datetime is returned for all observed real shapes."
verdict: VALIDATED
related: [001-stdlib-tls-handshake, 004-error-handling-async-wrap]
tags: [tls, parsing, datetime, format-strings]
---

# Spike 002: notAfter Format-String Loop

## What This Validates

The `parse_not_after()` function correctly handles every `notAfter` shape
that real-world CAs (Let's Encrypt, DigiCert, Sectigo, etc.) actually
emit, and correctly rejects malformed strings.

## Research

Approach: defense-in-depth per PITFALLS #14:

1. **Primary:** `ssl.cert_time_to_seconds()` — canonical C-locale parser
   handling `"%b %d %H:%M:%S %Y %Z"`. Returns Unix timestamp.
2. **Fallback:** manual `NOTAFTER_FORMATS` tuple with `strptime` for
   locale-dependent variants and double-space-padded days.

Findings (18 real hosts sampled):
- **100% of real certs** use `"%b %d %H:%M:%S %Y %Z"` with `GMT` tz
- Single-digit days use **double-space padding** (`Aug  5` not `Aug 5`)
  — `ssl.cert_time_to_seconds()` handles this natively
- **Zero observed variants** in the no-tz or ISO-8601 shapes
- CAs in our sample: Let's Encrypt, Google Trust Services, Sectigo,
  DigiCert, Cloudflare, Apple, Microsoft, GoDaddy, Amazon

## How to Run

```bash
cd .planning/spikes/03-tls-handshake/002-notafter-format-strings
python3 probe.py
```

## What to Expect

- All 6 synthetic `PASS` cases (canonical, no-tz, double-space, leap, etc.)
- All 6 malformed inputs correctly rejected with `ValueError`
- All 18 real hosts parse cleanly via the primary path
- `days_until_expiry` matches expected `~30d` (within ±1 day rounding)

## Observability

Stdout only — pure fact-validation spike.

## Investigation Trail

**v1:** Initial `NOTAFTER_FORMATS` had 3 entries (canonical, no-tz,
double-space). All 18 real-world samples went through the canonical
primary path (`ssl.cert_time_to_seconds`). The fallback never fired
on any real input.

**v2:** Verified `parse_not_after()` correctly rejects:
- ISO-8601 (`2025-11-15T12:00:00Z`)
- Human format (`15 Nov 2025`)
- US slash format (`11/15/2025`)
- Empty / garbage strings

If a future cert uses a non-canonical shape, the manual fallback loop
is the safety net. No code change needed unless we observe a new shape
in production telemetry.

## Results

**Verdict: VALIDATED ✓**

```
NOTAFTER_FORMATS fallback catalog (3):
  '%b %d %H:%M:%S %Y %Z'        ← primary path (real-world)
  '%b %d %H:%M:%S %Y'           ← no-tz fallback (unobserved in sample)
  '%b  %d %H:%M:%S %Y %Z'       ← double-space day fallback (real-world)

Catalogue of observed shapes: ['GMT: 18']
Synthetic: passed=12 failed=0
Real:      parsed_ok=18 probe_failed=0
```

**Signal for Phase 3 plan:**
- `parse_not_after()` is safe to ship as-is. Primary path covers all
  real-world cases; fallback handles theoretical locale variants.
- Consider keeping only the canonical + double-space fallback formats —
  the no-tz variant was never observed in 18 samples.
- `days_until_expiry` rounding: time-of-day rounding gives ±1 day
  variation. Acceptable for the threshold (default 14d).