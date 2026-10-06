# Phase 2 ter — mise à jour des dépendances : compte rendu

Branche locale `feat/dependances`, créée depuis `feat/text-api` (`0cec4c9`, identique à `origin/feat/text-api`), jamais poussée.
Date : 2026-10-06. VM : 4 vCPU, 8 919 Mo de RAM, pas de swap. Décisions humaines de la phase : D-043 (étape A).

## 1. Résumé

- **Toutes les dépendances de production à la dernière version stable vérifiée, sauf trois écarts justifiés** : FastAPI reste en 0.141.1
  (D-043 point 1, `opentelemetry-api` obligatoire et télémétrie active par défaut en 0.142) ; numpy et thinc sont plafonnés par leurs
  dépendants ; Python de l'analyseur inchangé (EXT-50, phase suivante).
- **Un correctif de sécurité** : Werkzeug 3.1.9 dans l'analyseur (GHSA-g6x2-hccm-hh4m, apparu le 2026-10-05).
- **Analyseur entièrement verrouillé** : 55 paquets et 2 modèles déclarés avec empreintes ; la construction échoue si l'environnement
  installé diffère (`check_lock.py`).
- **Confiance dans les en-têtes de proxy renforcée** : uvicorn lancé avec `--no-proxy-headers` ; test qui échouait avec uvicorn 0.35
  (un `X-Forwarded-For` venu de la boucle locale remplaçait l'adresse du client), vert après.
- **Effet sur la détection : nul.** Qualité, secrets et zones du flux documents identiques aux références ; seule l'empreinte
  `detection_config` change (version de spaCy).
- **Aucun appel système ajouté** au profil seccomp.
- **Procédure de maintenance** FR/EN, outils `tools/dependencies/`, cadence proposée.
- Retour arrière préparé et testé ; API texte désactivée en fin de phase.

## 2. Commits

| Commit | Objet |
|---|---|
| `7a458c6` | Inventaire à jour (étape A) |
| `47902bb` | D-043 (décisions de l'étape A) |
| `d841cf6` | Surcharge de retour arrière (étape B) |
| `f0bd015` | Lot 0 : base `python:3.12-slim` reconstruite |
| `14e9fbd` | Lot 1 : uvicorn 0.54.0, websockets 17.2, `--no-proxy-headers`, test de confiance |
| `6e10568` | Lot 2 : requests 2.34.2 |
| `ca05792` | Lot 4 : analyseur, service, verrou complet, `check_lock.py` |
| `e6d5f9a` | Lot 5 : analyseur, détection |
| `1dcd3b0` | Lot 6 : outils de développement |
| `a95823b` | Procédure de maintenance FR/EN et outils (étape D) — **commit de construction des images finales** |
| `74b8d0b` | Tests complémentaires (couverture) |
| `b53ad4d` | EXT-50 à EXT-52 |
| `59ae7e0` | `DEPENDENCIES.md` |
| (ce commit) | Compte rendu, résultats finaux des bancs |

Lot 3 (FastAPI) : non appliqué (D-043). Lot 7 (images tierces) : rien à appliquer, condensats inchangés.

## 3. Tableau par lot

| Lot | Versions avant → après | Vérifications | Effet sur la détection | Appels système ajoutés |
|---|---|---|---|---|
| 0 Base `app` | `python:3.12-slim` `02108f5d…` → `ddb0207a…` (3.12.15 des deux côtés) | `dpkg-query` identique ; 483 tests ; bout en bout documents ; zones identiques | Aucun (non concerné) | 0 |
| 1 Serveur HTTP | uvicorn 0.35.0 → 0.54.0 ; websockets 17.1 → 17.2 ; `--no-proxy-headers` | Test écrit avant (3 échecs observés) ; 486 tests ; trace sous `app-audit.json` (texte 29/29, documents, redémarrage) ; `X-Forwarded-For` forgé de trois sources ; latence | Aucun (non concerné) | 0 (journalisés : `io_uring_setup`, `io_uring_enter`, `openat2`, déjà connus) |
| 2 Clients | requests 2.33.1 → 2.34.2 | 486 tests ; texte 29/29 ; documents 0 valeur exposée | Aucun (non concerné) | 0 |
| 3 FastAPI | 0.141.1 (inchangé) | — | — | — |
| 4 Analyseur, service | 22 paquets, dont Werkzeug 3.1.9, gunicorn 26.2.0, filelock 4.0.12, setuptools 84.0.0, pydantic 2.13.5 ; verrou complet | `check_lock` 57 distributions ; 3 cas négatifs en échec ; gunicorn démarré ; bancs ; zones ; documents | **Nul** : qualité et secrets identiques ligne à ligne (en-tête compris hors date) | — (pas de profil seccomp sur l'analyseur) |
| 5 Analyseur, détection | spaCy 3.8.13 → 3.8.16, regex 2026.7.10 → 2026.9.29, phonenumbers 9.0.34 → 9.0.40, tldextract 5.3.1 → 5.4.0 | 486 tests (72 de reconnaisseurs avec regex 2026.9.29) ; garde-fou ; zones ; documents | **Nul** : masquage 0,973, secrets 21/21 ; `detection_config` `ed84b08ed089641b` → `1a04111540221092` | — |
| 6 Outils | regex (avec le lot 5), ast_serialize 0.12.1, filelock 4.0.12, platformdirs 4.12.3 | ruff, mypy, bandit, pip-audit | — | — |
| 7 Images tierces | inchangées (dernières versions, condensats identiques) | trivy | — | — |

## 4. Vérifications finales (sorties utiles)

Images finales construites sans cache au commit `a95823b` : app `73b5a666…` (541 Mo, contre 553 Mo avant la phase), analyseur `878fa63b…` (2,03 Go contre 1,74 : la mise à jour de spaCy et des paquets réinstallés ajoute une couche
par-dessus la base ; disparaîtra avec la reconstruction de l'analyseur sur une base Python, D-043 point 5).

**Tests** (`app/run-tests.sh`, `app-enforce.json`, `--read-only`, `--network none`) : `502 passed in 11.17s` (483 au départ ; + 3
confiance des en-têtes, + 16 outils de dépendances).

**Couverture** (hors seccomp) du code nouveau : `tools/dependencies/outdated.py` 100 %, `lock_from_report.py` 100 %,
`presidio/analyzer-build/check_lock.py` 97 % (point d'entrée seul, exercé par chaque construction).

**Analyse statique** (outils du verrou de développement mis à jour) : `ruff check` et `ruff format --check` verts, `mypy --strict`
`Success`, `bandit` 0 constat sur tous les fichiers Python créés (`check_lock.py`, `outdated.py`, `lock_from_report.py`,
`test_dependency_tools.py`, `test_proxy_headers_trust.py`). Aucun fichier Python existant de l'application modifié.

**pip-audit** (environnements installés, OSV) : `No known vulnerabilities found` dans les deux images ; `requirements-dev.txt` idem.

**trivy 0.75.0** (images finales) : app 0 corrigeable (76 HIGH, 1 CRITICAL sans correctif, inchangé) ; analyseur 0 corrigeable (52 HIGH
sans correctif) ; traefik, oauth2-proxy, busybox 0 ; keycloak 6 HIGH sans correctif ; docker-socket-proxy **6 corrigeables** (D-040
point 8, aucune version amont).

**Bancs** (`benchmarks/results/`) :

| Mesure | Résultat |
|---|---|
| Qualité (lot 4 `quality-20261006T071941.md`, lot 5 `quality-20261006T074946.md`, finale : voir §4 bis) | Identique ligne à ligne à `quality-20261005T142449.md` hors en-tête : masquage **0,973**, 27 exposées |
| Secrets (`secrets-20261006T070456`, `…073501`, finale `…081526`) | 21/21, 0 faux positif, tous thèmes ; identique hors en-tête |
| Zones du flux documents (`doc-zones-compare-20261006T062737`, `…070043`, `…073519`, finale `…082947`) | 16 cas : 0 perdue, 0 étendue, 0 ajoutée contre `doc-zones-20261005T065210.json` |
| Fichiers finaux (`e2e_document_flow.py`, à chaque lot et en final) | 8 cas finalisés : 0 valeur sensible connue exposée (6 cas contrôlables, 2 images) |
| API texte (`e2e_text_api.py`, lots 1, 2 et final) | 29/29 |
| Latence au repos (`latency-20261006T081146.md`, final) | p95 22,3 ms (200 car.), 45,5 (1 000), **76,1 ms (2 000, n = 61)** : objectif D-033 tenu ; 180,0 à 5 000 ; `/health` p95 9,0 ms pendant un document |

## 4 bis. Résultats finaux (images `73b5a666…` et `878fa63b…`)

- Qualité `quality-20261006T083012.md` : identique ligne à ligne à `quality-20261005T142449.md` hors en-tête (date, empreinte
  `1a04111540221092`) : masquage **0,973**. Secrets `secrets-20261006T081526.md` : identique hors en-tête, 21/21, 0 faux positif.
- `check_no_content_in_logs.py --since 2026-10-06T05:00:00` : 5 conteneurs (de 116 à 32 799 lignes), `audit.log` (157 lignes),
  `audit-extension.log` (20 672 lignes) : **633 valeurs recherchées, 0 trouvée**.
- Mot de passe des comptes de test (incident 1) : 0 occurrence dans `git log -p 0cec4c9..HEAD`, 0 fichier de l'arbre de travail
  (recherche faite sans afficher la valeur).
- Fin de phase : `.env` `ENABLE_EXTENSION_API=false`, pile redéployée sans surcharge d'environnement ; dans `app` :
  `ENABLE_EXTENSION_API=false`, `Seccomp: 2` ; `/api/v1/version` → 401 sans session, 404 avec session ; `/` 200, `/health` 200.

## 5. Revue sécurité

**Points vérifiés (observé)** :
- `X-Forwarded-For` forgé via Traefik (`203.0.113.66`), depuis un autre conteneur d'`app-internal` (`203.0.113.77`) et depuis la boucle
  locale du conteneur `app` (`203.0.113.88`) : journal d'accès d'uvicorn → adresses réelles (`10.89.18.10`, `10.89.18.132`,
  `127.0.0.1`), aucune valeur forgée. Test unitaire sur la commande du Dockerfile interprétée par l'uvicorn de l'image (en-têtes simples et
  dupliqués, `Forwarded`, quatre pairs dont Traefik et `::1`).
- `app` sous `Seccomp: 2` après chaque déploiement ; trace sous `app-audit.json` pendant les scénarios texte, documents et redémarrage :
  seuls `io_uring_setup` (425), `io_uring_enter` (426, libuv, preuve qu'uvloop est bien utilisé) et `openat2` (437, runc), déjà connus.
- Verrou de l'analyseur : construction refusée pour un paquet non déclaré, une entrée sans empreinte ou un modèle d'une autre empreinte.
- FastAPI 0.142 lu dans le code avant décision : exportation OTLP déclenchable par variables d'environnement si le SDK est présent.
- gunicorn 26 : socket de contrôle `gunicorn.ctl` déjà présente en 25.3 (même module `gunicorn/ctl/server.py`), sur le tmpfs existant.
- Aucun contenu dans les journaux ni l'audit : voir §4 bis.

**Points ouverts** : EXT-51 (quota global de tâches en attente, déni de service par un utilisateur authentifié, **observé**) ; EXT-50
(Python 3.12.13 dans l'analyseur) ; docker-socket-proxy (6 corrigeables amont) ; FastAPI à réexaminer à chaque cycle ; gunicorn 26.2.1
et 26.2.2 (durcissements HTTP/1) non publiés sur PyPI ; couche supplémentaire de l'analyseur (taille).

**À différer au pentest** : contrebande de requêtes et en-têtes malformés entre Traefik et gunicorn 26 (nouvelles règles de rejet
HTTP/1.1) puis uvicorn 0.54 ; saturation du quota de tâches (EXT-51) dans une configuration à plusieurs utilisateurs derrière un NAT.

## 6. Observé et supposé

| Observé | Supposé |
|---|---|
| Aucune différence de paquets Debian entre la base reconstruite et l'ancienne | La reconstruction de l'étiquette `python:3.12-slim` ne change que des métadonnées ou des fichiers hors paquets |
| Les quatre mises à jour du lot 5 ne changent aucun résultat des bancs | Elles ne changent rien non plus hors du corpus (spaCy 3.8.14-16 : aucun commit sur le NER ; phonenumbers et tldextract : données et cas limites) |
| Werkzeug 3.1.8 visé par GHSA-g6x2-hccm-hh4m | Non exploitable sous Linux (condition Windows et NTFS de l'avis) |
| L'analyseur n'utilise ni XML, ni HTML, ni archive | Exposition faible aux correctifs de Python 3.12.14 et 3.12.15 (EXT-50) ; non vérifié pour `unicodedata.normalize()` |

## 7. Incidents

1. **Mot de passe des comptes de test affiché dans la session.** En lisant `~/.obfusk8-test-accounts` à l'étape B, mon filtre de masquage
   ne correspondait pas au format du fichier (`libellé, mot de passe`) : le mot de passe commun des comptes de test s'est affiché dans la
   sortie de la session. Il n'a été écrit dans aucun fichier du dépôt, aucun journal, aucun commit (vérifiable : `git log -p` de la
   branche). Ensuite, lecture uniquement par un script qui exporte les variables sans rien afficher. Recommandation : changer ce mot de
   passe de laboratoire (même nature qu'EXT-36).
2. **Premier lancement des bancs du lot 4 sans identifiant.** Script lancé par `sh` (dash) : un fichier lu par `.` n'y reçoit pas
   d'arguments, l'identifiant était vide (`user_not_found` dans Keycloak). `login()` du client des bancs ne l'a pas vu (EXT-52). Relancé
   sous bash.
3. **Version de gunicorn de l'étape A erronée** : 26.2.2 citée depuis GitHub alors que PyPI publie au plus 26.2.0 ; corrigé dans
   l'inventaire (commit du lot 4).
4. **Test texte final lancé avec le compte sans courriel** (refus attendu d'oauth2-proxy, EXT-43) ; relancé avec un autre compte.
5. **503 sur `/api/detect` pendant l'instantané final** : le banc de latence et l'instantané ont ensemble atteint le quota global de 20
   tâches ; c'est ce qui a révélé EXT-51. Relancé après expiration des tâches.
6. **Licence de filelock** écrite « Unlicense » de mémoire dans le brouillon de `DEPENDENCIES.md`, corrigée en MIT après vérification sur
   PyPI, avant commit.

## 8. Retour arrière

Images d'avant la phase : `obfusk8-local:avant-2ter-{app,presidio-analyzer,traefik,keycloak,oauth2-proxy,docker-socket-proxy,
fix-app-dirs-perms}` (app `58540012…`, analyseur `e07bd4f2…`, traefik `24841fe2…`, keycloak `b0f60d48…`, oauth2-proxy `8498b0d0…`,
socket-proxy `1f5038b5…`, busybox `fd7dc986…`). Outils de développement d'avant : `~/.cache/obfusk8-devtools.avant-2ter`.

```sh
docker compose -f docker-compose.yml -f docker-compose.rollback-2ter.yml up -d   # images d'avant la phase
docker compose up -d                                                             # retour aux images de la phase
```

Testé le 2026-10-06 avant le premier lot : images locales en service, `Seccomp: 2`, comptes 1 à 3 connectés, `/health` 200,
`/api/v1/version` 404 (API désactivée) ; retour : mêmes contrôles. Un lot seul : `git revert <commit du lot>` puis reconstruction.

## 9. Préparation de la phase suivante : version de Python (D-043 point 5, sans modification)

Dernières versions publiées (python.org, 2026-10-06) : **3.14.8** (2026-09-30), 3.13.16 (2026-09-30), 3.12.15 (2026-09-30) ; 3.15 en
version candidate (3.15.0rc3, 2026-10-02), non stable.

Roues Linux x86_64 publiées sur PyPI pour les versions des verrous (ou celle visée) des dépendances compilées :

| Paquet (version) | Image | cp313 | cp314 | cp315 | `requires_python` |
|---|---|---|---|---|---|
| spaCy 3.8.16 | analyseur | oui | oui | non | `<3.15,>=3.9` |
| thinc 8.3.13 | analyseur | oui | oui | non | `<3.15,>=3.10` |
| blis 1.3.3 | analyseur | oui | oui | non | `<3.15,>=3.9` |
| numpy 2.4.6 | analyseur | oui | oui | non | `>=3.11` |
| cymem 2.0.13, preshed 3.0.13, murmurhash 1.0.15, srsly 2.5.4 | analyseur | oui | oui | non | `<3.15` |
| pydantic-core 2.46.5 | les deux | oui | oui | non | `>=3.9` |
| MarkupSafe 3.0.4, wrapt 2.5.0, regex 2026.9.29 | analyseur | oui | oui | oui | — |
| PyMuPDF 1.28.2 | app | oui (`abi3` cp310) | oui | non | `>=3.10` |
| lxml 6.1.3, Pillow 12.3.0, uvloop 0.23.0 | app | oui | oui | oui | — |
| httptools 0.8.0, PyYAML 6.0.3 | app | oui | oui | non | — |
| watchfiles 1.3.0 | app | oui (`abi3` cp310) | oui | non | `>=3.10` |
| presidio-analyzer 2.2.364 | analyseur | oui | oui | non | `<3.15,>=3.10` (spaCy 3.8.14 exclu sous 3.14 seulement) |

**Version la plus récente prise en charge par toutes les dépendances : Python 3.14 (3.14.8).** Python 3.15 est exclu par Presidio et
spaCy (`<3.15`) et par l'absence de roues de la pile spaCy. Le passage à 3.14 imposera la revalidation du profil seccomp sur le nouvel
interpréteur (D-040 point 2) et des bancs complets.

## 10. Proposition de mise à jour de `CLAUDE.md` §1 (non appliquée)

```diff
-Constatées dans le dépôt le 2 octobre 2026, complétées le 4 octobre 2026 (enseignements de la phase 1) et le 5 octobre 2026 (phases 2 et 2 bis).
+Constatées dans le dépôt le 2 octobre 2026, complétées le 4 octobre 2026 (enseignements de la phase 1), le 5 octobre 2026 (phases 2 et 2 bis) et le 6 octobre 2026 (phase 2 ter).
@@ Images et versions
-Paquets Python de `app` : `app/requirements.lock` (empreintes) ; `pip` est retiré des images. Versions de l'analyseur déclarées dans
+Paquets Python de `app` : `app/requirements.lock` (empreintes) ; de l'analyseur : `presidio/analyzer-build/requirements.lock` (environnement
+complet, contrôlé à la construction par `check_lock.py`) ; `pip` est retiré des images. Mise à jour : `docs/maintenance-dependances.md`
+(outils `tools/dependencies/`). FastAPI volontairement en 0.141.1 (D-043). Versions de l'analyseur déclarées dans
 `presidio/analyzer-build/versions.json` (vérifiées à sa construction) et copiées dans `app/analyzer_versions.json` (empreinte `detection_config`) :
-les deux copies doivent rester identiques. Retour arrière : `docker-compose.rollback-2bis.yml`.
+les deux copies doivent rester identiques. Retour arrière : `docker-compose.rollback-2ter.yml` (et `-2bis`).
+**uvicorn sans en-têtes de proxy** (`--no-proxy-headers`, D-043, `test_proxy_headers_trust.py`) : si l'adresse du client devient
+nécessaire, seule configuration acceptable : `forwarded_allow_ips` limité à l'adresse fixe de Traefik.
@@ Tests
+**Bancs** : le quota de tâches en attente (`MAX_PENDING_JOBS=20`, 600 s) est **global** (EXT-51) : bancs de documents en série, pas en
+parallèle ; le quatrième compte de test est le compte sans courriel (refusé par oauth2-proxy) ; scripts de bancs sous bash.
```

## 11. Décisions en attente

| # | Sujet | Recommandation |
|---|---|---|
| 1 | Appliquer le diff de `CLAUDE.md` (§10) | Oui |
| 2 | Cadence de maintenance (`docs/maintenance-dependances.md` §7) | Revue mensuelle, urgence sous 24 h ouvrées / 72 h |
| 3 | EXT-51 (quota global de tâches) | Phase dédiée ou phase 3 : quota par utilisateur et limite Traefik par `X-Auth-Request-User` |
| 4 | Mot de passe des comptes de test du laboratoire | Le changer (incident 1) |
| 5 | Suppression des images `avant-2ter` et de `~/.cache/obfusk8-devtools.avant-2ter` | Après validation de la phase |
| 6 | Validation de la phase | `git merge --ff-only feat/dependances` sur `feat/text-api`, puis push |
