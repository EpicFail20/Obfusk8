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
- **Conséquences** : EXT-08 et EXT-23 restent ouverts pour le flux documents (hors périmètre). **Phase 2** (réponse Q4 du 2026-10-04) : carte,
  NIR et courriel à domaine libre déplacés dans `app/themes/common.json`, appliqués à tous les flux ; `identifiers.json` supprimé ; le NIR reste
  repris par référence au thème médical (`include_theme_recognizers`, désormais aussi lu dans `common.json`) ; un même reconnaisseur commun et
  de thème n'est envoyé qu'une fois à Presidio.
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

---

# Décisions sur le compte rendu de la phase 2 (décision humaine du 2026-10-05)

Consignées depuis `docs/phase-2-report.md` §12. Les points 2, 3, 4, 5, 6 et le contrôle d'identité du point 9 seront mis en œuvre dans une
**phase 2.1** (branche locale `feat/detection-2-1`, créée depuis `feat/text-api` après la poussée), pas avant l'accord humain.

## D-027 — Filtre PROPN (EXT-18) conservé à titre provisoire — décision humaine du 2026-10-05

- **Contexte** : mesuré en phase 2, aucun faux négatif imputable au filtre sur les corpus synthétiques, 90 faux positifs évités.
- **Décision** : filtre conservé à titre provisoire. Une variante « noms en minuscules » (style messagerie) est ajoutée au corpus de mesure et son
  effet est mesuré avec et sans filtre avant une décision définitive (le filtre rejette les entités sans jeton PROPN, ce que des noms en
  minuscules peuvent provoquer).

## D-028 — Détection des secrets étendue à tous les flux (EXT-42) — décision humaine du 2026-10-05, phase 2.1

- **Décision** : les reconnaisseurs de secrets s'appliquent aussi au flux documents, après mesure des faux positifs sur des documents.

## D-029 — Jonction des lignes d'un même bloc PDF pour l'analyse (EXT-41) — décision humaine du 2026-10-05, phase 2.1

- **Décision** : pour l'analyse, les sauts de ligne internes à un bloc sont joints. Le test vérifie **dans le PDF final** que chaque morceau de
  la valeur est caviardé sur chaque ligne.

## D-030 — `detection_config` inclut une version de la normalisation — décision humaine du 2026-10-05, phase 2.1

- **Décision** : l'empreinte de `/version` intègre une version de la normalisation ; contrat FR/EN mis à jour.

## D-031 — Type retenu lors d'un chevauchement — décision humaine du 2026-10-05, phase 2.1

- **Décision** : le type le plus spécifique selon un ordre de priorité fixe et documenté (NIR, IBAN, carte, téléphone…). La zone masquée reste
  **l'union** des détections : le choix du type ne réduit jamais le masquage.

## D-032 — Motif « titre ou fonction + Prénom Nom » (EXT-32) — décision humaine du 2026-10-05, phase 2.1

- **Décision** : reconnaisseur commun avec une liste adaptée au milieu hospitalier (Dr, Pr, Mme, M., IDE, AS, cadre, interne…), faux positifs mesurés.

## D-033 — Objectifs de latence en valeur absolue — décision humaine du 2026-10-05

- **Contexte** : écart de la phase 2 sur les petits prompts (+5 ms à 200 caractères, +11 ms à 1 000), accepté.
- **Décision** : désormais, objectifs de latence en valeur absolue : **p95 < 100 ms sous 2 000 caractères**.

## D-034 — Zone « Antécédents » disparue : écart accepté — décision humaine du 2026-10-05

- **Décision** : écart accepté (faux positif supprimé par la recomposition NFD), sous réserve de confirmer que c'est la seule zone disparue sur
  l'ensemble des documents de référence, avec l'image reconstruite depuis le Dockerfile.

## D-035 — Comptes sans courriel et identité obligatoire sur `/api/v1/` — décision humaine du 2026-10-05

- **Décision** : l'humain rend l'attribut email facultatif dans Keycloak et crée le compte de test ; la vérification est faite ensuite.
  Dans tous les cas, l'application refuse (**403**) toute requête `/api/v1/` dont l'identité `X-Auth-Request-User` est vide ou absente, avec
  test (phase 2.1).

## D-036 — Latence à plusieurs utilisateurs acceptée pour un pilote restreint — décision humaine du 2026-10-05

- **Décision** : acceptée ; un second worker de l'analyseur sera réévalué avec les métriques du pilote.

## D-037 — Masquage de la version d'oauth2-proxy (EXT-44) — décision humaine du 2026-10-05

- **Décision** : `footer = "-"` dans `oauth2-proxy/oauth2-proxy.cfg` (option documentée : « Use "-" to disable default footer. (Can be used to
  obfuscate the version) »), appliqué en phase 2.

## D-038 — Jeu de test indépendant (holdout) hors dépôt — décision humaine du 2026-10-05

- **Décision** : un corpus indépendant, `~/obfusk8-holdout.jsonl` (hors dépôt, même format que le corpus de qualité), est mesuré par le banc de
  qualité sans que son contenu soit jamais affiché ni lu en dehors du banc ; seuls des chiffres agrégés sont rapportés.
- **Conséquences** : le rapport de ce corpus ne contient ni identifiant d'invite, ni catégorie, ni texte.

## D-039 — Priorité au prototype sûr ; phase 2.1 annulée — décision humaine du 2026-10-05

- **Contexte** : la phase 2 a atteint ses objectifs de détection mesurés ; il reste des familles de faux négatifs connues (EXT-18, EXT-32,
  EXT-41, EXT-42, EXT-46…) dont chaque correction demande banc et arbitrage.
- **Décision** : la priorité du projet est un **prototype fonctionnel, bien conçu et sûr**. La qualité de détection devient de l'amélioration
  continue et n'est plus un préalable aux phases suivantes. La phase 2.1 est annulée : ses points de sécurité (D-035, EXT-39) passent en
  phase 2 bis, ses points de qualité au backlog `docs/BACKLOG-detection.md`. D-028 à D-032 restent valables et y sont reportées.
- **Conséquences** : la révision humaine obligatoire et un avertissement clair à l'utilisateur restent la protection contre les faux négatifs
  résiduels (doctrine §0.1 inchangée). Ordre des phases de D-021 : la phase 2 bis précède la phase 3.

## D-040 — Choix de l'étape A de la phase 2 bis — décision humaine du 2026-10-05

Recommandations de l'inventaire de l'étape A, toutes validées :

1. **Service `presidio-anonymizer` retiré** (EXT-20) : jamais appelé pour anonymiser ; il ne servait qu'au contrôle de santé, à une jauge et à
   un `depends_on`. Le contrôle de santé, la jauge pour ce service et `_anonymize_text` (code mort) sont retirés du code applicatif.
2. **Python 3.12** conservé pour `app` (3.12.15, maintenu en sécurité jusqu'en 2028, même série que l'image Presidio) ; 3.14 écarté pour
   éviter la revalidation du profil seccomp sur un nouvel interpréteur sans bénéfice de sécurité.
3. **Keycloak 26.0 → 26.8.0**, après sauvegarde du volume `keycloak-data` (migration de schéma irréversible) : 26.0.8 contient
   CVE-2026-18963 (prise de contrôle de compte sans authentification, corrigée en 26.7.2).
4. **oauth2-proxy v7.15.5** (correctifs GHSA-63jm-59jj-478j et GHSA-wr5q-7wxw-x568), **busybox 1.38.0**, actions GitHub aux dernières
   versions majeures (Node 24).
5. **Schéma d'étiquetage** : images tierces `nom:version@sha256:…` ; images construites `ghcr.io/epicfail20/obfusk8-<service>:X.Y.Z@sha256:…`
   une fois publiées, construction locale par `docker-compose.build.yml` (Compose refuse une étiquette de construction contenant un
   condensat : « build tag cannot contain a digest », observé) ; workflow : `X.Y.Z` et `sha-<commit>` sur une étiquette git `vX.Y.Z`, plus de
   `latest` ni de `main`. **Version de cette phase : `0.2.0-dev`, en local uniquement** (aucune publication, aucune étiquette git).
6. **IP fixe pour Traefik** sur le réseau `app-internal` (IPAM), pour restreindre `trusted_proxy_ip` d'oauth2-proxy à Traefik seul.
7. **`libgl1` retiré** de l'image `app` : chargé par aucun processus (observé dans `/proc/<pid>/maps`), il tire `mesa`, LLVM (qui lie la
   libxml2 du système) et les bibliothèques X11 ; retrait conditionné aux tests PDF et OCR sous seccomp.
8. **docker-socket-proxy v0.5.0** : vulnérabilités Alpine corrigeables (openssl, pcre2) sans nouvelle version amont ; pas d'image dérivée
   maison, surveillance de la publication amont.
9. **D-035** : 403 sur `/api/v1/` si `X-Auth-Request-User` **ou** `X-Auth-Request-Email` est absent ou vide (l'application identifie
   l'utilisateur par le courriel) ; le repli « inconnu » du flux documents est consigné (EXT-47), non corrigé.

## D-016 (mise à jour) — Tests montés dans un conteneur jetable de l'image de production — phase 2 bis, 2026-10-05

- **Contexte** : EXT-09 corrigé, l'image de production ne contient plus ni `pytest` ni les tests.
- **Décision** : `app/run-tests.sh` lance la suite dans un conteneur jetable de l'image de production, sous `seccomp/app-enforce.json`,
  `--read-only`, `--network none`, tmpfs à la place des volumes ; `app/tests` est monté en lecture seule dans `/app/tests` (on teste le
  **code de l'image**, pas celui du dépôt), les outils de `app/requirements-dev.txt` sont montés en lecture seule et ajoutés au
  `PYTHONPATH`. Le script échoue avant tout test si l'image contient `pytest` ou `/app/tests`.
- **Alternative écartée** : une étape `test` dans le Dockerfile (image de test dérivée) : un second artefact à construire et à garder
  cohérent, alors que le montage suffit.
- **Conséquences** : pour tester du code non encore construit, reconstruire l'image (`docker-compose.build.yml`) ou monter `app/` en
  entier (méthode de la phase 2).

## D-041 — Fin de la phase 2 bis — décision humaine du 2026-10-05

1. **API texte désactivée** jusqu'à la phase 3 : `ENABLE_EXTENSION_API=false` inscrit explicitement dans `.env` de la VM, pile redéployée
   (vérifié : `/api/v1/version` → 401 sans session, 404 avec session).
2. **Diff de `CLAUDE.md` §1** : relu par l'humain avant application.
3. **PKCE** : S256 côté oauth2-proxy (`code_challenge_method`) et exigé côté client Keycloak ; appliqué et vérifié de bout en bout (EXT-49).
4. **Dépendances de production en retard** : phase dédiée (phase 2 ter), sous seccomp ; seule la liste est préparée
   (`docs/phase-2-ter-dependances.md`).
5. **Version de l'API** : reste `1.0`. **Règle** : jusqu'à la première publication, le contrat évolue librement ; après la première
   publication, tout changement visible par un client (nouveau code d'erreur, champ ajouté, retiré ou modifié, changement de sémantique)
   fait évoluer `api_version` (mineure si compatible, majeure et nouveau préfixe `/api/v2/` sinon, D-001).
6. **Volumes** `obfusk8_keycloak-data` et `obfusk8-kc-export-src` : conservés ; l'humain copie d'abord la sauvegarde tar hors de la VM ;
   suppression seulement après sa confirmation explicite.
7. **Aucune publication avant le pentest** : pas d'étiquette `v0.2.0`. EXT-01 reste ouvert pour `app` et l'analyseur seulement.
8. **Validation** : l'humain fait le `git merge --ff-only` et le push.

## D-042 — Publication verrouillée — décision humaine du 2026-10-05

- **Contexte** : aucune publication d'image avant le pentest externe (D-041). La branche `main` du dépôt distant porte encore l'ancien workflow,
  qui publie `main` et `latest` à chaque push sur `main`.
- **Décision** : GitHub Actions est **désactivé** sur le dépôt (réglage fait par l'humain). En plus, les deux tâches du workflow déclarent
  l'environnement `publication` ; ses règles de protection se règlent dans *Settings → Environments → publication* : relecteur obligatoire
  (l'humain), déploiement limité aux étiquettes `v*.*.*` (*Deployment branches and tags → Selected branches and tags*), et
  *Prevent self-review* désactivé tant qu'un seul relecteur existe.
- **Conséquences** : même après réactivation des Actions, aucune image n'est poussée sans approbation explicite dans l'interface GitHub.
  Tant que l'environnement n'est pas créé, GitHub le crée sans protection au premier lancement : le créer **avant** de réactiver les Actions.
  Vérifié localement : `actionlint` 1.7.12, 0 constat.

## D-043 — Choix de l'étape A de la phase 2 ter — décision humaine du 2026-10-06

Inventaire : `docs/phase-2-ter-dependances.md` (2026-10-06).

1. **FastAPI reste en 0.141.1** (écart à la dernière version 0.142.2). Cause : 0.142.0 rend `opentelemetry-api` obligatoire (nouvelle
   dépendance de production) et active par défaut une télémétrie native, capable d'exporter en OTLP si `OTEL_EXPORTER_OTLP_ENDPOINT` est
   défini et le SDK présent (`fastapi/telemetry/_runtime.py`, lu dans la roue 0.142.2) ; 0.142 n'apporte aucun correctif de sécurité.
   **Conditions de levée** : un correctif de sécurité disponible seulement à partir de 0.142, ou OpenTelemetry redevenu facultatif.
   **Réexamen à chaque cycle de maintenance** (`docs/maintenance-dependances.md`). Le jour de la montée : télémétrie désactivée
   explicitement à la construction de `FastAPI(...)`, avec un test prouvant qu'aucune exportation n'a lieu.
2. **Analyseur** : gunicorn 26, filelock 4 et setuptools 84 acceptés (versions majeures de paquets).
3. **Verrou complet de l'analyseur** : ensemble installé déclaré avec empreintes, installé par `--require-hashes --no-deps` sur la base
   Presidio, comme `app/requirements.lock`.
4. **uvicorn sans en-têtes de proxy** : `--no-proxy-headers` ajouté à la commande de lancement de `app` (accord donné pour cette
   modification du Dockerfile), plus un test sur la configuration de confiance. `app` n'utilise pas l'adresse du client. Si elle devient
   nécessaire, **la seule configuration acceptable** est `forwarded_allow_ips` limité à l'adresse fixe de Traefik (`10.89.18.10`).
5. **Python inchangé dans cette phase** : l'analyseur reste en 3.12.13 (image Presidio), consigné en EXT-50 avec les vulnérabilités
   corrigées par 3.12.14 et 3.12.15 (python.org). Préparation de la phase suivante, sans modification : compatibilité des dépendances
   compilées avec 3.13, 3.14 et suivantes, dans le compte rendu. **Décision prise pour la phase suivante** : l'analyseur sera reconstruit
   sur une base Python épinglée, installée depuis le verrou complet, et les deux images passeront à la même version de Python.
6. **Ordre des lots** validé (base `app`, uvicorn, requests, analyseur service, analyseur détection, outils, images) ; analyseur en deux lots.
7. **Disque** : jamais `docker image prune -a` ni `docker system prune -a` (ils supprimeraient les images de retour arrière non utilisées) ;
   seules les images intermédiaires construites pendant la phase sont supprimées, par leur identifiant.

## D-044 — Décisions sur le compte rendu de la phase 2 ter — décision humaine du 2026-10-06

1. **`CLAUDE.md`** : diff relu par l'humain avant application (non appliqué par la session). Règle ajoutée au diff, pour **tout secret** et
   pas seulement les comptes de test : `~/.obfusk8-test-accounts` et tout fichier de secrets ne sont jamais affichés ni lus par une
   commande dont la sortie s'affiche ; ils sont chargés uniquement par un script qui ne journalise rien ; tout filtre de masquage est
   d'abord testé sur un faux fichier au même format (incident 1 du compte rendu de la phase 2 ter).
2. **Cadence de maintenance validée** : revue mensuelle ; vulnérabilité critique : analyse sous 24 h ouvrées, correctif sous 72 h.
   Source d'alerte : alertes de sécurité GitHub (Dependabot alerts), **sans** correctifs automatiques (pas de Dependabot security
   updates ni de version updates) ; leur couverture de nos fichiers de verrou est vérifiée, avec une autre source proposée à défaut
   (`docs/maintenance-dependances.md` §8).
3. **EXT-51** : corrigé dans une **courte phase dédiée**, avant la phase Python ; rien n'est commencé en phase 2 ter. Proposition préparée
   dans le compte rendu (§12) : quota de documents en attente par utilisateur, limitation de débit par utilisateur sur les routes de
   documents, annulation de ses propres documents en attente. **EXT-52** : vérifier qu'aucune mesure publiée des phases 1 à 2 ter n'a été
   faussée (fait : compte rendu §13).
4. **Mots de passe des comptes de test** : conservés, **risque accepté** (EXT-53, 🟡 par décision humaine) : comptes de laboratoire sans
   valeur, voués à disparaître. **Condition** : tous les comptes de test sont supprimés de Keycloak avant tout pilote ou tout environnement
   réel.
5. **Images `obfusk8-local:avant-2ter-*` et volume `obfusk8_keycloak-data-avant-2bis`** : supprimés **après** confirmation de l'humain que
   la pile fonctionne depuis la branche poussée.
6. **Validation** : l'humain fait le `git merge --ff-only` vers `feat/text-api` et le push.

## D-045 — Préparation du pilote — liste consignée le 2026-10-06 (décisions humaines à prendre, sauf mention)

### A. Exécution de D-044 point 5 (décision humaine du 2026-10-06)

- **Test manuel de l'humain réussi** : la pile fonctionne depuis la branche poussée.
- **Observé** : les images en service (`obfusk8-app:0.2.0-dev` `3e0b62ee…`, analyseur `b5cef6eb…`) ont été construites le 2026-10-06 à
  09:36, après passage sur `feat/text-api` (`0cec4c9`, état **2 bis**) ; `feat/dependances` (2 ter) n'est pas sur le dépôt distant
  (`git ls-remote origin` : `main` et `feat/text-api` seulement). La pile testée est donc celle de la 2 bis, comme les images `avant-2ter`.
- **Fait** : étiquettes `obfusk8-local:avant-2ter-*` supprimées (`docker rmi`) ; images libérées : app `58540012…` et analyseur `e07bd4f2…`
  (les cinq autres étiquettes partageaient l'image d'un service en service, simplement désétiquetée). `docker-compose.rollback-2ter.yml` ne
  fonctionne plus. Pile inchangée (6 conteneurs en service).
- **Non fait** : volume `obfusk8_keycloak-data-avant-2bis` (suppression de volume interdite à la session, `CLAUDE.md` §9 : à faire par
  l'humain) ; `~/.cache/obfusk8-devtools.avant-2ter` (hors dépôt) ; anciennes images `avant-2bis`, `obfusk8-app-local:phase2-*`,
  `ghcr.io/epicfail20/obfusk8-app:main` et `obfusk8-presidio-analyzer:latest` (locales, non demandées).

### B. Port 8080 de Keycloak publié sur toutes les interfaces (EXT-54, EXT-55)

- **Nécessité** : réelle pour la **connexion** tant que Keycloak sert de fournisseur d'identité : l'émetteur
  `http://keycloak.lab.local:8080/realms/lab` (`oauth2-proxy.cfg`, `KC_HOSTNAME`) doit être joint par le navigateur (redirection de
  connexion) et par oauth2-proxy (alias sur `app-internal`), avec la même URL. **Aucune nécessité** d'exposer la console d'administration
  ni le royaume `master` aux utilisateurs, ni d'écouter sur l'IPv6 globale.
- **Exposition réelle** (observée) : `0.0.0.0:8080` et `[::]:8080` ; console d'administration et `master` répondent 200 sur la boucle
  locale, l'adresse du LAN et l'adresse IPv6 globale de la VM, en HTTP, mode `start-dev`. Joignabilité depuis Internet : **non vérifiée**
  (pare-feu de la box et de Proxmox ; à tester par l'humain depuis une machine extérieure, par exemple une connexion mobile en IPv6).
- **Options** :
  1. **B1 — restreindre la publication** : `"192.168.1.35:8080:8080"` (IPv4 du LAN seulement). Changement minimal ; supprime l'IPv6
     globale ; laisse HTTP, la console sur tout le LAN et l'accès sortant (EXT-55) ; fige l'adresse de la VM dans Compose.
  2. **B2 — Keycloak derrière Traefik en HTTPS (recommandée pour le pilote si Keycloak reste le fournisseur)** : port 8080 retiré,
     Keycloak seulement sur `app-internal` (plus d'accès sortant), routeur Traefik dédié `Host(keycloak.lab.local)` avec limitation de débit
     et plafond de corps, `/admin` et `/realms/master` limités par liste d'IP (ou non routés), émetteur passé en `https://…` dans
     `oauth2-proxy.cfg` et `KC_HOSTNAME`, certificat couvrant ce nom, Keycloak en mode `start` (production) avec une vraie base. Plus de
     travail ; touche Compose, Traefik et oauth2-proxy (accord requis, `CLAUDE.md` §9) ; options exactes de Keycloak 26.8 à vérifier dans sa
     documentation, pas de mémoire.
  3. **B3 — pare-feu de l'hôte** : écarté seul : le chemin IPv6 passe par `INPUT`, le chemin IPv4 par `FORWARD`/`DOCKER-USER` ; deux jeux
     de règles hors du dépôt, faciles à perdre.
  4. **B4 — Entra ID pour le pilote** : Keycloak disparaît de la pile ; `app-internal` doit alors joindre Microsoft (avertissement du
     réseau dans `docker-compose.yml`) : réseau sortant dédié à oauth2-proxy à concevoir.
- **Recommandation** : B1 tout de suite pour le laboratoire (coupe l'IPv6 globale), puis B2 ou B4 avant le pilote. Rien n'est appliqué.

### C. Liste de préparation du pilote

Compilée depuis le dépôt (décisions, constats, comptes rendus) ; ordre proposé, à valider.

**Bloquants avant tout utilisateur pilote**

1. **Phase 2 ter validée** : `git merge --ff-only feat/dependances` vers `feat/text-api`, push, reconstruction des images depuis la branche
   poussée et nouveau test manuel (D-044 point 6 ; la pile actuelle est en 2 bis, voir A). *Mise à jour du 2026-10-07 : fait (2 ter
   fusionnée et poussée, `5873e0e`).*
2. **EXT-51** : phase dédiée (D-044 point 3, proposition du compte rendu 2 ter §12), avec **EXT-47** (403 pour une tâche sans identité).
   *Mise à jour du 2026-10-07 : traités dans la phase « disponibilité » (D-048, D-050, `docs/phase-disponibilite-report.md`), en attente du
   test manuel et de la fusion par l'humain ; risque résiduel EXT-59 (plusieurs comptes).*
3. **Exposition de Keycloak** : EXT-54 et EXT-55, choix B2 ou B4 ci-dessus. *Mise à jour du 2026-10-07 : sans objet pour le pilote
   (Keycloak réservé au laboratoire, D-046) ; en laboratoire, IPv6 fermée (D-047), plus d'accès sortant (EXT-55 résolu), console encore
   joignable en HTTP depuis le réseau local (EXT-54).*
4. **Fournisseur d'identité du pilote** (décision humaine du 2026-10-07, D-046) : brancher oauth2-proxy sur le fournisseur d'identité de
   l'établissement, avec un accès sortant dédié et limité à ce fournisseur ; PKCE S256 conservé (EXT-49).
5. **Comptes** : comptes de test supprimés de Keycloak (D-044 point 4, EXT-53) ; comptes pilotes nominatifs avec adresse de courriel
   vérifiée (EXT-43 : sans courriel, oauth2-proxy répond 500). *Mise à jour du 2026-10-07 : le pilote n'utilise pas Keycloak (D-046) ni
   aucun secret du laboratoire (point 16) ; les comptes pilotes sont ceux du fournisseur de l'établissement, avec courriel vérifié.*
6. **Données** : pentest externe avant toute donnée réelle (D-021, `CLAUDE.md` §8.3). Sans pentest, pilote sur données synthétiques
   seulement.
7. **Certificats TLS de Traefik** : nature (auto-signé ou non), noms couverts et échéance **non vérifiés** dans cette session.

**À traiter avant le pilote, non bloquants pour un pilote restreint sur données synthétiques**

8. **Avertissement aux utilisateurs pilotes** : révision obligatoire et familles de faux négatifs connues (D-039 ; EXT-18, EXT-32,
   EXT-41, EXT-42, EXT-46).
9. **Sauvegardes et nettoyage** : sauvegarde de `keycloak-data-v26-8` et du journal d'audit ; suppression par l'humain des volumes
   `obfusk8_keycloak-data`, `obfusk8-kc-export-src` (D-041 point 6) et `obfusk8_keycloak-data-avant-2bis` après copie hors de la VM.
10. **Supervision** : destination syslog des alertes vérifiée ; métriques du pilote pour réévaluer un second worker de l'analyseur (D-036).
11. **Ressources de la VM** : remesurer EXT-25 sur la VM actuelle avant le pilote.
12. **Images construites** : pas de publication avant le pentest (D-041 point 7, EXT-01) ; le pilote tourne sur des images construites
    localement depuis une branche poussée, version et condensat notés.
13. **API texte** : reste désactivée (`ENABLE_EXTENSION_API=false`, D-041 point 1) sauf si le pilote inclut l'extension (phase 3).
14. **Licence** (EXT-05) : à régler avant toute distribution.
15. **GitHub Actions** : environnement `publication` créé avec ses protections avant toute réactivation (D-042).
16. **Secrets neufs** (D-049) : le pilote est une nouvelle installation avec des secrets générés pour lui ; aucun secret du
    laboratoire (passerelle, comptes de test, Keycloak, oauth2-proxy) n'est réutilisé.
17. **Dimensionner `MAX_PENDING_JOBS`** (D-051, EXT-59) selon le nombre d'utilisateurs du pilote, après mesure de la mémoire de `app` :
    les documents en attente de révision sont gardés en mémoire (original complet par document), limite du conteneur 1 Go.

## D-046 — Keycloak réservé au laboratoire — décision humaine du 2026-10-07

- **Contexte** : D-045 §B et §C point 4 laissaient ouvert le fournisseur d'identité du pilote (Keycloak en mode production ou Entra ID).
  Keycloak est aujourd'hui exposé sur toutes les interfaces en mode développement (EXT-54) et dispose d'un accès sortant (EXT-55).
- **Décision** : Keycloak est un composant de **laboratoire uniquement**. Le pilote et toute production utiliseront le **fournisseur
  d'identité de l'établissement**, branché sur oauth2-proxy.
- **Alternatives écartées** : Keycloak en mode production comme fournisseur du pilote (D-045 §B2 et §C point 4).
- **Conséquences, à traiter dans la prochaine phase** (rien n'est commencé) :
  1. Keycloak passe dans un profil Docker Compose `lab`, non démarré par défaut ;
  2. Keycloak ne dispose plus d'aucun accès sortant vers Internet ;
  3. la documentation FR/EN distingue clairement le déploiement de laboratoire (avec Keycloak) du déploiement réel (fournisseur d'identité
     externe), et avertit que le Keycloak de laboratoire tourne en mode développement et ne doit jamais servir en production.
- **D-045 §C point 4** remplacé en conséquence : « brancher oauth2-proxy sur le fournisseur d'identité de l'établissement, avec un accès
  sortant dédié et limité à ce fournisseur ».

## D-047 — Publication des ports sur une seule adresse — décision humaine du 2026-10-07

- **Contexte** : EXT-56 (et EXT-54) : Traefik (80, 443) et Keycloak (8080) publiés sur `0.0.0.0` et `[::]`, donc sur l'adresse IPv6
  publique de la VM ; console d'administration de Keycloak en HTTP. Défaut présent depuis l'état initial du dépôt.
- **Décision** : les trois ports sont publiés sur la seule adresse `BIND_ADDRESS` du `.env`. Valeur par défaut `127.0.0.1` dans
  `docker-compose.yml` et dans `env.fr.example` / `env.en.example` (sûre par défaut : accès depuis la VM seulement) ; adresse du réseau
  local à renseigner pour un accès depuis d'autres postes (laboratoire : `192.168.1.35`). Correspond à l'option B1 de D-045, étendue à
  Traefik et rendue paramétrable.
- **Alternatives écartées** : adresse codée en dur dans Compose (fige l'adresse de la VM, D-045 §B1) ; pare-feu de l'hôte seul (D-045 §B3) ;
  Keycloak derrière Traefik (D-045 §B2), sans objet depuis D-046 (Keycloak réservé au laboratoire).
- **Conséquences** :
  - sans `BIND_ADDRESS` dans `.env`, l'application n'est plus joignable que depuis la VM : la variable doit être ajoutée aux `.env`
    existants avant redéploiement (guides `deploiement-lab.md` / `deployment-lab.md` §4) ;
  - restent ouverts : HTTP et mode développement de Keycloak, console joignable depuis tout le réseau local (EXT-54), accès sortant de
    Keycloak (EXT-55) ; traités par les suites de D-046 ;
  - si le test depuis l'extérieur montre que l'exposition était effective, renouvellement des secrets à décider (mot de passe
    administrateur de Keycloak, secret du client OIDC, secret de cookie d'oauth2-proxy, secret de passerelle).

## D-048 — Décisions sur l'étape A de la phase « disponibilité » — décision humaine du 2026-10-07

1. **Lot 3 (Keycloak de laboratoire)** : conception validée. Keycloak seulement sur `app-internal` (plus d'accès sortant, EXT-55) ;
   Traefik relaie le port 8080 (point d'entrée dédié, publié sur `BIND_ADDRESS`, routeur défini par les labels de Keycloak) ; profil
   Compose `lab` ; `COMPOSE_PROFILES=lab` dans le `.env` du laboratoire. Essai préalable (Docker 29.8.1) : un conteneur attaché seulement à un
   réseau `internal: true` ne publie aucun port, d'où le relais. **Condition** : avec `KC_PROXY_HEADERS=xforwarded`, tester qu'un
   `X-Forwarded-For` envoyé par le client est remplacé par Traefik et ne parvient pas tel quel à Keycloak.
2. **Identifiant mal formé** : `/api/download` garde son 400 ; le 404 générique ne s'applique qu'à `/api/cancel`.
3. **Protection contre la falsification de requête** : vérification stricte d'`Origin` (à défaut `Referer`), égal à `https://${APP_DOMAIN}`, sur
   toutes les requêtes qui modifient un état : `/api/detect`, `/api/finalize`, `/api/cancel` ; en plus, en-tête `X-Obfusk8-Action` exigé pour
   `/api/cancel`. L'interface actuelle (formulaires) doit continuer de fonctionner, avec un test par route.
4. **Écart « onglet fermé »** (un utilisateur qui a quitté la page de révision ne connaît plus ses `job_id`) : accepté ; une page « mes documents
   en attente » va au backlog.
5. Rappel des décisions du prompt de phase : quota par utilisateur activé par défaut (`MAX_PENDING_JOBS_PER_USER=3`), annulation activée par
   défaut, 429 avec `Retry-After` pour le quota (503 conservé pour le plafond commun), limitation de débit par utilisateur (5/min, rafale 10)
   en plus de la limite par IP, EXT-47 refusé en 403.

## D-049 — Secrets du laboratoire jetables — décision humaine du 2026-10-07 (risque accepté 🟡)

- **Contexte** : trois expositions de secrets du laboratoire dans des sessions Claude Code, dont celle du secret de passerelle le
  2026-10-07 (recherche récursive dans `traefik/dynamic/` pendant l'étape A de la phase « disponibilité »). Régénération et règles
  d'interdiction de lecture (`.claude/settings.json`) proposées, puis abandonnées par cette décision.
- **Décision** (valable jusqu'à la fin du projet sur cette VM) : les secrets du laboratoire (secret de passerelle, comptes de test, Keycloak,
  oauth2-proxy) sont jetables : générés localement, jamais poussés, détruits avec la VM en fin de travail. Une exposition dans une session
  n'est plus un incident bloquant : elle est signalée en une ligne dans le compte rendu, sans arrêt ni proposition de régénération.
- **Exigence maintenue** : aucun secret dans le dépôt git ni dans un commit (vérification avant chaque commit).
- **Conséquences** : pas de régénération du secret de passerelle exposé le 2026-10-07 ; pas de `.claude/settings.json` ; dossier de leurres
  `leurre-claude/` supprimé (absent de tout commit : `git log --all -- leurre-claude` vide) ; D-045 §C point 16 : le pilote utilise des secrets
  neufs, jamais ceux du laboratoire.

## D-050 — Mise en œuvre de la phase « disponibilité » — décisions de la session du 2026-10-07 (à valider par l'humain)

1. **Code nouveau dans `main.py`**, pas dans un module séparé : un module de plus exigerait une ligne `COPY` dans `app/Dockerfile`,
   soumise à accord (`CLAUDE.md` §9) et non autorisée par le prompt de phase. Les fonctions nouvelles sont annotées ; les tests nouveaux
   passent `mypy --strict`. Proposition : `app/document_guard.py` si l'humain autorise la modification du Dockerfile.
2. **Ordre des contrôles** : `/api/detect` et `/api/finalize` vérifient l'origine puis l'identité **avant** tout traitement (FastAPI a
   déjà reçu le corps, borné par `MAX_REQUEST_BODY_BYTES`) ; `/api/cancel` : drapeau, origine, en-tête `X-Obfusk8-Action`, identité,
   format, propriété. Annulation désactivée : `StarletteHTTPException(404)`, réponse identique à une route absente (test). L'annulation ne
   passe pas par le fil documents (pas de PyMuPDF, ne doit pas attendre une longue détection).
3. **Quota** : place réservée sous `_PENDING_JOBS_LOCK` avant la détection et rendue en `finally` ; `Retry-After` = temps restant du plus
   ancien document de l'utilisateur + une période de balayage (60 s), au moins 1 s.
4. **`doc-auth-errors`** (EXT-57) : sur les routeurs de documents, la redirection vers la connexion ne porte plus que sur le 401 ;
   `oauth2-errors` (401-403) reste inchangé sur le routeur par défaut. Écarté : changer `oauth2-errors` lui-même (définition partagée, hors
   des labels de documents autorisés) ; retirer toute redirection des routes de documents (session expirée pendant une révision = page
   401 brute). Le routeur `app-cancel` n'a aucune redirection : l'interface l'appelle par `fetch`.
5. **Keycloak derrière Traefik** : `KC_PROXY_TRUSTED_ADDRESSES=10.89.18.10` en plus de `KC_PROXY_HEADERS=xforwarded` (sans elle, Keycloak
   fait confiance à ces en-têtes depuis toute adresse, documentation de Keycloak). Quatrième endroit où figure l'adresse fixe de Traefik
   (avec `ipam`, `ipv4_address` et `trusted_proxy_ips`). Limite de débit du routeur `keycloak` (300/min, rafale 300, par adresse) : une
   connexion scriptée coûte 2 requêtes (mesuré) ; le chargement de la console d'administration n'est **pas mesuré** (à vérifier au test
   manuel). Plafond de corps 10 Mio (import partiel de royaume, supposé suffisant).
6. **Client des bancs** : `BENCH_RESOLVE_ADDRESS` (la pile n'écoute plus sur 127.0.0.1 depuis D-047) ; la session porte l'`Origin` de
   l'application une fois connectée, jamais envoyée à Keycloak.

## D-051 — Décisions sur le compte rendu de la phase « disponibilité » — décision humaine du 2026-10-07

1. **D-050 validée**, y compris le correctif d'EXT-57 (`doc-auth-errors`).
2. **EXT-59 : risque accepté (🟡) pour le pilote** : comptes authentifiés et tracés. Ajouté à D-045 §C (point 17) : dimensionner
   `MAX_PENDING_JOBS` selon le nombre d'utilisateurs du pilote, après mesure de la mémoire de `app` (documents en attente en mémoire,
   limite 1 Go).
3. **EXT-54 : risque accepté (🟡)**, Keycloak étant réservé au laboratoire (D-046).
4. **EXT-58 corrigé** : README FR/EN cohérent avec le guide de déploiement (aucun secret dans `.env`), commit dédié (`f64adb9`).
5. **`CLAUDE.md`** : diff relu par l'humain avant application, avec une règle ajoutée : un nouveau module Python est autorisé, avec une
   ligne `COPY` explicite dans `app/Dockerfile`, sans demander d'accord ; plus de nouveau code dans `main.py` quand il forme un ensemble
   cohérent (lève la contrainte de D-050 point 1 pour les phases suivantes).
6. **Suite** : l'humain suit la procédure de test manuel (`docs/phase-disponibilite-report.md` §9), puis fait la fusion et le push.

## D-052 — Décisions sur l'étape A de la phase « Python » — décision humaine du 2026-10-07

Inventaire présenté en session (étape A) : image Presidio publiée reproduite sur notre base, Python 3.14.8 (python.org, 2026-09-30 ;
3.15 en version candidate, exclu par Presidio et la pile spaCy, `<3.15`), roues cp314 publiées pour toutes les dépendances compilées.

1. **Forme de l'image de l'analyseur validée** : Presidio installé comme distribution depuis la roue PyPI `presidio_analyzer-2.2.364`
   (empreinte dans le verrou ; ses 179 fichiers sont identiques au code de l'image publiée), fichiers du serveur (`app.py`,
   `logging.ini`, `entrypoint.sh`, licence MIT) copiés dans `presidio/analyzer-build/server/` (identiques à l'étiquette `2.2.364` de
   GitHub), configuration effective dans `/app/conf/`, `/app` appartenant à `root`. **Condition** : à chaque mise à jour de Presidio,
   ces fichiers sont recopiés depuis la nouvelle étiquette et comparés (`docs/maintenance-dependances.md`).
2. **Contrôle de santé en Python** (`urllib`), sans `curl`, mêmes délais que l'image amont.
3. **gunicorn ramené à la dernière 25.x** (25.3.0, PyPI 2026-03-27, aucun avis OSV au 2026-10-07) pour respecter la contrainte de
   Presidio (`gunicorn<26.0.0`, extra `server`) et garder un `pip check` strict, sans exception (EXT-60). **Condition** : `pip-audit`
   sans vulnérabilité sur cette version ; sinon arrêt et présentation de l'alternative (26.2.0 avec exception documentée).
   Revient sur D-043 point 2 pour gunicorn seulement.
4. **Version de Python dans `versions.json` et `app/analyzer_versions.json`** : elle entre dans l'empreinte `detection_config`
   (l'interpréteur peut changer la détection : base Unicode 15.0 en 3.12, 16.0 en 3.14).
5. **Paramètre unique, option A** : ancre YAML dans `docker-compose.build.yml`, argument de construction `PYTHON_BASE` sans valeur
   par défaut dans les deux Dockerfiles. Autorisation de modifier `.github/workflows/` pour les seules lignes qui passent
   `PYTHON_BASE` ; GitHub Actions reste désactivé (D-042), syntaxe validée par `actionlint`.
6. **Outils** : `ruff target-version = "py314"` et `mypy python_version = "3.14"` ; nouveaux constats signalés, sans reformatage global.
7. **Disque** : suppression des images `obfusk8-local:avant-2bis-*`, des étiquettes locales `:main`, `:latest` et `phase2-*`, et du
   cache de construction (`docker builder prune`). Les images `avant-py314` de l'étape B sont conservées.
8. **Modèles spaCy** : empreinte actuelle acceptée, avec contrôle octet par octet des modèles installés au lot 1. **Limite consignée** :
   GitHub ne publie aucun condensat pour ces fichiers ; l'empreinte garantit « même fichier qu'avant », pas l'authenticité à l'origine.

## D-053 — Mise en œuvre de la phase « Python » — décisions de la session du 2026-10-07 (à valider par l'humain)

1. **`open` (appel système 2) non ajouté au profil seccomp.** Python 3.14 l'appelle une fois par démarrage, par l'allocateur mimalloc
   intégré à CPython (lecture de `/proc/sys/vm/overcommit_memory`, `syscall(SYS_open, …)`). Refusé (`ENOSYS`), mimalloc garde sa valeur
   par défaut, identique à celle de l'hôte (`0`) ; l'interpréteur utilise `pymalloc` pour les objets Python (observé). Alternative
   écartée : l'autoriser, ce qui élargirait la surface sans effet utile. Conséquence : à revoir si l'hôte passe en
   `vm.overcommit_memory=2` ou si `PYTHONMALLOC=mimalloc` est adopté (`seccomp/README.md`).
2. **`pip check` complété par `check_lock.py`** pour les extras : `pip check` ignore les dépendances des extras, il n'aurait donc pas
   vérifié `gunicorn<26.0.0` (D-052 point 3 demandait un contrôle strict). La construction vérifie maintenant les exigences de
   `presidio-analyzer[server]` (tests écrits avant). Alternative écartée : installer avec l'extra déclaré, sans effet avec `--no-deps`.
3. **Version de Python déclarée dès le lot 1** (3.12.15, puis 3.14.8 au lot 3) : l'interpréteur changeait déjà (3.12.13 → 3.12.15),
   l'empreinte `detection_config` le reflète à chaque étape.
4. **Bancs de documents** : annulation de chaque document mesuré (EXT-61), condition pour mesurer sous le quota par utilisateur (D-048).
   `collect_stack_info.sh` lit la version de Presidio dans la distribution installée.
5. **Outils de développement** : réinstallés pour 3.14 dans le même répertoire (`~/.cache/obfusk8-devtools`) ; l'ancien est conservé
   sous `~/.cache/obfusk8-devtools.avant-py314` (retour arrière : `OBFUSK8_DEVTOOLS=…`). `app/run-tests.sh` inchangé.
6. **Workflow de publication** : la base Python est lue dans `docker-compose.build.yml` par une étape `sed` qui échoue si elle est vide,
   puis passée en `build-args` (seules lignes ajoutées, D-052 point 5) ; `actionlint` 1.7.12 sans constat.
