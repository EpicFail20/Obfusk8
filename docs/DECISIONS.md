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

## D-010 — Authentification de l'extension : options pour la phase suivante — option 1 retenue pour la phase 2 (D-020) ; options 2 et 3 ouvertes (début de phase 3)

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

## D-014 — Normalisation Unicode supplémentaire — validée (2026-10-04, décision humaine de l'étape A de la phase 2) ; mise en œuvre en phase 2, étape B

- **Contexte** : la normalisation actuelle (majuscules, tirets) conserve la longueur. Espaces insécables, caractères de largeur nulle, formes NFD et
  apostrophes typographiques peuvent causer des faux négatifs, mais une normalisation qui change la longueur exige une table de correspondance des positions.
- **Décision** : rien d'ajouté avant mesure ; le banc d'essai (étape E) quantifie chaque variante. Toute normalisation ajoutée conservera la correspondance
  des positions avec le texte reçu (`CLAUDE.md` §5) et sera testée.
- **Mesure (étape E, 2026-10-02)** : rappel en masquage par variante — largeur nulle 0,646, espaces insécables 0,789, NFD 0,810, chiffres
  espacés 0,846, contre 0,919 sans variante (`benchmarks/results/quality-20261002T091416.md`, EXT-26 à EXT-30).
- **Proposition pour la phase suivante (à valider)** : avant analyse, supprimer les caractères de format (largeur nulle, contrôles bidirectionnels),
  remplacer les espaces insécables par une espace et recomposer en NFC, **avec une table de correspondance** des positions vers le texte reçu ;
  appliquer au flux documents comme au flux texte (même fonction), tests de non-régression sur chaque variante du corpus.
- **Mise en œuvre (phase 2, étape B)** : `app/text_normalization.py`, module séparé (réponse Q2 du 2026-10-04, ligne `COPY` explicite dans
  `app/Dockerfile`). Caractères de format supprimés ; espaces spéciales (Zs) et contrôles hors tabulation et sauts de ligne (dont U+0000,
  EXT-38) remplacés par une espace ; séparateurs de ligne et de paragraphe remplacés par un saut de ligne ; ligatures U+FB00-FB06
  développées (observées dans les PDF à l'étape B) ; apostrophes typographiques ; recomposition NFC par grappe. Chaque caractère du résultat
  connaît son intervalle d'origine ; un intervalle détecté couvre les caractères supprimés entre son premier et son dernier caractère.
  Point d'entrée unique `main._analyze_normalized`, utilisé par le PDF, l'image, le DOCX, le CSV et, via `_detect_text_blocks`, l'API texte ;
  les normalisations existantes (majuscules, tirets) s'appliquent ensuite. Texte sans rien à normaliser : chemin identité, l'analyseur reçoit
  exactement le même texte qu'avant.
- **Alternatives écartées** : NFKC complète (change des chiffres, exposants et lettres compatibles au-delà du besoin mesuré, et rend la
  correspondance plus coûteuse) ; suppression des caractères de contrôle (U+0000 entre deux mots doit séparer les mots, EXT-38) ;
  normalisation par l'analyseur (positions perdues côté application).

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

---

# Phase 2 — serveur prêt pour un pilote

Décisions humaines du 2026-10-04 (validation de l'étape A et suite du projet). Celles qui concernent les phases suivantes sont
**consignées sans être implémentées**.

## D-019 — Un fil d'exécution unique pour tout usage de PyMuPDF — validée (décision humaine du 2026-10-04)

- **Contexte** : la documentation officielle de PyMuPDF indique « PyMuPDF does not support running on multiple threads - doing so may cause
  incorrect behaviour or even crash Python itself » (`recipes-multiprocessing`). Or `preview_image`, route synchrone, utilise déjà PyMuPDF dans
  le groupe de fils de Starlette, potentiellement en parallèle (EXT-34) ; et sortir le flux documents de la boucle d'événements (EXT-07) ajoute
  un second usage hors du fil principal.
- **Décision** : un seul fil d'exécution dédié exécute **tout** le traitement qui touche PyMuPDF (détection, finalisation et `preview_image`
  compris). Le débit reste celui d'aujourd'hui (un document à la fois), PyMuPDF n'est jamais utilisé par deux fils, et la boucle d'événements
  reste libre pour les autres requêtes (API texte, `/health`).
- **Alternatives écartées** : verrou global autour des appels PyMuPDF depuis le groupe de fils de Starlette (plusieurs fils différents
  utiliseraient PyMuPDF à tour de rôle, ce que la documentation ne garantit pas) ; plusieurs fils en parallèle (interdit par la documentation).
- **Reportée** : la piste **multiprocessus**, recommandée par la documentation de PyMuPDF pour paralléliser, est reportée à une phase ultérieure
  (coût mémoire avec 1 Go et un CPU, revalidation du profil seccomp).
- **Conséquences** : corrige EXT-34 ; la modification de `preview_image` est autorisée par cette décision.

## D-020 — Authentification de l'extension en phase 2 : 401 en texte brut — validée (décision humaine du 2026-10-04)

- **Contexte** : D-010, option 1. Mesuré à l'étape A : sans `oauth2-errors`, oauth2-proxy v7.15.4 répond `401`, `Content-Type: text/plain`,
  corps `Unauthorized\n`, quel que soit l'en-tête `Accept`, et Traefik v3.7.13 relaie cette réponse telle quelle (`forward.go`, branche non 2xx).
  Le prompt de phase demandait un 401 en JSON.
- **Décision** : le 401 en texte brut est accepté ; le client se fie **au code HTTP seul**. Seule l'option 1 de D-010 est mise en œuvre en phase 2 ;
  elle est nécessaire quelle que soit la suite. Le choix entre les options 2 et 3 de D-010 se fera au début de la phase 3.
- **Alternatives écartées** : middleware `errors` de Traefik vers un service ou une route qui fabrique du JSON — surface d'attaque ajoutée pour un
  gain cosmétique.
- **Conséquences** : contrat FR/EN mis à jour (étape F).

## D-021 — Priorité et ordre des phases — consignée (décision humaine du 2026-10-04)

La sécurité de l'application et du serveur est la **priorité absolue**. Ordre retenu :
1. phase 2 (en cours) : serveur prêt pour un pilote (détection, boucle d'événements, 401, mesures) ;
2. phase 2 bis : chaîne d'approvisionnement et durcissement des images (EXT-01, EXT-09, EXT-11, EXT-12, EXT-13, EXT-16, EXT-21, EXT-24, EXT-33) ;
3. phase 3 : extension de navigateur en panneau latéral, dépôt séparé ;
4. phase « mode forcé » : blocage des dépôts et collages directs sur les sites d'IA (D-024) ;
5. pentest externe avant toute donnée réelle.

Justification : l'extension ne vaut que ce que vaut la détection, et son contrat d'authentification doit être stable avant d'écrire le client.

## D-022 — Modèle de menace de l'extension — consignée (décision humaine du 2026-10-04)

On se protège de **l'erreur humaine** et d'un **contournement trop aisé** par l'utilisateur (sauter l'étape par habitude ou par commodité).
On ne prétend pas arrêter un initié déterminé : capture d'écran, téléphone ou poste personnel restent hors de portée d'une extension ; ce périmètre
relève des règles d'usage, du blocage réseau et de la formation. Tout choix de conception de l'extension se justifie par rapport à ce modèle.

## D-023 — Documents : l'extension redirige vers l'interface existante — consignée (décision humaine du 2026-10-04)

- **Décision** : l'extension ne traite **aucun** document. Son panneau ne gère que le texte (analyse, pseudonymisation, restauration locale).
  Pour un document, un bouton ouvre l'interface web obfusk8 existante dans un nouvel onglet, avec la même session ; l'utilisateur y dépose,
  révise, finalise et télécharge son fichier caviardé, puis le dépose lui-même dans l'IA.
- **Justification** : la révision humaine existante est éprouvée ; une seconde interface de révision doublerait les bugs et la maintenance.
- **Conséquence** : les corrections serveur de la phase 2 (EXT-07, EXT-34, EXT-35) bénéficient directement à ce parcours.

## D-024 — Mode forcé : contrôle des dépôts de fichiers par suivi des téléchargements — consignée (décision humaine du 2026-10-04)

- **Retenu** : l'extension observe les téléchargements du navigateur (API `chrome.downloads`). Un fichier caviardé téléchargé **depuis l'origine
  du serveur obfusk8** est noté localement (nom, taille, heure). Lors d'un dépôt sur un site d'IA, nom, taille et date de modification du fichier
  déposé sont comparés à cette liste : correspondance, le dépôt passe ; sinon il est bloqué et le panneau propose d'ouvrir obfusk8.
- **Justification** : décision **synchrone** (nom, taille et date lisibles immédiatement dans l'événement de dépôt) ; seuls les mauvais fichiers
  sont bloqués, les bons passent sans réinjection par script (fragile, refusée par certains sites) ; aucun registre côté serveur.
- **Écartés** : préfixe ou clé dans le nom non liée au contenu (un renommage le contourne) ; clé liée au contenu ou registre d'empreintes côté
  serveur (sûr, mais exige de lire le fichier : décision asynchrone, donc blocage puis réinjection fragile).
- **Option reportée** : double vérification par empreinte (premier dépôt bloqué, empreinte vérifiée et mémorisée, second dépôt accepté), si un usage
  exige une garantie plus forte.
- **À valider par un prototype** avant engagement, sur deux ou trois sites d'IA : interception synchrone du dépôt (glisser-déposer, sélection de
  fichier, collage), fiabilité de la date de modification du fichier téléchargé selon les systèmes, permission `downloads`.
- **Préparation en phase 2, sans implémentation** : relever comment la finalisation livre le fichier (URL, `Content-Disposition`, nom produit)
  et signaler ce qui rendrait le suivi fragile (compte rendu de phase).

## D-025 — Gestion des branches — consignée (décision humaine du 2026-10-04)

- `feat/pilote-serveur` reste **locale**, créée depuis `feat/text-api` ; aucun commit sur `feat/text-api` ni réécriture de son historique pendant la phase.
- Après validation de la phase 2, l'humain avance `feat/text-api` par `git merge --ff-only feat/pilote-serveur` et pousse `feat/text-api`.
- La fusion dans `main` se fera à la fin, en une fois ; les commits de `origin/main` absents de la branche (`ca3c50c`, licence AGPL-3.0 ;
  `7a7908e`, icône) y seront intégrés à ce moment, ce qui résoudra EXT-05. Ils ne sont pas intégrés avant.

## D-026 — Repli de localisation PDF par positions de caractères (EXT-35) et correction d'EXT-37 — validée (décision humaine du 2026-10-04)

- **Contexte** : EXT-35, mesuré à l'étape B (`benchmarks/results/pdf-localization-20261004T134306.md`) : un glyphe sans correspondance Unicode
  est extrait en U+0000, qui tronque la chaîne recherchée par `page.search_for` ; 6 noms partiellement exposés après caviardage. EXT-37 : une
  chaîne qui commence par U+0000 fait renvoyer `None` à `search_for`, d'où un plantage de la passe 2 et un PDF valide refusé.
- **Décision** : option R1. Quand `search_for` ne renvoie rien, ou laisse un caractère de la valeur non couvert, les rectangles manquants sont
  construits à partir des boîtes des caractères de la valeur (`get_text("rawdict")`, alignées sur `get_text()`), regroupées par ligne. Le
  comportement est inchangé quand `search_for` couvre toute la valeur. Ce qui reste non couvert après le repli (caractère sans boîte) reste
  signalé (avertissement, audit). EXT-37 corrigé : `None` traité comme une liste vide.
- **Alternatives écartées** : R2, découpage de la chaîne recherchée aux U+0000 (fragments courts trouvés ailleurs sur la page, une seule cause
  traitée) ; R3, caviardage de la ligne entière (masque bien plus que la valeur).
