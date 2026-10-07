# Maintenance des dépendances

*English version: [dependency-maintenance.md](dependency-maintenance.md).*

Procédure établie en phase 2 ter (2026-10-06, D-043) pour que la mise à jour des dépendances soit une **opération de routine**, rejouable
sans réinventer la méthode. Elle complète `CLAUDE.md` §6 (vérification des versions), §7 (bugs) et §9 (accords). Registre des versions :
`docs/DEPENDENCIES.md` ; écarts justifiés : `docs/DECISIONS.md`.

Toutes les commandes se lancent depuis la racine du dépôt, sur la VM de test, **une seule pile** (`CLAUDE.md` §1). `$SCRATCH` désigne un
répertoire de travail sur disque, **hors** du tmpfs `/tmp` (EXT-25) et hors du dépôt.

## 1. Ce qui est maintenu

| Ensemble | Fichiers | Remarque |
|---|---|---|
| Paquets Python de `app` | `app/requirements.txt` (premier niveau), `app/requirements.lock` (ensemble complet, empreintes) | Installés par `--require-hashes --no-deps` |
| Paquets Python de l'analyseur | `presidio/analyzer-build/requirements.lock` (ensemble complet, empreintes, modèles spaCy compris) | `check_lock.py` fait échouer la construction si l'environnement installé diffère |
| Versions de détection | `presidio/analyzer-build/versions.json` = `app/analyzer_versions.json` | Empreinte `detection_config` ; les deux copies restent identiques (`app/run-tests.sh` le vérifie) |
| pip de construction | `app/requirements-build.txt`, `presidio/analyzer-build/requirements-build.txt` | Retiré des images après installation |
| Outils de développement | `app/requirements-dev.in`, `app/requirements-dev.txt` | Jamais dans les images ; installés dans `~/.cache/obfusk8-devtools` ; `regex` suit la version de l'analyseur (D-017) |
| Image de base Python (les deux images) | `x-python-base` de `docker-compose.build.yml`, argument `PYTHON_BASE` des deux Dockerfiles | Épinglée par condensat, **un seul endroit** (D-052) ; §9 |
| Serveur REST de Presidio | `presidio/analyzer-build/server/` (copie amont, licence MIT) | Recopié et comparé à chaque mise à jour de Presidio (§10) |
| Images tierces | `docker-compose.yml` (et surcharges `docker-compose.rollback-*.yml`) | Épinglées par condensat |
| Paquets Debian | `apt-get upgrade` à chaque construction | Suivent la reconstruction |
| Actions GitHub | `.github/workflows/` | Épinglées par commit ; workflow désactivé (D-042) |

## 2. Lister les dépendances en retard et leurs vulnérabilités

1. **Environnements installés** (et non les seuls fichiers) :

   ```sh
   docker run --rm --network none --entrypoint python ghcr.io/epicfail20/obfusk8-presidio-analyzer:0.2.0-dev -c \
     'import importlib.metadata as m; [print("ana", d.metadata["Name"], d.version) for d in m.distributions()]' \
     | grep -vE 'en_core_web_lg|fr_core_news_md' > "$SCRATCH/list.txt"
   grep -E '^[a-zA-Z]' app/requirements.lock     | sed -E 's/^([^=]+)==([^ ]+).*/app \1 \2/' >> "$SCRATCH/list.txt"
   grep -E '^[a-zA-Z]' app/requirements-dev.txt  | sed -E 's/^([^=]+)==([^ ]+).*/dev \1 \2/' >> "$SCRATCH/list.txt"
   ```

2. **Dernière version stable et vulnérabilités de la version en place**, à la source (API JSON de PyPI, API OSV) :

   ```sh
   PYTHONPATH=~/.cache/obfusk8-devtools python3 tools/dependencies/outdated.py < "$SCRATCH/list.txt" > "$SCRATCH/inventory.tsv"
   grep -E 'OUTDATED|ERROR' "$SCRATCH/inventory.tsv"; awk -F'\t' '$7 != "-"' "$SCRATCH/inventory.tsv"
   ```

   Pour chaque avis : fiche OSV (`https://api.osv.dev/v1/vulns/<id>`), recoupée avec la GitHub Advisory Database ou le NVD ; jamais un blog.
3. **Plafonds** : une version « en retard » peut être plafonnée par un dépendant (`numpy<2.5.0` par Presidio, `thinc<8.4.0` par spaCy 3.8,
   `pydantic-core==` par pydantic) : lire les `requires_dist` (`https://pypi.org/pypi/<paquet>/<version>/json`).
4. **Notes de version lues** pour chaque saut : versions GitHub, journal du projet, à défaut la comparaison de commits. Lire aussi les
   **dépendances** de la nouvelle version (`requires_dist`) : FastAPI 0.142 a rendu `opentelemetry-api` obligatoire (D-043).
   Vérifier que la version est **publiée sur PyPI** (gunicorn 26.2.1 et 26.2.2 n'existaient que sur GitHub le 2026-10-06).
5. **Images** : version amont (versions GitHub ou registre) et condensat actuel de l'étiquette épinglée
   (`docker buildx imagetools inspect <image>:<version>`) : une étiquette peut être reconstruite sans changer de version
   (`python:3.12-slim`, 2026-10-06).
6. **Scans** : `trivy` (HIGH, CRITICAL) sur chaque image en service, en conteneur jetable à mémoire limitée, cache sur disque :

   ```sh
   docker run --rm --memory 2g -v /var/run/docker.sock:/var/run/docker.sock:ro -v ~/.cache/obfusk8-trivy/cache:/root/.cache \
     aquasec/trivy@<condensat> image --quiet --scanners vuln --severity HIGH,CRITICAL <image>
   ```

7. Consigner la liste dans un document de travail (modèle : `docs/phase-2-ter-dependances.md`).

## 3. Classer en lots, et dans quel ordre

Un lot = un ensemble testable séparément, un commit, un retour arrière. Ordre de la phase 2 ter (D-043), du plus isolé au plus large :

| Ordre | Lot | Pourquoi ce rang | Vérifications propres |
|---|---|---|---|
| 0 | Image de base de `app` (condensat, paquets Debian) | Change le socle de tous les lots suivants | Différence `dpkg-query`, OCR, flux documents |
| 1 | Serveur HTTP de `app` (uvicorn, uvloop, httptools, websockets, anyio) | Boucle d'événements sous seccomp (EXT-11), confiance des en-têtes | Trace sous `app-audit.json`, `test_proxy_headers_trust.py`, en-tête forgé de bout en bout, latence |
| 2 | Cadre web (FastAPI, Starlette, python-multipart, pydantic) | Plafonds de corps (EXT-22), format des erreurs, modèles stricts | Suite complète, bout en bout texte et documents |
| 3 | Clients et utilitaires de `app` (requests…) | Faible risque | Bout en bout |
| 4 | Traitement de documents (PyMuPDF, python-docx, Pillow, pytesseract, lxml) | Contenu des fichiers finaux | Zones du flux documents, fichiers finaux (`e2e_document_flow.py`) |
| 5 | Analyseur, service (gunicorn, Flask, Werkzeug, et tout ce qui ne touche pas la détection) | Doit laisser la détection **identique** | Bancs de qualité et de secrets identiques ligne à ligne |
| 6 | Analyseur, détection (spaCy et modèles, regex, phonenumbers, tldextract, Presidio) | Change la détection et `detection_config` | Bancs avec garde-fou (§5) |
| 7 | Outils de développement | Hors images | ruff, mypy, bandit, suite |
| 8 | Images tierces (correctifs seulement) | Hors code | Connexion des comptes de test, trivy |

Un **correctif de sécurité** passe en tête, seul dans son lot.

## 4. Mettre à jour, reconstruire, tester

**Avant le premier lot** : étiqueter les images en service (`obfusk8-local:avant-<phase>-<service>`), écrire la surcharge
`docker-compose.rollback-<phase>.yml` (modèle : `docker-compose.rollback-2ter.yml`, `pull_policy: never`) et **tester le retour arrière** dans
les deux sens (connexion des comptes de test, `Seccomp: 2` dans `/proc/1/status` du conteneur `app`).

**Verrou de `app`** (même méthode pour `requirements-dev.txt`, avec `requirements-dev.in`) :

```sh
# constraints.txt = les versions du verrou actuel, les paquets mis à jour à leur version cible
grep -E '^[a-zA-Z]' app/requirements.lock | sed -E 's/ .*//' | grep -v '^uvicorn==' > "$SCRATCH/constraints.txt"
echo 'uvicorn==<cible>' >> "$SCRATCH/constraints.txt"     # et le premier niveau dans app/requirements.txt
docker run --rm --memory 1g -v "$PWD/app:/src:ro" -v "$SCRATCH:/out" <valeur de x-python-base> sh -c '
  pip install -q --root-user-action=ignore --no-cache-dir --require-hashes --no-deps -r /src/requirements-build.txt &&
  pip install -q --root-user-action=ignore --no-cache-dir --dry-run --ignore-installed --only-binary=:all: \
      --report /out/report.json -r /src/requirements.txt -c /out/constraints.txt && chmod 644 /out/report.json'
python3 tools/dependencies/lock_from_report.py "$SCRATCH/report.json" > "$SCRATCH/body.txt"
# remplacer les entrées du verrou par body.txt en gardant l'en-tête ; contrôler que seuls les paquets visés changent :
git diff app/requirements.lock | grep '^[-+][a-zA-Z]'
```

**Verrou de l'analyseur** : même principe dans la même image de base (`x-python-base`), avec `-v "$PWD/presidio/analyzer-build:/src:ro"`,
`-r names.txt` (noms de toutes les entrées hors modèles, `presidio-analyzer` compris) et `-c constraints.txt`. La résolution doit rendre **exactement** l'ensemble contraint (ni
paquet ajouté ni paquet retiré) ; les deux lignes des modèles spaCy se conservent à la main. Si spaCy, Presidio ou le modèle change :
`versions.json` **et** `app/analyzer_versions.json`, puis reconstruire **aussi** `app` (empreinte `detection_config`).

**Construction et tests** :

```sh
docker compose -f docker-compose.yml -f docker-compose.build.yml build --no-cache <service>
app/run-tests.sh                       # sous seccomp/app-enforce.json ; jamais dans le conteneur app
ENABLE_EXTENSION_API=true docker compose up -d <service>   # API texte active le temps des mesures seulement
```

**Appels système** (lots 0 à 4, et toute bibliothèque compilée de `app`) : `app` sous `app-audit.json` par une surcharge Compose hors dépôt
(`security_opt: !override`), scénarios rejoués (`benchmarks/e2e/e2e_text_api.py`, `benchmarks/e2e/e2e_document_flow.py`, redémarrage
propre), puis `sudo dmesg | grep 'audit: type=1326'`. Connus et volontairement non autorisés : `io_uring_setup`, `io_uring_enter`, `openat2`.
Tout autre appel : méthode de `seccomp/README.md`, ajout justifié ; **arrêt** avant un appel sensible (`ptrace`, `mount`, `bpf`, `unshare`,
`setns`, `keyctl`, `perf_event_open`, `process_vm_*`).

**Bancs** (comptes de test lus à l'exécution depuis `~/.obfusk8-test-accounts`, par un script qui n'affiche rien, sous **bash**) :
`benchmarks/quality/run_quality_bench.py`, `benchmarks/secret_detection/run_secrets_bench.py`,
`benchmarks/documents/doc_zones_snapshot.py` (puis `--compare` contre la référence), `benchmarks/latency/run_latency_bench.py`
(`BENCH_CONTENTION_ROUNDS=1 BENCH_CONTENTION_DOCUMENTS=3`), `benchmarks/e2e/check_no_content_in_logs.py`.
Un compte par banc lancé en parallèle (limitation de débit par utilisateur, `MAX_PENDING_JOBS`).

**Scans finaux** : `trivy` sur chaque image construite ; `pip-audit` sur les environnements installés
(`pip-audit --path <site-packages> --vulnerability-service osv`, dans un conteneur de l'image, outils montés).

**Commit** : un par lot ; le message dit ce qui a changé et ce qui a été vérifié (sorties chiffrées). `docs/DEPENDENCIES.md` à jour.

## 5. Garde-fous et retour arrière

- **Détection** : si le masquage du corpus principal baisse de plus de **0,02** par rapport à la référence (0,973 au 2026-10-06), ou si un
  secret n'est plus détecté : **arrêt**, chiffres présentés, décision humaine. Une variation plus faible est rapportée, pas corrigée.
- **Deux tentatives** infructueuses sur un lot (`CLAUDE.md` §7) : retour à la version précédente pour ce lot, écart dans `DECISIONS.md`
  (version visée, cause, condition de levée), lot suivant.
- **Retour arrière d'un lot** : `git revert <commit du lot>` puis reconstruction ; **de toute la phase** :
  `docker compose -f docker-compose.yml -f docker-compose.rollback-<phase>.yml up -d`.
- **Disque** : jamais `docker image prune -a` ni `docker system prune -a` (D-043) ; supprimer par identifiant les seules images
  intermédiaires construites pendant le travail.
- **Fin de travail** : `ENABLE_EXTENSION_API=false` dans `.env`, pile redéployée dans cet état.

## 6. Ce qui exige une décision humaine

- Version **mineure ou majeure** d'une image tierce (Keycloak, oauth2-proxy, Traefik…) ; version de **Python**.
- **Nouvelle dépendance** de production, y compris celle qu'impose une mise à jour (FastAPI 0.142 et `opentelemetry-api`).
- Version **majeure** d'un paquet (gunicorn 26, filelock 4, setuptools 84 en phase 2 ter).
- Tout écart à la dernière version stable, et sa condition de levée.
- Tout **changement de détection** au-delà de l'effet mesuré d'une mise à jour ; tout franchissement du garde-fou.
- Tout ajout d'appel système sensible au profil seccomp.
- Modification de `docker-compose.yml`, d'`oauth2-proxy.cfg`, de Traefik ou d'un Dockerfile au-delà des versions (`CLAUDE.md` §9).

**Réexamen systématique à chaque cycle** : FastAPI (D-043 point 1 : rester en 0.141.1 tant qu'aucun correctif de sécurité n'existe que
dans 0.142+ et qu'OpenTelemetry est obligatoire ; le jour de la montée, télémétrie désactivée explicitement dans `FastAPI(...)` et test
prouvant qu'aucune exportation n'a lieu) ; docker-socket-proxy (D-040 point 8) ; numpy et thinc plafonnés ; gunicorn plafonné par Presidio (`<26.0.0`, EXT-60) ; version de Python (§9).

## 7. Cadence (validée le 2026-10-06, D-044 point 2)

| Déclencheur | Délai proposé | Contenu |
|---|---|---|
| **Urgence** : avis CRITICAL, ou HIGH exploitable dans notre usage, sur une dépendance **en production** (paquet de `app` ou de l'analyseur, image de base, Traefik, oauth2-proxy, Keycloak en production) | Analyse d'exposition sous 24 h ouvrées, correctif sous 72 h si exposé | Lot unique de sécurité, procédure complète ; à défaut de correctif amont, mesure compensatoire documentée |
| Avis HIGH ou MODERATE corrigé en amont | Au plus tard au cycle mensuel suivant ; plus tôt si exposé | Lot de sécurité |
| **Revue complète** | **Mensuelle** (première semaine du mois), validée | §2 entier, lots du §3, compte rendu court |
| Changement de version mineure ou majeure (images, Python) | Trimestriel, ou sur décision | Phase dédiée |

Urgence validée : analyse d'exposition sous **24 h ouvrées**, correctif sous **72 h** si exposé. Sources d'alerte : §8.

## 8. Sources d'alerte entre deux cycles

**Alertes de sécurité GitHub** (D-044 point 2) : *Settings → Code security* du dépôt, **Dependabot alerts** activées ;
**Dependabot security updates** et **version updates** désactivées (aucune demande de fusion automatique : chaque correctif suit la
procédure ci-dessus). Couverture, d'après la documentation officielle consultée le 2026-10-06 (*Dependency graph supported package
ecosystems*, *Supported ecosystems and repositories*) :

| Fichier du dépôt | Reconnu par le graphe de dépendances ? | Conséquence |
|---|---|---|
| `app/requirements.txt` | Oui (nom `requirements.txt`, écosystème pip) | Alertes sur les **paquets de premier niveau** de `app` seulement |
| `app/requirements.lock`, `presidio/analyzer-build/requirements.lock` | **Non** d'après la documentation (pip : `requirements.txt` et `Pipfile.lock` seulement) | Ni les dépendances transitives de `app`, ni **aucun** paquet de l'analyseur ne sont couverts |
| `app/requirements-dev.txt`, `requirements-build.txt` | Non vérifié (nom différent de `requirements.txt`) | — |
| Images (`FROM`, `docker-compose.yml`) | Non : Docker n'est pris en charge que pour les mises à jour de version, pas pour les alertes | Aucune alerte sur les images |

Limites : le graphe n'est calculé que sur la **branche par défaut** (`main`, en retard sur `feat/text-api` jusqu'à la fusion) ; non vérifié
par l'observation (l'export SBOM `GET /repos/EpicFail20/obfusk8/dependency-graph/sbom` répond 404 sans authentification) : à contrôler
par l'administrateur dans *Insights → Dependency graph* après la fusion dans `main`.

**Les alertes GitHub seules ne couvrent donc pas nos verrous.** Source proposée en complément (décision humaine) :

1. **OSV sur les verrous et les environnements installés, hebdomadaire**, avec l'outil déjà présent (aucune dépendance nouvelle) : §2 étapes
   1 et 2 (`tools/dependencies/outdated.py`). OSV agrège la GitHub Advisory Database et la base d'avis PyPA : même source que les alertes
   GitHub, appliquée à **tous** nos paquets. Plus `pip-audit` et `trivy` sur les images en service (§2 étape 6).
2. **Abonnement de l'administrateur** aux avis de sécurité (*Watch → Custom → Security alerts*) des dépôts amont : Presidio, spaCy, FastAPI,
   Starlette, uvicorn, PyMuPDF, Pillow, lxml, Traefik, oauth2-proxy, Keycloak, et aux annonces de sécurité de python.org.
3. Option à évaluer (non retenue sans décision) : renommer les verrous en `requirements.txt` dans des répertoires dédiés pour que GitHub les
   lise ; changement de convention de fichiers à vérifier après fusion dans `main`, et sans effet sur les images.

La pile reste sans accès à Internet : ces contrôles se lancent sur la VM, jamais depuis un conteneur de la pile.

## 9. Changer de version de Python (phase « Python », D-052)

La version de Python est **une décision humaine** (§6). Elle est définie à **un seul endroit** : l'ancre `x-python-base` de
`docker-compose.build.yml` (image officielle `python:X.Y.Z-slim@sha256:…`), passée aux deux Dockerfiles par l'argument `PYTHON_BASE`, sans
valeur par défaut (une construction sans lui échoue). Le workflow de publication lit la même ligne.

1. **À la source** : dernière version stable sur python.org (pas de version candidate) ; condensat par
   `docker buildx imagetools inspect python:X.Y.Z-slim` ; **roues binaires** `cpXY` Linux x86_64 publiées sur PyPI pour **toutes** les
   entrées compilées des trois verrous (`app`, analyseur, développement), et `requires_python` compatible (Presidio, spaCy et la pile
   thinc déclarent un plafond `<3.N`) ; notes « What's New » lues (suppressions, `asyncio`, `multiprocessing`, base Unicode).
2. **Lots séparés**, dans cet ordre : `app`, puis l'analyseur ; chacun avec son retour arrière (§4).
3. **Verrous** : les trois régénérés dans la nouvelle base, avec les **mêmes versions** en contraintes (§4) : seules les empreintes des
   roues compilées doivent changer (contrôle : `git diff` limité aux lignes `--hash`). Outils de développement réinstallés dans
   `~/.cache/obfusk8-devtools` (garder l'ancien répertoire pour le retour arrière) ; `target-version` de ruff et `python_version` de
   mypy dans `app/pyproject.toml`.
4. **`versions.json` et `app/analyzer_versions.json`** : champ `python` à la nouvelle version (l'empreinte `detection_config` change :
   l'interpréteur peut changer la détection, base Unicode). La construction de l'analyseur et `app/tests/test_python_base.py` échouent
   tant que l'ancre, l'interpréteur et ces fichiers ne concordent pas.
5. **Seccomp** : trace complète sous `app-audit.json` (§4, appels système), journal du noyau filtré sur la période ; tout appel nouveau
   identifié (adresse de l'instruction dans `/proc/<pid>/maps`, démarrages minimaux) avant décision. Passage à 3.14 : un `open` de
   mimalloc, refusé sans effet (`seccomp/README.md`).
6. **Bancs** avec le garde-fou (§5) à chaque lot, latence, `trivy`, `pip-audit`, reproductibilité de l'analyseur (deux constructions
   sans cache, même ensemble de paquets).

## 10. Mettre à jour Presidio, spaCy ou les modèles (analyseur construit sur notre base)

Depuis la phase « Python », l'analyseur ne dépend plus de l'image publiée par le projet Presidio : la bibliothèque vient de sa roue PyPI
(dans le verrou), le serveur REST de `presidio/analyzer-build/server/`, les modèles de leurs fichiers GitHub (dans le verrou, avec empreinte).

**Presidio** (décision humaine si la détection change, §6) :

1. Nouvelle version sur PyPI **et** étiquette GitHub correspondante ; `requires_dist` de la roue lus (plafonds de numpy, spaCy, gunicorn
   de l'extra `server`…).
2. **Fichiers du serveur** (condition de D-052 point 1) : recopier `app.py`, `logging.ini`, `entrypoint.sh` et `LICENSE` depuis
   `presidio-analyzer/` de la nouvelle étiquette (`https://raw.githubusercontent.com/data-privacy-stack/presidio/<étiquette>/presidio-analyzer/<fichier>`),
   **comparer** à la version précédente (`git diff presidio/analyzer-build/server/`), lire toute différence, mettre à jour le tableau
   d'empreintes de `server/README.md`.
3. **Fichiers corrigés** : le Dockerfile vérifie l'empreinte de l'original de `spacy_recognizer.py` et de `conf/default_recognizers.yaml`
   avant de les remplacer ou de les corriger. Si la construction échoue sur ces contrôles, comparer l'original amont avec notre
   correctif (`patches/spacy_recognizer.py`, `patch_recognizers.py`), reporter les changements amont dans le correctif, puis mettre à
   jour les deux empreintes du Dockerfile.
4. Verrou de l'analyseur (§4), `versions.json` et `app/analyzer_versions.json`, reconstruction des **deux** images, bancs avec garde-fou.

**spaCy** : même chemin (verrou, `versions.json`) ; vérifier la compatibilité des modèles (`spacy>=X,<Y` dans leurs métadonnées).

**Modèles spaCy** : publiés seulement comme fichiers des versions GitHub d'`explosion/spacy-models`, **sans condensat publié** (D-052
point 8). Pour une nouvelle version : télécharger la roue dans `$SCRATCH`, vérifier sa taille contre l'API GitHub
(`/repos/explosion/spacy-models/releases/tags/<modèle>-<version>`), calculer son SHA-256, l'écrire dans la ligne du verrou (`nom @ URL
--hash=sha256:…`) ; `check_lock.py` compare ensuite l'empreinte enregistrée à l'installation. L'empreinte prouve « même fichier que celui
vérifié ce jour-là », pas l'authenticité à l'origine : consigner la date et la source dans `docs/DEPENDENCIES.md`.

