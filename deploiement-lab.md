# Anonymiseur - déploiement lab sur VM Docker (Proxmox)

Ce guide part du principe d'une installation **entièrement neuve**, sur une VM qui n'a jamais fait tourner ce projet. Chaque étape existe parce qu'un test réel "from scratch" a buté dessus — suis-les dans l'ordre, sans en sauter aucune, même celles qui semblent évidentes.

## 0. Prérequis VM Proxmox — à vérifier AVANT de démarrer quoi que ce soit

**Type de CPU de la VM.** Si la VM est configurée avec un type de processeur générique (`qemu64` par défaut sur beaucoup de setups Proxmox), certains services (Keycloak notamment) plantent au démarrage avec une erreur cryptique :

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

## 2. Ajouter le domaine dans `/etc/hosts` (poste client, pas la VM)

```
<ip-de-la-vm>  anonymiseur.lab.local
```

C'est le fichier hosts de **ta machine locale** (celle avec le navigateur) qui doit contenir cette ligne — pas celui de la VM.

## 3. Générer les secrets — AVANT tout `docker compose up`

```bash
chmod +x generate-secrets.sh
./generate-secrets.sh
```

Ce script crée le dossier `secrets/` avec les bonnes permissions restrictives (700/600) et génère automatiquement deux secrets aléatoires (cookie secret, secret de passerelle interne). Il te demande aussi de saisir deux valeurs manuellement — voici comment répondre à chacune **lors d'une toute première installation**, où le client OIDC Keycloak n'existe pas encore :

- **`Mot de passe admin Keycloak`** : choisis et tape ici le mot de passe que tu veux donner au compte admin de Keycloak — c'est toi qui le définis, Keycloak n'existe pas encore donc rien à récupérer.
- **`OAUTH2_PROXY_CLIENT_SECRET`** : à ce stade, tu ne l'as pas encore (il sera généré par Keycloak à l'étape 5). Tape une valeur temporaire quelconque (ex. `placeholder-a-remplacer`) — elle sera remplacée manuellement plus tard, **sans avoir besoin de relancer tout le script**.

⚠️ **Important : lance ce script avant le tout premier `docker compose up`, jamais après un essai déjà raté.** Si Docker a déjà tenté de démarrer les services avant que ce script n'ait créé `secrets/`, le dossier peut se retrouver appartenant à `root`, provoquant un `Permission denied` au moment de la génération. Si ça arrive :

```bash
sudo chown -R $(whoami):$(whoami) ./secrets ./traefik/dynamic
./generate-secrets.sh --force
```

Vérifie que le cookie secret fait bien 32 octets avant de continuer (sinon `oauth2-proxy` refusera de démarrer avec `cookie_secret must be 16, 24, or 32 bytes`) :

```bash
wc -c < ./secrets/oauth2_cookie_secret.txt   # doit afficher 32
```

## 4. Copier et remplir `.env`

```bash
cp .env.example .env
```

Édite `.env` — à ce stade, seule `APP_DOMAIN` a besoin d'une vraie valeur (`anonymiseur.lab.local`). Le reste peut rester aux valeurs par défaut pour un premier test.

## 5. Démarrer Keycloak seul, le configurer, récupérer le vrai client secret

```bash
docker compose up -d keycloak
docker compose logs -f keycloak
```

Attends que les logs se stabilisent (ou vérifie `docker compose ps` — Keycloak doit passer à l'état `healthy`, ça peut prendre 1 à 2 minutes au premier démarrage).

- Ouvrir `http://<ip-de-la-vm>:8081` — connexion avec `admin` / le mot de passe que tu as saisi à l'étape 3.
- Créer un realm `lab`.
- Créer un groupe `SG-Anonymiseur-Utilisateurs`.
- Créer un client OIDC `anonymiseur-app` (confidentiel), redirect URI :
  `https://anonymiseur.lab.local/oauth2/callback`
- Ajouter un mapper "Group Membership" → claim `groups`.
- Créer 1-2 utilisateurs de test, les ajouter au groupe.
- **Récupérer le vrai client secret** généré par Keycloak (onglet *Credentials* du client).

**Remplace maintenant la valeur temporaire** saisie à l'étape 3, directement, sans relancer tout le script (ce qui régénérerait inutilement le cookie secret et forcerait un redémarrage de conteneurs déjà stables) :

```bash
printf '%s' '<le-vrai-secret-copié-depuis-keycloak>' > ./secrets/oauth2_client_secret.txt
chmod 600 ./secrets/oauth2_client_secret.txt
```

## 6. Démarrer le reste du stack

```bash
docker compose up -d --build --scale presidio-analyzer=2 --scale presidio-anonymizer=1
docker compose ps
```

Au passage, `app` attend désormais automatiquement qu'un petit service dédié (`fix-app-dirs-perms`) corrige les permissions de ses dossiers de données avant de démarrer — plus besoin d'un `chown` manuel avant le premier lancement, quel que soit l'état initial de ces dossiers sur l'hôte.

(2 réplicas suffisent pour un test fonctionnel ; monte à 6/2 uniquement pour le test de charge à 50 utilisateurs concurrents — dimensionnement déjà vu : 12-16 vCPU / 24-32 Go dans ce cas.)

Si des images publiées sont déjà référencées dans `docker-compose.yml` (`image: ghcr.io/...`), tu peux d'abord faire `docker compose pull` pour accélérer — `--build` reste sans risque à garder dans tous les cas, il ne fait rien de plus qu'un build local si une image locale à jour existe déjà.

**Vérifie que tout reste stable, pas juste démarré :**

```bash
docker compose ps
```

Tous les services doivent afficher `Up`/`Running`/`Healthy`, sans compteur de redémarrage (`Restarting (1)...`) qui augmente. Si `app` ou `oauth2-proxy` redémarre en boucle, retourne voir les logs (`docker compose logs <service> --tail=50`) — les causes les plus fréquentes à ce stade sont listées dans le dépannage plus bas.

## 7. Vérifier et se connecter

```bash
docker compose logs -f app
docker compose logs -f oauth2-proxy
```

Ouvrir **`https://anonymiseur.lab.local`** (bien `https`, pas `http` — Traefik ne sert l'application qu'en HTTPS). Le navigateur affichera un avertissement de certificat (normal, certificat auto-signé en lab) : accepte-le pour continuer. Tu dois ensuite être redirigé vers Keycloak, te connecter avec un utilisateur de test du groupe, puis atterrir sur l'app.

## Dépannage — erreurs rencontrées lors d'un test réel, et leur cause exacte

| Symptôme | Cause | Correction |
|---|---|---|
| `Fatal glibc error: CPU does not support x86-64-v2` (logs Keycloak) | Type de CPU de la VM trop générique | Étape 0 — type `host` dans Proxmox |
| `PermissionError: ... '/data/audit/audit.log'` (logs app) | Dossier hôte (`/var/log/anonymiseur-audit`, monté sur `/data/audit`) créé par Docker en root avant que l'utilisateur applicatif (UID 1000) puisse y écrire | Corrigé automatiquement depuis l'ajout du service `fix-app-dirs-perms` — si tu vois encore cette erreur, vérifie qu'il s'est bien terminé avec succès : `docker compose ps fix-app-dirs-perms` doit afficher `Exited (0)` |
| `cookie_secret must be 16, 24, or 32 bytes... but is N bytes` (logs oauth2-proxy, en boucle) | Fichier de secret invalide ou généré avec un encodage incompatible | Étape 3 — régénérer via `generate-secrets.sh --force` après avoir corrigé les permissions de `./secrets`, vérifier `wc -c` = 32 |
| `./generate-secrets.sh: ... Permission denied` en écrivant dans `secrets/` | Le dossier `secrets/` appartient à `root` (créé par un `docker compose up` lancé avant le script) | `sudo chown -R $(whoami):$(whoami) ./secrets ./traefik/dynamic` puis relancer avec `--force` |
| `oauth2-proxy` redémarre en boucle après la phase "cookie secret" réglée | Client secret encore à sa valeur temporaire/placeholder, Keycloak refuse la connexion | Étape 5 — écraser `secrets/oauth2_client_secret.txt` avec la vraie valeur récupérée dans Keycloak |
| Page inaccessible sur `http://anonymiseur.lab.local` | Traefik ne sert qu'en HTTPS | Utiliser `https://` |
| `git clone`/`docker pull` demande un login alors que le repo/package est censé être public | Le changement de visibilité n'a pas été validé jusqu'au bout côté GitHub (boîte de confirmation fermée avant le bouton final) | Revérifier `Settings → Danger Zone` (repo) ou `Package settings` (package), refaire la confirmation jusqu'au bout |

## Bascule vers Entra ID plus tard

Dans `oauth2-proxy.cfg`, remplacer `provider = "oidc"` par `provider = "entra-id"` et pointer `oidc_issuer_url` vers `https://login.microsoftonline.com/<tenant-id>/v2.0`, avec le vrai `client_id`/`client_secret` Entra (ce dernier remplace le contenu de `secrets/oauth2_client_secret.txt`, même mécanique qu'à l'étape 5). Aucun autre fichier ne change.
