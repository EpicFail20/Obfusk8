# Anonymiseur - déploiement lab sur VM Docker (Proxmox)

> **Laboratoire uniquement.** Ce guide installe un Keycloak de **laboratoire** : mode développement (`start-dev`), HTTP sans TLS, base H2
> de développement, compte administrateur d'amorçage. Ce Keycloak ne doit **jamais** servir à un pilote ni en production (D-046) : un
> déploiement réel utilise le fournisseur d'identité de l'établissement, voir [Déploiement réel](#déploiement-réel-fournisseur-didentité-de-létablissement).
> Keycloak ne démarre qu'avec le profil Compose `lab`.

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

`BIND_ADDRESS` est la seule adresse de l'hôte sur laquelle Traefik (80, 443) et Keycloak (8080) sont publiés. Par défaut `127.0.0.1` : l'application n'est alors joignable que depuis la VM. Pour y accéder depuis d'autres postes, mets l'adresse de la VM sur le réseau local (par exemple `BIND_ADDRESS=192.168.1.35`). Ne la remplace jamais par `0.0.0.0` : cela publierait aussi les ports sur toutes les interfaces, adresse IPv6 publique comprise (EXT-56).

Ajoute `COMPOSE_PROFILES=lab` à `.env` : le Keycloak de laboratoire démarre alors avec la pile, et les commandes `docker compose` habituelles
fonctionnent sans option. Sans cette ligne, ajoute `--profile lab` à chaque commande `docker compose` (sinon Keycloak ne démarre pas,
et oauth2-proxy redémarre en boucle faute de fournisseur d'identité). Les fichiers d'exemple ne contiennent pas cette ligne : un déploiement
réel n'en veut pas.

## 5. Démarrer Keycloak seul et le configurer

```bash
docker compose --profile lab up -d keycloak traefik
docker compose logs -f keycloak
```

Keycloak n'a pas de port publié ni d'accès à Internet (réseau interne `app-internal`) : Traefik relaie vers lui le port 8080 de
`BIND_ADDRESS`, d'où le démarrage de `traefik` avec lui.

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
3. *Capability config* : active **`Client authentication`**. *Standard flow* coché. *PKCE method* : **`S256`** (exigé depuis la phase 2 bis : oauth2-proxy envoie `code_challenge_method = "S256"`, voir `oauth2-proxy/oauth2-proxy.cfg`). **Next**.
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
docker compose --profile lab up -d --build --scale presidio-analyzer=2
docker compose ps
```

(2 réplicas suffisent pour un test fonctionnel ; monte à 6 pour le test de charge à 50 utilisateurs concurrents — 12-16 vCPU / 24-32 Go dans ce cas.)

```bash
docker compose ps
```

Tous les services doivent afficher `Up`/`Running`/`Healthy`, sans compteur de redémarrage qui augmente.

## 6 bis. Certificat du laboratoire (autorité restreinte à `.lab.local`)

Sans cette étape, Traefik sert son certificat par défaut : régénéré à chaque démarrage et sans le nom du service, il impose
d'accepter un avertissement à chaque redémarrage, et **l'extension de navigateur ne peut pas joindre le serveur** (une requête
d'extension échoue sur un certificat non reconnu ; constaté le 2026-10-07).

1. Sur la VM, une seule fois (puis tous les 397 jours pour le certificat serveur) :
   ```bash
   sudo chown "$USER" traefik/certs     # une seule fois, si le dossier appartient à root
   traefik/generate-lab-cert.sh
   ```
   - L'autorité (`~/.obfusk8-lab-ca/ca.crt`) porte une contrainte de noms critique : elle ne peut garantir **que** des noms en
     `.lab.local`. Un certificat qu'elle signerait pour un autre domaine est refusé par le navigateur (vérifié dans Chromium :
     `ERR_CERT_INVALID` pour `evil.example.com` et `lab.local.example.com`).
   - Sa **clé privée** (`~/.obfusk8-lab-ca/ca.key`) ne quitte jamais la VM et n'entre jamais dans le dépôt.
   - Traefik prend le nouveau certificat sans redémarrage (`traefik/dynamic/lab-tls.yml`).
2. Sur le poste, copier **seulement** `ca.crt` et vérifier son empreinte avec celle affichée sur la VM :
   ```bash
   scp debian@<VM>:.obfusk8-lab-ca/ca.crt obfusk8-lab-ca.crt
   openssl x509 -in obfusk8-lab-ca.crt -noout -subject -fingerprint -sha256   # sur le poste ET sur la VM
   ```
3. L'importer comme autorité racine **de l'utilisateur** (Chrome et Edge utilisent le magasin du système) :
   - **Windows** (PowerShell, sans droits d'administrateur) :
     `Import-Certificate -FilePath .\obfusk8-lab-ca.crt -CertStoreLocation Cert:\CurrentUser\Root` (confirmer la fenêtre) ;
   - **macOS** : `security add-trusted-cert -r trustRoot -k ~/Library/Keychains/login.keychain-db obfusk8-lab-ca.crt` ;
   - **Linux** (magasin NSS de Chrome et d'Edge ; paquet `libnss3-tools`) :
     `certutil -d sql:$HOME/.pki/nssdb -A -t "C,," -n "obfusk8 lab CA" -i obfusk8-lab-ca.crt`.
   Redémarrer le navigateur, puis ouvrir `https://<APP_DOMAIN>` : plus d'avertissement.
4. **Fin du laboratoire** : retirer l'autorité du poste, puis détruire `~/.obfusk8-lab-ca` avec la VM (D-049) :
   - **Windows** : `Get-ChildItem Cert:\CurrentUser\Root | Where-Object Subject -like '*obfusk8 lab CA*' | Remove-Item` ;
   - **macOS** : `security delete-certificate -c "obfusk8 lab CA (lab.local only)" ~/Library/Keychains/login.keychain-db` ;
   - **Linux** : `certutil -d sql:$HOME/.pki/nssdb -D -n "obfusk8 lab CA"`.

Pilote et production : certificat de l'établissement, jamais cette autorité.

## 7. Vérifier et se connecter

```bash
docker compose logs -f app
docker compose logs -f oauth2-proxy
```

Ouvre `https://anonymiseur.lab.local` (bien `https`). Avec l'autorité du laboratoire importée (§6 bis), aucun avertissement de certificat ; sinon, accepte-le. Tu dois être redirigé vers Keycloak, te connecter avec un utilisateur de test, puis atterrir sur l'app.

## 8. (Facultatif) Activer l'API texte pour l'extension de navigateur

Désactivée par défaut. Elle permet à l'extension de navigateur (dépôt séparé `obfusk8-extension`, panneau latéral) d'analyser un
prompt avant son envoi à un service d'IA. Contrat complet : [`docs/api-extension.md`](./docs/api-extension.md).

1. Dans `.env` : `ENABLE_EXTENSION_API=true`, et l'origine de l'extension dans `EXTENSION_ALLOWED_ORIGINS` (phase 3, D-054).
   Extension du laboratoire (identifiant stable, clé publique dans son `config/lab.json`) :
   `EXTENSION_ALLOWED_ORIGINS=chrome-extension://glaimpfdmfkidcgalcblojmkomplcgpa`. Vide : toute analyse est refusée (403).
   Les plafonds `MAX_TEXT_*` ont des valeurs par défaut mesurées (voir `env.fr.example`).
2. Recréer le conteneur de l'application :
   ```bash
   docker compose up -d app
   docker compose logs app | grep "API texte activée"
   ```
3. Vérifier, une fois connecté dans le navigateur : `https://anonymiseur.lab.local/api/v1/version` doit renvoyer un JSON
   (`api_version`, thèmes disponibles). Drapeau désactivé : 404.

À savoir :
- Les routes `/api/v1/` ont leur propre routeur Traefik (`app-text`) : limitation de débit **par utilisateur** (60 requêtes par minute,
  rafale de 20) et plafond de corps de 244 096 octets. **Si vous changez `MAX_TEXT_CHARS`**, recalculez le label
  `text-bodylimit` dans `docker-compose.yml` : `12 × MAX_TEXT_CHARS + 4096`.
- Les requêtes de prompts sont journalisées dans un journal d'audit séparé, `/var/log/anonymiseur-audit/audit-extension.log`
  (métadonnées seulement : utilisateur, types et nombres d'entités, longueur, durée ; jamais le texte).
- `POST /api/v1/text/*` exige l'`Origin` d'une extension autorisée (`chrome-extension://<id>`, envoyée par Chrome depuis le panneau
  latéral) ; `GET /api/v1/version` accepte une requête sans `Origin`. Toute autre origine : 403, issue d'audit `origin_refused`.
  L'extension ne joint qu'un serveur dont le navigateur reconnaît le certificat : voir §6 bis.
- Une requête non authentifiée sur `/api/v1/` reçoit un **401** en texte brut (`Unauthorized`), sans redirection : le routeur `app-text`
  n'utilise pas `oauth2-errors` (D-010 option 1, D-020). L'interface web garde la redirection vers la page de connexion.
  `oidc-auth` et le secret de passerelle restent exigés.
- L'analyseur Presidio n'a qu'un worker, partagé avec le flux documents : un traitement de document en cours retarde les prompts, et
  inversement dans une moindre mesure (mesures dans `benchmarks/results/`).

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
| `invalid_scope: Invalid scopes: openid email profile groups` (Keycloak, à l'écran de connexion) | `oauth2-proxy/oauth2-proxy.cfg` réclame un scope `groups` (via `allowed_groups`) qui n'existe pas comme Client Scope Keycloak | Étape 5.5 — créer et assigner le Client Scope `groups` |
| Keycloak répond 403 dès `/oauth2/start`, sans formulaire de connexion (journal Keycloak : paramètre `code_challenge_method` manquant) | Le client Keycloak exige PKCE S256 mais oauth2-proxy n'en envoie pas (ligne `code_challenge_method` absente de `oauth2-proxy/oauth2-proxy.cfg`) | Rétablir `code_challenge_method = "S256"`, puis `docker compose up -d --force-recreate oauth2-proxy` |
| `failed to create network obfusk8_app-internal ... networks have overlapping IPv4` au démarrage | Le réseau `app-internal` a un sous-réseau fixe (`10.89.18.0/24`, phase 2 bis) pour que Traefik ait l'adresse `10.89.18.10`, seule source de confiance d'oauth2-proxy (`trusted_proxy_ips`). Un réseau existant de l'hôte utilise déjà cette plage | Choisir une plage libre hors des pools par défaut de Docker (172.17.0.0/12, 192.168.0.0/16) et la reporter aux **trois** endroits : `networks.app-internal.ipam` et `ipv4_address` de Traefik dans `docker-compose.yml`, `trusted_proxy_ips` dans `oauth2-proxy/oauth2-proxy.cfg` |
| `[AuthFailure] Invalid authentication via OAuth2: unauthorized` (logs oauth2-proxy), **alors que l'utilisateur est bien membre du groupe et que le mapper existe** | Champ *Token Claim Name* du mapper *Group Membership* resté vide — le texte grisé affiché ("groups") est un exemple de placeholder, pas une valeur réellement saisie ; le claim `groups` n'apparaît alors nulle part dans le token, sans erreur visible | Décoder le token pour vérifier (`curl` vers `/realms/lab/protocol/openid-connect/token` avec `grant_type=password`, puis décodage base64 du payload de l'`id_token`) ; si `groups` est absent, rouvrir chaque mapper *Group Membership* (scope dédié ET Client Scope `groups`) et retaper `groups` explicitement dans *Token Claim Name* |
| `500 Internal Server Error` / `email in id_token isn't verified` après une authentification Keycloak par ailleurs réussie | Case *Email verified* non cochée sur l'utilisateur | Étape 5.6 — cocher *Email verified* sur la fiche utilisateur |
| `Bind for 192.168.1.35:8080 failed: port is already allocated` en recréant la pile après la mise à jour du profil `lab` | L'ancien conteneur Keycloak publiait lui-même le port 8080, désormais relayé par Traefik | `docker compose stop keycloak`, puis `docker compose --profile lab up -d` |
| oauth2-proxy redémarre en boucle, `Performing OIDC Discovery...` puis erreur de connexion | Pile démarrée sans le profil `lab` et sans fournisseur d'identité réel | Laboratoire : `COMPOSE_PROFILES=lab` dans `.env` (ou `--profile lab`). Déploiement réel : voir [Déploiement réel](#déploiement-réel-fournisseur-didentité-de-létablissement) |
| Page inaccessible sur `http://anonymiseur.lab.local` | Traefik ne sert qu'en HTTPS | Utiliser `https://` |
| `git clone`/`docker pull` demande un login alors que le repo/package est censé être public | Le changement de visibilité n'a pas été validé jusqu'au bout côté GitHub | Revérifier `Settings → Danger Zone` (repo) ou `Package settings` (package), refaire la confirmation jusqu'au bout |

## Déploiement réel (fournisseur d'identité de l'établissement)

Un pilote ou une production **n'utilise pas** le Keycloak de laboratoire (D-046) et ne réutilise **aucun** secret du laboratoire : nouvelle
installation, secrets neufs (`./generate-secrets.sh` sur la nouvelle machine, D-049).

1. **Pas de profil `lab`** : ne mets pas `COMPOSE_PROFILES=lab` dans `.env` et n'utilise pas `--profile lab`. Keycloak ne démarre pas ; le
   port 8080 de Traefik répond alors 404.
2. **oauth2-proxy** (`oauth2-proxy/oauth2-proxy.cfg`) : `provider` et `oidc_issuer_url` du fournisseur de l'établissement, `client_id` dans
   `.env` (`OAUTH2_PROXY_CLIENT_ID`), secret du client dans `secrets/oauth2_client_secret.txt`, `allowed_groups` au nom du groupe réel. Garde
   `code_challenge_method = "S256"` : le client doit exiger PKCE S256 côté fournisseur (EXT-49). Les comptes doivent porter une adresse de
   courriel vérifiée (EXT-43).
3. **Accès sortant dédié et limité à ce fournisseur** : oauth2-proxy est sur le réseau interne `app-internal`, sans accès à Internet. Il
   doit joindre le fournisseur (découverte OIDC, jetons) **et lui seul** : réseau sortant dédié à oauth2-proxy, filtré vers les adresses du
   fournisseur. Ce branchement est conçu et testé pendant la phase du pilote ; ne rattache pas `app-internal` ni `app` à un réseau ouvert.
4. Tant que le fournisseur n'est pas configuré, oauth2-proxy redémarre en boucle (échec de la découverte OIDC) : c'est attendu.

### Exemple : Entra ID

Dans `oauth2-proxy/oauth2-proxy.cfg`, remplacer `provider = "oidc"` par `provider = "entra-id"` et pointer `oidc_issuer_url` vers `https://login.microsoftonline.com/<tenant-id>/v2.0`, avec le vrai `client_id`/`client_secret` Entra (ce dernier remplace le contenu de `secrets/oauth2_client_secret.txt`, même mécanique qu'à l'étape 5.7). `redirect_url` n'a rien à faire ici (déjà dérivé d'`APP_DOMAIN`) ; seul `allowed_groups` dans ce même fichier doit être adapté au nom du groupe réel côté Entra ID.
