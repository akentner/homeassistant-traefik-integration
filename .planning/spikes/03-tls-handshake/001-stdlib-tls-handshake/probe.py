"""Spike 001 — Foundation: stdlib TLS handshake works against real hosts.

Validates that `ssl.SSLContext(PROTOCOL_TLS_CLIENT)` +
`check_hostname=False` + `load_default_certs()` returns a populated
cert dict with parseable `notAfter` for diverse real-world TLS hosts.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "shared"))
from tls import fetch_cert_info

HOSTS = [
    "letsencrypt.org",
    "traefik.io",
    "github.com",
    "cloudflare.com",
    "www.google.com",
    "www.apple.com",
    "www.microsoft.com",
    "duckduckgo.com",
    "kubernetes.io",
    "docker.com",
]


def main() -> int:
    print("=" * 60)
    print("Spike 001 — stdlib TLS handshake foundation")
    print("=" * 60)
    passed = 0
    failed = 0
    for host in HOSTS:
        result = fetch_cert_info(host, timeout=10.0)
        if isinstance(result, dict) and "error" in result:
            print(f"  [FAIL] {host:30s} → {result['error']}: {result['detail']}")
            failed += 1
        else:
            cn = result.subject.split(",")[0] if result.subject else "?"
            san_count = len(result.san)
            print(
                f"  [OK]   {host:30s} → expires={result.not_after.date()} "
                f"days={result.days_until_expiry:>4}  cn={cn}  san={san_count} entries"
            )
            passed += 1
    print()
    print(f"Verdict: {passed}/{passed + failed} hosts probed successfully")
    if failed == 0:
        print("VALIDATED ✓")
        return 0
    print("INVALIDATED ✗")
    return 1


if __name__ == "__main__":
    sys.exit(main())