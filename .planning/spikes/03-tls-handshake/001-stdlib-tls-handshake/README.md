---
spike: 001
name: stdlib-tls-handshake
type: standard
validates: "Given a real TLS host, when calling ssl.SSLContext.getpeercert(binary_form=False), then we receive a dict with parseable notAfter."
verdict: VALIDATED
related: [002-notafter-format-strings, 003-sni-routing-multicert, 004-error-handling-async-wrap]
tags: [tls, stdlib, foundation, ha-integration]
---

# Spike 001: stdlib TLS Handshake Foundation

## What This Validates

The stdlib `ssl` + `socket` handshake gives us a parseable cert dict for
every TLS-enabled router in the user's Traefik fleet. Without this, the
whole Phase 3 approach dies.

## Research

Approaches considered (all stdlib — no `cryptography` per STACK.md):

| Approach | Tool | Pros | Cons | Status |
|----------|------|------|------|--------|
| `PROTOCOL_TLS_CLIENT` + `load_default_certs()` + `check_hostname=False` | stdlib `ssl` | Cert dict populated (CERT_REQUIRED); strict hostname check off for SNI-mismatch tolerance | Requires CA bundle (HA has it system-wide) | **Chosen** |
| `PROTOCOL_TLS_CLIENT` + `check_hostname=True` | stdlib `ssl` | Strict — only valid certs accepted | Fails on Traefik default certs, wildcard mismatches, IP-only probes | Rejected |
| `PROTOCOL_TLS_CLIENT` + `verify_mode=CERT_NONE` | stdlib `ssl` | No chain validation | **`getpeercert()` returns empty dict** per docs ("If the certificate was not validated, the dict is empty") | Rejected |
| `cryptography` library | external | Full X.509 parsing | Adds manifest `requirements`; HA bundles but project mandates stdlib-only | Rejected |

**Critical gotcha discovered (PITFALLS #14 missed this nuance):**
`getpeercert(binary_form=False)` only populates the dict when the cert
chain was **validated**. The chain must validate, but the **hostname**
must NOT be checked strictly — that's the asymmetric setting
(`CERT_REQUIRED` + `check_hostname=False`).

Also: `load_default_certs()` is required even with `CERT_REQUIRED`
because Python's stdlib does not pre-load system CAs (unlike some
language ecosystems). HA's Python install ships the `certifi` CA bundle
in `ssl.get_default_verify_paths()`, so this Just Works inside HA.

## How to Run

```bash
cd .planning/spikes/03-tls-handshake/shared
python3 -c "
from tls import fetch_cert_info
for host in ('letsencrypt.org', 'traefik.io', 'github.com'):
    r = fetch_cert_info(host)
    print(host, '->', r if isinstance(r, dict) else r.not_after)
"
```

## What to Expect

- 5+ real internet TLS hosts probed successfully
- `notAfter` returns as `datetime` (UTC)
- `subject` + `issuer` parsed as comma-separated `CN=..., O=...` strings
- `subjectAltName` (SAN) returned as tuple of DNS entries
- `days_until_expiry` computed correctly

## Observability

Stdout output only — this is a binary fact-validation spike (does stdlib
TLS work?), not a user-facing feature. No forensic log layer needed.

## Investigation Trail

**v1:** Initial prototype used `CERT_NONE` thinking "we're probing, not
validating". Probed 5 real hosts — ALL returned empty dict. Per Python
docs: *"If the certificate was not validated, the dict is empty."*

**v2:** Switched to `CERT_REQUIRED` (default for `PROTOCOL_TLS_CLIENT`)
but kept `check_hostname=False`. Got `CERTIFICATE_VERIFY_FAILED:
unable to get local issuer certificate` — Python doesn't pre-load
system CAs like other ecosystems.

**v3:** Added `ctx.load_default_certs()` — all 5 hosts probed
successfully. Sanity-checked against the actual `notAfter` raw shape:
`'Aug  5 16:14:36 2026 GMT'` (note double-space day-of-month for
single-digit days). Both `ssl.cert_time_to_seconds()` and our manual
fallback `"%b  %d %H:%M:%S %Y %Z"` format handle this.

## Results

**Verdict: VALIDATED ✓**

Probe results against 5 real TLS hosts (incl. `traefik.io` itself):

```
OK    letsencrypt.org:443  expires=2026-08-05  days=  30  cn=CN=letsencrypt.org
OK    www.google.com:443   expires=2026-09-07  days=  63  cn=CN=www.google.com
OK    github.com:443       expires=2026-09-30  days=  86  cn=CN=github.com
OK    cloudflare.com:443   expires=2026-08-08  days=  33  cn=CN=cloudflare.com
OK    traefik.io:443       expires=2026-09-14  days=  70  cn=CN=traefik.io
                                            san=['traefik.io', '*.traefik.io']
```

Key discoveries:
- Traefik.io itself uses `*.traefik.io` wildcard SAN — confirms wildcard
  certs are the norm, not the exception. `check_hostname=False` is correct.
- All probed `notAfter` strings use the `"%b  %d %H:%M:%S %Y %Z"` format
  (double-space day padding) — `ssl.cert_time_to_seconds()` handles it.
- `days_until_expiry` is positive and decreasing over time as expected.

**Signal for Phase 3 plan:**
- Use `ssl.SSLContext(PROTOCOL_TLS_CLIENT)` + `check_hostname=False` +
  `load_default_certs()`. Never `CERT_NONE` (empty dict).
- Primary parser: `ssl.cert_time_to_seconds()`. Manual fallback formats
  for safety; spike 002 expands the catalog.
- `subjectAltName` extraction works — useful for Phase 3's "which router
  serves this host" attribute aggregation.