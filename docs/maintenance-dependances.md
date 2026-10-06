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
| Images de base | `FROM` de `app/Dockerfile` et `presidio/analyzer-build/Dockerfile` | Épinglées par condensat |
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
docker run --rm --memory 1g -v "$PWD/app:/src:ro" -v "$SCRATCH:/out" python:3.12-slim@<condensat du FROM> sh -c '
  pip install -q --root-user-action=ignore --no-cache-dir --require-hashes --no-deps -r /src/requirements-build.txt &&
  pip install -q --root-user-action=ignore --no-cache-dir --dry-run --ignore-installed --only-binary=:all: \
      --report /out/report.json -r /src/requirements.txt -c /out/constraints.txt && chmod 644 /out/report.json'
python3 tools/dependencies/lock_from_report.py "$SCRATCH/report.json" > "$SCRATCH/body.txt"
# remplacer les entrées du verrou par body.txt en gardant l'en-tête ; contrôler que seuls les paquets visés changent :
git diff app/requirements.lock | grep '^[-+][a-zA-Z]'
```

**Verrou de l'analyseur** : même principe dans l'image de base Presidio (`--entrypoint sh --user root`), avec `-r names.txt`
(noms de toutes les entrées hors modèles) et `-c constraints.txt`. La résolution doit rendre **exactement** l'ensemble contraint (ni
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
prouvant qu'aucune exportation n'a lieu) ; docker-socket-proxy (D-040 point 8) ; numpy et thinc plafonnés ; Python de l'analyseur (EXT-50).

## 7. Cadence proposée (décision humaine)

| Déclencheur | Délai proposé | Contenu |
|---|---|---|
| **Urgence** : avis CRITICAL, ou HIGH exploitable dans notre usage, sur une dépendance **en production** (paquet de `app` ou de l'analyseur, image de base, Traefik, oauth2-proxy, Keycloak en production) | Analyse d'exposition sous 24 h ouvrées, correctif sous 72 h si exposé | Lot unique de sécurité, procédure complète ; à défaut de correctif amont, mesure compensatoire documentée |
| Avis HIGH ou MODERATE corrigé en amont | Au plus tard au cycle mensuel suivant ; plus tôt si exposé | Lot de sécurité |
| **Revue complète** | **Mensuelle** (première semaine du mois) | §2 entier, lots du §3, compte rendu court |
| Changement de version mineure ou majeure (images, Python) | Trimestriel, ou sur décision | Phase dédiée |

Surveillance entre deux cycles (proposée, sans automatisation ajoutée à l'application ; la VM de test a accès à Internet, pas la pile) :
`tools/dependencies/outdated.py` et `trivy` hebdomadaires sur la VM, et abonnement de l'administrateur aux avis de sécurité GitHub des
dépôts amont (Presidio, spaCy, FastAPI, Starlette, uvicorn, PyMuPDF, Pillow, lxml, Traefik, oauth2-proxy, Keycloak).
