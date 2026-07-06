#!/usr/bin/env bash
# Generate self-signed certs for SNI-routing TLS server (spike 003).
# Creates 3 distinct leaf certs + a wildcard cert to simulate a Traefik
# deployment with multiple SNI-routed routes + a wildcard default.

set -euo pipefail

cd "$(dirname "$0")"
CERTS_DIR="$(pwd)"
mkdir -p "$CERTS_DIR"

DAYS=825  # ~2.25 years (browser-trusted range)

gen_leaf() {
    local cn="$1"
    local san="$2"
    local out="$3"
    openssl req -x509 -newkey rsa:2048 -nodes -days "$DAYS" \
        -keyout "$CERTS_DIR/${out}.key" -out "$CERTS_DIR/${out}.crt" \
        -subj "/CN=${cn}" \
        -addext "subjectAltName=${san}" \
        2>/dev/null
    # Bundle PEM (cert + key) for ssl.SSLContext.load_cert_chain
    cat "$CERTS_DIR/${out}.crt" "$CERTS_DIR/${out}.key" > "$CERTS_DIR/${out}.pem"
}

echo "Generating self-signed certs in $CERTS_DIR"

# Distinct cert per SNI host (mimics Traefik's Host() routing).
gen_leaf "router-a.example.test" "DNS:router-a.example.test" "router-a"
gen_leaf "router-b.example.test" "DNS:router-b.example.test" "router-b"
gen_leaf "router-c.example.test" "DNS:router-c.example.test,DNS:router-c-alt.example.test" "router-c"

# Wildcard cert (mimics *.example.test default cert on Traefik).
gen_leaf "*.example.test" "DNS:*.example.test,DNS:example.test" "wildcard"

# Expired cert — for negative testing.
openssl req -x509 -newkey rsa:2048 -nodes -days -10 \
    -keyout "$CERTS_DIR/expired.key" -out "$CERTS_DIR/expired.crt" \
    -subj "/CN=expired.example.test" \
    -addext "subjectAltName=DNS:expired.example.test" \
    2>/dev/null || true
# Note: openssl may reject negative -days; we'll generate a "past" cert
# differently if so.

echo "Done. Generated:"
ls -1 "$CERTS_DIR"