"""Spike 006 — Hostname mismatch detection (SAN mismatch).

Validates that `fetch_cert_info` can detect when the probe hostname is
NOT covered by the cert's SAN entries. Useful for surfacing a diagnostic
state when Traefik returns a default cert or wildcard cert for a hostname
that doesn't strictly match the cert's CN/SAN.

Test cases:
  - Exact match (hostname in SAN)        → san_mismatch = False
  - Wildcard match (hostname under *.x)   → san_mismatch = False
  - Subdomain match (e.g. *.traefik.io matches traefik.io via wildcard) → False
  - No match at all (hostname absent)     → san_mismatch = True
"""

from __future__ import annotations

import asyncio
import socket
import ssl
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# Use the shared prototype
sys.path.insert(0, str(Path(__file__).parent.parent / "shared"))
import tls as proto  # noqa: E402


@dataclass(frozen=True)
class CertInfoMismatch:
    """Extended CertInfo with mismatch flag."""
    host: str
    port: int
    not_after: datetime
    days_until_expiry: int
    subject: str
    issuer: str
    san: tuple[str, ...] = field(default_factory=tuple)
    san_mismatch: bool = False  # NEW
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def _hostname_matches_san(host: str, san: tuple[str, ...]) -> bool:
    """Return True if `host` is covered by any SAN entry (exact or wildcard)."""
    host_lower = host.lower().rstrip(".")
    for entry in san:
        entry_lower = entry.lower().rstrip(".")
        if entry_lower == host_lower:
            return True
        if entry_lower.startswith("*."):
            # Wildcard: *.example.com matches foo.example.com but not example.com itself
            suffix = entry_lower[1:]  # ".example.com"
            if host_lower.endswith(suffix) and host_lower.count(".") >= entry_lower.count("."):
                return True
    return False


async def fetch_cert_info_with_mismatch(host: str, port: int = 443, timeout: float = 5.0):
    """Same as proto.fetch_cert_info but adds san_mismatch field."""
    # Run the blocking handshake in a thread (per spike 004 pattern).
    result = await asyncio.to_thread(proto.fetch_cert_info, host, port, timeout=timeout)
    if proto.is_error(result):
        return result
    info = result  # type: ignore[assignment]
    san = info.san
    mismatch = not _hostname_matches_san(host, san)
    return CertInfoMismatch(
        host=info.host,
        port=info.port,
        not_after=info.not_after,
        days_until_expiry=info.days_until_expiry,
        subject=info.subject,
        issuer=info.issuer,
        san=san,
        san_mismatch=mismatch,
    )


# Hosts chosen for variety:
#   - traefik.io (exact CN match via SAN)
#   - www.traefik.io (wildcard match via *.traefik.io)
#   - www.google.com (exact match)
#   - traefik.com (no SAN cover; different domain)
#   - nonexistent.example.test (probe fails before SAN check)
TEST_CASES = [
    ("traefik.io", False, "exact CN match"),
    ("www.traefik.io", False, "wildcard *.traefik.io match"),
    ("www.google.com", False, "exact match"),
    ("nonexistent.example.test", None, "DNS resolution fails (error)"),
]


async def main() -> int:
    print("=" * 70)
    print("Spike 006 — Hostname mismatch detection (san_mismatch)")
    print("=" * 70)

    passed = 0
    failed = 0

    for host, expected_mismatch, note in TEST_CASES:
        result = await fetch_cert_info_with_mismatch(host, timeout=10.0)
        if proto.is_error(result):
            if expected_mismatch is None:
                print(f"  [PASS] {host:35s} → error (expected): {result['error']}  [{note}]")
                passed += 1
            else:
                print(f"  [FAIL] {host:35s} → unexpected error: {result['error']}  [{note}]")
                failed += 1
            continue

        info = result  # type: ignore[assignment]
        actual_mismatch = info.san_mismatch
        san_count = len(info.san)
        san0 = list(info.san[:2]) if info.san else []
        if expected_mismatch is None:
            # We expected an error but got a cert.
            print(f"  [FAIL] {host:35s} → got cert (expected DNS error); san_mismatch={actual_mismatch}  [{note}]")
            failed += 1
        elif actual_mismatch == expected_mismatch:
            print(
                f"  [PASS] {host:35s} → san_mismatch={actual_mismatch}  "
                f"(days={info.days_until_expiry:>4}, san={san_count} entries, first 2: {san0})  [{note}]"
            )
            passed += 1
        else:
            print(
                f"  [FAIL] {host:35s} → san_mismatch={actual_mismatch}  "
                f"(expected {expected_mismatch})  san={san0}  [{note}]"
            )
            failed += 1

    # Also test the wildcard detector directly on synthetic SAN entries.
    print()
    print("── Unit-test the wildcard detector ─────────────────")
    wildcard_cases = [
        # (host, san, expected_match)
        ("api.example.com", ("*.example.com",), True),
        ("example.com", ("*.example.com",), False),  # wildcard doesn't cover bare
        ("foo.api.example.com", ("*.example.com",), True),  # multi-level covered
        ("foo.api.example.com", ("*.api.example.com",), True),  # narrower wildcard
        ("example.com", ("example.com",), True),  # exact
        ("example.com", ("example.org",), False),  # no match
        ("api.example.com", ("api.example.com", "other.com"), True),  # one of many
    ]
    for host, san, expected in wildcard_cases:
        actual = _hostname_matches_san(host, san)
        marker = "PASS" if actual == expected else "FAIL"
        print(f"  [{marker}] host={host:25s} san={str(list(san)):35s} → match={actual} (expected {expected})")
        if actual == expected:
            passed += 1
        else:
            failed += 1

    print()
    print("=" * 70)
    total = passed + failed
    print(f"Verdict: {passed}/{total} {'VALIDATED ✓' if failed == 0 else 'FAILED ✗'}")
    print("=" * 70)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))