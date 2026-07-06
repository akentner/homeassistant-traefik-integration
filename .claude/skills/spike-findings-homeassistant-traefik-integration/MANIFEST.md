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

## Key Discoveries (across all 4 spikes)

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

## Signal for Phase 3 Plan

The stdlib TLS approach is **validated and ready for Phase 3**. The
planned `custom_components/traefik/tls.py` can be built directly from
the spike prototype at `.planning/spikes/03-tls-handshake/shared/tls.py`
with minimal changes:

- Add `host`-vs-`sni` separation in the production API (probe hosts ARE
  resolvable, so `host` serves both roles in prod; tests need explicit split).
- Drop the unused `notAfter no-tz` fallback format (never observed in 18 samples).
- Wrap `fetch_cert_info` in `asyncio.to_thread` from the coordinator (already
  done in `fetch_cert_info_async`).
- Add `_log_once_per_host_per_24h` throttle around parse-failure debug logs
  (CONTEXT.md D-11).
- Optionally: a sync `_fetch_cert(host, port, *, sni=None, timeout=5.0)` for
  unit tests that don't want asyncio.

Confidence: HIGH. All 4 spike questions answered with concrete evidence
from real internet hosts + local Traefik simulator.