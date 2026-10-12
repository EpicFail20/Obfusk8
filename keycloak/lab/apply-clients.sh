#!/bin/sh
# Copyright (C) 2026 CARROLAGGI Xavier
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# LAB ONLY (D-046): (re)creates the OIDC clients of the browser extension
# (option 2 of D-010, D-057) in the lab Keycloak's `lab` realm, exactly as
# versioned in this directory. Never used against a real identity provider:
# there, the establishment's administrators register the client by hand
# (docs/extension-oidc.md, "Entra ID").
#
# Idempotent and exact: an existing client of the same clientId is deleted,
# then created again from the file (kcadm's update would keep mappers that
# the file no longer lists). Deleting a client ends its sessions: lab only.
# The client of oauth2-proxy (anonymiseur-app) is never touched.
#
# The admin password is read INSIDE the container from the Docker secret
# and never printed; kcadm's session file lives in the container's /tmp and
# is removed on exit.
#
# Usage (from the repository root, lab stack running, profile `lab`):
#   keycloak/lab/apply-clients.sh
set -eu

dir=$(cd "$(dirname "$0")" && pwd)
container=${KEYCLOAK_CONTAINER:-obfusk8-keycloak-1}
realm=lab

apply() {
    file=$1
    client_id=$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["clientId"])' "$file")
    case $client_id in
        anonymiseur-app) echo "apply-clients.sh: refusing to touch oauth2-proxy's client" >&2; exit 1 ;;
    esac
    docker exec -i "$container" sh -c '
        set -eu
        K=/opt/keycloak/bin/kcadm.sh
        C=$(mktemp /tmp/kcadm.XXXXXX)
        trap "rm -f $C" EXIT
        $K config credentials --config "$C" --server http://localhost:8080 --realm master --user admin \
            --password "$(cat /run/secrets/keycloak_admin_password)" >/dev/null 2>&1
        for id in $($K get clients -r "$1" --config "$C" -q clientId="$2" --fields id --format csv --noquotes); do
            $K delete "clients/$id" -r "$1" --config "$C"
            echo "  supprimé : $2"
        done
        $K create clients -r "$1" --config "$C" -f - >/dev/null
        echo "  créé : $2"
    ' sh "$realm" "$client_id" < "$file"
}

echo "Royaume $realm du Keycloak de laboratoire ($container) :"
apply "$dir/obfusk8-extension-client.json"
apply "$dir/test-other-application-client.json"
