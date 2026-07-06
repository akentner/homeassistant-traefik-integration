# Spike Manifest

## Idea

Validate the stdlib `ssl` TLS-handshake approach for the Home Assistant
Traefik integration's Phase 3 (TLS Certificate Expiry). The integration
must surface each Traefik TLS router's certificate `notAfter` as an HA
timestamp sensor, but the Traefik HTTP API does **not** expose cert
metadata — the integration must do an out-of-band TLS handshake to each
router's public hostname, parse the leaf cert's `notAfter`, and compute
`days_until_expiry`.

This spike validates the stdlib `ssl` + `socket.create_connection`
approach against real-world scenarios (SNI routing, multi-cert chains,
wildcard certs, IPv6, hostname mismatch, format-string variations)
before committing to the approach in the Phase 3 plan.

## Requirements

Decisions locked from CONTEXT.md, PITFALLS #14, and PROJECT.md (and
re-confirmed by this spike):

- MUST use Python stdlib `ssl` + `socket` only (no `cryptography` import —
  HA bundles it but we keep `manifest.json` `requirements: []`).
- MUST use `ssl.SSLContext(PROTOCOL_TLS_CLIENT)` with `check_hostname=False`
  AND `load_default_certs()`. **Never** `CERT_NONE` (returns empty cert dict).
- MUST pass `server_hostname=host` to `wrap_socket` for SNI routing.
- MUST wrap blocking calls in `asyncio.to_thread(...)` + `asyncio.timeout(5)`.
- MUST use `ssl.cert_time_to_seconds()` as primary `notAfter` parser;
  manual format-string fallback for locale-dependent shapes.
- MUST catch every TLS error path; return typed `CertError`; never raise.
- MUST bound concurrent handshakes with `asyncio.Semaphore(4)`.
- MUST support IPv6 (verified — `socket.create_connection` handles
  `[host]:port` natively; failure modes identical to IPv4).

## Spikes

| #   | Name                     | Type     | Validates                                                                                                                            | Verdict      | Tags                                |
|-----|--------------------------|----------|--------------------------------------------------------------------------------------------------------------------------------------|--------------|-------------------------------------|
| 001 | stdlib-tls-handshake     | standard | Given a real TLS host, when calling `ssl.SSLContext.getpeercert(binary_form=False)`, then we receive a dict with parseable `notAfter`. | VALIDATED ✓  | tls, stdlib, foundation, ha-integration |
| 002 | notafter-format-strings  | standard | Given a `notAfter` string from `getpeercert`, when parsed by a format-string loop, then datetime is returned for all observed shapes. | VALIDATED ✓  | tls, parsing, datetime              |
| 003 | sni-routing-multicert    | standard | Given a TLS server with multiple certs (SNI-routed), when connecting with `server_hostname=X`, then the cert returned matches X.   | VALIDATED ✓  | tls, sni, multi-cert, traefik       |
| 004 | error-handling-async-wrap| standard | Given timeout/unreachable/SNI-mismatch/IPv6-fail conditions, when wrapped in `asyncio.to_thread`+`timeout`+`Semaphore`, then no crash. | VALIDATED ✓  | tls, async, error-handling          |
| 005 | asyncio-open-connection  | comparison | Given the same handshake, when using `asyncio.open_connection(ssl=...)` instead of raw `socket.create_connection` + `wrap_socket`, then SNI + cert dict identical. | VALIDATED ✓  | tls, async, comparison              |
| 006 | hostname-mismatch-detection | standard | Given a cert whose SAN doesn't include the probe hostname, when `fetch_cert_info` runs, then `CertInfo` exposes a `san_mismatch: bool` attribute. | VALIDATED ✓  | tls, san, ux, debugging             |
| 007 | dns-preflight            | standard | Given an unresolvable hostname, when probed, then fast-fail with `error='dns'` in <100ms instead of waiting for `socket.create_connection` timeout. | PARTIAL ⚠   | tls, dns, ux, fail-fast             |

## Key Discoveries (across all 7 spikes)

1. **`CERT_NONE` returns empty dict.** `getpeercert(binary_form=False)`
   only populates the dict when the cert chain was **validated**. Use
   `PROTOCOL_TLS_CLIENT` (default `CERT_REQUIRED`) + `check_hostname=False`
   + `load_default_certs()`. Asymmetric: validate chain, skip strict hostname.

2. **`ssl.cert_time_to_seconds()` is the canonical parser.** All 18 real
   certs (Let's Encrypt, DigiCert, Sectigo, Cloudflare, Apple, etc.) use
   the canonical `"%b %d %H:%M:%S %Y %Z"` format with `GMT` tz. Single-digit
   days use double-space padding (`Aug  5`) which the canonical parser handles.

3. **SNI server pattern: pre-built SSLContext per cert + context swap.**
   The `SSLContext.sni_callback` must set `ssl_sock.context = new_ctx` to
   switch to the right cert mid-handshake. `load_cert_chain` inside the
   callback has off-by-one issues with current handshake.

4. **IPv6 failures identical to IPv4 failures.** `ConnectionRefusedError`
   for `[::1]:1` (no listener) returns the same `error='refused'` TypedDict.

5. **`asyncio.to_thread` + `Semaphore(4)` scales.** 8 concurrent handshakes
   against localhost completed in 20ms; event loop stays responsive
   because all blocking work is in a worker thread.

6. **`asyncio.open_connection(ssl=...)` is a drop-in replacement** for the
   raw `socket.create_connection` + `wrap_socket` pattern (spike 005). Same
   cert dict, same SNI behavior, slightly faster. **RECOMMEND** for Phase 3.

7. **Hostname-SAN mismatch detection is straightforward** (spike 006).
   Add `san_mismatch: bool` to `CertInfo` and a `_hostname_matches_san()`
   helper. Wildcard RFC 6125 §6.4.3 rules are 5 lines of Python.

8. **DNS preflight is NOT worth it** (spike 007). Saves 5-12ms on bad
   hostnames but `socket.create_connection` already fails fast via its
   own `getaddrinfo` call. SKIP for Phase 3.

## Signal for Phase 3 Plan

The stdlib TLS approach is **validated and ready for Phase 3**. The
planned `custom_components/traefik/tls.py` can be built directly from
the spike prototype at `.planning/spikes/03-tls-handshake/shared/tls.py`
with these recommended changes:

- **Use `asyncio.open_connection(ssl=ctx, server_hostname=host)` directly**
  (spike 005) instead of `socket.create_connection` + `to_thread`.
- **Add `san_mismatch: bool` field to `CertInfo`** + `_hostname_matches_san()`
  helper (spike 006). Expose as `extra_state_attribute` on the timestamp sensor.
- **SKIP DNS preflight** (spike 007). Existing error path already
  returns `error='dns'` for unresolvable hostnames.
- Add `host`-vs-`sni` separation in the production API for tests.
- Drop the unused `notAfter no-tz` fallback format (never observed in 18 samples).
- Add `_log_once_per_host_per_24h` throttle around parse-failure debug logs
  (CONTEXT.md D-11).

Confidence: HIGH. All 7 spike questions answered with concrete evidence
from real internet hosts + local Traefik simulator.