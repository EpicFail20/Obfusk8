# Phase 2 bis — sécurité, chaîne d'approvisionnement et durcissement : compte rendu

Branche locale `feat/durcissement`, créée depuis `feat/text-api` (`b96aa54`, identique à `origin/feat/text-api`), jamais poussée.
Date : 2026-10-05. VM : 4 vCPU, 8 919 Mo de RAM, pas de swap. Décisions humaines de la phase : D-039 (priorité au prototype sûr,
phase 2.1 annulée), D-040 (choix de l'étape A), option 1 pour Keycloak (export puis import).

## 1. Résumé

- **Chaîne d'approvisionnement** : toutes les images tierces et de base sont épinglées par condensat, à la dernière version vérifiée à la source.
  Keycloak passe de 26.0.8 (CVE-2026-18963, prise de contrôle de compte) à 26.8.0, oauth2-proxy à 7.15.5 (deux contournements
  d'authentification corrigés), busybox à 1.38.0. Plus aucune étiquette `main` ni `latest`.
- **Analyseur reproductible** (EXT-16) : base Presidio 2.2.364 par condensat, modèle français par empreinte, versions vérifiées à la
  construction. **Effet sur la détection : nul**, rapport de qualité identique ligne à ligne à la référence.
- **Images minimales** : `app` sans pytest, sans tests, sans `pip`, sans `libgl1` (851 → 553 Mo) ; analyseur sans `uv` ni `pip` ;
  service `presidio-anonymizer` retiré. **0 vulnérabilité HIGH/CRITICAL corrigeable** dans les images construites, oauth2-proxy, Traefik,
  busybox et Keycloak ; 6 dans docker-socket-proxy, sans version amont corrigée (accepté, D-040 point 8).
- **Authentification et proxy** : configuration oauth2-proxy unique (EXT-13), en-têtes de transfert crus seulement depuis Traefik (EXT-48),
  retour après connexion sans redirection ouverte (EXT-45), 403 sur `/api/v1/` sans identité (D-035).
- **Contrat de l'extension** : empreinte `detection_config` complète (D-030), conduite attendue par code HTTP, formats mesurés à travers la pile.
- **Hygiène** : caractères invisibles littéraux remplacés et interdits par test (EXT-39), README corrigés (EXT-02 à EXT-04).
- **Retour arrière** testé en une commande, aller et retour.

## 2. Commits

| Commit | Objet |
|---|---|
| `d44e585` | D-039, D-040, backlog de détection (étape B) |
| `db7945c` | Analyseur reproductible et à jour (EXT-16, EXT-24) |
| `941fac6` | Base `app` épinglée, `libgl1` retiré (EXT-01, EXT-24, EXT-33) |
| `450f2ac` | Retrait de `presidio-anonymizer` (EXT-20) |
| `e3dd66d` | Épinglage de toutes les images, Keycloak 26.8.0, `docker-compose.build.yml` |
| `72ba130` | Image sans pytest ni tests, `requirements.lock`, `app/run-tests.sh` (EXT-09, D-016) |
| `7c5abc5` | Actions GitHub épinglées, étiquetage versionné (EXT-21) |
| `de62a81` | 403 sur identité absente (D-035) ; **contenait par erreur** la suppression d'`oauth2-proxy.cfg` (voir §7) |
| `ee7f684` | Rétablit `oauth2-proxy.cfg` (correction du commit précédent, sans réécriture d'historique) |
| `c485948` | uvloop et profil déployé documentés (EXT-11, EXT-12) |
| `71352c0` | Versions de détection dans `detection_config` (D-030) |
| `a161834` | Contrat FR/EN relu pour un client d'extension (étape G) |
| `69eeecb` | Caractères invisibles des tests, test anti-récidive (EXT-39) |
| `6711e4c` | oauth2-proxy : configuration unique, proxy de confiance, `whitelist_domains` (EXT-13, EXT-45, EXT-48) |
| `14f4211` | README FR/EN et note de dépendances (EXT-02 à EXT-04) |
| `5282b29`, `c9feaf3` | Colonnes en trop dans `FINDINGS.md` (erreur de script, voir §7) |
| `c1a60f5` | Test e2e : contrôle des fichiers finaux |
| `a9beed9` | `DEPENDENCIES.md` |
| `0fd2c28` | EXT-01, EXT-16, EXT-24, EXT-33 |
| `6110989` | Surcharge de retour arrière |
| (ce commit) | Compte rendu et résultats des bancs |

## 3. Critères d'acceptation

| Critère | État |
|---|---|
| Aucune étiquette flottante ; images épinglées par condensat, dernières versions | ✅ images tierces et de base ; ⚠️ **écart** : `app` et analyseur en `0.2.0-dev` local sans condensat, faute de publication (D-040) — EXT-01 reste ouvert pour elles |
| Analyseur reproductible ; effet de la reconstruction rapporté | ✅ effet nul (§4) |
| Aucune HIGH/CRITICAL corrigeable dans les images finales | ✅ sauf docker-socket-proxy (6, Alpine, pas de version amont) — **écart** accepté par D-040 point 8 |
| Image de production sans outils ni tests ; méthode de test mise à jour | ✅ (`app/run-tests.sh`, D-016 mis à jour) |
| Actions GitHub épinglées, permissions minimales | ✅ (`actionlint` 1.7.12 : 0 constat ; non déclenché) |
| oauth2-proxy : configuration unique, proxy de confiance, retour sans redirection ouverte | ✅ vérifié de bout en bout (§5) |
| 403 sur identité vide | ✅ par tests (12 cas) ; non atteignable de bout en bout (Traefik pose toujours les en-têtes ; compte sans courriel refusé par oauth2-proxy) |
| Contrat FR/EN complet ; empreinte complète | ✅ |
| Backlog créé ; aucun changement de comportement de détection | ✅ qualité, secrets et zones identiques |
| EXT-39 corrigé avec contrôle anti-récidive ; README corrigés | ✅ |
| Retour arrière documenté et testé | ✅ |
| Suite verte sous seccomp ; aucun secret ni contenu dans les journaux | ✅ 483 réussis ; 633 valeurs recherchées, 0 trouvée |

## 4. Vérifications (sorties utiles)

**Tests** (`app/run-tests.sh`, image `0.2.0-dev` reconstruite sans cache, `app-enforce.json`, `--read-only`, `--network none`) :
`483 passed in 9.97s` (référence de départ : 460). Ajouts : 2 (anonymiseur), 14 (D-035), 4 (empreinte), 3 (hygiène). Chaque correctif a son
test écrit avant (échecs observés : 2, 12 × `assert 200 == 403`, 47 `TypeError`, 1 sur l'hygiène).

**Couverture** (hors seccomp) des lignes ajoutées : `text_api.py` 13/13, `main.py` 7/7, `text_normalization.py` 1/1, 0 branche partielle.

**Analyse statique** : `ruff check` et `ruff format --check` verts sur les fichiers créés ou modifiés (`main.py` : 22 constats, identiques au
début de la phase) ; `mypy --strict` : `Success` sur `text_api.py`, `text_api_models.py`, `text_normalization.py`, les deux tests nouveaux ;
`bandit` : 0 sur `text_api.py`, `text_normalization.py`, `metrics.py`, `main.py` les 3 mêmes constats qu'avant (2 × B324, 1 × B101).

**`pip-audit`** (environnements installés) : `No known vulnerabilities found` dans les deux images (`en_core_web_lg` non audité : absent de PyPI).

**`trivy` 0.75.0, images finales** :

| Image | Corrigeables | Sans correctif | Exposition des non corrigeables |
|---|---|---|---|
| app 0.2.0-dev | 0 | 76 HIGH, 1 CRITICAL | libxml2 (CVE-2026-6653) : non chargée par l'application (lxml embarque la sienne), chargée par tesseract via libarchive pour ses modèles seulement (supposé) ; tesseract : CVE documentées dans le Dockerfile (modèle `.traineddata` forgé requis) ; util-linux, curl, expat… : bibliothèques du système, aucun binaire concerné appelé par l'application (supposé) |
| analyseur 0.2.0-dev | 0 | 52 HIGH | paquets Debian de l'image Presidio ; réseau `backend` interne, seul `app` l'appelle |
| keycloak 26.8.0 | 0 | 6 HIGH | paquets RHEL ; Keycloak est le fournisseur d'identité **du lab** |
| oauth2-proxy 7.15.5, traefik 3.7.13, busybox 1.38.0 | 0 | 0 | — |
| docker-socket-proxy 0.5.0 | **6** | 0 | openssl et pcre2 d'Alpine ; HAProxy sur le réseau interne `docker-proxy`, seul Traefik l'appelle, sans TLS ; motifs pcre2 fixés par la configuration (supposé) |

**Bancs** (`benchmarks/results/`) :

| Mesure | Résultat |
|---|---|
| Qualité, image reconstruite (`quality-20261005T134945.md`, puis finale `quality-20261005T142449.md`) | **identiques ligne à ligne** à la référence `quality-20261005T071403.md` hors en-tête : masquage 0,973, 27 exposées, chevauchement P 0,883 / R 0,975, tous thèmes |
| Secrets (`secrets-20261005T133120.md`, `secrets-20261005T140631.md`) | 21/21, 0 faux positif, tous thèmes |
| Latence au repos (`latency-20261005T142935.md`) | p95 23,8 ms (200 car.), 47,2 ms (1 000), **75,4 ms (2 000, n = 61)** : objectif D-033 (< 100 ms sous 2 000 caractères) atteint ; 191,8 ms à 5 000 ; `/health` p95 5,2 ms pendant un document |
| Zones du flux documents (`doc-zones-compare-20261005T140839.txt`) | 16 cas (PDF, DOCX, CSV, image × 4 thèmes) : 0 perdue, 0 étendue, 0 ajoutée contre `doc-zones-20261005T065210.json` |
| Fichiers finaux (`e2e_document_flow.py`, contrôle ajouté) | 8 cas finalisés et téléchargés : 0 valeur sensible connue sur 14 dans le PDF, le DOCX et le CSV finaux (contre-épreuve sur les entrées : 7/7 trouvées) |
| Journaux et audit (`check_no_content_in_logs.py --since 2026-10-05T12:00:00`) | 633 valeurs recherchées, 0 trouvée (5 conteneurs, `audit.log`, `audit-extension.log`) |

`detection_config` passe de `89111ef1fbf07382` à `ed84b08ed089641b` : attendu, l'empreinte intègre désormais les versions (D-030).
`analyzer_recognizers` inchangé (`51fa36bee769596e`).

## 5. Revue sécurité

**Points vérifiés (observé)** :
- En-tête de transfert forgé depuis un autre conteneur d'`app-internal` : avant, oauth2-proxy journalisait l'adresse forgée `203.0.113.66` ;
  après, l'adresse réelle `10.89.18.132`. Forgé de l'extérieur vers Traefik : absent du journal (Traefik ne le transmet pas).
- Retour après connexion : `rd` interne absolu ou relatif honoré ; `https://evil.example/`, `//evil.example/`,
  `https://obfusk8.lab.local.evil.example/`, `https://evil.example/@obfusk8.lab.local` → `/`.
- Pages d'oauth2-proxy : 0 mention de version (EXT-44 tient avec 7.15.5), pas d'en-tête `Server`.
- Comptes 1 à 3 : connexion et `/api/v1/version` 200 ; compte sans courriel : refus inchangé (`email in id_token () isn't verified`), rien transmis.
- `app` sous `Seccomp: 2` avec l'image reconstruite (mises à jour Debian, sans `libgl1`) : suite, OCR et bout en bout verts, aucun appel
  système nouveau requis, profil inchangé.
- Image `app` : code dans `/app` appartenant à root (l'utilisateur d'exécution ne peut pas le modifier, `touch` refusé) ; plus de `pip`.
- Surcharge de retour arrière : `pull_policy: never` sur toutes les étiquettes locales (aucun espace de noms `obfusk8-local/` résolu sur un registre).

**Points ouverts** : images construites sans condensat jusqu'à publication (EXT-01) ; docker-socket-proxy (6 corrigeables amont) ; EXT-47
(repli « inconnu » du flux documents) ; EXT-05 (licence, à la fusion de `main`) ; PKCE non activé (avertissement d'oauth2-proxy :
`--code-challenge-method` ; option non listée à l'étape F, à décider : `code_challenge_method = "S256"`) ; mises à jour de dépendances de
production disponibles (fastapi, requests, uvicorn 0.54, websockets, spaCy 3.8.16 ; `DEPENDENCIES.md`).

**À différer au pentest** : contournement de `trusted_proxy_ips` (usurpation d'adresse sur le pont Docker), en-têtes `X-Forwarded-*` à
travers Traefik avec des encodages exotiques, analyse des chemins `skip-auth` plus stricts de 7.15.5 (aucune règle `skip-auth` configurée),
redirections après connexion (encodages, `\`, Unicode), robustesse du 403 face à des en-têtes dupliqués, Keycloak 26.8 (lab seulement).

## 6. Observé et supposé

| Observé | Supposé |
|---|---|
| Base de l'analyseur inchangée depuis la construction précédente (15 couches identiques) ; spaCy et PyYAML déjà dans la base | Les vulnérabilités sans correctif ne sont pas atteignables par le flux d'envoi (tesseract, libarchive, docker-socket-proxy) |
| Bancs de qualité, de secrets et de zones identiques après reconstruction | Les images publiées par la CI se comporteront comme les images locales (même Dockerfile ; à vérifier au premier push) |
| Modèle installé = même roue (empreinte de `direct_url.json`) | — |
| Collision de sous-réseau au premier déploiement de l'IP fixe | `10.89.18.0/24` ne rentre pas en conflit sur un autre hôte (documenté en dépannage FR/EN) |

## 7. Incidents

1. **Keycloak 26.8.0 refuse la base H2 de 26.0** (« Wrong user name or password »), 26 redémarrages : changement des identifiants par défaut de
   `dev-file`, migration non prise en charge (guide officiel). Arrêt et décision humaine : option 1, export du royaume avec 26.0 sur une copie
   restaurée depuis la sauvegarde, import dans 26.8.0 sur le volume neuf `keycloak-data-v26-8`. La tentative a **modifié** l'ancien volume
   `keycloak-data` (empreinte différente) : il n'est plus déclaré ni utilisé, la sauvegarde tar fait foi. Volumes laissés en place, non
   supprimés : `obfusk8_keycloak-data`, `obfusk8-kc-export-src` (copie de travail de l'export), à supprimer avec ton accord.
2. **Collision de sous-réseau** : le premier choix (`172.18.0.0/16`) a été attribué dynamiquement par Docker à un autre réseau de la pile au
   moment du `down`/`up` ; pile arrêtée environ une minute, corrigée par un sous-réseau hors des pools par défaut.
3. **Commit au contenu mélangé** (`de62a81`) : la suppression d'`oauth2-proxy.cfg`, mise en index plus tôt, a été embarquée dans le commit de
   D-035. Rétablie par `ee7f684` puis supprimée dans son propre commit (`6711e4c`), sans réécriture d'historique.
4. **Colonnes en trop dans `FINDINGS.md`** (script de mise à jour), corrigées par deux commits ; le tableau est désormais contrôlé (7 séparateurs
   par ligne hors code).
5. **`ENABLE_EXTENSION_API`** : la pile tournait avec `true` passé à la main lors des bancs de la phase 2 ; mon premier `up -d` l'a recréée avec
   la valeur par défaut `false`. Rétabli à `true` pour les mesures ; **la pile reste avec `ENABLE_EXTENSION_API=true`**.
6. L'outil d'écriture de fichiers a converti des séquences `‮` en caractères réels ; le test d'hygiène l'a détecté sur son propre fichier.

## 8. Retour arrière

Images d'avant la phase : `obfusk8-local:avant-2bis-{app,presidio-analyzer,traefik,keycloak,oauth2-proxy,docker-socket-proxy,
presidio-anonymizer,fix-app-dirs-perms}` (condensats : app `3f5dbf6c…`, analyseur `b7295b15…` = instantané du conteneur en service, l'image
d'origine `ce18c22c…` n'existant plus ; Traefik `24841fe2…`, Keycloak `09a381c7…`, oauth2-proxy `b1b2021f…`, socket-proxy `1f5038b5…`,
anonymiseur `e5670138…`, busybox `73aaf090…`). Sauvegarde Keycloak : `~/obfusk8-backups/keycloak-data-avant-2bis-20261005.tar`
(`b9651438…`, droits 600) ; export du royaume : `~/obfusk8-backups/keycloak-export/lab-realm.json` (contient des empreintes de mots de
passe, droits 600, hors dépôt).

```sh
docker compose -f docker-compose.yml -f docker-compose.rollback-2bis.yml up -d   # images d'avant, Keycloak 26.0 sur volume restauré
docker compose up -d                                                             # retour aux images de la phase
```

Testé le 2026-10-05 : retour arrière → Keycloak 26.0.8, comptes 1 à 3 connectés, `app` sous seccomp ; retour → comptes connectés.
La configuration reste celle de la phase ; un retour complet de configuration est `git checkout b96aa54 -- <fichiers>`.

## 9. À contrôler au premier push (workflow)

Le workflow ne se déclenche que sur une étiquette `vX.Y.Z` : un push de branche ne publie plus rien (ni `main`, ni `latest`). À la première
étiquette : les deux tâches résolvent les commits épinglés des actions ; les images sortent sous `X.Y.Z` et `sha-<commit>` ; le paquet GHCR
reste privé ou public selon ton choix ; relever alors les condensats publiés et les reporter dans `docker-compose.yml` (fin d'EXT-01).
Les anciennes étiquettes `main` et `latest` restent sur GHCR jusqu'à suppression manuelle.

## 10. AGPL-3.0, section 13 (proposition, non implémentée)

L'article 13 demande d'offrir le code source aux utilisateurs qui interagissent avec le programme à travers le réseau. Proposition :
- un lien « Code source » dans le bandeau de l'interface (`branding.py`), vers `https://github.com/EpicFail20/obfusk8`, avec la version
  déployée et le commit, fournis à la construction (`ARG` du Dockerfile, par exemple `OBFUSK8_VERSION=0.2.0-dev`, `OBFUSK8_COMMIT`) ;
- les mêmes champs dans `GET /api/v1/version` (champ ajouté : changement compatible du contrat), pour que l'extension puisse l'afficher ;
- un administrateur qui modifie le code doit pointer ce lien vers **sa** version : variable `SOURCE_URL` documentée.
Prérequis : EXT-05 (le fichier `LICENSE` contient la GPL-3.0, corrigé dans `main`, à intégrer à la fusion).

## 11. Proposition de mise à jour de `CLAUDE.md` §1 (non appliquée)

```diff
@@ -24,12 +24,13 @@
 
 ## 1. Particularités de ce dépôt
 
-Constatées dans le dépôt le 2 octobre 2026, complétées le 4 octobre 2026 (enseignements de la phase 1). **Revérifie-les en début de session** : si une ligne ci-dessous n'est plus vraie, signale-le et propose la mise à jour de ce fichier.
+Constatées dans le dépôt le 2 octobre 2026, complétées le 4 octobre 2026 (enseignements de la phase 1) et le 5 octobre 2026 (phases 2 et 2 bis). **Revérifie-les en début de session** : si une ligne ci-dessous n'est plus vraie, signale-le et propose la mise à jour de ce fichier.
 
 **Chaîne de requête** : Traefik → `oauth2-proxy` (forward auth, en-têtes `X-Auth-Request-User` / `X-Auth-Request-Email`) → `app`
 (FastAPI, port 8000) → `presidio-analyzer` (image construite depuis `presidio/analyzer-build`), appelé en HTTP sur le réseau interne `backend`.
-`presidio-anonymizer` n'est **pas** appelé pour anonymiser : `_anonymize_text` est du code mort, le service ne sert qu'au contrôle de santé (EXT-20).
-Le caviardage est fait par `app` elle-même.
+Le service `presidio-anonymizer` a été **retiré** (phase 2 bis, D-040 : jamais appelé, EXT-20) ; le caviardage est fait par `app` elle-même.
+Point d'entrée unique vers l'analyseur : `main._analyze_normalized` (normalisation D-014 avec table de correspondance). PyMuPDF ne traite
+qu'un document à la fois, sur un fil dédié (D-019) ; repli de localisation PDF par positions de caractères (D-026).
 
 **Secret de passerelle** (audit 3.7) : `_GatewaySecretMiddleware` vérifie un secret injecté par Traefik (`gateway-secret@file`).
 Toute route de l'application doit rester derrière ce mécanisme et derrière `oidc-auth`. Ne crée jamais de contournement.
@@ -75,13 +76,26 @@
 Le nom du projet Compose est supposé être `obfusk8` (label `traefik.docker.network=obfusk8_app-internal`) et les ports 80, 443 et 8080 sont fixes.
 **Ne lance jamais une seconde pile sur le même hôte** : elle partagerait le journal d'audit et les fichiers de travail, et casserait le routage.
 
+**Images et versions** (phase 2 bis, D-040) : toutes les images tierces sont épinglées par condensat dans `docker-compose.yml` ; les images
+construites (`app`, analyseur) portent une version (`0.2.0-dev`, **locale**, jamais publiée) et se construisent par
+`docker compose -f docker-compose.yml -f docker-compose.build.yml build` (Compose refuse une étiquette de construction avec condensat).
+Paquets Python de `app` : `app/requirements.lock` (empreintes) ; `pip` est retiré des images. Versions de l'analyseur déclarées dans
+`presidio/analyzer-build/versions.json` (vérifiées à sa construction) et copiées dans `app/analyzer_versions.json` (empreinte `detection_config`) :
+les deux copies doivent rester identiques. Retour arrière : `docker-compose.rollback-2bis.yml`.
+
+**Adresse fixe de Traefik** : `10.89.18.10` sur `app-internal` (`10.89.18.0/24`, hors des pools par défaut de Docker), seule source de confiance
+d'oauth2-proxy pour les en-têtes de transfert (`trusted_proxy_ips`). À changer aux trois endroits ensemble. Keycloak 26.8 sur le volume
+`keycloak-data-v26-8` (la base H2 de développement ne se migre pas d'une version à l'autre : export puis import du royaume).
+
 **Modules existants à réutiliser** : `antivirus.py` (ICAP), `supervision.py` (alertes, syslog), journal d'audit, `branding.py`,
 `text_api.py` (API texte, limiteur et file bornée).
 
-**Tests** : `pytest` est présent dans l'image de production et `tests/` y est copié (EXT-09, non corrigé). **Ne lance jamais la suite dans le
-conteneur `app`** : elle écrirait dans le vrai journal d'audit et les vrais fichiers de travail. Méthode (D-016) : conteneur jetable de l'image de
-production, `--security-opt seccomp=seccomp/app-enforce.json`, `--read-only`, `--network none`, `tmpfs` à la place des volumes, outils de
-`app/requirements-dev.txt` installés hors de l'image et montés en lecture seule. La couverture se mesure hors seccomp (base SQLite de `coverage`
+**Tests** : l'image de production ne contient ni `pytest` ni les tests (EXT-09 corrigé). **Ne lance jamais la suite dans le conteneur `app`** :
+elle écrirait dans le vrai journal d'audit et les vrais fichiers de travail. Méthode (D-016 mis à jour) : `app/run-tests.sh`, conteneur jetable de
+l'image de production sous `seccomp/app-enforce.json`, `--read-only`, `--network none`, `tmpfs`, `app/tests` et le dépôt montés en lecture seule,
+outils de `app/requirements-dev.txt` installés hors de l'image et montés en lecture seule ; le script refuse une image qui contiendrait `pytest`.
+Aucun caractère Unicode de format écrit tel quel dans un fichier (séquences d'échappement, test `test_repo_hygiene.py`, EXT-39) ; jetons de test
+au format réel via `app/tests/fake_secrets.py`. La couverture se mesure hors seccomp (base SQLite de `coverage`
 bloquée), la suite fonctionnelle sous seccomp.
 
 **Mémoire de l'hôte** (EXT-25) : `/tmp` est un tmpfs qui consomme la RAM de la VM, sans swap. Aucun fichier volumineux dedans (archive d'image,
@@ -175,7 +189,7 @@
    Jamais un blog ou une réponse de modèle comme source d'une CVE.
 4. **Santé** : dernière publication, maintenance active, obsolescence, **licence compatible avec celle du projet** (AGPL-3.0 selon le README ;
    vérifie le fichier `LICENSE`).
-5. **Compatibilité** : version de Python de l'image, cohérence des versions Presidio entre `presidio/analyzer-build` et l'image `presidio-anonymizer`,
+5. **Compatibilité** : version de Python de l'image, cohérence de `presidio/analyzer-build/versions.json` avec l'image de base et avec `app/analyzer_versions.json`,
    notes de version en cas de changement majeur.
 6. **Épinglage reproductible** avec empreintes si l'outillage le permet ; dépendances de production et de développement séparées.
 7. **Traçabilité** dans `docs/DEPENDENCIES.md` : paquet, version retenue, dernière stable observée, date et source, résultat d'audit, licence, remarque.
```

## 12. Décisions en attente

| # | Sujet | Recommandation |
|---|---|---|
| 1 | Appliquer le diff de `CLAUDE.md` (§11) | Oui |
| 2 | PKCE (`code_challenge_method = "S256"`, oauth2-proxy le signale à chaque démarrage) | Oui, avec un test de connexion |
| 3 | Mises à jour de dépendances de production (uvicorn 0.54 notamment, revalidation seccomp) | Phase dédiée, avec tests sous seccomp |
| 4 | Version d'API : rester en `1.0` malgré le 403 ajouté | Oui (cas qui ne survient pas avec une session normale) ; `1.1` si tu préfères le signaler |
| 5 | Suppression des volumes `obfusk8_keycloak-data` et `obfusk8-kc-export-src` | Après validation de la phase, avec ton accord |
| 6 | Publication : première étiquette `v0.2.0` et épinglage des condensats publiés | À ta décision |
| 7 | Validation de la phase | Avancer `feat/text-api` par `git merge --ff-only feat/durcissement`, puis pousser |
