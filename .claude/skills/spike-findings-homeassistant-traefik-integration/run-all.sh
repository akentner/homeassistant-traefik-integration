#!/usr/bin/env bash
# Run all 4 spikes end-to-end. Exits non-zero if any spike fails.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Running all 4 spikes for 3-tls-handshake                   ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo

for spike in 001-stdlib-tls-handshake 002-notafter-format-strings 003-sni-routing-multicert 004-error-handling-async-wrap; do
    echo "┌── $spike ──"
    if [[ "$spike" == "003-sni-routing-multicert" ]]; then
        # Ensure certs are generated.
        if [[ ! -f "$DIR/$spike/certs/router-a.pem" ]]; then
            echo "│  (Generating self-signed certs...)"
            (cd "$DIR/$spike/certs" && bash generate.sh > /dev/null)
        fi
        # Spike 003 uses server.py (server + probe in one script).
        python3 "$DIR/$spike/server.py"
    else
        python3 "$DIR/$spike/probe.py"
    fi
    echo "└──"
    echo
done

echo "All spikes run. See MANIFEST.md for verdict summary."