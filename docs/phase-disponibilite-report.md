# Phase « disponibilité et Keycloak de laboratoire » — compte rendu

Branche locale `feat/disponibilite`, créée depuis `feat/text-api` (`fff0c0f`, identique à `origin/feat/text-api`), jamais poussée.
Date : 2026-10-07. VM : 4 vCPU, 8 919 Mo de RAM, pas de swap. Docker 29.8.1, Compose v5.5.1.
Décisions humaines de la phase : D-048 (étape A), D-049 (secrets du laboratoire jetables). Décisions de mise en œuvre : D-050.

## 1. Résumé

- **EXT-51 corrigé** : un utilisateur, même négligent, ne bloque plus le flux documents des autres. Quota par utilisateur
  (`MAX_PENDING_JOBS_PER_USER=3`, 429 + `Retry-After`), plafond commun conservé (503), annulation de ses propres documents, limitation de
  débit par utilisateur en plus de celle par adresse IP. Mesuré avec deux comptes.
- **EXT-47 corrigé** : identité absente ou vide refusée en 403 sur toutes les routes du flux documents.
- **Protection contre la falsification de requête** (D-048 point 3) : `Origin` (à défaut `Referer`) égal à `https://${APP_DOMAIN}` exigé
  sur `/api/detect`, `/api/finalize` et `/api/cancel` ; en-tête `X-Obfusk8-Action` en plus pour `/api/cancel`.
- **EXT-57, défaut introduit puis corrigé pendant la phase** : un 403 de l'application traversait `oauth2-errors` et déconnectait
  l'utilisateur. Middleware `doc-auth-errors` (401 seulement) sur les routes de documents.
- **EXT-52 corrigé** : le client des bancs vérifie une session réellement authentifiée (`/oauth2/auth` → 202) et s'arrête sinon.
- **Keycloak de laboratoire** : profil `lab`, réseau interne seulement (EXT-55 corrigé : plus d'accès sortant), relayé par Traefik sur
  `BIND_ADDRESS:8080`, émetteur OIDC inchangé, adresse réelle du client dans ses journaux, `X-Forwarded-For` falsifié sans effet.
- **Ports** : 80, 443 et 8080 publiés sur `BIND_ADDRESS` seulement (vérifié en fin de phase).
- **Suite** : 550 tests verts sous seccomp (502 au départ). API texte désactivée (`ENABLE_EXTENSION_API=false`).

## 2. Commits (`fff0c0f..HEAD`, branche `feat/disponibilite`)

| Commit | Objet |
|---|---|
| `13fe3f3` | D-048 (décisions sur l'étape A) et D-049 (secrets jetables) |
| `4a2613b` | Quota par utilisateur, annulation, contrôle d'origine, EXT-47 (code et tests) |
| `b3e2b3b` | Limitation de débit par utilisateur et routeur `app-cancel` (Compose, fichiers d'exemple) |
| `c605a84` | EXT-57 : les 403 de l'application ne déconnectent plus l'utilisateur |
| `44a0203` | EXT-52 : bancs sur session réellement authentifiée, `Origin`, `BENCH_RESOLVE_ADDRESS` |
| `4e15c4c` | Banc de bout en bout `benchmarks/e2e/e2e_availability.py` |
| `59ce459` | Keycloak de laboratoire : profil `lab`, sans accès sortant, relayé par Traefik |
| `7d660c2` | Documentation lab / déploiement réel (FR/EN) |
| (ce commit) | Guides utilisateur, sécurité, `FINDINGS.md`, `DECISIONS.md` (D-045, D-050), ce compte rendu |

## 3. Tableau avant / après par lot

### Lot 1 — disponibilité du flux documents (EXT-51, EXT-47, D-048)

| Situation | Avant | Après (observé) |
|---|---|---|
| Un compte envoie sans finaliser | Accepté jusqu'au plafond commun (20), puis 503 pour **tous** | 4ᵉ envoi : 429 de l'application, `Retry-After=621` ; l'autre compte envoie (200) |
| Libérer sa place | Attendre l'expiration (600 s + balayage) | « Annuler ce document » : 200, nouvel envoi accepté aussitôt |
| Annuler le document d'un autre / inconnu / mal formé | — | 404 générique identique, document intact, rien dans l'audit |
| Rafale d'un compte (une adresse) | Limite par IP seulement, partagée derrière un NAT | 10 requêtes, puis 429 de Traefik ; même compte depuis une autre adresse : 429 ; autre compte au même moment : passe |
| Requête sans identité | Repli « inconnu » partagé | 403 (`identity_missing`) sur detect, finalize, preview, cancel |
| Requête d'une autre origine | Acceptée (seul `SameSite=Lax`) | 403 sur detect, finalize, cancel ; session intacte (EXT-57) |
| `Retry-After` d'une `HTTPException` | Perdu par `http_exception_handler` | Transmis (JSON et HTML) |

### Lot 2 — bancs fiables (EXT-52)

| Situation | Avant | Après (observé) |
|---|---|---|
| Faux identifiants | `login()` réussit (cookie `_oauth2_proxy_csrf`), échec trompeur plus tard | `RuntimeError: login failed: /oauth2/auth answered HTTP 401` en 4 s, code 1, `benchmarks/results/` inchangé (99 fichiers) |
| Bancs de documents | — | Envoient l'`Origin` de l'application (sinon 403 depuis D-048) |
| Pile publiée sur `BIND_ADDRESS` | `BENCH_RESOLVE_LOOPBACK` (127.0.0.1) ne joignait plus rien | `BENCH_RESOLVE_ADDRESS=192.168.1.35` |

### Lot 3 — Keycloak de laboratoire (D-046, D-048 point 1)

| Situation | Avant | Après (observé) |
|---|---|---|
| Démarrage sans profil | Keycloak démarre toujours | Keycloak absent ; oauth2-proxy en boucle (« Failed to initialise OAuth2 Proxy ») ; application 404 ; port 8080 → 404 |
| Accès sortant de Keycloak | Connexion établie vers `1.1.1.1:443` (EXT-55) | Échec vers `1.1.1.1:443` et `example.com:443` |
| Réseau `frontend` | Traefik et Keycloak | Traefik seul |
| Port 8080 | Publié par Keycloak | Publié par Traefik sur `BIND_ADDRESS`, relayé vers Keycloak |
| Émetteur OIDC | `http://keycloak.lab.local:8080/realms/lab` | Identique ; découverte d'oauth2-proxy réussie, 0 redémarrage |
| Adresse du client dans les événements | `172.20.0.1` (passerelle Docker) pour tous | Adresse réelle (`192.168.1.35` depuis l'hôte) ; `X-Forwarded-For: 203.0.113.66` falsifié absent des journaux |
| Connexion de bout en bout (profil `lab`) | — | compte-1 : 200, retour à la page demandée (`/?depuis=controle-profil-lab`) |

## 4. Vérifications finales (sorties utiles)

**Point de départ** : `git rev-parse feat/text-api origin/feat/text-api` → `fff0c0f…` deux fois ; `app/run-tests.sh` → `502 passed` ;
`ss -tlnp` → `192.168.1.35` sur 80, 443, 8080.

**Tests écrits avant les correctifs** : `test_document_availability.py` → `31 failed, 5 passed`, dont la reproduction d'EXT-51
(`assert 200 == 429`) ; les deux tests du plafond commun passaient déjà (non-régression) ; `test_bench_client.py` → EXT-52 reproduit
(`DID NOT RAISE RuntimeError`).

**Images reconstruites depuis les Dockerfiles** (`docker compose -f docker-compose.yml -f docker-compose.build.yml --profile lab build`) ;
code en service identique au dépôt : `docker exec obfusk8-app-1 cat /app/main.py | cmp - app/main.py` → identique (ainsi que
`i18n/fr.json`, `i18n/en.json`, `text_api.py`) ; `docker compose --profile lab up -d` → aucun conteneur à recréer.

**Suite complète sous `seccomp/app-enforce.json`** (`app/run-tests.sh`, image finale) : `550 passed in 11.10s`.

**Couverture des lignes ajoutées à `main.py`** (hors seccomp) : 85 sur 92 avant les tests complémentaires ; les tests du `Referer` mal formé,
des réservations multiples et de la configuration invalide au démarrage couvrent ensuite les branches restantes, sauf l'avertissement
« APP_DOMAIN absent » du démarrage (journal seulement).

**Analyse statique** : `ruff check` et `ruff format --check` verts sur les fichiers nouveaux (`conftest.py`, `doc_headers.py`,
`test_document_availability.py`, `test_bench_client.py`, `e2e_availability.py`) et sur `obfusk8_client.py`, `multi_user_bench.py` ;
`main.py` : 22 constats `E,F,W,B,S,BLE` avant et après, même répartition. `mypy --strict` : `Success` sur les fichiers nouveaux et
`obfusk8_client.py`. `bandit` sur `main.py` : 1 × B101, 2 × B324, avant et après.

**`pip-audit`** (image `app`, environnement installé, OSV) : `No known vulnerabilities found`.
**`trivy` 0.75.0** (image `app` finale) : paquets Debian 77 HIGH/CRITICAL, **0 corrigeable** (dont CRITICAL libxml2 CVE-2026-6653,
exposition analysée dans EXT-33) ; paquets Python 0. Identique à la phase 2 bis. Image de l'analyseur inchangée (même construction).

**Ports** (fin de phase, profil `lab`) : `ss -tlnp` → `192.168.1.35:80`, `:443`, `:8080` seulement ; IPv6 publique et `127.0.0.1` →
connexion refusée.

**Non-régression du flux documents de bout en bout** : voir §4 bis.

**Journal d'audit** : 158 lignes au début de la phase ; les annulations des bancs ajoutent des lignes aux seules clés
`event, file_size_mb, filename_hash, format, job_id, theme, timestamp, user` (aucun contenu, vérifié aussi par test avec un texte canari).

**`.env`** : `ENABLE_EXTENSION_API=false`.

## 4 bis. Non-régression du flux documents (pile complète, seccomp)

`benchmarks/e2e/e2e_document_flow.py` (compte-1, `E2E_PACE=13`, image finale, profil `lab`, 2026-10-07 à partir de 07:17 UTC) :

```
pdf    theme=-        detect=200 (0.07s, 4 zone(s)) preview=200 finalize=200 total=4 download=200 928o exposed=0/2
docx   theme=-        detect=200 (0.04s, 10 zone(s)) preview=- finalize=200 total=10 download=200 37061o exposed=0/3
csv    theme=-        detect=200 (0.05s, 30 zone(s)) preview=- finalize=200 total=33 download=200 439o exposed=0/2
image  theme=-        detect=200 (0.24s, 7 zone(s)) preview=200 finalize=200 total=7 download=200 2697o exposed=-
pdf    theme=medical  detect=200 (0.03s, 4 zone(s)) preview=200 finalize=200 total=4 download=200 928o exposed=0/2
docx   theme=medical  detect=200 (0.03s, 10 zone(s)) preview=- finalize=200 total=10 download=200 37061o exposed=0/3
csv    theme=medical  detect=200 (0.04s, 28 zone(s)) preview=- finalize=200 total=31 download=200 439o exposed=0/2
image  theme=medical  detect=200 (0.18s, 7 zone(s)) preview=200 finalize=200 total=7 download=200 2697o exposed=-
unknown path -> 404 {"detail":"Not Found"}
GET /api/v1/version -> 404 {"detail":"Not Found"} x-request-id=None
POST /api/v1/text/analyze -> 404 {"detail":"Not Found"} x-request-id=None
POST /api/v1/text/pseudonymize -> 404 {"detail":"Not Found"} x-request-id=None
```

Code de sortie 0 ; le client envoie désormais l'`Origin` de l'application (sinon 403 depuis D-048). Puis
`benchmarks/e2e/check_no_content_in_logs.py --since 2026-10-07T06:00:00` : journaux de `app`, `presidio-analyzer`, `traefik`,
`oauth2-proxy`, `keycloak` et les deux journaux d'audit (172 et 20 672 lignes) → `633 valeurs recherchées, 0 trouvée(s)` ; contenu
fictif des bancs de la phase (`Camille Fictive`, noms de fichiers) : 0 occurrence dans les journaux et l'audit.

## 5. Revue sécurité

**Vérifié**
- Quota et annulation suivent l'identité nettoyée (`_document_user`), la même que la propriété des documents.
- Annulation : pas d'énumération (même 404 pour inconnu, expiré, mal formé, appartenant à un autre), document d'autrui intact, format
  validé avant recherche, aucune route quand le drapeau est désactivé (réponse identique à une route absente).
- Origine : comparaison exacte (`null`, autre schéma, sous-domaine piégé `…invalid.attaquant.invalid`, `Referer` mal formé refusés) ;
  sans `APP_DOMAIN`, tout est refusé (échec fermé, avertissement au démarrage).
- Aucun contenu utilisateur dans l'audit d'annulation (test avec texte canari) ; journal applicatif : identifiant de tâche seulement.
- EXT-57 : un refus de l'application ne touche plus au cookie de session.
- Keycloak : `X-Forwarded-For` falsifié sans effet ; confiance limitée à `10.89.18.10` ; aucun accès sortant.
- Limitation de débit par utilisateur placée **après** `oidc-auth` (identité posée par oauth2-proxy, jamais celle du client).

**Points ouverts**
- EXT-59 : sept comptes coordonnés peuvent saturer le plafond commun (décision humaine).
- EXT-54 (laboratoire) : console Keycloak et royaume `master` en HTTP depuis le réseau local ; proposition : liste d'adresses sur
  `/admin` et `/realms/master`.
- Partage des compteurs d'un même middleware de limitation entre les routeurs `app-upload` et `app-cancel` : **non mesuré**.
- Limite du routeur `keycloak` (300/min) : chargement de la console non mesuré.
- Un document dont l'onglet est fermé ne peut plus être annulé (accepté, D-048 point 4 ; page « mes documents en attente » au backlog).

**À différer au pentest**
- Falsification de requête depuis un autre sous-domaine réel du domaine de l'établissement, et depuis un navigateur ancien (envoi de
  `Origin` sur les formulaires).
- Contournement de la limitation par utilisateur (rotation de sessions, plusieurs comptes) et effet sur le plafond commun.
- Course entre annulation et finalisation du même document (verrou partagé, testé unitairement seulement).

## 6. Observé et supposé

| Observé | Supposé |
|---|---|
| Tous les résultats des §3, §4 et §4 bis | Les navigateurs du pilote envoient `Origin` sur les formulaires POST (comportement standard, non testé avec un navigateur réel dans cette session : voir la procédure §9) |
| `/oauth2/auth` : 401 sans session, 202 authentifié | Le chargement de la console Keycloak tient sous 300 requêtes par minute |
| Docker 29.8.1 ne publie aucun port d'un conteneur seulement sur un réseau interne | Les options `KC_PROXY_HEADERS` et `KC_PROXY_TRUSTED_ADDRESSES` se comportent comme documenté (comportement vérifié sur l'adresse enregistrée, pas dans le code de Keycloak) |

## 7. Incidents

- **Secret exposé** (D-049) : la valeur du secret de passerelle du laboratoire s'est affichée dans la session pendant l'étape A
  (recherche récursive dans `traefik/dynamic/`). Non régénéré, conformément à D-049.
- **Redéploiement lancé en parallèle d'une vérification qui a échoué** (avant la phase, correctif d'exposition) : la pile est partie sur
  `127.0.0.1` à cause d'une faute de frappe dans `.env` (`BIND_ADRESS`) ; corrigé par l'humain, redéployé après vérification.
- **Actions refusées par le classifieur de permissions** : inventaire des fichiers de secrets, régénération du secret, écriture de
  `.claude/settings.json` ; abandonnées par D-049.

## 8. Retour arrière

Revenir à `fff0c0f` (`git checkout feat/text-api`), puis : `docker compose stop keycloak` (le port 8080 passe de Traefik à Keycloak),
reconstruction (`docker compose -f docker-compose.yml -f docker-compose.build.yml build`) et `docker compose up -d`. Aucune migration de
données : le volume `keycloak-data-v26-8` est le même ; les documents en attente sont en mémoire et disparaissent au redémarrage, comme
avant.

## 9. Procédure de test manuel (pour l'humain)

1. **Fusion et publication** : `git checkout feat/text-api && git merge --ff-only feat/disponibilite && git push`.
2. **`.env`** : ajouter `COMPOSE_PROFILES=lab` (décision D-048) ; vérifier `ENABLE_EXTENSION_API=false` et `BIND_ADDRESS=192.168.1.35`.
3. **Reconstruction et démarrage** : `docker compose -f docker-compose.yml -f docker-compose.build.yml build`, puis
   `docker compose up -d` (le profil vient de `.env`).
4. **Commit en service** : `git rev-parse --short HEAD` (doit être celui poussé) ;
   `docker exec obfusk8-app-1 cat /app/main.py | cmp - app/main.py` (aucune sortie = identique) ;
   `ss -tlnp | grep -E ':(80|443|8080)\b'` (seulement `192.168.1.35`).
5. **Connexion** : ouvrir `https://obfusk8.lab.local` depuis le poste, se connecter avec compte-1 ; arrivée sur l'accueil.
6. **Quota avec deux comptes** : avec compte-1, envoyer trois petits fichiers CSV sans valider (laisser chaque page de révision ouverte
   dans un onglet) ; un quatrième envoi doit afficher « Trop de documents en attente » et « Tu as déjà 3 documents en attente de révision… ».
   Dans une fenêtre privée, compte-2 envoie un fichier : la page de révision s'affiche.
7. **Annulation** : dans un onglet de compte-1, « Annuler ce document » → confirmation → retour à l'accueil ; un nouvel envoi est accepté.
8. **Validation normale** : un document envoyé, révisé et validé se télécharge comme avant (PDF et DOCX au moins).
9. **API texte désactivée** : `https://obfusk8.lab.local/api/v1/version` → 404.
10. **Console Keycloak** : `http://keycloak.lab.local:8080` s'ouvre et se charge entièrement (vérifie la limite de 300 requêtes par minute).

## 10. Décisions qui reviennent à l'humain

1. Valider D-050 (mise en œuvre), en particulier `doc-auth-errors` (EXT-57) et le code nouveau dans `main.py` plutôt qu'un module
   séparé (sinon : autoriser la ligne `COPY` du Dockerfile).
2. EXT-59 : accepter le risque des comptes coordonnés ou dimensionner `MAX_PENDING_JOBS` pour le pilote.
3. EXT-54 (laboratoire) : liste d'adresses sur `/admin` et `/realms/master` ?
4. EXT-58 (README, secret du client dans `.env`) : correction dans une prochaine phase documentaire.
5. Diff de `CLAUDE.md` ci-dessous.

## 11. Proposition de mise à jour de `CLAUDE.md` (non appliquée)

```diff
-**Routage Traefik par labels** : des routeurs dédiés existent pour `/api/detect` et `/api/finalize` (limitation de débit + plafond de corps),
-`/api/preview_image` (limitation de débit), `/api/v1/` (routeur `app-text` : …
+**Routage Traefik par labels** : des routeurs dédiés existent pour `/api/detect` et `/api/finalize` (limitation de débit par IP **et** par
+utilisateur + plafond de corps), `/api/cancel/` (routeur `app-cancel` : mêmes limites, plafond de 4 096 octets, sans redirection),
+`/api/preview_image` (limitation de débit), `/api/v1/` (routeur `app-text` : …
+Les routes de documents utilisent `doc-auth-errors` (redirection vers la connexion sur 401 seulement) : `oauth2-errors` (401-403)
+transformait un 403 de l'application en déconnexion (EXT-57). Ne remets jamais `oauth2-errors` sur une route qui peut répondre 403.
+
+**Flux documents** : quota par utilisateur (`MAX_PENDING_JOBS_PER_USER`, D-048), annulation (`ENABLE_JOB_CANCEL`), identité obligatoire
+(`_document_user`, EXT-47) et `Origin` égal à `https://${APP_DOMAIN}` sur toute requête qui modifie un état (`APP_DOMAIN` transmis à `app`).
+Tests : `tests/doc_headers.py` (en-têtes d'une requête légitime) et `tests/conftest.py` (origine de test). Bancs :
+`BENCH_RESOLVE_ADDRESS=<BIND_ADDRESS>`.
@@
-Le nom du projet Compose est supposé être `obfusk8` (label `traefik.docker.network=obfusk8_app-internal`) et les ports 80, 443 et 8080 sont fixes.
+Le nom du projet Compose est supposé être `obfusk8` (label `traefik.docker.network=obfusk8_app-internal`) et les ports 80, 443 et 8080 sont fixes,
+tous publiés par Traefik sur la seule adresse `BIND_ADDRESS` (D-047), jamais sur `0.0.0.0` ni `[::]`.
@@
-**Adresse fixe de Traefik** : `10.89.18.10` sur `app-internal` (…), seule source de confiance
-d'oauth2-proxy pour les en-têtes de transfert (`trusted_proxy_ips`). À changer aux trois endroits ensemble.
+**Adresse fixe de Traefik** : `10.89.18.10` sur `app-internal` (…), seule source de confiance
+d'oauth2-proxy (`trusted_proxy_ips`) et de Keycloak (`KC_PROXY_TRUSTED_ADDRESSES`) pour les en-têtes de transfert. À changer aux quatre endroits ensemble.
@@
-… Keycloak 26.8 sur le volume `keycloak-data-v26-8` (…).
+… Keycloak 26.8 sur le volume `keycloak-data-v26-8` (…).
+**Keycloak = laboratoire uniquement** (D-046) : profil Compose `lab` (`COMPOSE_PROFILES=lab` dans le `.env` du laboratoire, sinon
+`docker compose --profile lab …`), mode développement, réseau `app-internal` seul (aucun accès sortant), joint par Traefik (point
+d'entrée `keycloak`, `BIND_ADDRESS:8080`). Sans le profil, oauth2-proxy redémarre en boucle : c'est attendu.
+
+**Secrets du laboratoire jetables** (D-049) : une exposition dans une session se signale en une ligne dans le compte rendu, sans arrêt ;
+aucun secret dans un commit, vérifié avant chaque commit.
```
