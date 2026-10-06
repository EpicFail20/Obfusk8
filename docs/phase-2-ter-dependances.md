# Phase 2 ter — inventaire des dépendances (étape A)

Première version préparée en fin de phase 2 bis (2026-10-05, D-041 point 3). **Mise à jour le 2026-10-06**, branche `feat/dependances`
(depuis `0cec4c9`, identique à `origin/feat/text-api`), avant toute modification.

Sources interrogées le 2026-10-06 :
- paquets Python : API JSON de PyPI (`https://pypi.org/pypi/<paquet>/json`, plus haute version hors préversions, développement et versions
  retirées ; date = premier envoi de la version) ; vulnérabilités de la **version en place** : API OSV (`/v1/query`, écosystème PyPI) ;
- notes de version : versions GitHub (`/repos/<dépôt>/releases`), `docs/release-notes.md` d'uvicorn, `NEWS.rst` de setuptools,
  comparaison de commits pour spaCy (notes de 3.8.16 vides) ; code de la roue FastAPI 0.142.2 lu (télémétrie) ;
- images : `docker buildx imagetools inspect` (condensat actuel de l'étiquette épinglée), versions GitHub des projets ;
- scans : `trivy` 0.75.0 (HIGH, CRITICAL) sur les sept images en service.

Inventaire des environnements **installés** (et non des seuls fichiers) : `app/requirements.lock` (32 paquets, image identique), environnement
de l'image de l'analyseur (`importlib.metadata`, 59 distributions dont les deux modèles), `app/requirements-dev.txt` (44 paquets).

## 1. Paquets en retard

### Image `app`

| Paquet | Actuelle | Dernière stable (date) | Ruptures annoncées (notes lues) | Vulnérabilités de l'actuelle |
|---|---|---|---|---|
| uvicorn (`[standard]`) | 0.35.0 | 0.54.0 (2026-09-25) | 0.36.1 `Config.setup_event_loop()` retiré (non utilisé par le dépôt) ; 0.45 contextvars ; 0.47/0.50 import de l'application dans le parent avec `--workers`/`--reload` (non utilisés) ; **0.48 puis 0.49 : en-têtes de transfert dupliqués** ignorés puis consommés par `ProxyHeadersMiddleware` ; 0.49 httptools ≥ 0.8.0 (en place) ; 0.50 code de sortie 3 au démarrage raté, `--ws auto` → `websockets-sansio` ; 0.51 colorama retiré ; 0.52-0.54 implémentation HTTP `zttp` **facultative et expérimentale** (non installée) ; **0.53 : `::1` ajouté à la valeur par défaut de `FORWARDED_ALLOW_IPS`** (`127.0.0.1,::1`). Aucune nouvelle dépendance de `[standard]` | Aucune |
| websockets | 17.1 | 17.2 (2026-10-03) | Aucune ; tirée par `uvicorn[standard]`, l'application n'a pas de route WebSocket | Aucune |
| fastapi | 0.141.1 | 0.142.2 (2026-09-30) | **0.142.0 : OpenTelemetry natif, `opentelemetry-api>=1.44.0` devient une dépendance obligatoire** (nouvelle dépendance de production : 1.45.0, Apache-2.0) ; traçage, métriques, journaux et `auto_configure` **actifs par défaut**, sans effet tant qu'aucun fournisseur n'est installé ; exportation OTLP déclenchée par `OTEL_EXPORTER_OTLP_ENDPOINT` si le SDK est présent (`fastapi/telemetry/_runtime.py`). 0.142.1 et 0.142.2 : correctifs de cette fonction | Aucune |
| requests | 2.33.1 | 2.34.2 (2026-05-14) | 2.34.0 `usedforsecurity=False` (Digest), Python 3.15 ; 2.34.1 annotations de `headers` | Aucune |
| pydantic-core | 2.46.5 | 2.49.0 (2026-09-09) | **Non applicable** : pydantic 2.13.5 (dernière) exige `pydantic-core==2.46.5` | Aucune |

Déjà à la dernière version (28) : annotated-doc, annotated-types, anyio, certifi, charset-normalizer, click, h11, httptools, idna, lxml,
packaging, pillow, pip (construction), prometheus_client, pydantic, pymupdf, pytesseract, python-docx, python-dotenv, python-icap,
python-multipart, PyYAML, starlette, typing-inspection, typing_extensions, urllib3, uvloop, watchfiles. Le **lot traitement de documents**
(PyMuPDF, python-docx, Pillow, pytesseract, lxml) n'a donc **aucune** mise à jour Python ; seuls les paquets Debian (tesseract, bibliothèques)
changent à la reconstruction.

### Image de l'analyseur

Python 3.12.13 (fourni par l'image Presidio 2.2.364, contre 3.12.15 dans `app`) ; Presidio 2.2.364 reste la dernière version (GitHub,
2026-07-22 ; condensat de l'étiquette inchangé).

| Paquet | Actuelle | Dernière stable (date) | Retenue possible | Remarque (notes lues) | Vulnérabilités de l'actuelle |
|---|---|---|---|---|---|
| **Werkzeug** | 3.1.8 | 3.1.9 (2026-09-27) | 3.1.9 | Version de correctif de sécurité, « should not result in breaking changes » | **GHSA-g6x2-hccm-hh4m** (CVE-2026-102598, MODERATE, revue GitHub du 2026-10-05) : `safe_join` et noms de périphériques **Windows** ; non exploitable sous Linux (supposé : condition « Windows et NTFS » de l'avis) — **apparue depuis la phase 2 bis** |
| spacy | 3.8.13 | 3.8.16 (2026-08-24) | 3.8.16 | 22 commits : CI, `spacy download`, dépendance `click` (3.8.15), roues `manylinux_2_28` ; aucun changement du NER ni des composants. **Change `versions.json` et donc `detection_config`** | Aucune |
| regex | 2026.7.10 | 2026.9.29 (2026-09-29) | 2026.9.29 | Moteur de tous les motifs Presidio ; D-017 : la copie de développement suit | Aucune |
| phonenumbers | 9.0.34 | 9.0.40 (2026-09-24) | 9.0.40 | Métadonnées de numérotation : peut changer la détection des téléphones | Aucune |
| tldextract | 5.3.1 | 5.4.0 (2026-10-03) | 5.4.0 | Utilisé par le reconnaisseur de courriel ; correctifs d'extraction (identifiants, ports, IPv6, suffixes privés) | Aucune |
| numpy | 2.4.6 | 2.5.3 (2026-09-06) | **2.4.6** | Presidio exige `numpy<2.5.0` : déjà la plus haute autorisée | Aucune |
| thinc | 8.3.13 | 9.1.1 (2024-09-12) | **8.3.13** | spaCy 3.8.16 exige `thinc<8.4.0` : déjà la plus haute autorisée | Aucune |
| pydantic / pydantic_core | 2.13.4 / 2.46.4 | 2.13.5 / 2.49.0 | 2.13.5 / 2.46.5 | pydantic épingle pydantic-core | Aucune |
| **gunicorn** | 25.3.0 | 26.2.2 (2026-09-06) | 26.2.2 | **Majeure** : 26.0.0 retire le worker `eventlet` (non utilisé : worker `sync` par défaut, `entrypoint.sh`) ; durcissement HTTP/1.1 (cible de requête, caractères de contrôle, `Content-Length` en liste, contrebande) ; 26.2.0 HTTP/2 en clair désactivé par défaut | Aucune |
| filelock | 3.29.7 | 4.0.12 (2026-10-05) | 4.0.12 | **Majeure** ; dépendance de tldextract (cache de la liste des suffixes) | Aucune |
| setuptools | 83.0.0 | 84.0.0 (2026-08-08) | 84.0.0 | **Majeure** : compilateurs C (sans objet à l'exécution) ; dépendance déclarée de spaCy | Aucune |
| typer | 0.26.8 | 0.27.2 (2026-08-28) | 0.27.2 | 0.27.0 : affichage des métavariables (CLI de spaCy, non utilisée) | Aucune |
| cloudpathlib | 0.24.0 | 0.26.0 (2026-10-02) | 0.26.0 | 0.25.0 : **correctif de sécurité** (GHSA-r4f8-3xc4-c8vw, traversée de chemin, sans avis OSV sur 0.24.0) ; 0.26 abandonne Python 3.9 | Aucune (OSV) |
| wrapt | 2.2.2 | 2.5.0 (2026-09-27) | 2.5.0 | Pas de rupture annoncée | Aucune |
| autres | — | — | dernières | annotated-doc 0.0.5, annotated-types 0.8.0, certifi 2026.7.22, charset-normalizer 3.5.2, click 8.5.0, idna 3.20, MarkupSafe 3.0.4, packaging 26.3, Pygments 2.21.0, smart_open 8.0.2, srsly 2.5.4, tqdm 4.70.1, typing-inspection 0.4.4 | Aucune |

Déjà à la dernière version (27) : Flask 3.1.3, Jinja2, PyYAML, anyio, blis, catalogue, confection, cymem, h11, httpcore, httpx, itsdangerous,
markdown-it-py, mdurl, murmurhash, preshed, requests 2.34.2, requests-file, rich, shellingham, spacy-legacy, spacy-loggers, typing_extensions,
urllib3, wasabi, weasel, blinker. Modèles : fr_core_news_md 3.8.0 (seule version pour spaCy 3.8), en_core_web_lg 3.8.0 (image de base).

### Outils de développement (`app/requirements-dev.txt`, jamais dans les images)

Outils de premier niveau tous à jour (ruff 0.16.10, mypy 2.4.0, bandit 1.9.4, pytest 9.1.1, pytest-cov 7.1.0, coverage 7.16.2,
pip-audit 2.10.1). Transitifs en retard : ast_serialize 0.11.2 → 0.12.1, filelock 4.0.9 → 4.0.12, platformdirs 4.12.2 → 4.12.3 ;
regex 2026.7.10 → 2026.9.29 **seulement si l'analyseur passe à 2026.9.29** (D-017). Aucune vulnérabilité (OSV).
Outils hors dépôt : trivy 0.75.0 et actionlint 1.7.12 restent les dernières versions.

## 2. Images

| Image | Épinglée | Dernière stable (GitHub / registre) | Condensat de l'étiquette | trivy HIGH/CRITICAL (en service) |
|---|---|---|---|---|
| traefik | v3.7.13 | v3.7.13 (2026-09-04) | inchangé | 0 |
| keycloak | 26.8.0 | 26.8.0 (2026-10-01) | inchangé | 6 HIGH sans correctif |
| oauth2-proxy | v7.15.5 | v7.15.5 (2026-10-01) | inchangé | 0 |
| docker-socket-proxy | v0.5.0 | v0.5.0 (2026-07-27) | inchangé | **6 HIGH corrigeables** (openssl 3.5.8, pcre2 10.48/10.49), toujours sans version amont (D-040 point 8) |
| busybox | 1.38.0 | 1.38.0 | inchangé | 0 |
| python (base `app`) | 3.12-slim, 3.12.15, `02108f5d…` | 3.12.15 | **reconstruite** : `ddb0207a…` (= `3.12.15-slim`) | (voir `app`) |
| presidio-analyzer (base) | 2.2.364 | 2.2.364 | inchangé | (voir analyseur) |
| app 0.2.0-dev (construite) | — | — | — | 77 sans correctif (76 HIGH, 1 CRITICAL), 0 corrigeable |
| analyseur 0.2.0-dev (construite) | — | — | — | 52 HIGH sans correctif, 0 corrigeable |

Actions GitHub épinglées toujours aux dernières versions (checkout v7.0.1, login v4.6.0, metadata v6.2.0, build-push v7.4.0) : aucun
changement du workflow.

## 3. Écarts avec la liste de la phase 2 bis

- **Nouveau** : Werkzeug 3.1.8 vulnérable (avis revu le 2026-10-05 au soir) ; la phrase « aucune version en place n'a de vulnérabilité
  connue » n'est plus vraie pour l'analyseur.
- **FastAPI** : la liste notait « aucune rupture d'API » pour 0.142 ; c'est exact pour l'API, mais 0.142.0 ajoute une dépendance obligatoire
  (`opentelemetry-api`) et une télémétrie active par défaut, ce qui relève de ta décision (nouvelle dépendance, `CLAUDE.md` §9).
- L'analyseur a **28 paquets en retard** au-delà de spaCy (la liste ne portait que sur spaCy) ; numpy et thinc sont plafonnés par leurs
  dépendants.
- `python:3.12-slim` a été reconstruite depuis l'épinglage.
