# Décisions structurantes

Format : contexte, décision, alternatives écartées, conséquences. Statuts : **proposée** (en attente de validation humaine),
**validée** (date et validation humaine), **ouverte** (non tranchée). Les décisions de la phase 1 portent sur l'API texte de l'extension
(`docs/api-extension.md`).

---

## D-001 — Préfixe versionné `/api/v1/` — validée (2026-10-02, validation humaine de l'étape B)

- **Contexte** : les routes existantes suivent le style `/api/<verbe>` sans version. L'extension sera publiée et mise à jour indépendamment du serveur ;
  elle doit détecter une incompatibilité.
- **Décision** : `/api/v1/version`, `/api/v1/text/analyze`, `/api/v1/text/pseudonymize`. `api_version` (majeure.mineure) dans `/version` :
  la mineure augmente pour un ajout compatible (champ de réponse facultatif), la majeure change le préfixe d'URL.
- **Alternatives écartées** : `/api/text/analyze` (style existant) — pas de moyen propre de faire coexister deux versions ; version dans un en-tête —
  invisible dans les journaux Traefik et plus fragile à router.
- **Conséquences** : un seul routeur Traefik `PathPrefix(/api/v1/)` couvre toutes les routes de l'extension, y compris les futures.
  Aucune route existante ne commence par `/api/v1/` (vérifié dans `main.py`).

## D-002 — Positions en points de code et en unités UTF-16 — validée (2026-10-02, validation humaine de l'étape B)

- **Contexte** : Python indexe les chaînes en points de code ; JavaScript (l'extension) en unités UTF-16. Ils divergent dès qu'un caractère hors
  plan de base (émoji…) précède l'entité. Une position fausse côté client fait masquer le mauvais passage : faux négatif silencieux.
- **Décision** : renvoyer les deux (`start`/`end` et `start_utf16`/`end_utf16`), calculés sur la chaîne reçue.
- **Alternatives écartées** : points de code seuls (charge la conversion sur chaque client, source d'erreurs) ; paramètre client choisissant l'unité
  (inutilement complexe).
- **Conséquences** : coût de calcul linéaire négligeable ; tests dédiés avec émojis et formes NFD.

## D-003 — Pas de score dans les réponses — validée (2026-10-02, validation humaine de l'étape B)

- **Contexte** : doctrine §0.2, les scores ne sont pas calibrés (0,85 fixe pour spaCy). Un score exposé invite le client à filtrer.
- **Décision** : aucun score dans `analyze`.
- **Alternatives écartées** : renvoyer le score avec un avertissement dans la documentation — le risque d'usage est réel et le bénéfice nul pour l'extension.
- **Conséquences** : le diagnostic des scores reste possible dans les journaux serveur (types et scores, jamais le texte, comme aujourd'hui).

## D-004 — Marqueurs `⟦TYPE_N⟧`, fusion des chevauchements — validée (2026-10-02, format `⟦TYPE_N⟧` retenu)

- **Contexte** : les marqueurs doivent être non ambigus, ne pas apparaître naturellement dans un texte, et survivre à l'aller-retour par un modèle d'IA.
- **Décision** : `⟦` U+27E6 et `⟧` U+27E7, type d'entité en ASCII majuscule, numéro par type à partir de 1. Même valeur exacte, même marqueur.
  Numéro sauté si le marqueur existe déjà dans le texte reçu. Chevauchements fusionnés (union) ; type de l'entité la plus longue, puis `SECRET`,
  puis ordre alphabétique.
- **Alternatives écartées** : `<PERSON_1>` (balises HTML/XML et génériques de code), `[[PERSON_1]]` (syntaxe des liens MediaWiki),
  `{{PERSON_1}}` (gabarits Jinja/Mustache), jetons aléatoires (illisibles pour le modèle d'IA, qui perd le sens du prompt).
- **Conséquences** : le banc d'essai ne peut pas mesurer la survie au modèle d'IA (hors ligne) ; à vérifier par l'extension. Alternative de repli
  documentée si le format pose problème : `[[TYPE_N]]` avec la même règle de non-collision.

## D-005 — 503 (et non 502) quand Presidio est indisponible, sur les routes v1 — validée (2026-10-02, validation humaine de l'étape B)

- **Contexte** : le flux documents renvoie 502 avec un message codé en dur (EXT-17). Le prompt de phase demande 503.
- **Décision** : 503 et `Retry-After` sur les routes v1, message traduit ; le flux documents n'est pas modifié.
- **Conséquences** : deux codes différents pour la même panne selon le flux, documenté.

## D-006 — Plafond de corps propre aux routes v1, dérivé de `MAX_TEXT_CHARS` — validée (2026-10-02, validation humaine de l'étape B)

- **Contexte** : `MAX_REQUEST_BODY_BYTES` (27 Mio) est global. Le lever ou l'abaisser changerait le flux documents. De plus, une exception levée depuis
  `receive` est convertie en 400 par le middleware de `branding.py` (EXT-22).
- **Décision** : les routes v1 lisent elles-mêmes le flux du corps, avec un contrôle préalable de `Content-Length` et un arrêt dès que
  `12 × MAX_TEXT_CHARS + 4096` octets sont dépassés ; 413 levé dans le code de la route, pas dans `receive`. Même valeur en bordure (Traefik).
- **Alternatives écartées** : variable `MAX_TEXT_BODY_BYTES` séparée (deux réglages à garder cohérents au lieu d'un) ; limite par préfixe dans
  `_RequestBodyLimitMiddleware` (modifie un composant partagé et hérite d'EXT-22).
- **Conséquences** : le label Traefik doit être recalculé si `MAX_TEXT_CHARS` change (commentaire à côté).

## D-007 — Journal d'audit séparé `audit-extension.log` — validée (2026-10-02, réponse humaine à l'étape A)

- **Contexte** : la rotation de `audit.log` (10 Mio × 10) évincerait l'historique des documents si chaque prompt y écrivait une ligne.
- **Décision** : second `RotatingFileHandler` dans `/data/audit/`, même format JSON, rotation indépendante. Pas de changement de volume ni de Compose.
- **Conséquences** : `GET /api/audit` ne montre pas les événements de prompts (consultation sur l'hôte) ; une route de consultation dédiée est hors
  périmètre (prompt de phase).

## D-008 — Limitation de débit par utilisateur (`X-Auth-Request-User`) — validée (2026-10-02, validation humaine de l'étape B)

- **Contexte** : la clé par IP pénalise les utilisateurs derrière un même NAT ou proxy d'entreprise.
- **Vérifié** (Traefik 3.7, documentation et code source) : `sourceCriterion.requestHeaderName` existe ; `forwardAuth` supprime puis repose les
  `authResponseHeaders` (`pkg/middlewares/auth/forward.go`, boucle sur `fa.authResponseHeaders` : `req.Header.Del` puis copie) ; un en-tête absent
  donne la clé vide (`vulcand/oxy` `makeHeaderExtractor` : `req.Header.Get`), sans erreur.
- **Décision** : `text-ratelimit` après `oidc-auth`, clé `X-Auth-Request-User`.
- **Alternatives écartées** : IP (NAT) ; `X-Auth-Request-Email` (peut manquer si le jeton n'a pas d'adresse ; supposé, à vérifier) ;
  limitation applicative par utilisateur (état en mémoire supplémentaire, redondant avec Traefik).
- **Conséquences** : stockage en mémoire de Traefik (une seule instance, pas de Redis) ; vérification de bout en bout à l'étape F.

## D-009 — Analyse du corps dans la route, sans le 422 par défaut de FastAPI — validée (2026-10-02, validation humaine de l'étape B)

- **Contexte** : le 422 par défaut de FastAPI renvoie un champ `input` qui recopie la valeur fautive, donc potentiellement le prompt.
  Remplacer le gestionnaire global modifierait aussi le flux documents.
- **Décision** : les routes v1 lisent le corps brut (D-006), le valident par `model_validate_json`, et convertissent les erreurs en messages
  génériques traduits (400 pour JSON invalide, 422 sinon).
- **Conséquences** : la documentation OpenAPI automatique ne décrit pas le corps ; le contrat de référence est `docs/api-extension.md`.

## D-010 — Authentification de l'extension : options pour la phase suivante — ouverte (phase suivante)

- **Contexte** : `oauth2-errors` transforme le 401 en 302 vers la connexion. Une extension qui attend du JSON ne sait pas traiter cette redirection.
- **Options** :
  1. **Routeur `/api/v1/` sans `oauth2-errors`** : 401 brut au lieu de 302. Simple, aucun changement d'oauth2-proxy. L'extension doit alors ouvrir
     elle-même la page de connexion. Sécurité inchangée (`oidc-auth` toujours exigé).
  2. **Jetons Bearer acceptés par oauth2-proxy** (`skip_jwt_bearer_tokens`, `extra_jwt_issuers`) : l'extension obtient un jeton OIDC (PKCE) auprès
     du fournisseur d'identité. Conséquences : client OIDC public à déclarer dans Keycloak puis Entra ID ; vérification stricte de l'audience
     indispensable (sinon tout jeton du même émetteur serait accepté) ; durée de vie et révocation des jetons ; stockage du jeton dans l'extension.
  3. **Réutilisation du cookie de session du navigateur** (`host_permissions` de l'extension) : rien à changer côté serveur. Conséquences : session
     de 1 h (`cookie_expire`), renouvellement par une ouverture de page ; dépend de la politique de cookies du navigateur (`SameSite=Lax`,
     cookies tiers) ; mêlé à la session web de l'utilisateur.
  4. **Jetons d'API propres à Obfusk8** : gestion du cycle de vie (émission, révocation, stockage haché) à construire et à auditer. Écartée sauf besoin
     hors OIDC.
- **Recommandation** : 1 en complément de 3 pour un premier pilote ; 2 pour la production.

## D-011 — Révision humaine déléguée à l'extension — validée (2026-10-02, réponse humaine à l'étape A)

- **Contexte** : la doctrine rend la révision humaine obligatoire ; une API de pseudonymisation ne peut pas l'imposer.
- **Décision** : documenté dans le contrat. L'extension (phase ultérieure) affiche les détections et permet de corriger avant l'envoi.
- **Conséquences** : l'API renvoie des positions exploitables (D-002) pour cet affichage.

## D-012 — Propagation des valeurs exactes à tous les types (routes texte) — validée (2026-10-02, validation humaine de l'étape B)

- **Contexte** : le flux documents ne propage que PERSON et LOCATION (avec variantes de casse et d'ordre). Pour un prompt pseudonymisé,
  une autre occurrence non détectée d'une valeur déjà détectée (courriel, identifiant…) fuirait en clair à côté de son marqueur.
- **Proposition** : en plus de la propagation existante (réutilisée telle quelle), signaler toute autre occurrence exacte d'une valeur détectée,
  quel que soit son type, à partir de 3 caractères (même seuil que le flux documents).
- **Risque** : quelques faux positifs (une année répétée détectée une fois comme date). Conforme à la doctrine §0.1.
- **Validée** : recommandation retenue (frontières de mot, voir l'implémentation).

## D-013 — Source de `presidio_version` — validée (2026-10-02, validation humaine de l'étape B)

- **Contexte** : l'API REST de Presidio n'expose pas sa version (`/health` renvoie un texte fixe).
- **Options** : (a) variable d'environnement dans `docker-compose.yml`, à mettre à jour avec l'image (changement de Compose hors de la liste autorisée,
  accord requis ; risque de dérive) ; (b) `null` et `detection_config` seul ; (c) empreinte de `GET /recognizers?language=fr`, interrogée au premier appel
  (reflète les reconnaisseurs effectivement chargés, pas le numéro de version).
- **Décision** (recommandation retenue) : (c), exposée dans un champ distinct `analyzer_recognizers`, et `presidio_version: null` tant que (a) n'est pas autorisé.

## D-014 — Normalisation Unicode supplémentaire — principe validé (2026-10-02) ; décision finale après la mesure de l'étape E

- **Contexte** : la normalisation actuelle (majuscules, tirets) conserve la longueur. Espaces insécables, caractères de largeur nulle, formes NFD et
  apostrophes typographiques peuvent causer des faux négatifs, mais une normalisation qui change la longueur exige une table de correspondance des positions.
- **Décision** : rien d'ajouté avant mesure ; le banc d'essai (étape E) quantifie chaque variante. Toute normalisation ajoutée conservera la correspondance
  des positions avec le texte reçu (`CLAUDE.md` §5) et sera testée.
- **Mesure (étape E, 2026-10-02)** : rappel en masquage par variante — largeur nulle 0,646, espaces insécables 0,789, NFD 0,810, chiffres
  espacés 0,846, contre 0,919 sans variante (`benchmarks/results/quality-20261002T091416.md`, EXT-26 à EXT-30).
- **Proposition pour la phase suivante (à valider)** : avant analyse, supprimer les caractères de format (largeur nulle, contrôles bidirectionnels),
  remplacer les espaces insécables par une espace et recomposer en NFC, **avec une table de correspondance** des positions vers le texte reçu ;
  appliquer au flux documents comme au flux texte (même fonction), tests de non-régression sur chaque variante du corpus.

## D-015 — Reconnaisseurs propres à l'API texte dans `app/themes/extension/` — validée (2026-10-02, validation humaine de l'étape B)

- **Contexte** : secrets (validé : fichier séparé) et couverture d'EXT-08 (carte bancaire, NIR ; validé : à couvrir). `_load_themes` charge tout
  `app/themes/*.json` comme thème sélectionnable ; un nouveau répertoire à la racine d'`app/` exigerait de modifier le Dockerfile (accord requis).
- **Décision** : sous-répertoire `app/themes/extension/` (non parcouru par le `glob` non récursif, copié par le `COPY themes/` existant). Reconnaisseurs
  ponctuels envoyés à Presidio par le mécanisme existant (`ad_hoc_recognizers`), ajoutés pour les seules routes texte, quel que soit le thème.
  Le flux documents reste strictement inchangé.
- **Conséquences** : EXT-08 et EXT-23 restent ouverts pour le flux documents (hors périmètre).
- **Mise en œuvre (étape D)** : `secrets.json` (type `SECRET`) et `identifiers.json` (`CREDIT_CARD`, `FR_NIR`, `EMAIL_ADDRESS` à domaine libre).
  Le NIR est **repris par référence** au thème médical (`include_theme_recognizers`), pas recopié : une correction du thème s'applique ici aussi,
  et un nom introuvable fait échouer le démarrage. Numéro de carte : même motif que le `CreditCardRecognizer` de Presidio 2.2.364, **sans** contrôle
  de Luhn (impossible dans un reconnaisseur ponctuel) ; faux positifs acceptés (doctrine §0.1), score 0,6 pour garder une marge au-dessus de tout seuil.
  Chaque format de secret est sourcé dans le nom du motif. Les marqueurs sont écrits insensibles à la casse là où `main.py` normalise les mots en
  majuscules avant l'analyse (défaut trouvé par le banc d'essai de bout en bout, test de non-régression ajouté).

## D-016 — Tests exécutés dans un conteneur jetable sous le profil seccomp — validée (2026-10-02, validation humaine de l'étape B)

- **Contexte** : EXT-09, lancer `pytest` dans le conteneur de production écrit dans le vrai journal d'audit et le vrai répertoire de travail.
- **Décision** : pendant la phase, tests unitaires dans un conteneur jetable (image de production, `--security-opt seccomp=seccomp/app-enforce.json`,
  `--read-only`, `--network none`, `tmpfs` à la place des volumes). Outils de développement (ruff, mypy, bandit, pytest-cov, pip-audit) dans
  `app/requirements-dev.txt`, installés dans un environnement isolé, jamais dans l'image.
- **Conséquences** : la présence de `pytest` dans l'image (EXT-09) n'est pas corrigée dans cette phase.

## D-017 — Module `regex` de développement à la version de l'analyseur — validée par délégation (étape D, 2026-10-02)

- **Contexte** : Presidio compile les reconnaisseurs ponctuels avec le module `regex` (et non `re`), avec `IGNORECASE | DOTALL | MULTILINE`
  (vérifié dans `pattern_recognizer.py` de l'image `presidio-analyzer`). Tester les motifs avec `re` donnerait des résultats différents
  (quantificateurs possessifs, regards arrière de longueur variable).
- **Décision** : dépendance de **développement** `regex==2026.7.10`, la version présente dans l'image de l'analyseur, et non la dernière stable
  (2026.9.29) : la parité du moteur prime ici. Aucune vulnérabilité connue (OSV) pour 2026.7.10.
- **Condition de levée** : réaligner sur la version de l'analyseur à chaque reconstruction de son image.

## D-018 — Heuristiques des affectations de secrets — validée par délégation (étape D, 2026-10-02)

- **Contexte** : `password = valeur`, `"api_key": "valeur"`, `mot de passe : valeur` sont la forme la plus fréquente d'un secret dans un prompt,
  mais du code ordinaire contient les mêmes clés sans secret.
- **Décision** : seule la **valeur** est marquée ; clé non précédée d'une lettre ou d'un chiffre (`DB_PASSWORD`, `spring.datasource.password`) ;
  séparateur et valeur **sur la même ligne** ; exclusion des valeurs qui sont des références (`os.environ`, `process.env`, `${…}`, `{{…}}`, `<…>`),
  des littéraux (`true`, `false`, `null`…) et des expressions de code (valeur suivie de `(` ou `[`). Ces exclusions viennent des faux positifs
  mesurés de bout en bout (3 sur 30 textes au premier passage), chacun couvert par un test.
- **Risque accepté par construction** : une vraie valeur secrète qui commencerait par `$` ou serait suivie de `(` ne serait pas marquée par cette
  règle (elle peut l'être par une autre : préfixe de fournisseur, URI). Le corpus de l'étape E sert de contrôle indépendant.
