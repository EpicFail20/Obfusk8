# CLAUDE.md — Règles permanentes du projet Obfusk8

Ce fichier s'applique à **toute** session Claude Code sur ce dépôt, quelle que soit la phase.
Les prompts de phase disent *quoi* faire ; ce fichier dit *comment*.
En cas de conflit entre un prompt de phase et ce fichier, applique la règle **la plus stricte** et signale le conflit.

---

## 0. Doctrine

Obfusk8 est un outil auto-hébergé d'anonymisation (PDF, DOCX, CSV, images avec OCR) : détection Presidio + reconnaisseurs
personnalisés, **révision humaine obligatoire**, fonctionnement **100 % hors ligne**. Cible : données sensibles, notamment médicales.

1. **Un faux négatif est plus grave qu'un faux positif.** Un faux positif se corrige en un clic pendant la révision ; un faux négatif
   fuit en silence. Ne réduis **jamais** les faux positifs au prix de faux négatifs. Cette règle a déjà été apprise à la dure
   (voir le commentaire de `DEFAULT_SCORE_THRESHOLD` dans `docker-compose.yml`).
2. **Les scores de détection ne sont pas calibrés.** Le reconnaisseur spaCy de Presidio attribue un score fixe (0,85) quel que soit le cas.
   Un seuil supérieur supprime toute détection générique de personnes et de lieux. Conséquence : **aucun client ne règle le seuil**,
   et aucun client ne filtre sur le score. Les seuils sont portés par la configuration serveur et les thèmes.
3. **Aucune donnée réelle** (patient, salarié, client) dans le dépôt, les tests, les corpus, les logs ou les messages de commit. Uniquement du synthétique.
4. **Hors ligne** : l'application n'a pas d'accès Internet (réseaux Docker `internal: true`). Aucune fonction ne doit en dépendre.

---

## 1. Particularités de ce dépôt

Constatées dans le dépôt le 2 octobre 2026, complétées le 4 octobre 2026 (enseignements de la phase 1), le 5 octobre 2026 (phases 2 et 2 bis) et le 7 octobre 2026 (phases « disponibilité » et « Python »). **Revérifie-les en début de session** : si une ligne ci-dessous n'est plus vraie, signale-le et propose la mise à jour de ce fichier.

**Chaîne de requête** : Traefik → `oauth2-proxy` (forward auth, en-têtes `X-Auth-Request-User` / `X-Auth-Request-Email`) → `app`
(FastAPI, port 8000) → `presidio-analyzer` (image construite depuis `presidio/analyzer-build`), appelé en HTTP sur le réseau interne `backend`.
Le service `presidio-anonymizer` a été **retiré** (phase 2 bis, D-040 : jamais appelé, EXT-20) ; le caviardage est fait par `app` elle-même.
Point d'entrée unique vers l'analyseur : `main._analyze_normalized` (normalisation D-014 avec table de correspondance). PyMuPDF ne traite
qu'un document à la fois, sur un fil dédié (D-019) ; repli de localisation PDF par positions de caractères (D-026).

**Secret de passerelle** (audit 3.7) : `_GatewaySecretMiddleware` vérifie un secret injecté par Traefik (`gateway-secret@file`).
Toute route de l'application doit rester derrière ce mécanisme et derrière `oidc-auth`. Ne crée jamais de contournement.

**Routage Traefik par labels** : des routeurs dédiés existent pour `/api/detect` et `/api/finalize` (limitation de débit par adresse IP **et**
par utilisateur + plafond de corps), `/api/cancel/` (routeur `app-cancel` : mêmes limites, plafond de corps de 4 096 octets, sans redirection),
`/api/preview_image` (limitation de débit), `/api/v1/` (routeur `app-text` : API texte de l'extension, **désactivée jusqu'à la phase 3**, `ENABLE_EXTENSION_API=false` dans `.env`, D-041 ; limitation de débit **par utilisateur**
sur `X-Auth-Request-User`, plafond de corps dérivé de `MAX_TEXT_CHARS`, voir `docs/api-extension.md` §7) et `/metrics` (liste d'IP autorisées). **Toute autre route tombe dans le routeur `app` par défaut :
authentifiée, mais sans limitation de débit ni plafond de corps en bordure.** Toute nouvelle route exposée exige donc son propre routeur.
Les routes de documents utilisent `doc-auth-errors` (redirection vers la connexion sur 401 seulement) : `oauth2-errors` (401-403) transformait
un 403 de l'application en déconnexion de l'utilisateur (EXT-57). Ne mets jamais `oauth2-errors` sur une route qui peut répondre 403.

**Flux documents** (phase « disponibilité », D-048) : quota par utilisateur de documents en attente (`MAX_PENDING_JOBS_PER_USER`, 429 +
`Retry-After`, plafond commun `MAX_PENDING_JOBS` en 503), annulation (`ENABLE_JOB_CANCEL`), identité obligatoire (`_document_user`, 403,
EXT-47) et `Origin` (à défaut `Referer`) égal à `https://${APP_DOMAIN}` sur toute requête qui modifie un état (`APP_DOMAIN` transmis à `app` ;
absent = tout refusé). Toute nouvelle route qui modifie un état applique le même contrôle d'origine. Tests : `tests/doc_headers.py` (en-têtes
d'une requête légitime) et `tests/conftest.py` (origine de test). Bancs : `BENCH_RESOLVE_ADDRESS=<BIND_ADDRESS>`, session vérifiée par
`/oauth2/auth` (EXT-52).

**Plafonds en double** : la taille des requêtes est bornée en bordure (Traefik) **et** dans l'application (`MAX_REQUEST_BODY_BYTES` dans `main.py`).
Les deux valeurs doivent rester cohérentes et leur lien doit être commenté.

**Seccomp en mode bloquant** : `app` tourne avec `seccomp/app-enforce.json`, une liste blanche d'appels système construite par observation (strace).
Un nouveau chemin de code ou une nouvelle dépendance peut déclencher un appel système bloqué, avec des erreurs `EPERM` difficiles à interpréter.
Tous les tests de bout en bout se font **sous ce profil**. En cas de besoin, suis la méthode de `seccomp/README.md`
(profil `app-audit.json`, `dmesg | grep 'audit: type=1326'`) et documente chaque appel système ajouté.
**Dépendance implicite à uvloop** (EXT-11) : le profil autorise `epoll_pwait` mais ni `epoll_wait`, ni `select`, ni `shutdown`. La boucle asyncio
standard est donc inutilisable sous ce profil ; la production fonctionne parce qu'uvicorn choisit uvloop (`uvicorn[standard]`). Tout code ou test
qui crée sa propre boucle ou son propre sélecteur doit être exécuté sous le profil.
Python 3.14 appelle `open` (n° 2) une fois par démarrage (mimalloc, `/proc/sys/vm/overcommit_memory`) : refusé volontairement, sans
effet (D-053, `seccomp/README.md`). Un changement de version de Python impose la trace complète sous `app-audit.json`.

**Ressources contraintes** : `app` dispose d'un CPU, 1 Go de RAM, 256 processus et d'un worker unique. Un traitement bloquant ou coûteux pénalise
**tous** les utilisateurs, y compris le flux documents. `presidio-analyzer` : 1,5 CPU, 2 Go.

**Seuils et thèmes** : `DEFAULT_SCORE_THRESHOLD=0.4` pour le flux sans thème ; chaque thème (médical, informatique, comptabilité)
définit ses propres valeurs et reconnaisseurs. Réutilise ce mécanisme, ne crée pas de chemin de détection parallèle.
Les reconnaisseurs ponctuels sont envoyés à Presidio à chaque requête (`ad_hoc_recognizers`) : `app/themes/common.json` pour tous les flux,
`app/themes/<thème>.json` pour un thème, `app/themes/extension/*.json` pour les seules routes `/api/v1/` (D-015 ; sous-répertoire non parcouru
par `_load_themes`). Un reconnaisseur réutilisé est **repris par référence** (`include_theme_recognizers`), jamais recopié.
Presidio compile ces motifs avec le module `regex` (et non `re`), drapeaux `IGNORECASE | DOTALL | MULTILINE` : teste-les avec la version
de `regex` de l'image de l'analyseur (D-017).

**Internationalisation** : les messages visibles par l'utilisateur passent par `app/i18n/` (`fr`, `en`, sélection par `UI_LANG`,
voir `docs/traduire-interface.md`). Aucun message utilisateur codé en dur.

**Documentation bilingue** : README, guides, sécurité et déploiement existent en français et en anglais. Toute documentation destinée aux utilisateurs
ou administrateurs est mise à jour **dans les deux langues**.

**Style des commentaires** : le code et la configuration sont commentés en anglais et expliquent le *pourquoi* (incident observé, mesure réalisée,
référence d'audit). Garde ce style : une valeur ou un choix non évident est justifié par un commentaire, avec la mesure qui l'appuie.

**Volumes sur chemins absolus de l'hôte** : `/var/lib/anonymiseur/workdir` (fichiers de travail) et `/var/log/anonymiseur-audit` (journaux d'audit :
`audit.log` pour les documents, `audit-extension.log` pour l'API texte, rotation indépendante, D-007).
Le nom du projet Compose est supposé être `obfusk8` (label `traefik.docker.network=obfusk8_app-internal`) et les ports 80, 443 et 8080 sont fixes,
tous publiés par Traefik sur la seule adresse `BIND_ADDRESS` de `.env` (D-047), jamais sur `0.0.0.0` ni `[::]`.
**Ne lance jamais une seconde pile sur le même hôte** : elle partagerait le journal d'audit et les fichiers de travail, et casserait le routage.

**Images et versions** (phase 2 bis, D-040) : toutes les images tierces sont épinglées par condensat dans `docker-compose.yml` ; les images
construites (`app`, analyseur) portent une version (`0.2.0-dev`, **locale**, jamais publiée) et se construisent par
`docker compose -f docker-compose.yml -f docker-compose.build.yml build` (Compose refuse une étiquette de construction avec condensat).
**Python 3.14.8 dans les deux images, défini à un seul endroit** : `x-python-base` de `docker-compose.build.yml`, argument
`PYTHON_BASE` des deux Dockerfiles, sans valeur par défaut (`docker build` seul échoue) ; le workflow lit la même ligne. Changer de
Python : `docs/maintenance-dependances.md` §9 (trois verrous, `versions.json`, seccomp, bancs).
**L'analyseur est construit sur notre base** (D-052) : Presidio par sa roue PyPI dans `presidio/analyzer-build/requirements.lock`,
serveur REST copié dans `presidio/analyzer-build/server/` (recopié et comparé à chaque mise à jour de Presidio, §10), configuration dans
`/app/conf`, correctifs appliqués après vérification de l'empreinte du fichier amont ; gunicorn plafonné par Presidio (`<26.0.0`,
EXT-60, vérifié par `check_lock.py`, que `pip check` ne couvre pas pour les extras).
Paquets Python de `app` : `app/requirements.lock` (empreintes) ; `pip` est retiré des images. Versions de l'analyseur (Python compris) déclarées dans
`presidio/analyzer-build/versions.json` (vérifiées à sa construction) et copiées dans `app/analyzer_versions.json` (empreinte `detection_config`) :
les deux copies doivent rester identiques. Retour arrière : `docker-compose.rollback-py314.yml` (et `-2ter`, `-2bis`).

**Adresse fixe de Traefik** : `10.89.18.10` sur `app-internal` (`10.89.18.0/24`, hors des pools par défaut de Docker), seule source de confiance
d'oauth2-proxy (`trusted_proxy_ips`) et de Keycloak (`KC_PROXY_TRUSTED_ADDRESSES`) pour les en-têtes de transfert. À changer aux quatre endroits ensemble. PKCE S256 exigé entre
oauth2-proxy (`code_challenge_method`) et le client Keycloak (*PKCE method*) : l'un sans l'autre bloque toute connexion (EXT-49). Keycloak 26.8 sur le volume
`keycloak-data-v26-8` (la base H2 de développement ne se migre pas d'une version à l'autre : export puis import du royaume).

**Keycloak = laboratoire uniquement** (D-046) : profil Compose `lab` (`COMPOSE_PROFILES=lab` dans le `.env` du laboratoire, sinon
`docker compose --profile lab …`), mode développement, réseau `app-internal` seul (aucun accès sortant), joint par Traefik (point d'entrée
`keycloak`, `BIND_ADDRESS:8080`). Sans le profil, oauth2-proxy redémarre en boucle : c'est attendu. Le pilote et la production utilisent le
fournisseur d'identité de l'établissement, avec des secrets neufs (D-045 §C point 16).

**Modules existants à réutiliser** : `antivirus.py` (ICAP), `supervision.py` (alertes, syslog), journal d'audit, `branding.py`,
`text_api.py` (API texte, limiteur et file bornée).
**Nouveau module Python** (D-051) : quand du code nouveau forme un ensemble cohérent, il va dans un nouveau module de `app/` (typé, `mypy --strict`),
avec sa ligne `COPY` explicite dans `app/Dockerfile`, **sans demander d'accord** ; plus de nouveau code dans `main.py` dans ce cas.

**Tests** : l'image de production ne contient ni `pytest` ni les tests (EXT-09 corrigé). **Ne lance jamais la suite dans le conteneur `app`** :
elle écrirait dans le vrai journal d'audit et les vrais fichiers de travail. Méthode (D-016 mis à jour) : `app/run-tests.sh`, conteneur jetable de
l'image de production sous `seccomp/app-enforce.json`, `--read-only`, `--network none`, `tmpfs`, `app/tests` et le dépôt montés en lecture seule,
outils de `app/requirements-dev.txt` installés hors de l'image et montés en lecture seule ; le script refuse une image qui contiendrait `pytest`.
Aucun caractère Unicode de format écrit tel quel dans un fichier (séquences d'échappement, test `test_repo_hygiene.py`, EXT-39) ; jetons de test
au format réel via `app/tests/fake_secrets.py`. La couverture se mesure hors seccomp (base SQLite de `coverage`
bloquée), la suite fonctionnelle sous seccomp.
Outils de développement pour Python 3.14 dans `~/.cache/obfusk8-devtools`, et outils d'analyse (mypy, ruff) lancés dans un conteneur de
l'image `app` (l'hôte n'a pas la même version de Python). Bancs de documents : un document en attente à la fois (`cancel_job`, EXT-61) ;
ne jamais lancer `e2e_text_api.py` pendant un autre banc de l'API texte (il sature volontairement la file globale).

**Mémoire de l'hôte** (EXT-25) : `/tmp` est un tmpfs qui consomme la RAM de la VM, sans swap. Aucun fichier volumineux dedans (archive d'image,
cache d'outil) ; vérifie `free -m` avant un outil lourd ; conteneurs d'outils avec limite mémoire et cache sur disque. Un OOM global tue en priorité
le worker unique de l'analyseur.

**Données fictives** : n'utilise **jamais** une valeur trouvée dans l'environnement (mot de passe, jeton, identifiant de compte) comme exemple
« fictif ». Les jetons de test au format réel (Stripe, Slack, GitHub…) sont **générés à l'exécution** (préfixe + remplissage déterministe), jamais
écrits en littéral dans le dépôt, **sans exception** : les exemples publiés par les fournisseurs (`AKIA…EXAMPLE`, clé Azurite…) suivent
la même règle (décision humaine du 2026-10-04). Les comptes de test sont lus à l'exécution depuis `~/.obfusk8-test-accounts`, hors dépôt, et désignés par un libellé.

**Secrets du laboratoire jetables** (D-049) : une exposition d'un secret du laboratoire dans une session se signale en une ligne dans le compte
rendu, sans arrêt ni proposition de régénération. Exigence maintenue : aucun secret dans le dépôt ni dans un commit, vérifié avant chaque commit.

---

## 2. Principes de travail

1. **La source de vérité est le dépôt.** Lis un fichier dans son état actuel avant de le modifier. Un incident passé (fichier `docker-compose.yml`
   obsolète retransmis, configuration réelle divergente) a montré le coût de l'inverse.
2. **Vérifie avant d'affirmer.** Pas de « ça marche », « c'est corrigé » ou « c'est à jour » sans exécution et résultat observé. Colle la commande et sa sortie utile.
3. **Ne devine pas.** Si l'information n'est pas dans le code, pose la question.
4. **Périmètre strict.** Ce qui est hors périmètre va dans `docs/FINDINGS.md` (§7), pas dans le code.
5. **Existant d'abord.** Suis les conventions en place (structure, nommage, variables `MAX_*`, `try/finally`, i18n, style de commentaires).
6. **Compatibilité ascendante.** Fonction nouvelle désactivée : comportement strictement identique à la version précédente.
7. **Langue** (décision humaine du 2026-10-04) : réponses, comptes rendus, messages de commit et documents de travail en **français**.
   Les identifiants du code et les commentaires du code restent en anglais ; la documentation destinée aux utilisateurs et aux administrateurs
   reste bilingue FR/EN.

---

## 3. Workflow git

- Travaille uniquement sur la branche de la phase. Jamais de commit sur `main`.
- **Ne pousse pas** : l'humain relit le diff et pousse.
- Ne crée ni tag ni release ; ne modifie pas les workflows de `.github/workflows/`.
- Commits petits, un changement logique par commit, dans le style des messages existants.
- Pas de `git add -A` : ajoute les fichiers explicitement, relis `git diff --staged` avant chaque commit.
- Interdit sans accord explicite : `git push --force`, `git reset --hard`, réécriture d'historique, `git tag -f`.
- Toute nouvelle fonction est derrière un drapeau d'environnement **désactivé par défaut**, déclaré dans `docker-compose.yml`
  et dans les deux fichiers `env.*.example`.

---

## 4. Qualité du code

- **Typage** complet sur le code nouveau ; modèles Pydantic stricts (types, bornes, longueurs maximales, champs supplémentaires refusés).
- **Outils** : `ruff` (lint et format), `mypy` (strict sur les modules nouveaux), `pytest` et `pytest-cov`, `bandit`.
  Ils vont dans des dépendances de développement séparées, **jamais dans l'image**. S'ils n'existent pas encore dans le dépôt, propose leur ajout dans le plan.
- **Pas de reformatage de l'existant** : n'applique `ruff format` qu'aux fichiers nouveaux ou à ceux que tu modifies pour la phase.
  Un reformatage global rendrait le diff illisible et masquerait les vrais changements.
- **Tests** : tout code nouveau est testé, chemins d'erreur compris. Un bug corrigé a son test de non-régression écrit **avant** le correctif.
  Cible : couverture du code nouveau d'au moins 90 %. Ne modifie jamais un test existant pour le faire passer sans expliquer pourquoi il était faux.
- **Lisibilité** : fonctions courtes, pas de code mort ni commenté, pas de duplication, pas de valeur « magique » non commentée.
- **Erreurs** : jamais de `except:` nu ni d'exception avalée. Ressources libérées en `try/finally` ou gestionnaire de contexte.
  Messages clients génériques, traduits via i18n, sans trace, chemin ni contenu ; détail dans les logs avec un identifiant de corrélation.
- **Décisions** structurantes dans `docs/DECISIONS.md` (contexte, décision, alternatives écartées, conséquences).

---

## 5. Sécurité (non négociable)

- **Aucun secret dans le dépôt** : Docker secrets uniquement, générés par `generate-secrets.sh`. Fichiers d'exemple avec des valeurs factices évidentes.
- **Aucun contenu utilisateur dans les logs ni dans le journal d'audit** : ni texte, ni entités détectées, ni noms de fichiers sensibles.
  Seules des métadonnées (utilisateur, type d'entité, nombre, longueur, durée, identifiant de corrélation). Démontre-le par un test.
- **Toute nouvelle route** : derrière `oidc-auth` et le secret de passerelle, avec **son propre routeur Traefik** (limitation de débit et plafond de corps),
  plus un plafond identique dans l'application.
- **Aucun paramètre client ne peut affaiblir la détection** : pas de seuil réglable, pas de liste d'entités désactivables, pas de reconnaisseur fourni par le client.
- **Validation stricte des entrées** : taille, type, encodage ; on rejette plutôt que de corriger silencieusement.
- **Anti-épuisement** : bornes de taille, de durée et de concurrence, par variables `MAX_*` documentées ; une nouvelle fonction ne doit pas
  pouvoir affamer le flux documents (worker unique).
- **Sans état** quand c'est demandé : rien sur disque, rien conservé au-delà de la requête.
- **CORS** : jamais `*`. Par défaut, aucune origine.
- **Pas de sortie réseau** nouvelle.
- **Durcissement des conteneurs** inchangé ou renforcé : seccomp, `read_only`, `cap_drop: ALL`, `no-new-privileges`, limites de ressources, rotation des logs.
- **Modèle de menace** court pour toute nouvelle surface exposée (entrée géante, Unicode piégeux, concurrence, déni de service, énumération).
- **Normalisation Unicode** : toute normalisation avant détection conserve la correspondance des positions avec le texte reçu.
  Un constat d'audit (tiret non-ASCII dans des dates) montre que c'est une source de faux négatifs.
- Compte rendu : section « Revue sécurité » avec les points vérifiés, les points ouverts et les tests à différer au pentest.

---

## 6. Dépendances et versions — vérification obligatoire

Ta connaissance des versions est périmée. **N'écris jamais un numéro de version de mémoire.**

Pour chaque dépendance ajoutée, mise à jour ou touchée (Python, image Docker, outil de développement) :

1. **Besoin** : justifie-la ; préfère la bibliothèque standard, une dépendance existante ou un mécanisme Presidio existant.
   Toute dépendance ajoutée à l'image `app` impose de revalider le profil seccomp (§1) : c'est un coût réel, compte-le.
2. **Dernière version stable**, interrogée à la source au moment du travail (`pip index versions`, API JSON de PyPI, registre officiel de l'image),
   hors préversions et versions retirées. Images Docker : épinglage par condensat (`docker buildx imagetools inspect`).
3. **Vulnérabilités** : `pip-audit`, `osv-scanner`, `trivy` ; recoupement avec une base officielle (NVD, GitHub Advisory Database, OSV).
   Jamais un blog ou une réponse de modèle comme source d'une CVE.
4. **Santé** : dernière publication, maintenance active, obsolescence, **licence compatible avec celle du projet** (AGPL-3.0 selon le README ;
   vérifie le fichier `LICENSE`).
5. **Compatibilité** : version de Python de l'image, cohérence de `presidio/analyzer-build/versions.json` avec l'image de base et avec `app/analyzer_versions.json`,
   notes de version en cas de changement majeur.
6. **Épinglage reproductible** avec empreintes si l'outillage le permet ; dépendances de production et de développement séparées.
7. **Traçabilité** dans `docs/DEPENDENCIES.md` : paquet, version retenue, dernière stable observée, date et source, résultat d'audit, licence, remarque.

Si un outil manque (`pip-audit`, `trivy`…), installe-le dans un environnement isolé (venv ou conteneur jetable), jamais dans l'image ni sur le système.
**Si tu ne peux pas vérifier** (pas de réseau, registre inaccessible) : arrête-toi et signale-le.
**Si la dernière version n'est pas utilisable** : documente la raison, la version visée et la condition de levée dans `docs/DECISIONS.md`.

---

## 7. Gestion des bugs et des constats

1. **Reproduis d'abord** (test qui échoue ou commande reproductible).
2. **Cause racine**, pas symptôme. Interdit : avaler l'erreur, augmenter une limite « pour que ça passe », désactiver un test ou un contrôle,
   assouplir le profil seccomp sans identifier l'appel système en cause.
3. Corrige, vérifie la suite complète, commite test et correctif ensemble.
4. **Hors périmètre : ne corrige pas**, consigne dans `docs/FINDINGS.md`, au format de l'audit, avec un préfixe `EXT-` pour ne pas se confondre
   avec la numérotation de l'audit initial :

   | # | Statut | Constat | Preuve / reproduction | Sévérité | Décision |
   |---|--------|---------|------------------------|----------|----------|

   Statuts : ✅ résolu · 🟡 risque accepté (décision humaine documentée) · ⏳ ouvert. **Tu ne passes jamais un constat en 🟡.**
5. **Règle d'arrêt** : après deux tentatives de correction infructueuses, arrête, décris l'état exact et demande de l'aide.
6. Un **faux négatif de détection est un bug de sécurité** : test de non-régression obligatoire.

---

## 8. Méthode de test (trois niveaux, comme l'audit)

1. **Revue statique** : code, Dockerfiles, configuration ; `ruff`, `mypy`, `bandit`, `pip-audit`, `trivy`.
2. **Tests dynamiques ciblés**, sur la pile complète **avec le profil seccomp bloquant** : entrées malformées, énormes, vides, Unicode piégeux
   (tirets et apostrophes typographiques, espaces insécables, caractères de largeur nulle, formes NFD), concurrence, requêtes non authentifiées,
   requêtes sans secret de passerelle.
3. **Pentest externe** avant toute donnée réelle : liste les tests que tu recommandes d'y différer.

**Environnement** : la VM de test **est** l'environnement de développement, avec une seule pile (voir §1). Avant un test qui écrit dans le journal d'audit
ou les fichiers de travail, note leur état ; n'efface jamais leur contenu sans accord.

---

## 9. Actions interdites ou soumises à accord

**Interdit** : supprimer des volumes ou des données (`docker compose down -v`, `docker volume rm`, `rm -rf` hors du dépôt), vider le journal d'audit,
désactiver une protection, ajouter de la télémétrie, contacter un service externe depuis l'application.

**Soumis à accord explicite** : toute modification de `docker-compose.yml`, de `oauth2-proxy.cfg`, des fichiers Traefik, du profil seccomp ou des Dockerfiles,
**sauf** les changements précisément autorisés par le prompt de phase et la ligne `COPY` d'un nouveau module Python dans `app/Dockerfile`
(D-051, voir §1). Également : nouveau service ou port, changement d'un contrat d'API existant,
nouvelle dépendance de production, migration de données.

---

## 10. Définition de « terminé »

- [ ] Drapeau désactivé : comportement strictement identique (preuve par tests)
- [ ] `ruff`, `mypy`, `bandit`, `pytest --cov` verts ; sorties collées
- [ ] Tests de bout en bout passés **sous `seccomp/app-enforce.json`**
- [ ] Dépendances vérifiées (§6), `docs/DEPENDENCIES.md` à jour, `pip-audit` et `trivy` sans vulnérabilité non traitée
- [ ] Aucun secret, aucune donnée réelle, aucun contenu utilisateur dans les logs ni l'audit (vérifié par test)
- [ ] Documentation utilisateur et admin à jour en français et en anglais ; variables dans `env.fr.example` et `env.en.example`
- [ ] Modèle de menace, revue sécurité, `DECISIONS.md`, `FINDINGS.md` à jour
- [ ] Compte rendu `docs/phase-N-report.md`, qui sépare **observé** et **supposé**
