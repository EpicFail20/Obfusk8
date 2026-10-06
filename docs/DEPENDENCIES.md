# Traçabilité des dépendances

Registre tenu selon `CLAUDE.md` §6 : toute dépendance ajoutée, mise à jour ou touchée y figure, avec la dernière version stable observée
**à la source** au moment du travail, le résultat d'audit et la licence. Licence du projet : en-têtes et README annoncent l'AGPL-3.0,
le fichier `LICENSE` contient la GPL-3.0 (EXT-05) ; toutes les licences ci-dessous (MIT, Apache-2.0) sont compatibles avec l'une et l'autre.

## Phase 1 — API texte (2026-10-02)

### Production (image `app`)

**Aucune dépendance de production ajoutée ni mise à jour.** Le nouveau code (`app/text_api.py`, `app/text_api_models.py`) utilise directement
deux paquets déjà présents dans l'image comme dépendances de FastAPI ; ils sont listés ici parce qu'ils sont désormais « touchés ».
Aucune revalidation du profil seccomp n'est donc due à une dépendance (le nouveau chemin de code a été testé sous `app-enforce.json`).

| Paquet | Version dans l'image | Dernière stable observée (PyPI, 2026-10-02) | Audit | Licence | Remarque |
|---|---|---|---|---|---|
| anyio | 4.15.1 | 4.15.1 (publiée le 2026-09-05) | OSV : aucune vulnérabilité | MIT | Dépendance de Starlette ; `CapacityLimiter`, `to_thread`, `fail_after` |
| pydantic | 2.13.5 | 2.13.5 (publiée le 2026-08-28) | OSV : aucune vulnérabilité | MIT | Dépendance de FastAPI ; modèles stricts du contrat |
| pydantic-core | 2.46.5 | — (épinglée par pydantic) | OSV : aucune vulnérabilité | MIT | |

Audit de l'environnement installé dans l'image reconstruite (`pip-audit --path /usr/local/lib/python3.12/site-packages`, service OSV,
37 distributions) : **une seule** distribution vulnérable, `pip` 25.0.1, fournie par l'image de base `python:3.12-slim`
(12 identifiants PYSEC, corrigés à partir de 26.2) — préexistante, hors périmètre, consignée en EXT-24.

### Développement (jamais dans l'image)

Fichiers : `app/requirements-dev.in` (versions de premier niveau) et `app/requirements-dev.txt` (verrou complet, 44 paquets, empreintes SHA-256,
généré par `pip install --dry-run --report` avec le Python 3.12 de l'image ; méthode en tête du fichier). Installation uniquement dans un
environnement isolé : `pip install --require-hashes --no-deps -r requirements-dev.txt` (décision D-016).

| Paquet | Version retenue | Dernière stable observée (PyPI, 2026-10-02) | Audit | Licence | Remarque |
|---|---|---|---|---|---|
| ruff | 0.16.10 | 0.16.10 (2026-10-01) | pip-audit (OSV) : aucune vulnérabilité sur le verrou complet | MIT | Lint et format, fichiers nouveaux seulement |
| mypy | 2.4.0 | 2.4.0 (2026-10-01) | idem | MIT | Strict sur les modules nouveaux |
| bandit | 1.9.4 | 1.9.4 (2026-02-25) | idem | Apache-2.0 | |
| pytest | 9.1.1 | 9.1.1 (2026-06-19) | idem | MIT | Même version que celle présente dans l'image (EXT-09) |
| pytest-cov | 7.1.0 | 7.1.0 (2026-03-21) | idem | MIT | |
| coverage | 7.16.2 | 7.16.2 (2026-09-27) | idem | Apache-2.0 | Sa base SQLite échoue sous `app-enforce.json` : couverture mesurée sans seccomp, suite fonctionnelle sous seccomp |
| pip-audit | 2.10.1 | 2.10.1 (2026-06-10) | idem | Apache-2.0 | |
| regex | 2026.7.10 | 2026.9.29 (2026-09-29) — **non retenue** (D-017) | OSV : aucune vulnérabilité pour 2026.7.10 | Apache-2.0 AND CNRI-Python | Même version que dans `presidio-analyzer`, pour tester les motifs avec le même moteur |

Sources : API JSON de PyPI (`https://pypi.org/pypi/<paquet>/json`, champ `info.version`, hors préversions), API OSV (`https://api.osv.dev/v1/query`).

### Outils d'analyse exécutés hors dépôt (étape F)

| Outil | Version retenue | Dernière stable observée (2026-10-02) | Source | Remarque |
|---|---|---|---|---|
| trivy | 0.75.0, image `aquasec/trivy@sha256:af6acf9a6b85dfe389a1941505c0ce9efef52a4719635e1a962f022a3d855daa` | 0.75.0 (publiée le 2026-10-01) | API GitHub des versions d'aquasecurity/trivy ; condensat par `docker buildx imagetools inspect` | Conteneur jetable, mémoire limitée à 800 Mo, cache et archive d'image sur disque (pas dans le tmpfs `/tmp`, voir EXT-25). Résultat : EXT-33 |

## Phase 2 — serveur prêt pour un pilote (2026-10-04)

### Production (image `app`)

**Aucune dépendance de production ajoutée ni mise à jour.** Le nouveau module `app/text_normalization.py` n'utilise que la bibliothèque
standard (`unicodedata`, `re`, `dataclasses`) ; le fil d'exécution dédié aux documents utilise `concurrent.futures` et `asyncio` (bibliothèque
standard). Aucun appel système ajouté au profil seccomp : suite de tests et bouts en bout exécutés sous `app-enforce.json`.

Audit de l'environnement installé de l'image de test (image de référence de la phase, seuls les fichiers modifiés remplacés ;
`pip-audit --path /usr/local/lib/python3.12/site-packages --vulnerability-service osv`, 2026-10-04) : une seule distribution vulnérable,
`pip` 25.0.1 de l'image de base (12 identifiants PYSEC), inchangé depuis la phase 1 (EXT-24, hors périmètre).

### Développement (jamais dans l'image)

Verrou `app/requirements-dev.txt` inchangé. `pip-audit -r app/requirements-dev.txt --no-deps` (service OSV, 2026-10-04) : aucune vulnérabilité connue.

### Outils et artefacts de mesure exécutés hors dépôt

| Élément | Version retenue | Dernière stable observée (2026-10-04) | Source | Remarque |
|---|---|---|---|---|
| trivy | 0.75.0, `aquasec/trivy@sha256:af6acf9a6b85dfe389a1941505c0ce9efef52a4719635e1a962f022a3d855daa` | 0.75.0 (publiée le 2026-10-01) | API GitHub des versions d'aquasecurity/trivy ; condensat par `docker buildx imagetools inspect` | Conteneur jetable, mémoire limitée à 800 Mo, archive et cache sur disque. Image `app` de test : 79 HIGH/CRITICAL dans les paquets Debian (EXT-33, 1 corrigeable), 0 dans les paquets Python |
| presidio-analyzer (roue PyPI) | 2.2.364, `presidio_analyzer-2.2.364-py3-none-any.whl`, SHA-256 `0a9eeb60ccc416c505367b4989d056becb84ef082703ee3361c046fb75941739` | 2.2.364 (API JSON de PyPI) | API JSON de PyPI, empreinte vérifiée au téléchargement | **Pas une dépendance** : seul `spacy_recognizer.py` en est extrait, pour l'image de mesure d'EXT-18 (sans filtre PROPN, sans étiquette, jamais poussée). Licence MIT (métadonnées de la roue : `License-Expression: MIT`) |

## Phase 2 bis — chaîne d'approvisionnement (2026-10-05)

Sources interrogées le 2026-10-05 : API GitHub (versions publiées, hors préversions), Docker Hub, quay.io, ghcr.io (`docker buildx imagetools
inspect` pour les condensats d'index), python.org, API JSON de PyPI, OSV. Licences : champ `license` de l'API GitHub, métadonnées des paquets.
Scans : `trivy` 0.75.0 (dernière version, 2026-10-01), HIGH et CRITICAL ; `pip-audit` 2.10.1 sur les environnements installés.

### Images

| Image | Version retenue | Condensat (index) | Dernière stable observée | trivy (image finale) | Licence | Remarque |
|---|---|---|---|---|---|---|
| `traefik` | v3.7.13 | `sha256:24841fe2…` | v3.7.13 (2026-09-04) | 0 | MIT | Inchangée, épinglée |
| `quay.io/keycloak/keycloak` | 26.8.0 | `sha256:b0f60d48…` | 26.8.0 (2026-10-01) | 6 HIGH sans correctif | Apache-2.0 | Depuis 26.0.8 (90 corrigeables dont CVE-2026-18963, prise de contrôle de compte) ; base H2 non migrable, royaume réimporté (D-040) |
| `quay.io/oauth2-proxy/oauth2-proxy` | v7.15.5 | `sha256:8498b0d0…` | v7.15.5 (2026-10-01) | 0 | MIT | Depuis v7.15.4 : GHSA-63jm-59jj-478j, GHSA-wr5q-7wxw-x568 |
| `tecnativa/docker-socket-proxy` | v0.5.0 | `sha256:1f5038b5…` | v0.5.0 (2026-07-27) | **6 HIGH corrigeables** (Alpine : openssl 3.5.7, pcre2 10.47) | Apache-2.0 | Pas de version amont plus récente ; accepté et surveillé (D-040, point 8) |
| `busybox` | 1.38.0 | `sha256:fd7dc986…` | 1.38.0 (2026-09-23) | 0 | GPL-2.0 | Tâche ponctuelle `chown`, aucun lien avec le code du projet |
| `python` (base de `app`) | 3.12-slim, Python 3.12.15, Debian 13 | `sha256:02108f5d…` | 3.12.15 (python.org, 2026-09-30) ; 3.14.8 existe | — | PSF-2.0 | 3.12 conservé (D-040, point 2) |
| `ghcr.io/data-privacy-stack/presidio-analyzer` (base de l'analyseur) | 2.2.364 | `sha256:ae8f6f11…` | 2.2.364 (2026-07-22) ; `2.2.364-distroless-preview` exclue (préversion) | — | MIT | Dépôt officiel (anciennement `microsoft/presidio`, même identifiant 132129752) |
| `ghcr.io/epicfail20/obfusk8-app` | 0.2.0-dev (local) | — (jamais publiée) | — | 77 sans correctif (dont 1 CRITICAL, libxml2) | AGPL-3.0 (en-têtes ; `LICENSE` : EXT-05) | 553 Mo (851 avant) |
| `ghcr.io/epicfail20/obfusk8-presidio-analyzer` | 0.2.0-dev (local) | — | — | 52 sans correctif | idem | |
| `ghcr.io/data-privacy-stack/presidio-anonymizer` | **retirée** | — | 2.2.364 | (113 HIGH/CRITICAL avant retrait) | MIT | Jamais appelée (EXT-20, D-040) |

### Paquets ajoutés ou touchés à la construction

| Paquet | Version | Empreinte | Dernière stable observée | Audit | Licence | Remarque |
|---|---|---|---|---|---|---|
| pip (construction seulement) | 26.2.1 | `sha256:71138adf…` | 26.2.1 (2026-08-04) | OSV : aucune | MIT | Installe les verrous, puis **désinstallé** des deux images (EXT-24) |
| anyio (analyseur) | 4.15.1 | `sha256:6152fdbb…` | 4.15.1 | OSV : aucune | MIT | Remplace 4.14.1 (CVE-2026-63374, CVE-2026-63349) |
| urllib3 (analyseur) | 2.8.0 | `sha256:0cf3cae5…` | 2.8.0 | OSV : aucune | MIT | Remplace 2.7.0 (CVE-2026-97687, CVE-2026-97689) |
| fr_core_news_md (analyseur) | 3.8.0 | `sha256:8a70d090…` | 3.8.0, seule version pour spaCy 3.8 | OSV : aucune | **LGPL-LR** | Même artefact qu'avant (`direct_url.json`) ; licence des ressources linguistiques, inchangée, à noter pour la conformité |
| uv (analyseur) | retiré | — | — | — | — | Binaire de construction de l'image amont (quinn-proto, rustls-webpki) |
| `app/requirements.lock` | 32 paquets, versions **inchangées** | toutes | voir ci-dessous | `pip-audit` : aucune | — | Ensemble transitif de `requirements.txt` rendu explicite, `--require-hashes --no-deps` |

Versions plus récentes publiées, **non appliquées** (mise à jour de dépendances de production non autorisée dans cette phase ; aucune
vulnérabilité connue dans les versions en place) : fastapi 0.142.2 (0.141.1), requests 2.34.2 (2.33.1), uvicorn 0.54.0 (0.35.0 ; touche
uvloop, revalidation seccomp nécessaire, EXT-11), websockets 17.2 (17.1), spaCy 3.8.16 (3.8.13, fournie par l'image Presidio ; changerait
la détection). À décider.

### Outils (jamais dans les images)

| Outil | Version | Condensat | Licence |
|---|---|---|---|
| `aquasec/trivy` | 0.75.0 | `sha256:af6acf9a…` | Apache-2.0 |
| `rhysd/actionlint` | 1.7.12 (2026-03-30) | `sha256:b1934ee5…` | MIT |

### Actions GitHub

| Action | Version | Commit | Licence |
|---|---|---|---|
| actions/checkout | v7.0.1 | `3d3c42e5…` | MIT |
| docker/login-action | v4.6.0 | `dbcb8138…` | Apache-2.0 |
| docker/metadata-action | v6.2.0 | `dc802804…` | Apache-2.0 |
| docker/build-push-action | v7.4.0 | `c3c9e263…` | Apache-2.0 |

## Phase 2 ter — mise à jour des dépendances (2026-10-06)

Méthode : `docs/maintenance-dependances.md`. Inventaire complet : `docs/phase-2-ter-dependances.md`. Sources interrogées le 2026-10-06 :
API JSON de PyPI (`tools/dependencies/outdated.py`), API OSV, versions GitHub et notes de version, `docker buildx imagetools inspect`,
python.org. Scans des images finales : `pip-audit` 2.10.1 (OSV) sur les environnements installés, `trivy` 0.75.0 (HIGH, CRITICAL).
**Aucune nouvelle dépendance**. Licences relues sur PyPI (`license_expression`, à défaut `license` ou classificateurs) pour chaque paquet
mis à jour : MIT (gunicorn, filelock, setuptools, cloudpathlib, spacy), BSD-3-Clause (Werkzeug, tldextract, uvicorn, websockets),
BSD-2-Clause (wrapt), Apache-2.0 (phonenumbers, requests), Apache-2.0 AND CNRI-Python (regex, inchangée) ; compatibles avec l'AGPL-3.0
et la GPL-3.0 (EXT-05).

### Production — image `app`

| Paquet | Avant | Retenue | Dernière stable observée | Audit | Remarque |
|---|---|---|---|---|---|
| python (base) | 3.12.15, `02108f5d…` | 3.12.15, `ddb0207a…` | 3.12.15 (étiquette reconstruite) | trivy : 0 corrigeable | Lot 0 ; aucun paquet Debian ni Python changé |
| uvicorn (`[standard]`) | 0.35.0 | **0.54.0** | 0.54.0 (2026-09-25) | OSV : aucune | Lot 1 ; `--no-proxy-headers` (D-043) ; aucun appel système nouveau |
| websockets | 17.1 | **17.2** | 17.2 (2026-10-03) | OSV : aucune | Lot 1 ; tirée par `uvicorn[standard]` |
| requests | 2.33.1 | **2.34.2** | 2.34.2 (2026-05-14) | OSV : aucune | Lot 2 |
| fastapi | 0.141.1 | 0.141.1 | 0.142.2 (2026-09-30) | OSV : aucune | **Écart** D-043 point 1 (`opentelemetry-api` obligatoire, télémétrie active par défaut) |
| pydantic-core | 2.46.5 | 2.46.5 | 2.49.0 | OSV : aucune | Épinglée par pydantic 2.13.5 (dernière) |
| 28 autres paquets | — | inchangés | déjà les dernières | OSV : aucune | `app/requirements.lock` |

`pip-audit --path /usr/local/lib/python3.12/site-packages` sur l'image finale (`73b5a666…`) : *No known vulnerabilities found*.

### Production — image de l'analyseur

Environnement entier déclaré dans `presidio/analyzer-build/requirements.lock` (55 paquets et 2 modèles, empreintes), contrôlé à la
construction par `check_lock.py` (D-043 point 3). Base Presidio 2.2.364 inchangée (dernière, même condensat).

| Paquet | Avant | Retenue | Dernière stable observée | Audit (version avant) | Remarque |
|---|---|---|---|---|---|
| Werkzeug | 3.1.8 | **3.1.9** | 3.1.9 (2026-09-27) | **GHSA-g6x2-hccm-hh4m** (CVE-2026-102598, MODERATE, Windows) | Lot 4, correctif de sécurité |
| gunicorn | 25.3.0 | **26.2.0** | 26.2.0 sur PyPI (2026-08-24) ; 26.2.1 et 26.2.2 GitHub seulement | OSV : aucune | Lot 4, majeure acceptée (D-043) |
| filelock | 3.29.7 | **4.0.12** | 4.0.12 (2026-10-05) | OSV : aucune | Lot 4, majeure acceptée |
| setuptools | 83.0.0 | **84.0.0** | 84.0.0 (2026-08-08) | OSV : aucune | Lot 4, majeure acceptée |
| cloudpathlib | 0.24.0 | **0.26.0** | 0.26.0 (2026-10-02) | OSV : aucune (GHSA-r4f8-3xc4-c8vw corrigé en 0.25.0) | Lot 4 |
| pydantic / pydantic-core | 2.13.4 / 2.46.4 | **2.13.5 / 2.46.5** | 2.13.5 | OSV : aucune | Lot 4 |
| 16 autres (MarkupSafe 3.0.4, Pygments 2.21.0, annotated-doc 0.0.5, annotated-types 0.8.0, certifi 2026.7.22, charset-normalizer 3.5.2, click 8.5.0, idna 3.20, packaging 26.3, smart_open 8.0.2, srsly 2.5.4, tqdm 4.70.1, typer 0.27.2, typing-inspection 0.4.4, wrapt 2.5.0) | — | dernières | dernières | OSV : aucune | Lot 4 |
| spacy | 3.8.13 | **3.8.16** | 3.8.16 (2026-08-24) | OSV : aucune | Lot 5 ; `versions.json`, `detection_config` → `1a04111540221092` |
| regex | 2026.7.10 | **2026.9.29** | 2026.9.29 | OSV : aucune | Lot 5 ; copie de développement alignée (D-017) |
| phonenumbers | 9.0.34 | **9.0.40** | 9.0.40 (2026-09-24) | OSV : aucune | Lot 5 |
| tldextract | 5.3.1 | **5.4.0** | 5.4.0 (2026-10-03) | OSV : aucune | Lot 5 |
| numpy | 2.4.6 | 2.4.6 | 2.5.3 | OSV : aucune | Plafonnée par Presidio (`<2.5.0`) |
| thinc | 8.3.13 | 8.3.13 | 9.1.1 | OSV : aucune | Plafonnée par spaCy 3.8 (`<8.4.0`) |
| Python (base Presidio) | 3.12.13 | 3.12.13 | 3.12.15 | voir EXT-50 | D-043 point 5 |

`pip-audit` sur l'image finale (`878fa63b…`) : *No known vulnerabilities found* (modèles spaCy absents de PyPI, non audités).
Effet sur la détection : **nul** (qualité, secrets et zones identiques aux références hors empreinte).

### Développement (jamais dans les images)

regex 2026.7.10 → 2026.9.29 (D-017), ast_serialize 0.11.2 → 0.12.1, filelock 4.0.9 → 4.0.12, platformdirs 4.12.2 → 4.12.3 ; outils de
premier niveau déjà aux dernières versions. `pip-audit -r app/requirements-dev.txt` : aucune vulnérabilité.

### Images (trivy, images finales)

| Image | Corrigeables | Sans correctif | Remarque |
|---|---|---|---|
| app 0.2.0-dev (`73b5a666…`) | 0 | 76 HIGH, 1 CRITICAL | Inchangé (paquets Debian, EXT-33) |
| analyseur 0.2.0-dev (`878fa63b…`) | 0 | 52 HIGH | Inchangé |
| traefik v3.7.13, oauth2-proxy v7.15.5, busybox 1.38.0 | 0 | 0 | Dernières versions, condensats inchangés |
| keycloak 26.8.0 | 0 | 6 HIGH | Dernière version |
| docker-socket-proxy v0.5.0 | **6** | 0 | Toujours sans version amont (D-040 point 8) |
