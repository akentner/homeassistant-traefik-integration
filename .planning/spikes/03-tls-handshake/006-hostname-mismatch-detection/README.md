---
spike: 006
name: hostname-mismatch-detection
type: standard
validates: "Given a cert whose SAN doesn't include the probe hostname, when fetch_cert_info runs, then CertInfo exposes a san_mismatch: bool attribute."
verdict: VALIDATED
related: [001-stdlib-tls-handshake, 003-sni-routing-multicert]
tags: [tls, san, ux, debugging, attribute]
---

# Spike 006: Hostname Mismatch Detection

## What This Validates

A small `_hostname_matches_san()` helper that checks if the probe hostname
is covered by any cert SAN entry (exact or wildcard), exposed as
`CertInfo.san_mismatch: bool`. Useful for:

- Surfacing a diagnostic state when Traefik returns a default cert for a
  hostname that doesn't strictly match (e.g. wildcard mismatch)
- Helping users debug "why is my cert not being detected" issues
- Potentially toggling a separate "cert-not-for-this-host" binary_sensor

## Research

The wildcard matching rule (RFC 6125 §6.4.3):
- `*.example.com` matches `foo.example.com` (one subdomain level)
- `*.example.com` does NOT match `example.com` (bare domain)
- `*.example.com` does NOT match `foo.bar.example.com` (multi-level)

Implementation:

```python
def _hostname_matches_san(host: str, san: tuple[str, ...]) -> bool:
    host_lower = host.lower().rstrip(".")
    for entry in san:
        entry_lower = entry.lower().rstrip(".")
        if entry_lower == host_lower:
            return True
        if entry_lower.startswith("*."):
            suffix = entry_lower[1:]  # ".example.com"
            if host_lower.endswith(suffix) and host_lower.count(".") >= entry_lower.count("."):
                return True
    return False
```

## How to Run

```bash
cd .planning/spikes/03-tls-handshake/006-hostname-mismatch-detection
python3 probe.py
```

## What to Expect

4 live internet probes + 7 unit tests = 11/11 cases PASS.

## Observability

Stdout only.

## Investigation Trail

**v1:** Initially planned to use `cryptography` library's proper X.509
SAN walking — rejected per STACK.md (project mandates stdlib-only).

**v2:** Implemented stdlib-only SAN matching with explicit wildcard rule.
Verified against Traefik's real `*.traefik.io` cert: probing `www.traefik.io`
returns `san_mismatch=False` because Traefik serves an explicit
`www.traefik.io` SAN entry (not relying on the wildcard).

**v3:** Added 7 unit tests covering edge cases:
- Exact match
- Wildcard match (single level)
- Multi-level subdomain under wildcard
- Narrower wildcard (`*.api.example.com` under `foo.api.example.com`)
- Wildcard does NOT cover bare domain
- No match at all
- One-of-many match

## Results

**Verdict: VALIDATED ✓ (11/11)**

Live probes:
- `traefik.io` → `san_mismatch=False` (exact CN match)
- `www.traefik.io` → `san_mismatch=False` (explicit SAN, not wildcard)
- `www.google.com` → `san_mismatch=False` (exact match)
- `nonexistent.example.test` → error='dns' (probe fails before SAN check)

Unit tests for wildcard detector:
```
[PASS] api.example.com       *.example.com         → True
[PASS] example.com           *.example.com         → False  (wildcard doesn't cover bare)
[PASS] foo.api.example.com   *.example.com         → True   (multi-level covered)
[PASS] foo.api.example.com   *.api.example.com     → True   (narrower wildcard)
[PASS] example.com           example.com           → True   (exact)
[PASS] example.com           example.org           → False  (no match)
[PASS] api.example.com       api.example.com + ... → True   (one of many)
```

**RECOMMENDATION for Phase 3 plan:**
- Add `san_mismatch: bool = False` field to `CertInfo` dataclass
- Add `_hostname_matches_san()` helper in `tls.py`
- Set `san_mismatch = not _hostname_matches_san(host, cert_dict['subjectAltName'])`
- Expose as `extra_state_attribute` on the `TraefikCertTimestampSensor` so
  users can see it in HA without a separate binary_sensor
- Consider also exposing as a separate `binary_sensor.traefik_<host>_cert_mismatch`
  for users who want to automate on it (out of Phase 3 success criteria 1-5;
  could be a small Phase 4 addition)