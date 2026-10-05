# API texte pour l'extension de navigateur — contrat v1

> English version: [`api-extension.en.md`](./api-extension.en.md).
> Statut : **implémenté** (phase 1), complété en phase 2 (normalisation D-014, 401 sans redirection D-020) et en phase 2 bis
> (403 sur identité absente D-035, empreinte D-030). Relu de bout en bout du point de vue d'un client le 2026-10-05 (§3.1).
> Les raisons de chaque choix sont dans [`DECISIONS.md`](./DECISIONS.md).

## 1. Objet

Permettre à une future extension de navigateur d'envoyer un **prompt** (texte libre) au serveur Obfusk8 avant qu'il parte vers un service d'IA,
pour y **détecter** les données sensibles, ou les **pseudonymiser** (remplacement par des marqueurs réversibles côté client).

Principes, hérités de la doctrine du projet :

- **Même moteur que le flux documents** : mêmes thèmes, mêmes seuils, même normalisation, même propagation des valeurs. Aucun chemin de détection parallèle.
- **Aucun paramètre client ne peut affaiblir la détection** : pas de seuil, pas de liste d'entités désactivables, pas de reconnaisseur fourni par le client.
  Tout champ inconnu est rejeté (422).
- **Sans état** : rien sur disque, rien conservé au-delà de la requête, pas d'endpoint de restauration.
- **La révision humaine reste obligatoire** : l'API ne peut pas l'imposer elle-même. Elle incombe à l'extension, qui devra montrer à l'utilisateur
  ce qui est détecté et lui permettre de corriger avant envoi (phase ultérieure, décision D-011).
- **Désactivée par défaut** : `ENABLE_EXTENSION_API=false`. Les routes n'existent alors pas (404, réponse identique à celle d'aujourd'hui).

## 2. Points d'accès

Préfixe versionné **`/api/v1/`** (D-001). Une incompatibilité future prendra la forme d'un nouveau préfixe `/api/v2/` ; l'extension compare aussi
`api_version` (majeure.mineure) renvoyé par `GET /api/v1/version`. **Règle de version** (D-041) : jusqu'à la première publication, le contrat
évolue librement et `api_version` reste `1.0` ; après, tout changement visible par un client fait évoluer `api_version` (mineure si compatible,
majeure et nouveau préfixe sinon).

| Méthode | Chemin | Rôle |
|---|---|---|
| `GET` | `/api/v1/version` | Version de l'API et de la configuration de détection, thèmes disponibles, plafond de taille |
| `POST` | `/api/v1/text/analyze` | Détection : liste des entités avec leurs positions |
| `POST` | `/api/v1/text/pseudonymize` | Texte pseudonymisé et table de correspondance |

Toutes les routes passent par la chaîne existante : Traefik → `oidc-auth` (oauth2-proxy) → limitation de débit → plafond de corps →
`gateway-secret@file` → application. Elles ont leur **propre routeur Traefik** (§7), sans `oauth2-errors` depuis la phase 2 (§8).

### 2.1 `GET /api/v1/version`

Réponse 200 :

```json
{
  "api_version": "1.0",
  "detection_config": "3f1c9a0b7e2d4c55",
  "analyzer_language": "fr",
  "analyzer_recognizers": "a41c07d29e5b3f18",
  "presidio_version": null,
  "themes": [
    {"key": "compta", "label": "Comptabilité"},
    {"key": "it", "label": "IT / Infrastructure"},
    {"key": "medical", "label": "Médical"}
  ],
  "max_text_chars": 20000
}
```

- `detection_config` : empreinte (SHA-256 tronquée à 16 caractères hexadécimaux) calculée au démarrage sur le contenu canonique des thèmes,
  des reconnaisseurs communs, des reconnaisseurs propres à l'API texte, de `DEFAULT_SCORE_THRESHOLD`, de la langue d'analyse et, depuis la
  phase 2 bis (D-030), des **versions** de ce qui façonne le résultat hors configuration : version de la normalisation Unicode
  (`NORMALIZATION_VERSION`), version des autres étapes de détection du code (`DETECTION_PIPELINE_VERSION` : majuscules, tirets,
  découpage en blocs, propagation D-012, fusion des chevauchements D-004), versions **déclarées** de presidio-analyzer, de spaCy et du
  modèle français (`app/analyzer_versions.json`, que la construction de l'analyseur vérifie contre ce qui est installé).
  Elle change dès qu'un de ces éléments change. Changement compatible : même champ, même format, plus sensible. L'extension peut l'afficher ou la journaliser pour savoir avec quelle configuration un texte a été vérifié.
- `analyzer_recognizers` : empreinte (16 caractères hexadécimaux) de la liste des reconnaisseurs effectivement chargés par `presidio-analyzer`
  pour la langue d'analyse, interrogée au premier appel puis mise en cache ; `null` si l'analyseur ne répond pas (D-013).
- `presidio_version` : toujours `null` pour l'instant, l'API REST de Presidio n'exposant pas sa version (D-013).
- `label` : libellé dans la langue `UI_LANG` du serveur (`app/i18n/themes.json`).

### 2.2 `POST /api/v1/text/analyze`

Requête (`Content-Type: application/json`, encodage UTF-8) :

```json
{"text": "Bonjour, je suis Camille Martin, joignable au 06 12 34 56 78.", "theme": "medical"}
```

| Champ | Type | Règle |
|---|---|---|
| `text` | chaîne | Obligatoire, au moins 1 caractère, au plus `MAX_TEXT_CHARS` points de code (au-delà : 413). Surrogate UTF-16 isolée : rejetée. |
| `theme` | chaîne ou `null` | Facultatif. Clé d'un thème existant (`GET /api/v1/version`). Thème inconnu : **422**, jamais un repli silencieux sur « aucun thème ». Absent ou `null` : `DEFAULT_SCORE_THRESHOLD`, comme le flux documents sans thème. |

Tout autre champ (par exemple `score_threshold`, `entities`, `ad_hoc_recognizers`) est rejeté en 422.

Réponse 200 :

```json
{
  "request_id": "8f0d6c3e1b2a4f5e9d7c6b5a4f3e2d1c",
  "theme": "medical",
  "text_length": 61,
  "text_length_utf16": 61,
  "entities": [
    {"entity_type": "PERSON", "start": 17, "end": 31, "start_utf16": 17, "end_utf16": 31},
    {"entity_type": "PHONE_NUMBER", "start": 46, "end": 60, "start_utf16": 46, "end_utf16": 60}
  ]
}
```

- **Positions sur la chaîne reçue telle quelle**, données deux fois (D-002) : en points de code Unicode (`start`/`end`, indexation Python)
  et en unités UTF-16 (`start_utf16`/`end_utf16`, indexation JavaScript : `text.slice(start_utf16, end_utf16)`). Les deux diffèrent dès qu'un caractère
  hors du plan multilingue de base (émoji, certains idéogrammes) précède l'entité. `end` est exclusif.
- Avant détection, le serveur normalise le texte (D-014) : caractères de format supprimés (largeur nulle, contrôles bidirectionnels, trait d'union
  conditionnel), espaces spéciales et caractères de contrôle remplacés par une espace, ligatures développées, apostrophes typographiques,
  recomposition NFC, puis majuscules et tirets typographiques comme avant. **Les positions renvoyées portent toujours sur le texte reçu** :
  une valeur qui contient un caractère invisible est renvoyée avec lui (« Camille\u200bMartin » couvre aussi le caractère de largeur nulle),
  elle n'est jamais coupée en deux. Un caractère invisible juste avant ou juste après une valeur n'en fait pas partie.
- Les entités sont triées par `start`, puis par `end`. **Elles peuvent se chevaucher** (deux reconnaisseurs sur le même passage) : le client ne doit pas
  supposer des intervalles disjoints.
- Les entités incluent celles trouvées par **propagation** : toute autre occurrence exacte d'une valeur déjà détectée est signalée aussi (D-012).
- **Pas de score** (D-003) : les scores de Presidio ne sont pas calibrés ; un client qui filtrerait dessus créerait des faux négatifs.
- Types d'entités : ceux de Presidio et des thèmes (`PERSON`, `LOCATION`, `EMAIL_ADDRESS`, `PHONE_NUMBER`, `DATE_TIME`, `PATIENT_ID`, `FR_NIR`…),
  plus ceux propres à l'API texte, actifs quel que soit le thème (`app/themes/extension/`) : `SECRET` (clés privées PEM, JWT, URI avec identifiants,
  affectations `password`/`token`/`secret`/`api_key`, en-têtes `Authorization`, clés AWS, Google Cloud, Azure, GitHub, GitLab, Slack, Telegram,
  Stripe). Depuis la phase 2, `CREDIT_CARD`, `FR_NIR` (EXT-08) et `EMAIL_ADDRESS` quel que soit le domaine, y compris interne (EXT-23),
  sont détectés par les reconnaisseurs communs à tous les flux (`app/themes/common.json`), documents compris. Le client doit traiter
  tout type inconnu comme sensible.

### 2.3 `POST /api/v1/text/pseudonymize`

Requête : identique à `analyze`.

Réponse 200 :

```json
{
  "request_id": "0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f",
  "theme": null,
  "text": "Bonjour, je suis ⟦PERSON_1⟧. ⟦PERSON_1⟧ est joignable au ⟦PHONE_NUMBER_1⟧.",
  "mapping": [
    {"placeholder": "⟦PERSON_1⟧", "entity_type": "PERSON", "original": "Camille Martin"},
    {"placeholder": "⟦PHONE_NUMBER_1⟧", "entity_type": "PHONE_NUMBER", "original": "06 12 34 56 78"}
  ]
}
```

Règles :

1. **Détection identique à `analyze`** (même texte, même thème : mêmes entités).
2. **Chevauchements** : les entités qui se chevauchent sont fusionnées en un seul passage remplacé (l'union des intervalles). Le type retenu est celui
   de l'entité la plus longue ; à égalité, `SECRET` l'emporte, puis l'ordre alphabétique (règle déterministe, D-004).
3. **Une même valeur reçoit le même marqueur dans tout le texte.** La clé est la valeur originale exacte (sensible à la casse) : « Camille Martin »
   et « CAMILLE MARTIN » reçoivent deux marqueurs distincts, ce qui garantit une restauration exacte.
4. **Format du marqueur** : `⟦TYPE_N⟧`, avec `⟦` (U+27E6) et `⟧` (U+27E7), `TYPE` le type d'entité, `N` un entier commençant à 1 par type (D-004).
   Ces crochets n'apparaissent pratiquement jamais dans un texte ordinaire ni dans du code.
5. **Aucune collision** : si un marqueur candidat figure déjà dans le texte reçu, le numéro suivant est utilisé. Un marqueur de `mapping` n'apparaît
   donc dans le texte renvoyé **que** là où le serveur l'a placé.
6. **Restauration côté client** : remplacer chaque `placeholder` par son `original` dans la réponse du service d'IA. Le serveur ne conserve rien ;
   il n'existe pas d'endpoint de restauration.
7. `mapping` est trié par ordre de première apparition dans le texte. **Il contient les données sensibles en clair** : l'extension doit le garder
   en mémoire seulement le temps de la conversation, jamais dans un stockage persistant ni dans des journaux.

## 3. Erreurs

Même format que les erreurs existantes de l'application (champ `detail`), plus l'identifiant de corrélation :

```json
{"detail": "Le texte dépasse la taille maximale autorisée (20000 caractères).", "request_id": "8f0d6c3e1b2a4f5e9d7c6b5a4f3e2d1c"}
```

- `detail` est traduit (`app/i18n/`, langue `UI_LANG`) et **générique** : jamais d'écho du texte soumis, de trace, de chemin ni de valeur de champ.
  Le message de validation par défaut de FastAPI, qui recopie la valeur fautive, n'est pas utilisé (D-009).
- `request_id` (32 caractères hexadécimaux) figure aussi dans l'en-tête `X-Request-ID` de **toutes** les réponses des routes v1, succès compris.
  Il est repris dans les journaux de l'application et le journal d'audit pour retrouver une requête.

| Code | Cas |
|---|---|
| 400 | Corps non UTF-8, JSON invalide (y compris surrogate isolée dans une séquence `\u`) |
| 401 | Secret de passerelle absent ou faux (requête qui contourne Traefik) — réponse existante de l'application, sans `request_id` |
| 401 | **Non authentifié** (pas de session, session expirée) : réponse d'oauth2-proxy relayée telle quelle par Traefik, `Content-Type: text/plain`, corps `Unauthorized`, **sans redirection ni JSON ni `request_id`** (D-020). Le client se fie au **code HTTP seul** ; conduite attendue au §8 |
| 403 | **Identité absente** (phase 2 bis, D-035) : en-tête `X-Auth-Request-User` ou `X-Auth-Request-Email` absent, vide, ou fait uniquement de caractères de contrôle, de format ou d'espaces. Refus avant toute lecture du corps, sur les trois routes (`/version` comprise) ; JSON avec `request_id` ; ligne d'audit `forbidden` (analyse et pseudonymisation). Ne se produit pas pour un utilisateur connecté normalement : oauth2-proxy renseigne toujours ces en-têtes. Conduite attendue : ne pas réessayer ; proposer de se reconnecter, puis de contacter l'administrateur avec le `request_id` |
| 404 | `ENABLE_EXTENSION_API=false` (réponse FastAPI standard `{"detail":"Not Found"}`, identique à aujourd'hui) |
| 413 | Corps au-delà du plafond (en bordure par Traefik, sinon par l'application), ou `text` au-delà de `MAX_TEXT_CHARS` |
| 415 | `Content-Type` autre que `application/json` (protège aussi contre les envois de formulaire intersites, §6) |
| 422 | Corps valide mais non conforme : champ manquant, type faux, champ inconnu, texte vide, thème inconnu ou mal formé |
| 429 | File d'attente des analyses de texte pleine (application, en-tête `Retry-After`), ou limitation de débit Traefik (réponse texte de Traefik, en-têtes `Retry-After` et `X-Retry-In`, **sans** corps JSON ni `request_id`) |
| 503 | `presidio-analyzer` injoignable, en erreur, ou délai `MAX_TEXT_ANALYSIS_SECONDS` dépassé (attente comprise) ; alerte de supervision envoyée |
| 500 | Erreur inattendue : message générique, détail dans les journaux sous le même `request_id` |

Note : le flux documents répond 502 quand Presidio est indisponible. Les routes v1 répondent **503**, plus juste et plus simple à traiter
pour un client qui réessaie (D-005).

### 3.1 Conduite attendue du client, par code (relecture de la phase 2 bis)

Formats **mesurés** à travers la pile le 2026-10-05 (Traefik 3.7.13, oauth2-proxy 7.15.5). Seules les réponses de l'**application** portent
du JSON avec `request_id` ; celles de Traefik et d'oauth2-proxy n'en ont pas : le client se fie au **code HTTP**, jamais au corps.

| Code | Émetteur | Corps | Conduite du client |
|---|---|---|---|
| 200 | application | JSON (§2) | — |
| 400, 415, 422 | application | JSON `detail` + `request_id` | Erreur du client lui-même : ne pas réessayer ; journaliser le `request_id` (jamais le texte) |
| 401 | oauth2-proxy | `Unauthorized` (texte brut) | Session absente ou expirée : ne pas réessayer en boucle, proposer d'ouvrir la page de connexion (§8), puis renvoyer une fois |
| 403 | application | JSON `detail` + `request_id` | Identité absente (D-035) : ne pas réessayer ; proposer de se reconnecter, puis de contacter l'administrateur avec le `request_id` |
| 404 | application | JSON `{"detail":"Not Found"}`, sans `request_id` | API désactivée (`ENABLE_EXTENSION_API=false`) ou chemin inconnu : informer l'utilisateur que le serveur n'offre pas l'API |
| 405 | application | JSON `{"detail":"Method Not Allowed"}`, sans `request_id` | Bogue du client (mauvaise méthode) |
| 413 | Traefik **ou** application | Traefik : `Request Entity Too Large` (texte brut, sans `Content-Type`) ; application : JSON + `request_id` | Texte trop long : le client vérifie lui-même `max_text_chars` (points de code, `GET /version`) avant l'envoi ; ne pas réessayer tel quel |
| 429 | application **ou** Traefik | Application : JSON + `request_id`, `Retry-After: 1` (file pleine) ; Traefik : texte brut, `Retry-After` et `X-Retry-In` (débit par utilisateur, §7) | Réessayer **une** fois après `Retry-After` secondes, puis abandonner en informant l'utilisateur |
| 500 | application | JSON `detail` + `request_id` | Ne pas réessayer automatiquement ; afficher le `request_id` pour l'administrateur |
| 502, 504 | Traefik | texte brut | Application injoignable ou arrêtée : réessayer plus tard, sans boucle |
| 503 | application | JSON + `request_id` ; `Retry-After: 1` (délai dépassé, `outcome` `timeout`) ou `Retry-After: 5` (analyseur injoignable) | Réessayer **une** fois après `Retry-After`, puis informer l'utilisateur |

Dans **tous** les cas d'échec, le texte n'a pas été pseudonymisé : l'extension ne doit **jamais** envoyer le texte d'origine au service d'IA
par repli silencieux (doctrine : un faux négatif est plus grave qu'un faux positif).

Ce qu'un client ne peut pas apprendre de l'API et doit tenir de sa configuration : l'URL du serveur, l'URL de connexion (§8), les limites de
débit de Traefik (§7, non exposées par `/version`).

## 4. Plafonds et variables d'environnement

Toutes déclarées dans `docker-compose.yml` et dans `env.fr.example` / `env.en.example`.

| Variable | Défaut | Justification |
|---|---|---|
| `ENABLE_EXTENSION_API` | `false` | Fonction nouvelle désactivée par défaut (`CLAUDE.md` §3). |
| `MAX_TEXT_CHARS` | `20000` | Mesuré le 2026-10-02 sur la VM (analyseur seul, sans thème, médiane de 5 appels) : 0,29 s à 10 000 caractères, **0,71 s à 20 000**, 2,7 s à 50 000 (croissance plus que linéaire). L'analyseur n'a qu'**un seul worker** partagé avec le flux documents : 20 000 caractères bornent à moins d'une seconde l'attente qu'une requête texte impose à un lot de document. Un prompt courant fait moins de 5 000 caractères. |
| `MAX_TEXT_ANALYSIS_SECONDS` | `10` | Délai total d'une requête (attente dans la file comprise), et délai passé à l'appel HTTP vers Presidio. Environ 14 fois le temps mesuré au plafond de taille : couvre l'attente derrière un lot de document (8 000 caractères au plus, environ 0,25 s) sans laisser un client interactif suspendu. |
| `MAX_TEXT_CONCURRENCY` | `1` | Nombre d'analyses de texte **en cours** à la fois. L'analyseur traite les requêtes une par une ; plus d'une analyse de texte en vol n'accélère rien et allonge l'attente des lots de documents. Avec 1, un lot de document attend au plus une analyse de texte (moins de 1 s au plafond). |
| `MAX_TEXT_QUEUE` | `8` | Requêtes de texte autorisées à **attendre** leur tour. Au-delà : 429 immédiat. Borne la mémoire retenue par les requêtes en attente (au plus 8 corps de 244 Kio). |

Plafond de corps, **dérivé** et non réglable séparément (D-006) :

```
MAX_TEXT_BODY_BYTES = 12 × MAX_TEXT_CHARS + 4096 = 244096 octets (238 Kio) par défaut
```

- **12 octets par point de code** couvrent le pire cas d'encodage JSON : un caractère hors plan de base échappé en deux séquences `\uXXXX`
  (12 octets), comme le produit un client qui échappe tout l'hors-ASCII (`json.dumps` de Python par défaut). Un caractère UTF-8 brut fait au plus 4 octets.
- **4 096 octets** pour l'enveloppe JSON (`{"text":…,"theme":…}`, espaces, nom de thème de 64 caractères au plus).
- Le plafond est appliqué **dans l'application** (lecture du flux interrompue au-delà, sans tout lire) **et en bordure** par Traefik
  (`maxRequestBodyBytes=244096` sur le routeur dédié). **Les deux valeurs doivent rester égales** ; si `MAX_TEXT_CHARS` change, le label Traefik
  doit être recalculé (commentaire en place à côté du label, comme pour `MAX_UPLOAD_MB`).
- Le plafond global existant (`MAX_REQUEST_BODY_BYTES`, 27 Mio) reste en place, mais il est bien plus large ; le plafond propre aux routes v1
  est vérifié dans leur propre code (D-006).

## 5. Journalisation et audit

- **Journal d'audit séparé** : `/data/audit/audit-extension.log` (même répertoire, même format JSON par ligne, même rotation 10 Mio × 10, rotation
  indépendante). Les événements de prompts, potentiellement très fréquents, ne peuvent ainsi pas évincer l'historique des documents de `audit.log`
  (D-007). `GET /api/audit` continue de lire `audit.log` seul.
- Une ligne par requête aboutie ou rejetée par l'application :

  ```json
  {"timestamp": "2026-10-02T10:00:00+0000", "event": "text_pseudonymize", "request_id": "…", "user": "utilisateur.fictif@exemple.invalid",
   "theme": "aucun", "text_chars": 61, "entities_found": {"PERSON": 2, "PHONE_NUMBER": 1}, "total_entities": 3,
   "duration_ms": 42, "outcome": "ok"}
  ```

  `outcome` : `ok`, ou une catégorie fermée (`forbidden`, `too_large`, `invalid`, `busy`, `analyzer_unavailable`, `timeout`, `error`).
- **Jamais** de texte, de valeur détectée, de marqueur ni de `mapping` : uniquement des métadonnées (utilisateur, types et nombres, longueur, durée,
  identifiant). Un test le démontrera sur le journal d'audit **et** sur les journaux des conteneurs (`app`, `presidio-analyzer`, Traefik).
- Journaux applicatifs : une ligne par requête (`request_id`, route, code, durée, nombre d'entités).
- Métriques Prometheus : compteur de requêtes par route et par issue, histogramme de durée (catégories fermées, aucune donnée utilisateur).

## 6. Modèle de menace (résumé)

| Menace | Contre-mesure |
|---|---|
| Accès non authentifié | Chaîne `oidc-auth` existante. Contournement de Traefik depuis un conteneur voisin : secret de passerelle (401). |
| Usurpation d'identité par en-tête | `forwardAuth` supprime puis repose `X-Auth-Request-*` à partir de la réponse d'oauth2-proxy (vérifié dans le code de Traefik 3.7, `pkg/middlewares/auth/forward.go`). |
| Requête intersites (CSRF) avec le cookie de session | `Content-Type: application/json` exigé (415 sinon) : un formulaire intersites ne peut pas l'envoyer, et un `fetch` intersites avec ce type déclenche un prévol CORS que le serveur refuse (aucun en-tête CORS, aucune origine autorisée). Cookie `SameSite=Lax` en plus. |
| Entrée géante | Plafond de corps en bordure et dans l'application (lecture interrompue), `MAX_TEXT_CHARS`, délai maximal. |
| JSON piégé (imbrication profonde, doublons) | Parseur JSON de Pydantic, borné par le plafond de corps ; schéma strict, champs inconnus refusés. |
| Unicode piégeux (surrogates isolées, largeur nulle, NFD, espaces insécables, contrôle bidirectionnel) | Surrogates isolées rejetées. Les autres sont acceptées, puis normalisées avant détection avec une table de correspondance (D-014, phase 2) : positions et pseudonymisation portent sur le texte reçu, invisibles intérieurs compris. Le texte n'est jamais écrit dans un journal, donc pas de risque d'usurpation visuelle des journaux. |
| Déni de service, famine du flux documents | Limitation de débit par utilisateur en bordure ; file bornée et une seule analyse de texte à la fois dans l'application ; délai maximal. Le traitement des documents ne bloque plus la boucle d'événements depuis la phase 2 (EXT-07, D-019 : fil dédié). |
| Identité vide ou absente | 403 avant toute lecture du corps (D-035, phase 2 bis) : aucune requête n'est servie ni auditée sous une identité vide ou commune. |
| Affaiblissement de la détection par le client | Aucun paramètre de seuil, d'entités ni de reconnaisseur ; champs inconnus refusés (422) ; thème inconnu refusé. |
| Énumération (thèmes, utilisateurs) | Les thèmes sont publics pour un utilisateur authentifié (`/version`). Aucune donnée d'autres utilisateurs n'est accessible : pas d'état, pas d'identifiant de tâche. |
| Fuite par les journaux ou l'audit | Métadonnées seulement, démontré par test (§5). `Cache-Control: no-store` sur toutes les réponses (en-têtes de sécurité existants). |
| Fuite par la réponse | La réponse de `pseudonymize` contient les originaux par nécessité : elle ne transite que vers l'utilisateur authentifié qui a envoyé le texte, sur TLS. Rien n'est conservé côté serveur. |
| Marqueur forgé dans le texte d'entrée | Règle de non-collision (§2.3, règle 5) : un marqueur présent dans l'entrée n'est jamais réutilisé, la restauration reste non ambiguë. |

Tests à différer au pentest externe : contournement de la limitation de débit (rotation de sessions, alias d'en-têtes `X_Auth_Request_User`),
injection d'en-têtes à travers la chaîne Traefik → oauth2-proxy, comportement sous charge réelle multi-utilisateurs, robustesse du parseur JSON
face à des corpus de fuzzing, attaques temporelles sur le secret de passerelle depuis le réseau interne.

## 7. Routeur Traefik dédié

```
traefik.http.routers.app-text.rule=Host(`${APP_DOMAIN}`) && PathPrefix(`/api/v1/`)
traefik.http.routers.app-text.priority=100
traefik.http.routers.app-text.middlewares=oidc-auth,text-ratelimit,text-bodylimit,gateway-secret@file
traefik.http.middlewares.text-ratelimit.ratelimit.average=60
traefik.http.middlewares.text-ratelimit.ratelimit.period=1m
traefik.http.middlewares.text-ratelimit.ratelimit.burst=20
traefik.http.middlewares.text-ratelimit.ratelimit.sourcecriterion.requestheadername=X-Auth-Request-User
traefik.http.middlewares.text-bodylimit.buffering.maxRequestBodyBytes=244096
traefik.http.middlewares.text-bodylimit.buffering.memRequestBodyBytes=244096
```

- **Débit** : 60 requêtes par minute et par utilisateur, rafale de 20. Un prompt envoyé coûte une ou deux requêtes (`analyze` puis `pseudonymize`,
  ou `pseudonymize` seul) ; un usage interactif soutenu dépasse rarement un envoi toutes les 2 secondes. C'est plus que les dépôts de fichiers
  (5/min) et moins que les aperçus (120/min). Valeurs de départ, à revoir après le banc de latence (étape E).
- **Clé par utilisateur plutôt que par IP** (D-008) : la clé par IP pénalise tous les utilisateurs derrière un même NAT ou proxy d'entreprise.
  Vérifié dans la documentation et le code de Traefik 3.7 :
  - `sourceCriterion.requestHeaderName` regroupe les requêtes par valeur d'en-tête (exclusif de `ipStrategy`) ;
  - `forwardAuth` pose les `authResponseHeaders` sur la requête transmise, **après suppression** de toute valeur fournie par le client. Les middlewares
    suivants de la chaîne voient donc l'identité authentifiée : `text-ratelimit` doit être placé **après** `oidc-auth` ;
  - si l'en-tête est absent, la valeur extraite est une chaîne vide : toutes ces requêtes partagent un même seau (pas d'erreur côté Traefik) ;
    depuis la phase 2 bis, l'application les refuse ensuite en 403 (D-035).
    `X-Auth-Request-User` est retenu plutôt que `X-Auth-Request-Email` parce qu'oauth2-proxy le renseigne à partir de l'identifiant de l'utilisateur, alors que l'adresse électronique
    peut manquer dans le jeton (supposé d'après la documentation d'oauth2-proxy, à vérifier de bout en bout).
  - Vérifié de bout en bout en phase 2 (`benchmarks/results/multi-user-20261005T061725.md` : deux comptes, même IP, seaux distincts).
- **Plafond de corps** : `buffering` met tout le corps en mémoire jusqu'à `memRequestBodyBytes` ; au plafond de 238 Kio, le tampon disque n'est jamais utilisé.

## 8. Authentification de l'extension

**Phase 2 (D-010, option 1 ; D-020)** : le routeur `/api/v1/` n'utilise plus `oauth2-errors`. Une requête non authentifiée reçoit le **401**
d'oauth2-proxy, relayé tel quel : corps `Unauthorized` en texte brut, sans redirection. Le corps n'est **pas** du JSON : seul le code compte.
`oidc-auth` et le secret de passerelle restent exigés ; les autres routes (interface web) gardent la redirection vers la connexion.

Ce que doit faire le client sur un 401 : ne pas réessayer en boucle ; proposer à l'utilisateur d'**ouvrir la page de connexion** d'Obfusk8
dans un onglet (`https://<domaine>/oauth2/start?rd=%2F`), puis renvoyer la requête une fois la session ouverte. Aucun texte n'a été
analysé ni conservé côté serveur.

Phase 3 : choix entre les options 2 (jetons Bearer acceptés par oauth2-proxy, `skip_jwt_bearer_tokens`) et 3 (réutilisation du cookie de
session du navigateur) de D-010 ; l'option 4 (jetons d'API propres à Obfusk8) reste écartée.

## 9. Décisions ouvertes

- Source de `presidio_version` (D-013) : toujours `null`, l'API REST de Presidio n'exposant pas sa version ; les versions **déclarées** de
  l'analyseur entrent dans `detection_config` (phase 2 bis).
- Type retenu lors d'un chevauchement (D-031) : au backlog de détection (`docs/BACKLOG-detection.md`), règle actuelle inchangée (§2.3).
- Authentification de l'extension : choix entre les options 2 et 3 de D-010 en phase 3 (§8).
