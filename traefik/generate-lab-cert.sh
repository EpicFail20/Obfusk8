#!/usr/bin/env bash
# LAB ONLY (D-054 point 2): a certificate authority restricted to *.lab.local
# and a server certificate for APP_DOMAIN, served by Traefik instead of its
# default certificate. Traefik's default certificate (CN=TRAEFIK DEFAULT
# CERT) is regenerated at every start and does not name the service: no
# workstation can trust it, and the browser extension can only reach a
# server whose certificate the browser trusts (observed on 2026-10-07).
#
#   traefik/generate-lab-cert.sh            # CA (once) + server certificate
#   LAB_CA_DIR=/path traefik/generate-lab-cert.sh
#
# - The CA carries a critical nameConstraints extension (permitted DNS
#   .lab.local): a certificate it signs for any other name is refused by a
#   browser that trusts it (checked in Chromium on 2026-10-07). Importing it
#   on a workstation therefore cannot let it vouch for a real site.
# - The CA private key stays OUTSIDE the repository (LAB_CA_DIR, default
#   ~/.obfusk8-lab-ca, mode 700). Never copy it to a workstation: only
#   ca.crt is imported there (deploiement-lab.md / deployment-lab.md).
# - The server key is written to traefik/certs/ (ignored by git). Mode 644,
#   like traefik/dynamic/gateway-secret.yml: Traefik runs with cap_drop ALL
#   (no CAP_DAC_OVERRIDE) and must read it; the parent traefik/ directory
#   stays 700, so no other host account can.
# - Pilot and production use the establishment's certificates, never this CA.
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
domain="${APP_DOMAIN:-$(sed -n 's/^APP_DOMAIN=//p' "$repo/.env" 2>/dev/null | tail -n 1)}"
ca_dir="${LAB_CA_DIR:-$HOME/.obfusk8-lab-ca}"
certs="$repo/traefik/certs"
# 397 days: below the 398-day limit browsers apply to server certificates.
leaf_days=397
ca_days=730

case "$domain" in
  *.lab.local) ;;
  *) echo "APP_DOMAIN must end in .lab.local (got '${domain}'): this CA is for the lab only" >&2; exit 1 ;;
esac
[ -w "$certs" ] || { echo "$certs is not writable (fix once: sudo chown \"\$USER\" $certs)" >&2; exit 1; }
[ "$(stat -c %a "$repo/traefik")" = 700 ] || { echo "$repo/traefik must be mode 700 (it protects the server key)" >&2; exit 1; }

umask 077
mkdir -p "$ca_dir"
chmod 700 "$ca_dir"
work="$(mktemp -d "$ca_dir/work.XXXXXX")"
trap 'rm -rf "$work"' EXIT

if [ ! -f "$ca_dir/ca.key" ]; then
  openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$ca_dir/ca.key"
  cat > "$work/ca.cnf" <<'EOF'
[req]
distinguished_name = dn
prompt = no
x509_extensions = ca
[dn]
CN = obfusk8 lab CA (lab.local only)
[ca]
basicConstraints = critical, CA:true, pathlen:0
keyUsage = critical, keyCertSign, cRLSign
nameConstraints = critical, permitted;DNS:.lab.local, permitted;DNS:lab.local
subjectKeyIdentifier = hash
EOF
  openssl req -new -x509 -key "$ca_dir/ca.key" -days "$ca_days" -sha256 -config "$work/ca.cnf" -out "$ca_dir/ca.crt"
  echo "lab CA created: $ca_dir/ca.crt (private key $ca_dir/ca.key, never leaves this directory)"
fi

openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$work/server.key"
cat > "$work/server.cnf" <<EOF
[req]
distinguished_name = dn
prompt = no
[dn]
CN = ${domain}
[server]
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature
extendedKeyUsage = serverAuth
subjectAltName = DNS:${domain}
authorityKeyIdentifier = keyid
subjectKeyIdentifier = hash
EOF
openssl req -new -key "$work/server.key" -config "$work/server.cnf" -out "$work/server.csr"
openssl x509 -req -in "$work/server.csr" -CA "$ca_dir/ca.crt" -CAkey "$ca_dir/ca.key" -CAcreateserial \
  -days "$leaf_days" -sha256 -extfile "$work/server.cnf" -extensions server -out "$work/server.crt"
openssl verify -CAfile "$ca_dir/ca.crt" "$work/server.crt" >/dev/null

install -m 644 "$work/server.key" "$certs/lab-server.key"
install -m 644 "$work/server.crt" "$certs/lab-server.pem"
echo "server certificate for ${domain}: $certs/lab-server.pem (valid ${leaf_days} days)"
openssl x509 -in "$certs/lab-server.pem" -noout -subject -enddate -fingerprint -sha256
