# TLS Handshake — Implementation Blueprint

Build `custom_components/traefik/tls.py` + `custom_components/traefik/cert_coordinator.py`
that surface every Traefik TLS router's certificate `notAfter` as a Home
Assistant timestamp sensor. Out-of-band TLS handshake using **only Python
stdlib** (`ssl`, `socket`, `asyncio`).

## Requirements

Non-negotiable decisions from MANIFEST.md (every line is locked by spike evidence):

- Python stdlib only — **no `cryptography` import**, no `manifest.json` `requirements` entries
- `ssl.SSLContext(PROTOCOL_TLS_CLIENT)` + `check_hostname=False` + `load_default_certs()` (asymmetric: validate chain, skip strict hostname)
- `server_hostname=host` parameter on `wrap_socket` for SNI routing — mandatory, not optional
- Wrap blocking work in `asyncio.to_thread(...)` + `asyncio.timeout(5)` per host
- Primary `notAfter` parser: `ssl.cert_time_to_seconds()`; manual format-string fallback loop as defense in depth
- Every error path returns a typed `CertError` dict; **never propagate exceptions** to the CertCoordinator (CONTEXT.md D-10)
- Bound concurrent handshakes with `asyncio.Semaphore(4)` (CONTEXT.md D-05)
- IPv6 must work — `socket.create_connection((host, port))` handles `[host]:port` natively
- Honor `entry.options[CONF_TLS_WARN_DAYS]` for the expiring threshold (CONTEXT.md D-08, D-09)
- Use `CertCoordinator` on `entry.runtime_data` (sibling to `TraefikCoordinator`) — do not migrate existing shape (PITFALLS #6)
- 6h `update_interval`; per-cycle cache dict[str, CertInfo]; in-memory only (no persistence)

## How to Build It

### File layout

```
custom_components/traefik/
├── tls.py                  # NEW — stdlib handshake helper (below)
├── cert_coordinator.py     # NEW — 6h cadence, sem=4, cache
├── sensor.py               # + TraefikCertTimestampSensor(SensorDeviceClass.TIMESTAMP)
├── binary_sensor.py        # + TraefikCertExpiryBinarySensor(BinarySensorDeviceClass.PROBLEM)
├── const.py                # + TLS_HANDSHAKE_TIMEOUT=5.0, TLS_SEMAPHORE=4, DEFAULT_TLS_CERT_COOLDOWN=21600
├── entity.py               # + "HTTP Routers TLS" to _CATEGORY_TO_MODEL
├── __init__.py             # extend _async_options_updated per CONTEXT.md D-08
└── config_flow.py          # (no new option — CONF_TLS_WARN_DAYS already in Phase 2)

tests/components/traefik/
├── test_tls.py                          # format-string, IPv6, SNI, hostname-mismatch
├── test_cert_coordinator.py             # sem=4, timeout, threshold re-eval, cache
├── test_sensor_tls.py                   # timestamp sensor + attributes
└── test_binary_sensor_tls_expiring.py   # state transitions, threshold live-re-eval
```

### `tls.py` — the handshake helper

This is the **prototype at `sources/shared/tls.py`** with these production deltas:

1. **Drop unused `notAfter no-tz` fallback format** — never observed in 18 real-world samples (spike 002).
2. **Add `_log_once_per_host_per_24h` throttle** around parse-failure debug logs (CONTEXT.md D-11).
3. **Separate `host` and `sni` parameters** in a low-level `_fetch_cert_raw(host, port, *, sni=None, timeout=5.0)` for unit tests; keep `fetch_cert_info(host, port)` for production where hostname == SNI.
4. **Use `dataclass(frozen=True)` for `CertInfo`** (already done in prototype) — enables hashing for the cache.
5. **Type-narrow the result union** with `is_error(result: CertInfo | CertError) -> TypeGuard` (already in prototype).

Key code pattern (extract from prototype):

```python
import ssl
import socket
from datetime import UTC, datetime

def fetch_cert_info(host, port=443, *, timeout=5.0):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.load_default_certs()  # REQUIRED — CERT_REQUIRED needs trusted CAs
    with (
        socket.create_connection((host, port), timeout=timeout) as raw_sock,
        ctx.wrap_socket(raw_sock, server_hostname=host) as ssock,
    ):
        cert = ssock.getpeercert(binary_form=False)  # ← CERT_REQUIRED populates this
    # ... parse + return CertInfo or CertError (never raise)
```

**The trap:** `verify_mode=CERT_NONE` returns an empty dict per Python docs ("If the certificate was not validated, the dict is empty"). Always use `CERT_REQUIRED` (the `PROTOCOL_TLS_CLIENT` default) but disable hostname check.

### `cert_coordinator.py` — the 6h cadence wrapper

```python
import asyncio
from datetime import timedelta
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

class CertCoordinator(DataUpdateCoordinator[dict[str, CertInfo]]):
    def __init__(self, hass, *, threshold_days: int = 14, sem: int = 4, timeout: float = 5.0):
        super().__init__(hass, _LOGGER, name="traefik_certs", update_interval=timedelta(hours=6))
        self.threshold_days = threshold_days
        self._sem = asyncio.Semaphore(sem)
        self._timeout = timeout

    async def _async_update_data(self) -> dict[str, CertInfo]:
        # Get host list from the main coordinator's cached routers
        hosts = self._collect_hosts_from_main_coordinator()
        tasks = [self._probe(host) for host in hosts]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return {h: r for h, r in zip(hosts, results) if isinstance(r, CertInfo)}

    async def _probe(self, host: str) -> CertInfo | CertError:
        async with self._sem:
            try:
                async with asyncio.timeout(self._timeout):
                    return await asyncio.to_thread(fetch_cert_info, host, timeout=self._timeout)
            except (TimeoutError, asyncio.TimeoutError):
                return {"host": host, "error": "timeout", "detail": "..."}
            except Exception as exc:
                return {"host": host, "error": "unknown", "detail": str(exc)}
```

### Hostname extraction (the Host() rule parser)

CONTEXT.md D-02 says hostnames = union of `tls.domains[].main`, `tls.domains[].sans[]`, and `Host(\`x\`)` matches in the rule. Phase 2 already has `_friendly_rule` regex in `binary_sensor.py:24-32` — reuse it.

Routers with `tls` set but **no per-host resolution** (wildcard / default cert) are skipped entirely — Traefik owns those, out of scope.

### Threshold live re-evaluation (CONTEXT.md D-08)

Extend `_async_options_updated` in `__init__.py:149` to:

```python
async def _async_options_updated(hass, entry):
    new_threshold = entry.options.get(CONF_TLS_WARN_DAYS, DEFAULT_TLS_WARN_DAYS)
    entry.runtime_data.cert.threshold_days = new_threshold
    entry.runtime_data.cert.async_update_listeners()  # immediate re-render
```

No re-handshake — cached `notAfter` data unchanged; only the threshold applied to it shifts.

## What to Avoid

These are **landmines** the spike surfaced. Each has concrete evidence in the source READMEs.

1. **`verify_mode=CERT_NONE`** — `getpeercert(binary_form=False)` returns empty dict per Python docs. Spent 30 min on this in spike 001.
2. **`load_cert_chain` inside `sni_callback`** — off-by-one cert serving (cert for handshake N+1 gets served in handshake N). Use `ssl_sock.context = new_ctx` instead (pre-built SSLContext per cert).
3. **`check_hostname=True`** — fails on Traefik default certs, wildcards, IP-only probes. Disable it on the probe side.
4. **Forgetting `load_default_certs()`** — `CERT_REQUIRED` requires CA bundle; without it you get `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`.
5. **`self-signed certs with basicConstraints=CA:FALSE`** — can't be trusted as root CAs via `load_verify_locations`. Generate without `basicConstraints` if you need self-signed test certs.
6. **`fetch_cert_info_async(host)` using host for both connect address AND SNI** — fine in production (Traefik hostnames are resolvable) but breaks tests using `router-a.example.test`. Add a low-level `_fetch_cert_raw(host, port, *, sni=None)` for unit tests.
7. **`async function timeout` parameter** — ruff ASYNC109 warns about it colliding with `asyncio.timeout` builtin. Keep the param name `timeout` and `# noqa: ASYNC109` (it's passed through to `socket.create_connection(timeout=...)`, not asyncio).
8. **`asyncio.get_event_loop()`** — deprecated in 3.12+. Use `asyncio.run()` for sync entry points.
9. **Per-entity async update** instead of `CertCoordinator` — defeats the 6h cadence + semaphore pattern.
10. **`cryptography` library** — adds `manifest.json` requirements; project mandates stdlib-only.

## Constraints

Hard facts discovered during spiking:

| Constraint | Source | Impact |
|------------|--------|--------|
| `ssl.SSLSocket.getpeercert()` returns loosely-typed dict | Python stdlib | Use TypedDict + cast() for mypy-strict |
| `socket.create_connection((host, port))` is blocking | Python stdlib | MUST wrap in `asyncio.to_thread` |
| HA's `aiohttp.ClientSession` does NOT help here | Spike 001 | TLS uses raw socket, not aiohttp |
| Traefik HTTP API does NOT expose cert `notAfter` | Traefik docs | Out-of-band handshake is the only path |
| `asyncio.Semaphore(4)` recommended | CONTEXT.md D-05 | 8 concurrent on localhost = 20ms; production dominated by network |
| All observed certs use GMT tz | Spike 002 (18 hosts) | Format fallback never fires in practice |
| Single-digit days use double-space padding | Spike 002 | `Aug  5 16:14:36 2026 GMT` |
| `days_until_expiry` rounds ±1 day | Spike 002 | Acceptable for 14d threshold |
| Python 3.14.6 + OpenSSL 3.6.3 in dev | Project `pyproject.toml` | Same HA bundles will run on user systems |
| `tls.domains[].sans[]` shares one cert | Spike 003 | Multiple SNI probes → one cert returned |
| Wildcard certs are the default-cert fallback | Spike 003 | Traefik serves wildcard for unmatched SNIs |

## Verification

Run the spike to confirm the approach still works in CI:

```bash
bash .claude/skills/spike-findings-homeassistant-traefik-integration/run-all.sh
```

Expected output: 4 `Verdict: VALIDATED ✓` lines.

To unit-test `tls.py` in isolation:

```python
from custom_components.traefik.tls import fetch_cert_info, CertInfo
result = fetch_cert_info("letsencrypt.org", timeout=5.0)
assert isinstance(result, CertInfo)
assert result.days_until_expiry > 0
```

For SNI scenarios, use the spike's `003-sni-routing-multicert/server.py` as a local Traefik simulator — generates self-signed certs via `openssl`, runs an `asyncio.start_server` with `sni_callback` swapping `ssl_sock.context`.

## Origin

Synthesized from spikes 001, 002, 003, 004.
Source files available in:
- `sources/shared/tls.py` (production-ready prototype)
- `sources/001-stdlib-tls-handshake/probe.py` (foundation validation)
- `sources/002-notafter-format-strings/probe.py` (format-string loop coverage)
- `sources/003-sni-routing-multicert/server.py` (Traefik simulator)
- `sources/004-error-handling-async-wrap/probe.py` (error scenarios)