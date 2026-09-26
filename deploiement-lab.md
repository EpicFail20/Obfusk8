# Anonymiseur - déploiement lab sur VM Docker (Proxmox)

Ce guide part du principe d'une installation **entièrement neuve**, sur une VM qui n'a jamais fait tourner ce projet. Suis les étapes dans l'ordre. En cas de blocage à n'importe quelle étape, consulte directement la table de [Dépannage](#dépannage) en fin de document — chaque erreur rencontrée en lab y est référencée avec sa cause exacte.

## 0. Prérequis VM Proxmox

Vérifie le type de CPU de la VM avant de démarrer quoi que ce soit :

1. VM → **Hardware** → **Processor** → **Edit**.
2. Type : **`host`** (ou un type explicite compatible v2, `x86-64-v2-AES`, si migration future prévue vers un autre hôte).
3. Redémarre la VM.

```bash
grep -o 'sse4_2' /proc/cpuinfo | head -1
```

Si `sse4_2` s'affiche, c'est bon.

## 1. Cloner le dépôt

```bash
ssh debian@<ip-de-la-vm>
git clone https://github.com/EpicFail20/obfusk8.git
cd obfusk8
```

## 2. Ajouter les domaines dans `/etc/hosts` (poste client, pas la VM)

```
<ip-de-la-vm>  anonymiseur.lab.local
<ip-de-la-vm>  keycloak.lab.local
```

Les deux entrées sont nécessaires. C'est le fichier hosts de **ta machine locale** (celle avec le navigateur), pas celui de la VM.

## 3. Générer les secrets

```bash
chmod +x generate-secrets.sh
./generate-secrets.sh
```

Lance ce script **avant** le tout premier `docker compose up`. Il te demande deux valeurs manuelles :

- **`Mot de passe admin Keycloak`** : choisis-le toi-même.
- **`OAUTH2_PROXY_CLIENT_SECRET`** : tape une valeur temporaire (ex. `placeholder-a-remplacer`) — remplacée à l'étape 5.7.

```bash
wc -c < ./secrets/oauth2_cookie_secret.txt   # doit afficher 32
```

## 4. Copier et remplir `.env`

```bash
cp env.fr.example .env
```

Édite `.env` — `APP_DOMAIN` et `OAUTH2_PROXY_CLIENT_ID` sont les deux valeurs à changer en priorité ; les autres ont des valeurs par défaut sûres.

## 5. Démarrer Keycloak seul et le configurer

```bash
docker compose up -d keycloak
docker compose logs -f keycloak
```

Attends `Keycloak ... started in ...s. Listening on: http://0.0.0.0:8080` (ou `docker compose ps` → `healthy`).

Ouvre `http://keycloak.lab.local:8080`, connexion avec `admin` / le mot de passe saisi à l'étape 3.

### 5.1 Créer le realm

1. Sélecteur de realm (en haut à gauche) → **Create realm**.
2. Realm name : `lab`.
3. **Create**.

### 5.2 Créer le groupe

1. **Groups** → **Create group**.
2. Name : `SG-Anonymiseur-Utilisateurs`.
3. **Create**.

### 5.3 Créer le client OIDC confidentiel

1. **Clients** → **Create client**.
2. *General settings* : Client ID : `anonymiseur-app`. **Next**.
3. *Capability config* : active **`Client authentication`**. *Standard flow* coché. **Next**.
4. *Login settings* : *Valid redirect URIs* : `https://anonymiseur.lab.local/oauth2/callback`. **Save**.
5. Onglet **Credentials** → copie la valeur de **Client secret**.

### 5.4 Ajouter le mapper "Group Membership"

1. Sur la page du client, onglet **Client scopes** → clique sur `anonymiseur-app-dedicated`.
2. **Add mapper** → **By configuration** → **Group Membership**.
3. *Name* : `groups`. *Token Claim Name* : tape `groups` explicitement. *Full group path* : **On**. *Add to ID token* et *Add to access token* : **On**.
4. **Save**.

### 5.5 Créer et assigner le Client Scope "groups"

1. Menu de gauche → **Client scopes** → **Create client scope**.
2. Name : `groups`. Protocol : `openid-connect`. **Save**.
3. Onglet **Mappers** → **Add mapper** → **By configuration** → **Group Membership**. Même configuration qu'à l'étape 5.4. **Save**.
4. **Clients** → `anonymiseur-app` → **Client scopes** → **Add client scope** → coche `groups` → **Default**.

### 5.6 Créer un ou deux utilisateurs de test

1. **Users** → **Add user**. Username : `testuser1`. **Create**.
2. Onglet **Details** → active **Email verified**.
3. Onglet **Credentials** → **Set password** → désactive *Temporary* → **Save**.
4. Onglet **Groups** → **Join Group** → coche `SG-Anonymiseur-Utilisateurs` → **Join**.
5. (Optionnel) Répète pour un second utilisateur, sans l'étape *Join Group*, pour tester le refus d'accès.

### 5.7 Enregistrer le vrai client secret

```bash
printf '%s' '<le-vrai-secret-copié-depuis-keycloak>' > ./secrets/oauth2_client_secret.txt
chmod 644 ./secrets/oauth2_client_secret.txt
docker compose up -d --force-recreate oauth2-proxy
```

## 6. Démarrer le reste du stack

```bash
docker compose up -d --build --scale presidio-analyzer=2 --scale presidio-anonymizer=1
docker compose ps
```

(2 réplicas suffisent pour un test fonctionnel ; monte à 6/2 pour le test de charge à 50 utilisateurs concurrents — 12-16 vCPU / 24-32 Go dans ce cas.)

```bash
docker compose ps
```

Tous les services doivent afficher `Up`/`Running`/`Healthy`, sans compteur de redémarrage qui augmente.

## 7. Vérifier et se connecter

```bash
docker compose logs -f app
docker compose logs -f oauth2-proxy
```

Ouvre `https://anonymiseur.lab.local` (bien `https`). Accepte l'avertissement de certificat (auto-signé en lab). Tu dois être redirigé vers Keycloak, te connecter avec un utilisateur de test, puis atterrir sur l'app.

## Changer de domaine après coup

Quatre endroits à mettre à jour si `APP_DOMAIN` change après une installation fonctionnelle :

1. **`.env`** : nouvelle valeur d'`APP_DOMAIN`.
2. Recréer les conteneurs concernés :
   ```bash
   docker compose up -d --force-recreate traefik app oauth2-proxy
   ```
3. **`/etc/hosts`** côté client : remplace l'ancienne entrée du domaine principal (`keycloak.lab.local` ne change pas).
4. **Keycloak** → **Clients** → `anonymiseur-app` → **Settings** → *Valid redirect URIs* : remplace l'ancien domaine par le nouveau.

Si le nom du dossier du projet change également, les labels `traefik.docker.network=obfusk8_app-internal` dans `docker-compose.yml` doivent eux aussi être mis à jour.

## Dépannage

| Symptôme | Cause | Correction |
|---|---|---|
| `Fatal glibc error: CPU does not support x86-64-v2` (logs Keycloak) | Type de CPU de la VM trop générique | Étape 0 — type `host` dans Proxmox |
| Page bloquée sans erreur claire en ouvrant la console Keycloak | `keycloak.lab.local` absent de `/etc/hosts` côté client ; Keycloak redirige systématiquement vers ce nom (`KC_HOSTNAME`) | Étape 2 — ajouter les deux entrées, pas une seule |
| Console Keycloak inaccessible sur le port 8081 | Le port réel mappé est 8080 | Utiliser `http://keycloak.lab.local:8080` |
| `PermissionError: ... '/data/audit/audit.log'` (logs app) | Dossier hôte créé par Docker en root avant que l'utilisateur applicatif (UID 1000) puisse y écrire | Corrigé automatiquement par le service `fix-app-dirs-perms` — si l'erreur persiste, vérifier `docker compose ps fix-app-dirs-perms` (doit afficher `Exited (0)`) |
| `./generate-secrets.sh: ... Permission denied` en écrivant dans `secrets/` ou `traefik/dynamic/` | Le script a été lancé après un premier `docker compose up` raté ; le dossier appartient à `root` | `sudo chown -R $(whoami):$(whoami) ./secrets ./traefik/dynamic` puis relancer avec `--force` |
| `cookie_secret must be 16, 24, or 32 bytes... but is 51 bytes` (oauth2-proxy, en boucle, **persiste même après avoir régénéré et vérifié le fichier à 32 octets**) | Une variable `OAUTH2_PROXY_COOKIE_SECRET` traîne dans `.env` et prend la priorité sur le Docker secret — ne jamais ajouter `OAUTH2_PROXY_CLIENT_SECRET`/`OAUTH2_PROXY_COOKIE_SECRET` à `.env`, même temporairement ; 51 est la longueur du texte placeholder, pas celle d'un vrai secret | Retirer toute ligne `OAUTH2_PROXY_*_SECRET` de `.env` ; `docker compose up -d --force-recreate oauth2-proxy` |
| `unauthorized_client` puis `invalid_client_credentials` (logs Keycloak), **malgré un `secrets/oauth2_client_secret.txt` vérifié correct** | Même bug que ci-dessus, sur `OAUTH2_PROXY_CLIENT_SECRET` cette fois | Vérifier `grep -i client_secret .env` (doit être vide) et `docker compose config \| grep CLIENT_SECRET` (une seule ligne `_FILE` doit apparaître) |
| `could not read cookie secret file: /run/secrets/oauth2_cookie_secret` | Fichier de secret en `600`, illisible par l'UID non-root du conteneur `oauth2-proxy` (différent de celui de ton compte VM) | `chmod 644` sur les fichiers dans `./secrets/` (déjà fait par défaut si `generate-secrets.sh` a été régénéré après cette correction) |
| Une correction de secret ne semble avoir aucun effet malgré un fichier hôte vérifié correct | Le conteneur concerné n'a jamais été recréé — un simple redémarrage ne relit pas le fichier | Toujours utiliser `docker compose up -d --force-recreate <service>` après avoir modifié un fichier de secret |
| `Cannot start the provider *file.Provider: ... /etc/traefik/dynamic: permission denied` (logs Traefik), suivi de `middleware "gateway-secret@file" does not exist` sur **tous** les routers `app` | Dossier `traefik/dynamic` en `700`, illisible par l'UID non-root de Traefik — casse tout le file provider | `chmod 755 ./traefik/dynamic && chmod 644 ./traefik/dynamic/gateway-secret.yml`, puis `docker compose up -d --force-recreate traefik` |
| `404 page not found` (réponse Traefik brute) sur le domaine principal, alors que `docker compose config \| grep "app.rule"` confirme une règle correcte | Label `traefik.docker.network=...` pointant vers un nom de réseau obsolète (dossier de projet renommé, ou reliquat d'un ancien nom) — Traefik matche la règle mais ne trouve pas le conteneur cible sur le réseau indiqué | Vérifier `docker network ls`, corriger le label pour qu'il corresponde à `<nom-du-dossier-actuel>_app-internal`, puis `docker compose up -d --force-recreate traefik app oauth2-proxy` |
| Keycloak affiche `Invalid parameter: redirect_uri`, avec l'ancien domaine visible dans l'URL | *Valid redirect URIs* du client Keycloak pas mis à jour après un changement d'`APP_DOMAIN` | Voir [Changer de domaine après coup](#changer-de-domaine-après-coup), point 4 |
| `invalid_scope: Invalid scopes: openid email profile groups` (Keycloak, à l'écran de connexion) | `oauth2-proxy.cfg` réclame un scope `groups` (via `allowed_groups`) qui n'existe pas comme Client Scope Keycloak | Étape 5.5 — créer et assigner le Client Scope `groups` |
| `[AuthFailure] Invalid authentication via OAuth2: unauthorized` (logs oauth2-proxy), **alors que l'utilisateur est bien membre du groupe et que le mapper existe** | Champ *Token Claim Name* du mapper *Group Membership* resté vide — le texte grisé affiché ("groups") est un exemple de placeholder, pas une valeur réellement saisie ; le claim `groups` n'apparaît alors nulle part dans le token, sans erreur visible | Décoder le token pour vérifier (`curl` vers `/realms/lab/protocol/openid-connect/token` avec `grant_type=password`, puis décodage base64 du payload de l'`id_token`) ; si `groups` est absent, rouvrir chaque mapper *Group Membership* (scope dédié ET Client Scope `groups`) et retaper `groups` explicitement dans *Token Claim Name* |
| `500 Internal Server Error` / `email in id_token isn't verified` après une authentification Keycloak par ailleurs réussie | Case *Email verified* non cochée sur l'utilisateur | Étape 5.6 — cocher *Email verified* sur la fiche utilisateur |
| Page inaccessible sur `http://anonymiseur.lab.local` | Traefik ne sert qu'en HTTPS | Utiliser `https://` |
| `git clone`/`docker pull` demande un login alors que le repo/package est censé être public | Le changement de visibilité n'a pas été validé jusqu'au bout côté GitHub | Revérifier `Settings → Danger Zone` (repo) ou `Package settings` (package), refaire la confirmation jusqu'au bout |

## Bascule vers Entra ID plus tard

Dans `oauth2-proxy.cfg`, remplacer `provider = "oidc"` par `provider = "entra-id"` et pointer `oidc_issuer_url` vers `https://login.microsoftonline.com/<tenant-id>/v2.0`, avec le vrai `client_id`/`client_secret` Entra (ce dernier remplace le contenu de `secrets/oauth2_client_secret.txt`, même mécanique qu'à l'étape 5.7). `redirect_url` n'a rien à faire ici (déjà dérivé d'`APP_DOMAIN`) ; seul `allowed_groups` dans ce même fichier doit être adapté au nom du groupe réel côté Entra ID.
