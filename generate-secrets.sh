#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Génère le dossier ./secrets/ utilisé par docker-compose (top-level "secrets:").
# À exécuter UNE FOIS depuis la racine du projet (là où se trouve docker-compose.yml).
#
# Pourquoi un script plutôt que des commandes tapées à la main :
# - évite que les valeurs générées se retrouvent dans l'historique shell (~/.bash_history)
# - garantit des permissions restrictives dès la création (pas de fenêtre où le
#   fichier est lisible par tout le monde avant un chmod correctif)
# -----------------------------------------------------------------------------
set -euo pipefail

SECRETS_DIR="./secrets"
mkdir -p "$SECRETS_DIR"
chmod 700 "$SECRETS_DIR"

generate_random_secret() {
    local target_file="$1"
    if [ -f "$target_file" ]; then
        echo "  -> $target_file existe déjà, on ne l'écrase pas (utilise --force pour régénérer)."
        return
    fi
    # 32 octets aléatoires bruts -> exactement la taille attendue par oauth2-proxy
    # (16, 24 ou 32 octets after décodage) pour cookie_secret.
    umask 077
    openssl rand -base64 32 | tr -d '\n' > "$target_file"
    chmod 600 "$target_file"
    echo "  -> $target_file généré (600)."
}

prompt_secret() {
    local label="$1"
    local target_file="$2"
    if [ -f "$target_file" ]; then
        echo "  -> $target_file existe déjà, on ne l'écrase pas (utilise --force pour ressaisir)."
        return
    fi
    umask 077
    read -rsp "  Colle la valeur pour [$label] (rien ne s'affiche à l'écran) : " value
    echo
    printf '%s' "$value" > "$target_file"
    chmod 600 "$target_file"
    echo "  -> $target_file écrit (600)."
}

echo "== Secrets générés automatiquement =="
generate_random_secret "$SECRETS_DIR/oauth2_cookie_secret.txt"

echo
echo "== Secrets à saisir manuellement (valeurs existantes, ex: client secret Keycloak/Entra) =="
echo "⚠️  Si ces valeurs ont déjà circulé en clair ailleurs (chat, .env commité, capture d'écran),"
echo "    considère-les comme compromises : régénère un NOUVEAU secret côté Keycloak/Entra"
echo "    avant de le coller ici, plutôt que de recopier l'ancien."
prompt_secret "OAUTH2_PROXY_CLIENT_SECRET (client OIDC)" "$SECRETS_DIR/oauth2_client_secret.txt"
prompt_secret "Mot de passe admin Keycloak (lab uniquement)" "$SECRETS_DIR/keycloak_admin_password.txt"

echo
echo "Terminé. Contenu de $SECRETS_DIR :"
ls -la "$SECRETS_DIR"
echo
echo "Rappel : ajoute '$SECRETS_DIR/' à .gitignore si ce n'est pas déjà fait (voir gitignore-additions.txt)."
