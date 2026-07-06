"""Spike 002: Validate parse_not_after against synthetic + real inputs.

Exercises both the primary path (ssl.cert_time_to_seconds) and the manual
format-string fallback loop. Documents the catalogue of observed notAfter
shapes so Phase 3 can encode them in NOTAFTER_FORMATS.
"""

from __future__ import annotations

import socket
import ssl
import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "shared"))
from tls import NOTAFTER_FORMATS, parse_not_after

# --- Synthetic inputs (manual fallback coverage) -------------------------

SYNTHETIC_OK = [
    # (raw, expected_year, expected_month, expected_day) — None means skip check
    ("Nov 15 12:00:00 2025 GMT", 2025, 11, 15),       # canonical
    ("Nov 15 12:00:00 2025", 2025, 11, 15),            # no timezone
    ("Nov  1 12:00:00 2025 GMT", 2025, 11, 1),         # single-digit day (double-space)
    ("Jan  5 09:34:43 2018 GMT", 2018, 1, 5),          # from Python docs
    ("Feb 29 12:00:00 2024 GMT", 2024, 2, 29),         # leap year
    ("Dec 31 23:59:59 2025 GMT", 2025, 12, 31),
]

SYNTHETIC_FAIL = [
    "",                                              # empty
    "not-a-date",                                    # garbage
    "2025-11-15T12:00:00Z",                          # ISO-8601
    "15 Nov 2025",                                   # human format
    "11/15/2025",                                    # US slash
    "Nov 15 2025",                                   # missing time
]


def test_synthetic() -> tuple[int, int]:
    """Returns (passed, failed) counts."""
    passed = 0
    failed = 0
    print("── Synthetic notAfter inputs ─────────────────────────────")
    for raw, year, month, day in SYNTHETIC_OK:
        try:
            dt = parse_not_after(raw)
            ok = dt.year == year and dt.month == month and dt.day == day
            status = "PASS" if ok else "FAIL"
            print(f"  [{status}] {raw!r:50s} → {dt.date()}  (tzinfo={dt.tzinfo})")
            if ok:
                passed += 1
            else:
                failed += 1
        except Exception as exc:
            print(f"  [FAIL] {raw!r:50s} → raised {type(exc).__name__}: {exc}")
            failed += 1

    print()
    print("── Synthetic notAfter inputs (expected to FAIL) ─────────")
    for raw in SYNTHETIC_FAIL:
        try:
            dt = parse_not_after(raw)
            print(f"  [FAIL] {raw!r:50s} → unexpectedly parsed as {dt.date()}")
            failed += 1
        except ValueError:
            print(f"  [PASS] {raw!r:50s} → correctly rejected (ValueError)")
            passed += 1
        except Exception as exc:
            print(f"  [FAIL] {raw!r:50s} → unexpected {type(exc).__name__}: {exc}")
            failed += 1

    return passed, failed


def fetch_raw_not_after(host: str, port: int = 443, timeout: float = 10.0) -> str | None:
    """One-shot probe to capture the raw notAfter string from a real cert."""
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.load_default_certs()
        with socket.create_connection((host, port), timeout=timeout) as s:
            with ctx.wrap_socket(s, server_hostname=host) as ss:
                cert = ss.getpeercert(binary_form=False)
                return cert.get("notAfter")
    except Exception as exc:
        return f"<probe error: {type(exc).__name__}: {exc}>"


# --- Real CA sampling (drives catalogue of observed shapes) ---------------

# Diverse hosts: cloud, CDNs, finance, gov, vendor sites, etc.
REAL_HOSTS = [
    "letsencrypt.org",
    "traefik.io",
    "github.com",
    "cloudflare.com",
    "www.google.com",
    "www.apple.com",
    "www.microsoft.com",
    "www.mozilla.org",
    "duckduckgo.com",
    "www.wikipedia.org",
    "stripe.com",
    "www.gov.uk",
    "kubernetes.io",
    "docker.com",
    "nginx.org",
    "haproxy.org",
    "www.akamai.com",
    "aws.amazon.com",
]


def test_real_hosts() -> tuple[int, int, list[str]]:
    """Fetch notAfter from many real hosts and confirm our parser handles each.

    Returns (parsed_ok, probe_failed, catalogue_of_observed_shapes).
    """
    catalogue: dict[str, int] = {}
    parsed_ok = 0
    probe_failed = 0
    print()
    print("── Real hosts (raw notAfter → parsed via parse_not_after) ──")
    for host in REAL_HOSTS:
        raw = fetch_raw_not_after(host)
        if raw is None or raw.startswith("<probe error"):
            print(f"  [SKIP] {host:30s} → {raw if raw else 'no notAfter'}")
            probe_failed += 1
            continue
        try:
            dt = parse_not_after(raw)
            days = (dt - datetime.now(UTC)).days
            print(f"  [OK]   {host:30s} raw={raw!r:32s} → {dt.date()} ({days}d)")
            # Catalogue the shape
            shape = raw.split()[-1] if raw.endswith("GMT") else raw
            # Reduce to date pattern: month-name, single/double space, day, time, year
            shape_key = " ".join(raw.split()[:5]) if raw else raw
            # Simpler: just bucket by ending token
            bucket = "GMT" if raw.endswith("GMT") else ("+TZ" if "+" in raw[-6:] else "OTHER")
            catalogue[bucket] = catalogue.get(bucket, 0) + 1
            parsed_ok += 1
        except Exception as exc:
            print(f"  [FAIL] {host:30s} raw={raw!r} → {type(exc).__name__}: {exc}")
            catalogue["UNPARSED"] = catalogue.get("UNPARSED", 0) + 1
            probe_failed += 1

    return parsed_ok, probe_failed, [f"{k}: {v}" for k, v in sorted(catalogue.items())]


def test_age_computation() -> None:
    """Verify days_until_expiry semantics with a synthetic near-future cert.

    Models what the cert coordinator will do every 6 hours.
    """
    print()
    print("── days_until_expiry semantics ──────────────────────────")
    future = datetime.now(UTC) + timedelta(days=30)
    # Format like real cert: 'Nov 15 12:00:00 2025 GMT'
    raw = future.strftime("%b  %d %H:%M:%S %Y GMT")
    parsed = parse_not_after(raw)
    days = (parsed - datetime.now(UTC)).days
    print(f"  raw={raw!r}")
    print(f"  parsed={parsed.isoformat()}")
    print(f"  days_until_expiry={days}  (expected ~30, ±1 for time-of-day rounding)")


def main() -> int:
    print("=" * 60)
    print("Spike 002 — notAfter format-string loop")
    print("=" * 60)
    print(f"NOTAFTER_FORMATS fallback catalog ({len(NOTAFTER_FORMATS)}):")
    for f in NOTAFTER_FORMATS:
        print(f"  {f!r}")

    p1, f1 = test_synthetic()
    p2, f2, catalogue = test_real_hosts()
    test_age_computation()

    print()
    print("=" * 60)
    print(f"Catalogue of observed shapes (real hosts): {catalogue}")
    print(f"Synthetic: passed={p1} failed={f1}")
    print(f"Real:      parsed_ok={p2} probe_failed={f2}")
    total_fail = f1 + f2
    if total_fail == 0:
        print("Verdict: VALIDATED ✓")
    else:
        print(f"Verdict: INVALIDATED ✗ ({total_fail} failures)")
    print("=" * 60)
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())