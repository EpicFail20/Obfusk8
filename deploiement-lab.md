# Anonymiseur - déploiement lab sur VM Docker (Proxmox)

Ce guide part du principe d'une installation **entièrement neuve**, sur une VM qui n'a jamais fait tourner ce projet. Chaque étape existe parce qu'un test réel "from scratch" a buté dessus — suis-les dans l'ordre, sans en sauter aucune, même celles qui semblent évidentes.

## 0. Prérequis VM Proxmox — à vérifier AVANT de démarrer quoi que ce soit

**Type de CPU de la VM.** Si la VM est configurée avec un type de processeur générique (`qemu64` par défaut sur beaucoup de setups Proxmox, ou hérité d'un template cloné), certains services (Keycloak notamment) plantent au démarrage avec une erreur cryptique :

```
Fatal glibc error: CPU does not support x86-64-v2
```

Ce n'est pas un bug de l'application — l'image Docker cible un jeu d'instructions processeur (x86-64-v2) que le type de CPU générique n'expose pas à l'intérieur de la VM, même si le CPU physique de l'hôte le supporte.

**Correction, dans l'interface Proxmox, avant de continuer :**

1. Éteins la VM (pas juste redémarrer les conteneurs).
2. VM → **Hardware** → **Processor** → **Edit**.
3. Type : **`host`** (le plus simple, hérite du CPU physique) — ou un type explicite compatible v2 (`x86-64-v2-AES`) si tu prévois de migrer la VM vers un hôte différent un jour.
4. Redémarre la VM.

Vérifie une fois redémarrée, avant même d'installer Docker :

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

⚠️ Le **nom du dossier compte** : Docker Compose nomme automatiquement ses réseaux `<nom-du-dossier>_<nom-du-réseau>`. Si tu clones ailleurs que dans un dossier nommé `obfusk8` (par exemple un renommage manuel, ou un ancien nom de projet comme `anonymiseur`), certains labels Traefik du fichier (`traefik.docker.network=obfusk8_app-internal`) ne correspondront plus au vrai nom de réseau généré, et l'application restera injoignable (404 générique) malgré une configuration par ailleurs correcte — vécu en lab, voir la table de dépannage. Si tu dois absolument utiliser un autre nom de dossier, adapte ces deux labels en conséquence.

## 2. Ajouter les domaines dans `/etc/hosts` (poste client, pas la VM)

**Deux entrées sont nécessaires**, pas une seule — c'est facile à manquer car seule la première est utilisée après la configuration initiale :

```
<ip-de-la-vm>  anonymiseur.lab.local
<ip-de-la-vm>  keycloak.lab.local
```

`keycloak.lab.local` est nécessaire dès l'étape 5 : Keycloak redirige systématiquement vers ce nom d'hôte (configuré en dur dans `docker-compose.yml` via `KC_HOSTNAME`), même quand on y accède via l'IP ou `localhost`. Sans cette entrée, ton navigateur suit la redirection vers un nom qu'il ne sait pas résoudre et la page reste bloquée, sans message d'erreur clair.

C'est le fichier hosts de **ta machine locale** (celle avec le navigateur) qui doit contenir ces deux lignes — pas celui de la VM.

## 3. Générer les secrets — AVANT tout `docker compose up`

```bash
chmod +x generate-secrets.sh
./generate-secrets.sh
```

Ce script crée le dossier `secrets/` avec les bonnes permissions et génère automatiquement deux secrets aléatoires (cookie secret, secret de passerelle interne pour Traefik). Il te demande aussi de saisir deux valeurs manuellement — voici comment répondre à chacune **lors d'une toute première installation**, où le client OIDC Keycloak n'existe pas encore :

- **`Mot de passe admin Keycloak`** : choisis et tape ici le mot de passe que tu veux donner au compte admin de Keycloak — c'est toi qui le définis, Keycloak n'existe pas encore donc rien à récupérer.
- **`OAUTH2_PROXY_CLIENT_SECRET`** : à ce stade, tu ne l'as pas encore (il sera généré par Keycloak à l'étape 5). Tape une valeur temporaire quelconque (ex. `placeholder-a-remplacer`) — elle sera remplacée manuellement plus tard, **sans avoir besoin de relancer tout le script**.

⚠️ **Important : lance ce script avant le tout premier `docker compose up`, jamais après un essai déjà raté.** Si Docker a déjà tenté de démarrer les services avant que ce script n'ait créé `secrets/` ou `traefik/dynamic/`, ces dossiers peuvent se retrouver appartenant à `root`, provoquant un `Permission denied` au moment de la génération. Si ça arrive :

```bash
sudo chown -R $(whoami):$(whoami) ./secrets ./traefik/dynamic
./generate-secrets.sh --force
```

Vérifie que le cookie secret fait bien 32 octets avant de continuer :

```bash
wc -c < ./secrets/oauth2_cookie_secret.txt   # doit afficher 32
```

## 4. Copier et remplir `.env`

```bash
cp env.fr.example .env
```

Édite `.env` — `APP_DOMAIN` et `OAUTH2_PROXY_CLIENT_ID` sont les deux valeurs à changer en priorité ; les autres (plafonds anti-abus `MAX_*`, `FILE_TTL_SECONDS`...) ont des valeurs par défaut sûres, à ajuster plus tard si besoin.

⚠️ **N'ajoute JAMAIS `OAUTH2_PROXY_CLIENT_SECRET` ni `OAUTH2_PROXY_COOKIE_SECRET` dans ce fichier**, même temporairement, même pour "tester vite fait". Ces deux valeurs passent exclusivement par les Docker secrets générés à l'étape 3 (`OAUTH2_PROXY_CLIENT_SECRET_FILE` / `OAUTH2_PROXY_COOKIE_SECRET_FILE`, déjà câblés dans `docker-compose.yml`). Si l'une de ces deux variables apparaît dans `.env`, `oauth2-proxy` lui donne la priorité sur le Docker secret correspondant et échoue au démarrage avec une erreur qui n'a l'air de venir de rien. **Ce piège a été rencontré deux fois séparément en lab** — une fois sur le cookie secret (`cookie_secret must be 16, 24, or 32 bytes... but is 51 bytes`), une fois sur le client secret (`unauthorized_client` / `invalid_client_credentials` côté Keycloak) — 51 étant très précisément la longueur du texte placeholder d'un ancien `.env.example`, pas celle d'un vrai secret. Si tu vois l'une de ces erreurs, **le premier réflexe est de vérifier `.env`, avant toute autre piste**.

⚠️ **Historique : `redirect_url` a longtemps été codé en dur dans `oauth2-proxy.cfg`, indépendant de `.env`** — c'était le cas au tout début de ce projet et ça a causé un vrai blocage lors d'un changement de domaine en lab. Ce n'est plus le cas : `redirect_url` est maintenant dérivé d'`APP_DOMAIN` via la variable d'environnement `OAUTH2_PROXY_REDIRECT_URL` dans `docker-compose.yml`, exactement comme le reste. `APP_DOMAIN` dans `.env` est donc bien la seule source de vérité à modifier — voir [Changer de domaine après coup](#changer-de-domaine-après-coup) pour la checklist complète (plus courte qu'avant).

## 5. Démarrer Keycloak seul et le configurer

```bash
docker compose up -d keycloak
docker compose logs -f keycloak
```

Attends que les logs affichent une ligne du type `Keycloak ... started in ...s. Listening on: http://0.0.0.0:8080` (ou vérifie `docker compose ps` — Keycloak doit passer à l'état `healthy`, ça peut prendre 1 à 2 minutes au premier démarrage). Les avertissements `WARN` habituels (fuite JDBC mineure de la base H2 de dev, rappel "development mode") sont normaux en mode `start-dev` et n'indiquent aucun échec.

Ouvre **`http://keycloak.lab.local:8080`** — pas `:8081` (erreur présente dans une version antérieure de ce guide), et bien `keycloak.lab.local`, pas l'IP directement (tu serais redirigé de toute façon, autant partir du bon nom).

Connexion avec `admin` / le mot de passe saisi à l'étape 3.

### 5.1 Créer le realm

1. En haut à gauche, clique sur le sélecteur de realm (affiche "master" par défaut).
2. **Create realm**.
3. Realm name : `lab` (exactement, en minuscules).
4. **Create**. Vérifie que le sélecteur affiche bien "lab" avant de continuer — toutes les étapes suivantes doivent se faire dans ce realm, pas dans "master".

### 5.2 Créer le groupe

1. Menu de gauche → **Groups** → **Create group**.
2. Name : `SG-Anonymiseur-Utilisateurs` (respecte la casse).
3. **Create**.

### 5.3 Créer le client OIDC confidentiel

1. Menu de gauche → **Clients** → **Create client**.
2. Écran *General settings* : Client type reste `OpenID Connect`. Client ID : `anonymiseur-app`. **Next**.
3. Écran *Capability config* : **active l'interrupteur `Client authentication`** (éteint par défaut — c'est lui, et uniquement lui, qui rend le client confidentiel plutôt que public ; sans ça, aucun onglet *Credentials* n'apparaîtra ensuite et `oauth2-proxy` n'aura pas de client secret à utiliser). Sous *Authentication flow*, laisse `Standard flow` coché. **Next**.
4. Écran *Login settings* : *Valid redirect URIs* : `https://anonymiseur.lab.local/oauth2/callback` (exactement, adapte au domaine réellement utilisé). **Save**.
5. Une fois le client créé, onglet **Credentials** (n'apparaît que parce que *Client authentication* a été activé) → copie la valeur de **Client secret**.

### 5.4 Ajouter le mapper "Group Membership"

1. Sur la page du client, onglet **Client scopes**.
2. Clique sur la ligne `anonymiseur-app-dedicated` (le scope "Dedicated" propre à ce client).
3. **Add mapper** → **By configuration**.
4. Type : **Group Membership**.
5. *Name* : libre (ex. `groups`). *Token Claim Name* : **`groups`** exactement — c'est ce nom précis que l'application ira lire dans le token. *Full group path* : **On** (important — `oauth2-proxy.cfg` référence le groupe avec son chemin complet, `/SG-Anonymiseur-Utilisateurs`, slash inclus ; sur `Off`, la comparaison échouerait silencieusement). Vérifie que *Add to ID token* et *Add to access token* sont bien activés (par défaut ils le sont).
6. **Save**.

### 5.5 Créer et assigner le Client Scope "groups"

⚠️ Étape indispensable, distincte de 5.4, et facile à ne pas soupçonner : `oauth2-proxy.cfg` contient `allowed_groups = ["/SG-Anonymiseur-Utilisateurs"]`, ce qui pousse `oauth2-proxy` à réclamer explicitement un scope OAuth nommé **`groups`** lors de la connexion. Sans cette étape, Keycloak refuse la requête avec `invalid_scope: Invalid scopes: openid email profile groups` — le mapper de l'étape 5.4 seul ne suffit pas, il faut aussi que ce nom de scope existe et soit assigné au client.

1. Menu de gauche (pas dans la fiche du client) → **Client scopes** → **Create client scope**.
2. Name : `groups` (exactement). Protocol : `openid-connect`. **Save**.
3. Sur la page du scope créé, onglet **Mappers** → **Add mapper** → **By configuration** → **Group Membership**. Même configuration qu'à l'étape 5.4 (*Token Claim Name* = `groups`, *Full group path* : On). **Save**.
4. Retourne sur **Clients** → `anonymiseur-app` → onglet **Client scopes** (celui de la fiche du client cette fois) → **Add client scope** → coche `groups` → assigne-le en **Default** (pas *Optional*).

### 5.6 Créer un ou deux utilisateurs de test

1. Menu de gauche → **Users** → **Add user**. Username : `testuser1`. **Create**.
2. Onglet **Details** de sa fiche → active **Email verified**. Si cette case reste décochée, `oauth2-proxy` refuse de finaliser la connexion avec une erreur `500` (`email in id_token isn't verified`), même après une authentification Keycloak par ailleurs réussie.
3. Onglet **Credentials** → **Set password** → tape un mot de passe → désactive *Temporary* (sinon Keycloak forcera un changement de mot de passe au premier login) → **Save**.
4. Onglet **Groups** → **Join Group** → coche `SG-Anonymiseur-Utilisateurs` → **Join**. Sans cette étape, le token ne contiendra jamais le bon groupe, même avec un mot de passe valide.
5. (Optionnel) Répète pour un second utilisateur, sans l'étape *Join Group* cette fois, si tu veux vérifier que l'accès est bien refusé à quelqu'un hors du groupe.

### 5.7 Enregistrer le vrai client secret

Remplace maintenant la valeur temporaire saisie à l'étape 3, directement, sans relancer tout le script (ce qui régénérerait inutilement le cookie secret et forcerait un redémarrage de conteneurs déjà stables) :

```bash
printf '%s' '<le-vrai-secret-copié-depuis-keycloak>' > ./secrets/oauth2_client_secret.txt
chmod 644 ./secrets/oauth2_client_secret.txt
docker compose up -d --force-recreate oauth2-proxy
```

`644`, pas `600` : les conteneurs comme `oauth2-proxy` tournent sous leur propre utilisateur non-root, dont l'UID ne correspond généralement pas à celui de ton compte VM — un fichier `600` serait illisible pour eux (voir dépannage plus bas). Le `--force-recreate` n'est pas optionnel : un simple redémarrage ne relit pas le fichier de secret, Docker le monte à la création du conteneur.

## 6. Démarrer le reste du stack

```bash
docker compose up -d --build --scale presidio-analyzer=2 --scale presidio-anonymizer=1
docker compose ps
```

`app` attend automatiquement qu'un service dédié (`fix-app-dirs-perms`) corrige les permissions de ses dossiers de données avant de démarrer — aucune manipulation manuelle nécessaire pour ça.

(2 réplicas suffisent pour un test fonctionnel ; monte à 6/2 uniquement pour le test de charge à 50 utilisateurs concurrents — dimensionnement déjà vu : 12-16 vCPU / 24-32 Go dans ce cas.)

Si des images publiées sont déjà référencées dans `docker-compose.yml` (`image: ghcr.io/...`), tu peux d'abord faire `docker compose pull` pour accélérer — `--build` reste sans risque à garder dans tous les cas.

**Vérifie que tout reste stable, pas juste démarré :**

```bash
docker compose ps
```

Tous les services doivent afficher `Up`/`Running`/`Healthy`, sans compteur de redémarrage (`Restarting (1)...`) qui augmente.

## 7. Vérifier et se connecter

```bash
docker compose logs -f app
docker compose logs -f oauth2-proxy
```

Ouvre **`https://anonymiseur.lab.local`** (bien `https`, pas `http`). Le navigateur affichera un avertissement de certificat (normal, certificat auto-signé en lab) : accepte-le pour continuer. Tu dois ensuite être redirigé vers Keycloak, te connecter avec un utilisateur de test du groupe, puis atterrir sur l'app.

## Changer de domaine après coup

Si `APP_DOMAIN` doit changer après une première installation fonctionnelle (test avec un autre nom, passage d'un lab à un autre), **quatre endroits distincts** doivent être mis à jour — modifier uniquement `.env` laisse l'application inaccessible avec un `404` ou une authentification cassée :

1. **`.env`** : nouvelle valeur d'`APP_DOMAIN`.
2. **Recréer les conteneurs concernés**, pour que les labels Traefik (résolus à la création, pas en continu) et `oauth2-proxy` (dont `OAUTH2_PROXY_REDIRECT_URL` est aussi dérivé d'`APP_DOMAIN`) prennent en compte le changement :
   ```bash
   docker compose up -d --force-recreate traefik app oauth2-proxy
   ```
3. **`/etc/hosts`** côté client : remplace l'ancienne entrée du domaine principal par la nouvelle (`keycloak.lab.local` ne change pas, il est indépendant d'`APP_DOMAIN`).
4. **Keycloak** → **Clients** → `anonymiseur-app` → **Settings** → *Valid redirect URIs* : remplace l'ancien domaine par le nouveau. C'est la seule étape qui reste manuelle en dehors de `.env` — Keycloak ne peut pas être piloté depuis `docker-compose.yml`.

`oauth2-proxy.cfg` n'a plus besoin d'être touché : `redirect_url` en est volontairement absent, remplacé par `OAUTH2_PROXY_REDIRECT_URL=https://${APP_DOMAIN}/oauth2/callback` dans le bloc `environment:` d'`oauth2-proxy` (les variables d'environnement priment sur ce fichier `.cfg`, comme déjà expliqué pour `client_id` dans ses propres commentaires).

Si le nom du dossier du projet change également (voir l'avertissement de l'étape 1), les labels `traefik.docker.network=obfusk8_app-internal` dans `docker-compose.yml` doivent eux aussi être mis à jour avec le nouveau nom de dossier.

## Dépannage — erreurs rencontrées lors d'un test réel, et leur cause exacte

| Symptôme | Cause | Correction |
|---|---|---|
| `Fatal glibc error: CPU does not support x86-64-v2` (logs Keycloak) | Type de CPU de la VM trop générique | Étape 0 — type `host` dans Proxmox |
| Page bloquée sans erreur claire en ouvrant la console Keycloak | `keycloak.lab.local` absent de `/etc/hosts` côté client ; Keycloak redirige systématiquement vers ce nom (`KC_HOSTNAME`) | Étape 2 — ajouter les deux entrées, pas une seule |
| Console Keycloak inaccessible sur le port 8081 | Le port réel mappé est 8080 (erreur d'une version antérieure de ce guide) | Utiliser `http://keycloak.lab.local:8080` |
| `PermissionError: ... '/data/audit/audit.log'` (logs app) | Dossier hôte créé par Docker en root avant que l'utilisateur applicatif (UID 1000) puisse y écrire | Corrigé automatiquement par le service `fix-app-dirs-perms` — si l'erreur persiste, vérifier `docker compose ps fix-app-dirs-perms` (doit afficher `Exited (0)`) |
| `cookie_secret must be 16, 24, or 32 bytes... but is 51 bytes` (oauth2-proxy, en boucle, **persiste même après avoir régénéré et vérifié le fichier à 32 octets**) | Une variable `OAUTH2_PROXY_COOKIE_SECRET` traîne dans `.env` et prend la priorité sur le Docker secret — 51 est la longueur du texte placeholder, pas celle d'un vrai secret | Étape 4 — retirer toute ligne `OAUTH2_PROXY_*_SECRET` de `.env` ; `docker compose up -d --force-recreate oauth2-proxy` |
| `unauthorized_client` puis `invalid_client_credentials` (logs Keycloak), **malgré un `secrets/oauth2_client_secret.txt` vérifié correct** | Même bug que ci-dessus, sur `OAUTH2_PROXY_CLIENT_SECRET` cette fois — une ligne oubliée dans `.env` prend la priorité sur le Docker secret | Étape 4 — vérifier `grep -i client_secret .env` (doit être vide) et `docker compose config \| grep CLIENT_SECRET` (une seule ligne `_FILE` doit apparaître) |
| `could not read cookie secret file: /run/secrets/oauth2_cookie_secret` | Fichier de secret en `600`, illisible par l'UID non-root du conteneur `oauth2-proxy` (différent de celui de ton compte VM) | `chmod 644` sur les fichiers dans `./secrets/` (déjà fait par défaut si `generate-secrets.sh` a été régénéré après cette correction) |
| `./generate-secrets.sh: ... Permission denied` en écrivant dans `secrets/` ou `traefik/dynamic/` | Le dossier appartient à `root` (créé par un `docker compose up` lancé avant le script) | `sudo chown -R $(whoami):$(whoami) ./secrets ./traefik/dynamic` puis relancer avec `--force` |
| Une correction de secret ne semble avoir aucun effet malgré un fichier hôte vérifié correct | Le conteneur concerné n'a jamais été recréé — un simple redémarrage ne relit pas le fichier, Docker copie/monte le secret à la création du conteneur | Toujours utiliser `docker compose up -d --force-recreate <service>` après avoir modifié un fichier de secret |
| `Cannot start the provider *file.Provider: ... /etc/traefik/dynamic: permission denied` (logs Traefik), suivi de `middleware "gateway-secret@file" does not exist` sur **tous** les routers `app` | Dossier `traefik/dynamic` en `700` (via `umask 077` dans `generate-secrets.sh`), illisible par l'UID non-root de Traefik — casse tout le file provider, donc tous les routers qui dépendent du middleware `gateway-secret@file` | `chmod 755 ./traefik/dynamic && chmod 644 ./traefik/dynamic/gateway-secret.yml`, puis `docker compose up -d --force-recreate traefik` |
| `404 page not found` (réponse Traefik brute) sur le domaine principal, alors que `docker compose config \| grep "app.rule"` confirme une règle correcte | Label `traefik.docker.network=...` pointant vers un nom de réseau obsolète (ex. reliquat d'un ancien nom de projet) — Traefik matche la règle mais ne trouve pas le conteneur cible sur le réseau indiqué | Vérifier `docker network ls`, corriger le label pour qu'il corresponde à `<nom-du-dossier-actuel>_app-internal`, puis `docker compose up -d --force-recreate traefik app oauth2-proxy` |
| Keycloak affiche `Invalid parameter: redirect_uri`, avec l'ancien domaine visible dans l'URL | *Valid redirect URIs* du client Keycloak pas mis à jour après un changement d'`APP_DOMAIN` | Voir [Changer de domaine après coup](#changer-de-domaine-après-coup), point 4 |
| `invalid_scope: Invalid scopes: openid email profile groups` (Keycloak, à l'écran de connexion) | `oauth2-proxy.cfg` réclame un scope `groups` (via `allowed_groups`) qui n'existe pas comme Client Scope Keycloak | Étape 5.5 — créer et assigner le Client Scope `groups` |
| `500 Internal Server Error` / `email in id_token isn't verified` après une authentification Keycloak par ailleurs réussie | Case *Email verified* non cochée sur l'utilisateur | Étape 5.6 — cocher *Email verified* sur la fiche utilisateur |
| Page inaccessible sur `http://anonymiseur.lab.local` | Traefik ne sert qu'en HTTPS | Utiliser `https://` |
| `git clone`/`docker pull` demande un login alors que le repo/package est censé être public | Le changement de visibilité n'a pas été validé jusqu'au bout côté GitHub | Revérifier `Settings → Danger Zone` (repo) ou `Package settings` (package), refaire la confirmation jusqu'au bout |

## Bascule vers Entra ID plus tard

Dans `oauth2-proxy.cfg`, remplacer `provider = "oidc"` par `provider = "entra-id"` et pointer `oidc_issuer_url` vers `https://login.microsoftonline.com/<tenant-id>/v2.0`, avec le vrai `client_id`/`client_secret` Entra (ce dernier remplace le contenu de `secrets/oauth2_client_secret.txt`, même mécanique qu'à l'étape 5.7). `redirect_url` n'a rien à faire ici (déjà dérivé d'`APP_DOMAIN`) ; seul `allowed_groups` dans ce même fichier doit être adapté au nom du groupe réel côté Entra ID.
