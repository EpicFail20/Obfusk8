# Prompt Claude Code — Phase 1 : socle serveur pour l'extension (API texte)

> À coller dans Claude Code, depuis la racine du dépôt sur la VM de test, branche `feat/text-api`.
> Prérequis : `CLAUDE.md` est committé à la racine. Il s'applique intégralement.

---

## Cadre

Lis d'abord `CLAUDE.md` en entier, en particulier la doctrine (§0) et les particularités du dépôt (§1). Applique-les strictement.

## Objectif

Préparer le serveur à recevoir, plus tard, des **prompts** (texte libre) envoyés par une extension de navigateur, pour y détecter et pseudonymiser
les données sensibles avant envoi à un service d'IA. **Aucun code d'extension** dans cette phase.

Livrables :

1. Un contrat d'API documenté (français et anglais) pour l'analyse et la pseudonymisation de texte.
2. Les endpoints, sans état, désactivés par défaut, avec leur routeur Traefik dédié.
3. La détection des secrets courants dans les prompts.
4. Un banc d'essai de qualité **et de latence** sur un corpus synthétique de prompts.

## Hors périmètre

- Extension de navigateur, quoi que ce soit côté client.
- Authentification par jeton Bearer, modification d'`oauth2-proxy.cfg` ou de Keycloak. Les nouvelles routes utilisent la chaîne d'authentification actuelle
  (`oauth2-errors`, `oidc-auth`, `gateway-secret@file`). Tu documentes les options pour la phase suivante dans `docs/DECISIONS.md` (voir étape B).
- Certificat TLS, endpoints d'audit pour l'extension ou de politique par domaine.
- Toute modification du flux documents.
- Correction des constats préliminaires listés à l'étape A : tu les **vérifies et les consignes**, tu ne les corriges pas.
- Tag, release, publication d'image.

## Changements autorisés hors code applicatif

Par exception au §9 de `CLAUDE.md`, cette phase t'autorise **uniquement** à :

- ajouter dans `docker-compose.yml`, sur le service `app`, les labels d'**un routeur Traefik dédié** aux nouvelles routes et ses middlewares
  (limitation de débit, plafond de corps), sur le modèle des routeurs `app-upload` et `app-preview`, avec la même chaîne d'authentification ;
- ajouter les variables d'environnement de la phase (drapeau et plafonds) dans `docker-compose.yml` et dans `env.fr.example` / `env.en.example` ;
- étendre le profil seccomp **si et seulement si** un appel système manquant est identifié selon la méthode de `seccomp/README.md`, avec justification.

Tout autre changement de configuration se propose et attend mon accord.

---

## Étape A — Reconnaissance, constats préliminaires et plan (puis ARRÊT)

Sans rien modifier :

1. **Lis le code réel** et résume ce que tu y trouves :
   - `app/main.py` : structure des routes, `_GatewaySecretMiddleware`, `MAX_REQUEST_BODY_BYTES`, format des erreurs, identifiant de corrélation s'il existe,
     façon dont l'identité de l'utilisateur est lue (`X-Auth-Request-*`), écriture du journal d'audit ;
   - l'appel à `presidio-analyzer` : client HTTP utilisé, synchrone ou asynchrone, délais, gestion des pannes, et **comment le travail est exécuté
     sans bloquer le worker unique** ;
   - le mécanisme des thèmes et des seuils (dossier des thèmes, `DEFAULT_SCORE_THRESHOLD`), et les reconnaisseurs personnalisés
     (côté `app` ou côté `presidio/analyzer-build`), ainsi que la logique de propagation des valeurs identiques ;
   - `app/i18n/`, `supervision.py`, `antivirus.py`, `branding.py` ;
   - `presidio/analyzer-build` : version de Presidio, modèles spaCy, reconnaisseurs ;
   - `seccomp/README.md` et les deux profils ;
   - `.github/workflows/` : quelles images sont construites, sous quels noms et tags, sur quel déclencheur ;
   - les tests existants, l'outillage existant, la version de Python de l'image ;
   - `SECURITE.fr.md` / `SECURITY.md`, section limites connues.
2. **Vérifie et consigne dans `docs/FINDINGS.md`** (préfixe `EXT-`, statut ⏳, sans les corriger) ces constats relevés en lisant le dépôt.
   Confirme ou infirme chacun, preuve à l'appui :
   - images non épinglées : `ghcr.io/epicfail20/obfusk8-app:main`, `ghcr.io/epicfail20/obfusk8-presidio-analyzer:latest`, `keycloak:26.0`
     (ligne mineure flottante), `busybox:1.36` à vérifier ;
   - incohérence entre le README (images `ghcr.io/epicfail20/obfusk8:vX.Y.Z`) et les noms d'images réels ;
   - le démarrage rapide du README copie `.env.example`, alors que le dépôt contient `env.fr.example` et `env.en.example` ;
   - liens du README vers `SECURITE.md` et `CHANGELOG.md`, fichiers absents à la racine ;
   - licence : le README annonce l'AGPL-3.0, la page GitHub détecte la GPL-3.0 ; vérifie le texte du fichier `LICENSE`
     (la différence compte pour un service utilisé à travers le réseau) ;
   - nom du projet Compose codé en dur (`traefik.docker.network=obfusk8_app-internal`) et volumes sur chemins absolus de l'hôte.
3. **Établis la référence** : la suite de tests existante (s'il y en a une) et un scénario manuel du flux documents passent **avant** toute modification. Note le résultat.
4. **Rédige le plan** : fichiers créés et modifiés, découpage en commits, risques, questions ouvertes. Le prompt a été écrit sans lire `main.py` :
   **si le code contredit le prompt, le code a raison**, signale l'écart.

**STOP : présente l'état des lieux, les constats et le plan. Attends ma validation.**

---

## Étape B — Contrat d'API (puis ARRÊT)

Rédige `docs/api-extension.md` et `docs/api-extension.en.md`, plus les modèles Pydantic. Propositions de départ, à adapter aux conventions découvertes à l'étape A :

- **Préfixe** : choisis entre le style existant (`/api/<verbe>`) et un préfixe versionné (`/api/v1/text/...`). Justifie : l'extension aura besoin
  de détecter une incompatibilité de version.
- `GET  .../version` : version de l'API et de la configuration de détection (version Presidio, thèmes disponibles).
- `POST .../text/analyze` : entrée `{text, theme?}`. Sortie : entités `{entity_type, start, end}` avec positions **sur la chaîne reçue telle quelle**.
  **Pas de paramètre de seuil, pas de liste d'entités à désactiver, pas de reconnaisseur fourni par le client** (doctrine §0).
  Si tu renvoies le score, documente qu'il n'est pas calibré et ne doit pas servir à filtrer.
- `POST .../text/pseudonymize` : sortie `{text, mapping:[{placeholder, entity_type, original}]}`. Une même valeur reçoit le même placeholder
  dans tout le texte, en cohérence avec la propagation déjà en place pour les documents. Placeholders non ambigus et non susceptibles d'apparaître
  naturellement dans un texte. **La restauration se fait côté client** à partir du `mapping` : pas d'endpoint de restauration, rien n'est conservé.
- **Thème** : les mêmes thèmes et seuils que le flux documents ; sans thème, `DEFAULT_SCORE_THRESHOLD`.
- **Erreurs** : même format que les erreurs existantes, messages via i18n, identifiant de corrélation, codes HTTP précis (400, 404, 413, 422, 429, 503).
- **Drapeau** `ENABLE_EXTENSION_API` (défaut `false`) : désactivé, les routes n'existent pas (404).
- **Plafonds** : `MAX_TEXT_CHARS`, délai maximal d'analyse, concurrence maximale **propre** à ces routes (sémaphore ou équivalent), pour qu'elles
  ne puissent pas affamer le flux documents. Justifie chaque valeur par défaut, et le lien entre `MAX_TEXT_CHARS`, le plafond de corps de l'application
  et celui de Traefik (un caractère UTF-8 peut faire jusqu'à 4 octets, plus l'enveloppe JSON).
- **Routeur Traefik dédié** : limitation de débit adaptée à un usage interactif (plus fréquent que les dépôts de fichiers, mais borné) et plafond de corps.
  Point à étudier : la limitation de débit par IP pénalise des utilisateurs derrière un même NAT ou proxy d'entreprise. Vérifie dans la documentation
  de la version de Traefik utilisée si elle peut s'appuyer sur un en-tête d'identité posé par `oidc-auth`, et si l'ordre des middlewares le permet.
- **Modèle de menace** court de ces routes.
- **Options pour la phase suivante** (documentées, pas implémentées) : le middleware `oauth2-errors` transforme les 401 en redirection 302 vers la page de connexion,
  ce qui convient à un navigateur mais pas à une extension qui attend du JSON. Décris les options (routeur sans `oauth2-errors` pour l'API, acceptation de jetons
  Bearer par `oauth2-proxy`, etc.) avec leurs conséquences de sécurité.
- **Décisions ouvertes** : ce que tu n'as pas pu trancher seul.

**STOP : présente le contrat. Attends ma validation avant d'implémenter.**

---

## Étape C — Implémentation

- Modules séparés du flux documents ; aucun changement de comportement existant.
- Réutilise le client Presidio, les thèmes, la propagation et l'i18n existants. Pas de chemin de détection parallèle.
- Sans état : rien sur disque, rien conservé ; **le journal d'audit ne reçoit que des métadonnées** (utilisateur, types et nombres d'entités, longueur, durée,
  identifiant de corrélation), dans le format existant.
- Aucun appel bloquant sur le worker unique ; délai maximal vers `presidio-analyzer` et erreur 503 propre s'il ne répond pas, avec alerte via `supervision.py`
  si c'est cohérent avec l'usage existant.
- Routeur Traefik, variables et documentation selon le contrat validé.

## Étape D — Détection des secrets

Couvre les secrets les plus fréquents dans des prompts : blocs de clé privée PEM, JWT, chaînes de connexion avec identifiants, affectations
`password`/`token`/`secret`/`api_key` = valeur, clés d'API de grands fournisseurs (cloud, forges de code, messagerie, paiement).

- **Emplacement** : utilise d'abord les mécanismes existants (reconnaisseurs des thèmes, `presidio/analyzer-build`, ou reconnaisseurs ponctuels si l'API
  REST de la version de Presidio utilisée le permet). Une bibliothèque tierce n'est acceptable qu'avec la justification du §6 de `CLAUDE.md`,
  en comptant la revalidation seccomp.
- **Formats** : vérifie chaque format de clé dans la documentation officielle du fournisseur et cite la source en commentaire. Pas de format de mémoire.
- Type d'entité distinct (par exemple `SECRET`), actif quel que soit le thème.
- Mesure les **faux positifs** sur du texte ordinaire et du code sans secret : un détecteur trop bavard sera contourné par les utilisateurs.

## Étape E — Banc d'essai de qualité et de latence

L'audit a établi qu'aucune mesure systématique de rappel et de précision n'existe, et que des angles morts ont été découverts au fil de l'eau
(adresses, noms en contexte dense, dates avec tiret non-ASCII). Cette étape crée la mesure pour les prompts.

1. `benchmarks/` : corpus **synthétique** de prompts annotés (JSONL : texte et entités `{start, end, type}`) en français, avec une part en anglais et en texte mixte :
   courriels, notes de travail, extraits de contrat, notes cliniques fictives, RH, tickets informatiques, code contenant des secrets.
   Explique comment le corpus est produit et pourquoi les valeurs sont fictives (plages de numéros réservées à la fiction quand elles existent).
2. **Variantes adversariales** : tirets et apostrophes typographiques, espaces insécables, caractères de largeur nulle, formes NFD, chiffres espacés
   (téléphone, IBAN, numéro de sécurité sociale), noms en majuscules, adresses sur plusieurs lignes, noms dans un contexte dense.
3. **Qualité** : précision, rappel et F1 par type d'entité et au global, en mode strict et en mode chevauchement, **pour chaque thème et sans thème**.
4. **Latence** : p50 et p95 par taille de prompt, mesurés à travers la pile complète (Traefik compris) ; plus l'effet d'un flux de prompts soutenu
   sur le temps de réponse d'un traitement de document en parallèle. Un outil interactif trop lent sera contourné.
5. Rapport Markdown et JSON, horodaté, avec les versions du moteur, des modèles et des thèmes.
6. **Pas de seuil de réussite inventé.** La première exécution fixe la référence. Rapporte les chiffres tels quels et consigne les familles de faux négatifs
   dans `docs/FINDINGS.md` (⏳).

## Étape F — Vérifications finales

1. `ruff`, `mypy`, `bandit`, `pytest --cov`, `pip-audit`, `trivy` sur les images reconstruites. Sorties utiles collées.
2. **Non-régression** du flux documents (PDF, DOCX, CSV, image avec OCR, révision, finalisation, téléchargement), drapeau désactivé puis activé.
3. **Sous le profil seccomp bloquant** : toute la pile, y compris les nouvelles routes et la détection des secrets.
4. Tests dynamiques : texte vide, énorme, binaire, JSON invalide, Unicode piégeux, dépassement de concurrence, dépassement de débit, requête non authentifiée,
   requête qui contourne Traefik sans secret de passerelle, drapeau désactivé (404).
5. Test démontrant qu'**aucun contenu de texte** n'apparaît dans les logs des conteneurs ni dans le journal d'audit.
6. Compte rendu `docs/phase-1-report.md`, en séparant **observé** et **supposé**.

## Critères d'acceptation

- [ ] Drapeau désactivé : comportement strictement identique (preuve par tests)
- [ ] Drapeau activé : contrat respecté, routeur dédié actif, plafonds appliqués en bordure et dans l'application, authentification et secret de passerelle exigés
- [ ] Aucun paramètre client ne peut affaiblir la détection
- [ ] Les nouvelles routes ne peuvent pas affamer le flux documents (mesuré)
- [ ] Tout fonctionne sous `seccomp/app-enforce.json`
- [ ] Aucun contenu utilisateur en logs ou en audit ; aucun état conservé
- [ ] Dépendances vérifiées et consignées
- [ ] Détection des secrets avec mesure des faux positifs
- [ ] Référence de qualité et de latence publiée, familles d'erreurs consignées
- [ ] Constats préliminaires vérifiés et consignés
- [ ] Documentation FR/EN, `DECISIONS.md`, `FINDINGS.md`, modèle de menace, compte rendu

## Format de tes réponses

À chaque étape : ce que tu as fait, les commandes exécutées et leurs sorties utiles, ce qui reste, tes questions.
Sépare **observé** et **supposé**. Ne franchis pas les points d'arrêt sans mon accord. Si une règle de `CLAUDE.md` t'empêche de suivre ce prompt, dis-le.
