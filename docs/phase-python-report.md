# Phase « Python » — analyseur sur notre base et passage à Python 3.14 : compte rendu

Branche locale `feat/python-314`, créée depuis `feat/text-api` (`879d246`, identique à `origin/feat/text-api`), jamais poussée.
Date : 2026-10-07. VM : 4 vCPU, 8 919 Mo de RAM, pas de swap. Décisions humaines de la phase : D-052 (étape A). Décisions de mise en
œuvre : D-053 (à valider).

## 1. Résumé

- **L'analyseur ne dépend plus de l'image publiée par Presidio** : il est construit sur la même base Python épinglée que `app`.
  - Presidio est installé depuis sa roue PyPI avec empreinte ; ses 179 fichiers sont identiques au code de l'image amont.
  - Le serveur REST est copié de l'étiquette 2.2.364 (licence MIT).
  - Pip vérifie toutes les entrées du verrou, avec empreinte.
  - Deux constructions sans cache produisent le même ensemble de paquets et les mêmes fichiers.
  - EXT-50 est fermé et EXT-16 achevé.
- **`app` et l'analyseur sont en Python 3.14.8**, dernière version stable, vérifiée sur python.org (3.15 n'est qu'en version candidate
  et Presidio comme la pile spaCy l'excluent). La version est **définie à un seul endroit** : `x-python-base` dans
  `docker-compose.build.yml`, passée en argument `PYTHON_BASE` aux deux Dockerfiles, sans valeur par défaut.
- **Effet sur la détection : nul**, à chaque lot.
  - Qualité et secrets identiques ligne à ligne à la référence de la phase 2 ter, hors en-tête : masquage 0,973, secrets 21/21.
  - Zones du flux documents identiques octet par octet.
  - L'empreinte `detection_config` change seulement parce que la version de Python y entre désormais (D-052 point 4).
- **Seccomp : aucun appel système ajouté.** Python 3.14 fait un `open` par démarrage (allocateur mimalloc). Il est refusé sans effet,
  identifié et documenté.
- **gunicorn 26.2.0 → 25.3.0** pour respecter la contrainte de Presidio, que `pip check` ne voyait pas (EXT-60). La construction
  vérifie désormais les exigences de `presidio-analyzer[server]`.
- **Bancs de documents réparés** (EXT-61) : ils s'arrêtaient sur le quota par utilisateur de la phase « disponibilité ».
- **Images** :
  - analyseur : 1,63 Go (2,03 avant), 44 HIGH sans correctif (52 avant), 0 corrigeable ;
  - `app` : 544 Mo, 0 corrigeable ;
  - `pip-audit` : aucune vulnérabilité dans les deux images ni dans les outils.
- **Fin de phase** : 564 tests verts sous seccomp, API texte désactivée, ports sur `BIND_ADDRESS` seulement, retour arrière testé.

## 2. Commits (`879d246..HEAD`)

| Commit | Objet |
|---|---|
| `db50c7f` | D-052 (décisions sur l'étape A) |
| `e84abb9` | Étape B : surcharge de retour arrière `docker-compose.rollback-py314.yml`, testée dans les deux sens |
| `b0c146e` | Bancs : annulation des documents mesurés, versions lues dans l'analyseur installé (EXT-61) |
| `5de7c93` | Lot 1 : analyseur sur notre base Python 3.12.15, gunicorn 25.3.0, contrôle de l'extra `server` (EXT-16, EXT-60) |
| `1ae0caa` | Lot 2 : `app` en Python 3.14.8 |
| `ac404cb` | Lot 3 : analyseur en Python 3.14.8 (EXT-50) |
| `9a0854c` | Lot 4 : version de Python définie à un seul endroit (`x-python-base`, `PYTHON_BASE`, workflow) — **commit des images finales** |
| `5505881` | Documentation : maintenance FR/EN (§9, §10), README FR/EN, `DEPENDENCIES.md`, `FINDINGS.md`, D-053 |
| (ce commit) | Compte rendu |

## 3. Tableau par lot

| Lot | Versions | Taille | Vulnérabilités (trivy HIGH/CRITICAL ; pip-audit) | Effet sur la détection | Appels système ajoutés |
|---|---|---|---|---|---|
| Avant | `app` 3.12.15 ; analyseur : image Presidio, Python 3.12.13, gunicorn 26.2.0 | `app` 541 Mo ; analyseur 2,03 Go | `app` 77 sans correctif ; analyseur 52 sans correctif | Référence : masquage 0,973, secrets 21/21 | — |
| 1 Analyseur sur notre base | analyseur Python 3.12.15 (`ddb0207a…`), Presidio par roue PyPI, gunicorn 25.3.0, sans curl | analyseur 1,63 Go | analyseur 44 HIGH, 0 corrigeable ; pip-audit 58 paquets, 0 | **Nul** : qualité, secrets identiques ligne à ligne ; zones identiques octet par octet ; `detection_config` → `db3c647830927b1c` (Python déclaré) | — (pas de profil sur l'analyseur) |
| 2 `app` en 3.14 | `app` Python 3.14.8 (`f85c5697…`), mêmes paquets, 8 roues cp314 | `app` 544 Mo | `app` 77 sans correctif (dont 1 CRITICAL), 0 corrigeable ; pip-audit 0 | **Nul** (identique au lot 1, empreinte comprise) | **0** (`open` de mimalloc refusé sans effet) |
| 3 Analyseur en 3.14 | analyseur Python 3.14.8, mêmes paquets, 14 roues cp314 | analyseur 1,63 Go | analyseur 44 HIGH, 0 corrigeable ; pip-audit 0 | **Nul** : identique au lot 1 hors empreinte → `d2fa3efcf16bdce9` | — |
| 4 Paramètre unique | `x-python-base`, `PYTHON_BASE`, workflow | inchangées | inchangées | **Nul** (images au contenu identique au lot 3) | 0 |

Garde-fou (masquage du corpus principal, baisse de plus de 0,02 sous 0,973, ou secret non détecté) : **jamais franchi**, aucune variation.

## 4. Vérifications (sorties utiles)

**Point de départ** : `feat/text-api` = `origin/feat/text-api` = `879d246` ; `app/run-tests.sh` → `550 passed` ; `ss -tlnp` →
`192.168.1.35` sur 80, 443 et 8080.

**Identité de l'analyseur (lot 1, contre l'image d'avant)** :
- Distributions : seuls `gunicorn==26.2.0` → `25.3.0` et `presidio-analyzer==2.2.364` (désormais installée) diffèrent.
- Configuration effective et correctif identiques (empreintes) : `317c3a5a…`, `9d12375d…`, `454ec712…`, `9dc55569…`.
- Modèles : 56 fichiers identiques octet par octet.
- `/recognizers` et `/supportedentities` (`fr`, `en`) identiques.
- Démarrage identique, hors version de gunicorn et chemins de configuration.
- Mémoire : 1,066 Gio (1,068 avant) ; en 3.14, 1,075 Gio.

**Reproductibilité** (lots 1 et 3) : deux constructions sans cache → `meme ensemble de paquets (58)` et la même empreinte de
l'arborescence (`site-packages` et `/app`, hors `.pyc`, 5 336 fichiers) : `ba221905…` (3.12), `35725351…` (3.14).

**Tests** (`app/run-tests.sh`, `app-enforce.json`, `--read-only`, `--network none`) : `564 passed in 11.46s`.
- +3 tests d'annulation des bancs ;
- +5 tests du contrôle des extras ;
- +1 cas d'empreinte (interpréteur) ;
- +5 tests du paramètre unique.

Chaque test nouveau a été observé en échec avant le correctif.

**Analyse statique** (outils du verrou de développement, Python 3.14) :
- `ruff check` et `ruff format --check` verts sur les fichiers créés ou modifiés ;
- cible py314 contre py312 : mêmes 143 constats préexistants, aucun nouveau ;
- `mypy --strict` : `Success` (10 fichiers, en 3.12 comme en 3.14) ;
- `bandit` : 0 constat ;
- `actionlint` 1.7.12 : 0 constat.

**Construction sans `PYTHON_BASE`** : `ERROR: failed to solve: base name (${PYTHON_BASE}) should not be blank`, pour les deux images.

**Bancs finaux** (images du commit `9a0854c`, `benchmarks/results/`) :

| Mesure | Résultat |
|---|---|
| Qualité `quality-20261007T114321.md` | Identique ligne à ligne à `quality-20261006T083012.md` (phase 2 ter) hors en-tête : masquage **0,973** |
| Secrets `secrets-20261007T110711.md` | Identique hors en-tête : 21/21 dans tous les thèmes, 0 faux positif |
| Zones `doc-zones-20261007T111528.json` | 16 cas, 0 perdue, 0 étendue, 0 ajoutée ; `cases` identiques octet par octet à `doc-zones-20261006T082947.json` |
| Flux documents (`e2e_document_flow.py`) | 8 cas finalisés, 0 valeur sensible exposée ; `/api/v1/*` 404 quand l'API est désactivée |
| API texte (`e2e_text_api.py`) | 29/29 |
| Disponibilité (`e2e_availability.py quota`) | 0 échec |
| Latence au repos (`latency-20261007T112133.md`) | p95 **74,8 ms** (2 000 caractères, n = 98) ; 25,2 (200), 42,7 (1 000), 173,4 (5 000) ; `/health` p95 4,1 ms |
| Journaux et audit (`check_no_content_in_logs.py --since 2026-10-07T08:55:00`) | 5 conteneurs et les 2 journaux d'audit (310 et 25 818 lignes) : **633 valeurs recherchées, 0 trouvée** |

**Fin de phase** :
- `.env` : `ENABLE_EXTENSION_API=false` ; dans `app` : `false`, `Seccomp: 2`, Python 3.14.8 ; analyseur : Python 3.14.8, sain.
- Comptes 1 à 3 connectés : `/` 200, `/health` 200, `/api/v1/version` 404.
- `ss -tlnp` : `192.168.1.35` sur 80, 443 et 8080 seulement.
- Code en service identique au dépôt (`main.py`, `text_api.py`, `analyzer_versions.json`, `server/app.py`, `cmp`).
- Aucun secret dans `git log -p 879d246..HEAD` (recherche par motifs).

## 5. Revue sécurité

**Points vérifiés (observé)**
- **Chaîne d'approvisionnement de l'analyseur** :
  - plus d'image tierce opaque : chaque fichier Python installé vient d'une entrée du verrou vérifiée par pip ;
  - le correctif PROPN et la correction de `default_recognizers.yaml` ne s'appliquent qu'après vérification de l'empreinte du fichier
    amont (un Presidio modifié fait échouer la construction) ;
  - `check_lock.py` vérifie aussi l'extra `server` ;
  - le code, la configuration et les modèles appartiennent à `root` (l'image amont donnait `/app` à l'utilisateur d'exécution).
- **Surface réduite** : `curl` et ses bibliothèques retirés, `uv` absent ; trivy 44 HIGH sans correctif contre 52 (écart dû à `curl` et à la base différente, non ventilé).
- **Seccomp sur 3.14** :
  - trace complète sous `app-audit.json` (texte, documents, quota et annulation, redémarrage) ;
  - seul appel nouveau : `open` de mimalloc, identifié par son adresse (fonction `syscall` de la libc) et par des démarrages minimaux
    (`python -c pass` → 1 ; `PYTHONMALLOC=malloc` → 0 ; 3.12 → 0) et par le code de CPython v3.14.8 ;
  - non ajouté (D-053 point 1) ; aucun appel sensible rencontré.
- **Python 3.14 et notre code** : aucune utilisation des API retirées (`get_event_loop`, observateurs d'enfants) ; aucun
  `multiprocessing` ; uvloop toujours utilisé (`io_uring_*` journalisés comme en 3.12).
- **Aucun contenu dans les journaux ni l'audit** : voir §4.

**Points ouverts**
- Base Unicode 16.0 en 3.14 (15.0 en 3.12) : aucun effet sur nos corpus. Un caractère nouvellement assigné en Unicode 16 change de
  catégorie dans `text_normalization.py`. **Supposé** sans conséquence pour du texte français ; non testé caractère par caractère.
- gunicorn 25.3.0 n'a pas le durcissement HTTP/1.1 de la série 26 (contrebande de requêtes). L'analyseur n'est joint que par `app`, sur le
  réseau interne `backend`. À réexaminer quand Presidio lèvera sa contrainte (`docs/maintenance-dependances.md` §6).
- Modèles spaCy : authenticité à l'origine non vérifiable (aucun condensat publié, D-052 point 8).
- Images `avant-2bis` et cache de construction non supprimés (voir §7).

**À différer au pentest**
- Requêtes HTTP malformées et contrebande entre `app` (requests) et gunicorn 25.3.0 sur `backend`, depuis un conteneur compromis.
- Comportement de l'analyseur sous pression mémoire (limite de 2 Go) avec l'allocateur de 3.14.

## 6. Observé et supposé

| Observé | Supposé |
|---|---|
| Roues cp314 publiées pour toutes les dépendances compilées (API JSON de PyPI) ; installation et `pip check` réussis | Aucun écart numérique de spaCy/thinc entre cp312 et cp314 hors de nos corpus (identiques sur 257 prompts et 16 documents) |
| Bancs, zones, reconnaisseurs et entités identiques à chaque lot | La normalisation Unicode 16 ne change rien pour du texte métier français |
| `open` refusé sans effet visible (suite, bout en bout, bancs) | mimalloc n'alloue pas les objets Python (pymalloc par défaut, observé par `PYTHONMALLOCSTATS`) : effet du refus nul aussi hors de nos scénarios |
| Les fichiers du serveur sont identiques à l'étiquette 2.2.364 | Le contenu de l'étiquette GitHub et la roue PyPI viennent du même code (les fichiers de la roue sont identiques à ceux de l'image amont) |

## 7. Incidents

1. **Suppression d'images refusée par le classifieur de permissions** : images `avant-2bis-*`, étiquettes `:main`, `:latest`, `phase2-*`
   et cache de construction, pourtant autorisés par D-052 point 7. Je ne l'ai pas contourné. Les images intermédiaires de la phase
   (constructions de reproductibilité) ont été supprimées par identifiant. Disque en fin de phase : 5,3 Go libres. **À faire par
   l'humain** (§9, étape 7).
2. **Bancs de documents bloqués par le quota par utilisateur** (EXT-61) : découvert en préparant le lot 1. Corrigé, test d'abord, avant
   toute mesure.
3. **Premier banc de qualité final interrompu** (429 à `q-0229`) : je l'avais lancé en parallèle d'`e2e_text_api.py`, dont le test de file
   bornée sature volontairement la file globale de l'API texte. Relancé seul : identique. Erreur d'ordonnancement, pas une régression.
4. **mypy lancé sur l'hôte** (Python 3.13) avec des outils compilés pour 3.12 : échec d'import. Relancé dans un conteneur de l'image `app`,
   comme prévu par la méthode.
5. **Code de sortie 1 d'une commande en arrière-plan** : `tail -3` sur deux fichiers (syntaxe refusée par GNU tail), pas un banc.

## 8. Retour arrière

Images d'avant la phase : `obfusk8-local:avant-py314-{app,presidio-analyzer,traefik,keycloak,oauth2-proxy,docker-socket-proxy,
fix-app-dirs-perms}` (app `fb6a03e6…`, analyseur `9a5e59a0…`). Outils de développement d'avant : `~/.cache/obfusk8-devtools.avant-py314`.

```sh
docker compose -f docker-compose.yml -f docker-compose.rollback-py314.yml up -d   # images d'avant la phase
docker compose up -d                                                              # retour aux images de la phase
```

Testé le 2026-10-07 avant le premier lot, dans les deux sens : images attendues, `Seccomp: 2`, comptes 1 à 3 connectés, `/health` 200,
`/api/v1/version` 404, ports sur `BIND_ADDRESS`. Pour revenir sur un lot seul : `git revert <commit du lot>`, puis reconstruction. Pour
la suite de tests sur les anciennes images : `OBFUSK8_DEVTOOLS=~/.cache/obfusk8-devtools.avant-py314 OBFUSK8_TEST_IMAGE=obfusk8-local:avant-py314-app app/run-tests.sh`.

## 9. Procédure de test manuel (pour l'humain)

1. **Fusion et publication** : `git checkout feat/text-api && git merge --ff-only feat/python-314 && git push`.
2. **`.env`** : vérifier `ENABLE_EXTENSION_API=false`, `BIND_ADDRESS=192.168.1.35`, `COMPOSE_PROFILES=lab`.
3. **Reconstruction depuis les Dockerfiles** : `docker compose -f docker-compose.yml -f docker-compose.build.yml build --no-cache`
   (le téléchargement des modèles spaCy prend environ 450 Mo), puis `docker compose up -d`.
4. **Commit et versions en service** :
   - `git rev-parse --short HEAD` (doit être celui poussé) ;
   - `docker exec obfusk8-app-1 cat /app/main.py | cmp - app/main.py` (aucune sortie = identique) ;
   - `docker exec obfusk8-app-1 python -V` et `docker exec obfusk8-presidio-analyzer-1 python -V` → `Python 3.14.8` deux fois ;
   - `docker compose ps` → `presidio-analyzer` `(healthy)` (contrôle de santé en Python) ;
   - `docker exec obfusk8-app-1 grep Seccomp: /proc/1/status` → `2` ;
   - `ss -tlnp | grep -E ':(80|443|8080)\b'` → seulement `192.168.1.35`.
5. **Suite** : `app/run-tests.sh` → `564 passed`.
6. **Parcours navigateur** :
   - ouvrir `https://obfusk8.lab.local` et se connecter avec compte-1 ;
   - envoyer un PDF et un DOCX de test, réviser, valider, télécharger : les zones proposées et les fichiers caviardés sont les mêmes
     qu'avant la phase ;
   - envoyer une image PNG (OCR) ;
   - annuler un document en attente avec « Annuler ce document » ;
   - `https://obfusk8.lab.local/api/v1/version` → 404.
7. **Nettoyage du disque** (D-052 point 7, refusé à la session) : supprimer les images `obfusk8-local:avant-2bis-*`,
   `obfusk8-app-local:phase2-*`, `ghcr.io/epicfail20/obfusk8-app:main`, `ghcr.io/epicfail20/obfusk8-presidio-analyzer:latest`, l'image
   `ghcr.io/data-privacy-stack/presidio-analyzer:2.2.364` (plus utilisée) et le cache (`docker builder prune`) ; garder les
   `avant-py314-*` jusqu'à ta confirmation que la pile fonctionne depuis la branche poussée.

## 10. Décisions qui te reviennent

1. **Valider D-053**, en particulier :
   - `open` laissé refusé dans le profil seccomp (point 1) ;
   - contrôle de l'extra `server` dans `check_lock.py` (point 2).
2. **Suppression des images `avant-py314-*`** et de `~/.cache/obfusk8-devtools.avant-py314`, après ta vérification.
3. **gunicorn** : réexamen à chaque cycle de la contrainte `<26.0.0` de Presidio (ajouté à la liste de réexamen de la maintenance).
4. **Diff de `CLAUDE.md`** ci-dessous.

## 11. Proposition de mise à jour de `CLAUDE.md` (non appliquée)

```diff
-Constatées dans le dépôt le 2 octobre 2026, complétées le 4 octobre 2026 (enseignements de la phase 1), le 5 octobre 2026 (phases 2 et 2 bis) et le 7 octobre 2026 (phase « disponibilité »).
+Constatées dans le dépôt le 2 octobre 2026, complétées le 4 octobre 2026 (enseignements de la phase 1), le 5 octobre 2026 (phases 2 et 2 bis) et le 7 octobre 2026 (phases « disponibilité » et « Python »).
@@ Seccomp en mode bloquant
 **Dépendance implicite à uvloop** (EXT-11) : …
+Python 3.14 appelle `open` (n° 2) une fois par démarrage (mimalloc, `/proc/sys/vm/overcommit_memory`) : refusé volontairement, sans
+effet (D-053, `seccomp/README.md`). Un changement de version de Python impose la trace complète sous `app-audit.json`.
@@ Images et versions
-construites (`app`, analyseur) portent une version (`0.2.0-dev`, **locale**, jamais publiée) et se construisent par
-`docker compose -f docker-compose.yml -f docker-compose.build.yml build` (Compose refuse une étiquette de construction avec condensat).
+construites (`app`, analyseur) portent une version (`0.2.0-dev`, **locale**, jamais publiée) et se construisent par
+`docker compose -f docker-compose.yml -f docker-compose.build.yml build` (Compose refuse une étiquette de construction avec condensat).
+**Python 3.14.8 dans les deux images, défini à un seul endroit** : `x-python-base` de `docker-compose.build.yml`, argument
+`PYTHON_BASE` des deux Dockerfiles, sans valeur par défaut (`docker build` seul échoue) ; le workflow lit la même ligne. Changer de
+Python : `docs/maintenance-dependances.md` §9 (trois verrous, `versions.json`, seccomp, bancs).
+**L'analyseur est construit sur notre base** (D-052) : Presidio par sa roue PyPI dans `presidio/analyzer-build/requirements.lock`,
+serveur REST copié dans `presidio/analyzer-build/server/` (recopié et comparé à chaque mise à jour de Presidio, §10), configuration dans
+`/app/conf`, correctifs appliqués après vérification de l'empreinte du fichier amont ; gunicorn plafonné par Presidio (`<26.0.0`,
+EXT-60, vérifié par `check_lock.py`, que `pip check` ne couvre pas pour les extras).
-Paquets Python de `app` : `app/requirements.lock` (empreintes) ; `pip` est retiré des images. Versions de l'analyseur déclarées dans
+Paquets Python de `app` : `app/requirements.lock` (empreintes) ; `pip` est retiré des images. Versions de l'analyseur (Python compris) déclarées dans
 …
-les deux copies doivent rester identiques. Retour arrière : `docker-compose.rollback-2bis.yml`.
+les deux copies doivent rester identiques. Retour arrière : `docker-compose.rollback-py314.yml` (et `-2ter`, `-2bis`).
@@ Tests
+Outils de développement pour Python 3.14 dans `~/.cache/obfusk8-devtools`, et outils d'analyse (mypy, ruff) lancés dans un conteneur de
+l'image `app` (l'hôte n'a pas la même version de Python). Bancs de documents : un document en attente à la fois (`cancel_job`, EXT-61) ;
+ne jamais lancer `e2e_text_api.py` pendant un autre banc de l'API texte (il sature volontairement la file globale).
```
