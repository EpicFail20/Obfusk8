# Compte rendu — phase 2 : serveur prêt pour un pilote

Branche locale `feat/pilote-serveur`, créée depuis `feat/text-api` (`89e5132`), le 2026-10-04, sur la VM de test (pile unique `obfusk8`).
Rien n'est poussé ; aucun tag ni release ; aucun commit sur `feat/text-api` (D-025).

Ce compte rendu sépare **observé** (exécuté, sortie lue) et **supposé** (non démontré). Les chiffres viennent des rapports de
`benchmarks/results/` et des sorties reproduites ci-dessous. Toutes les mesures passent par la pile complète (Traefik, oauth2-proxy,
Keycloak, `app` sous `seccomp/app-enforce.json`, `presidio-analyzer`), sauf mention contraire.

## 1. Résumé

- **Normalisation Unicode commune** (D-014) avec table de correspondance, pour l'API texte et le flux documents, par un point d'entrée unique :
  masquage sur le corpus principal 0,846 → 0,910 (étape B seule).
- **Familles de faux négatifs** : carte, NIR et courriel à domaine libre pour tous les flux (EXT-08, EXT-23) ; code postal dans l'adresse
  (EXT-29) ; chiffres espacés (EXT-30) ; dates en lettres (EXT-31). Masquage final 0,973 (27 valeurs exposées sur 1 018, contre 157) ;
  corpus complémentaire 252/252.
- **Détections PDF perdues en silence** (EXT-35, priorité de la décision du 2026-10-04) : mesurées, rendues visibles (audit, avertissement),
  puis couvertes par le repli R1 (D-026) ; EXT-37 corrigé. Le nom qui fuyait dans le PDF caviardé téléchargé ne fuit plus.
- **Boucle d'événements libérée** (EXT-07, EXT-34, D-019) : un prompt pendant un traitement de document p95 732 ms → 261 ms.
- **401 en texte brut** sur `/api/v1/` sans redirection (D-020) ; interface web inchangée.
- **EXT-22, EXT-15, EXT-17** corrigés ; suite de tests entièrement verte sous seccomp.
- **Aucun littéral au format réel d'un secret** dans le dépôt (Q5).
- Premiers bancs du flux documents et d'EXT-18 publiés. **Mesure multi-utilisateurs faite le 2026-10-05** après régularisation des comptes :
  limitation par utilisateur effective, aucune famine (§4). La vérification d'un compte **sans courriel** reste impossible (§7).

## 2. Commits

| Commit | Contenu |
|---|---|
| `86e3668` | Référence de mesure de la phase (étape A), EXT-25 mis à jour |
| `4eed4b4` | `CLAUDE.md` §1 (enseignements de la phase 1) et §2 (langue) |
| `9500b2f` | Décisions D-019 à D-025 ; constats EXT-34 à EXT-36 |
| `e63e86c` | EXT-36 : branche de sauvegarde absente de ce clone |
| `b8edf79` | Outil d'instantané des zones du flux documents et référence (avant toute modification) |
| `b7e2d7f` | Banc EXT-35 ; constats EXT-37, EXT-38 |
| `c140741` | EXT-35 : pertes rendues visibles (audit, journal, avertissement i18n) |
| `306a897` | Repli R1 de localisation PDF (D-026) ; EXT-37 corrigé |
| `462ab55` | Normalisation Unicode commune (D-014) |
| `88c472e` | Carte, NIR, courriel à domaine libre pour tous les flux (EXT-08, EXT-23) |
| `ebb4ee4` | Mesures de l'étape B |
| `e5bb9f1` | EXT-29 à EXT-31, corpus complémentaire, mesures de l'étape C, étude d'EXT-32 |
| `b5ef39e` | **Régression** de `462ab55` corrigée : espaces doubles créées par la normalisation (§7) |
| `d822e04` | Fil d'exécution dédié aux documents (EXT-07, EXT-34) |
| `f258391` | EXT-22 |
| `3a40bb2` | EXT-15 |
| `acd3e82` | EXT-17 |
| `50ce67c` | 401 sans redirection sur `/api/v1/` (Compose : labels du routeur `app-text` seulement) ; contrat et guides FR/EN |
| `b2a8706` | Secrets fictifs reconstruits à l'exécution (Q5) ; corpus non versionnés |
| `c35c8de` | Mesures de latence de l'étape D |
| `780fcd7` | Mesures de l'étape G : flux documents, EXT-18, EXT-35, qualité ; EXT-41, EXT-42 |
| `2e97008` | Vérifications finales et première version de ce compte rendu |
| *(dernier)* | Vérifications finales et ce compte rendu |

## 3. Objectifs chiffrés (validés à l'étape A) et écarts

| Objectif | Référence (`86e3668`) | Résultat final | État |
|---|---|---|---|
| `SECRET` : rappel 21/21, 0 faux positif | 21/21, 0 | 21/21, 0 (tous thèmes) | ✅ observé |
| Largeur nulle, insécables, NFD, typographique : au plus 0,02 sous `base` | 0,646 / 0,789 / 0,810 / 0,932 (base 0,919) | 0,969 / 0,969 / 0,974 / 0,981 (base 0,984) | ✅ observé (écart maximal 0,015) |
| NIR, IBAN, carte en chiffres espacés : au moins 0,95 entièrement couverts | 0/5, 0/3, 0/1 | 36/36, 36/36, 36/36 (corpus complémentaire) ; variante chiffres espacés 0,989 | ✅ observé |
| Adresses de la variante `base` : 0 partiellement couverte | 12/24 partielles | 0 (adresses 62/62 sur tout le corpus) | ✅ observé |
| Dates en toutes lettres : au moins 0,95 entièrement couvertes | 0/25 | 25/25 (corpus principal) ; 108/108 (complémentaire : FR, EN, tirets) | ✅ observé |
| Précision : baisse rapportée, signalée au-delà de 0,05 | 0,886 (chevauchement) | 0,883 (−0,003) | ✅ observé ; précision par type CARD 0,28 (§6) |
| Prompt de 2 000 car. pendant un document : p95 ≤ 300 ms | 732 ms (n = 27) | 261 ms (n = 52) | ✅ observé |
| p95 au repos ≤ +20 % | 70 ms (2 000 car.) ; 18,8 / 38,2 / 168 / 367 / 914 ms | 76 ms (+9 %) ; 23,6 / 49,2 / 181 / 404 / 1 021 ms | ⚠️ **écart** à 200 et 1 000 car. (+25 %, +29 %, soit +5 et +11 ms) ; ✅ ailleurs |
| Flux documents : zones identiques sauf ajouts | 1 717 zones | voir §5 : une zone disparue par cas (faux positif « Antécédents »), ajouts listés | ⚠️ **écart** expliqué |
| Zéro détection PDF perdue sans avertissement (ajouté le 2026-10-04) | non mesuré | 0 valeur exposée sur 10 scénarios ; perte résiduelle signalée et auditée | ✅ observé |

**Écart de latence (observé, cause supposée)** : les petites tailles prennent 5 à 11 ms de plus. Cause supposée : les reconnaisseurs ajoutés
(4 reconnaisseurs communs, un second motif de carte, le NIR inclus) passés à chaque requête par l'analyseur, et marginalement la normalisation
(chemin identité sur ce texte ASCII). Non optimisé dans cette phase ; piste : mesurer le coût par reconnaisseur côté analyseur.

## 4. Vérifications (sorties utiles)

**Tests** (conteneur jetable, image de test, `--security-opt seccomp=seccomp/app-enforce.json`, `--read-only`, `--network none`, tmpfs) :

```
460 passed in 9.55s
```

Référence de départ : `1 failed, 295 passed` (EXT-22). Le test d'EXT-22 passe **sans avoir été modifié**.

**Couverture du code nouveau** (hors seccomp) : `text_normalization.py` 100 % (lignes et branches) ; lignes ajoutées dans `main.py` pendant la
phase : 138 exécutables, 0 non couverte, une branche partielle (caractère introuvable dans l'alignement des boîtes PDF).

**Analyse statique** : `ruff check` et `ruff format --check` verts sur les fichiers créés ou modifiés pendant la phase, sauf
`app/tests/test_main_units.py`, existant, non reformaté (CLAUDE.md §4) : 18 constats, **identiques** avant et après la phase ; `main.py` :
22 constats `E,F,W,B,S,BLE` avant et après. `mypy --strict` : `Success` sur `text_normalization.py`, `text_api.py`, `text_api_models.py`,
`tests/fake_secrets.py` et les bancs nouveaux. `bandit` : 0 constat sur `text_normalization.py` et `text_api.py` ; `main.py` : les 3 mêmes
constats qu'au début de la phase (MD5 d'attributs d'affichage, un `assert`).

**`pip-audit`** : environnement installé de l'image de test, seul `pip` 25.0.1 (12 identifiants, EXT-24, inchangé) ; verrou de développement
sans vulnérabilité. **`trivy` 0.75.0** : 79 HIGH/CRITICAL dans les paquets Debian de l'image de base (EXT-33, inchangé), 0 en Python.
Aucune dépendance de production ajoutée (`docs/DEPENDENCIES.md`).

**Bancs** :

| Mesure | Résultat |
|---|---|
| Qualité, corpus principal, sans thème (`quality-20261004T210913.md`) | masquage 0,973 (27 exposées) ; chevauchement P 0,883 / R 0,975 |
| Corpus complémentaire (`quality-supplementary-20261004T212705.md`) | 252/252 |
| Secrets (`secrets-20261004T205057.md`) | 0 faux positif, 21/21, tous thèmes |
| Latence (`latency-20261004T204532.md`) | §3 ; `/health` p95 5,4 ms pendant un traitement ; 9 processus au plus dans `app` |
| EXT-35 (`pdf-localization-20261004T214610.md`) | 0 entité sans rectangle, 0 valeur exposée avec les zones de l'application, 16 rattrapées par le repli |
| Flux documents (`doc-quality-20261004T214804.md`) | masquage DOCX 0,965, CSV 0,965, PDF 0,951 (sans thème et thème médical) |
| EXT-18 (`propn-sans_filtre-20261004T214628.md`) | §6 |
| Plusieurs utilisateurs (`multi-user-20261005T061725.md`, 4 comptes, même IP) | Limitation par utilisateur : compte-1 20 × 200 puis 10 × 429 (Traefik), compte-2 5 × 200 juste après. Famine : 3 utilisateurs à un prompt de 2 000 car. par seconde pendant que le 4ᵉ fait détecter 18 documents : 954/954 prompts en 200 (aucun 429 ni 503), documents tous détectés (p95 1 583 ms seul, 1 904 ms sous charge) ; prompt p95 210 ms hors traitement, 884 ms pendant (1 seul utilisateur : 261 ms) ; 8 processus au plus. Audit : 3 identités distinctes, aucune vide |

## 5. Non-régression du flux documents

Référence `b8edf79` (1 717 zones, 16 cas : PDF, DOCX, CSV, image × sans thème et 3 thèmes), capturée avant toute modification.

- **Étapes B (normalisation)** : image inchangée ; DOCX, CSV, PDF : 6 zones ajoutées par cas (noms désormais entiers, courriel complet au lieu
  de deux fragments d'URL), 6 à 8 étendues. **Une zone disparue par cas** : « Antécédents », intitulé médical en forme NFD pris à tort pour
  un lieu (LOCATION), plus détecté une fois recomposé : un faux positif disparu, pas une donnée. L'outil de comparaison n'a pas été assoupli.
- **Étape C** : même disparition ; ajouts par cas (sans thème) — NIR (6 en DOCX/CSV, 9 en PDF, 7 en image : EXT-08 dans les flux sans thème),
  cartes et IBAN espacés, 3 dates en lettres, une adresse avec son code postal, noms entiers. Détail ligne par ligne :
  `benchmarks/results/doc-zones-compare-20261004T175359.txt` (types et positions, jamais les valeurs).
- **Final** : voir §9.

## 6. Revue sécurité

**Points vérifiés (observé)**
- Chaîne d'authentification : `oidc-auth`, limitation de débit, plafond de corps et secret de passerelle toujours exigés sur `/api/v1/` ;
  seul `oauth2-errors` est retiré de ce routeur. Réponse non authentifiée : `401`, `text/plain`, `Unauthorized`, en-têtes `Content-Length`,
  `Content-Type`, `Date`, `X-Content-Type-Options` ; ni version, ni URL interne. Interface web : 302 inchangé.
- Aucun contenu utilisateur dans les journaux et l'audit : tests (journaux capturés, audit redirigé) et contrôle de bout en bout (§9).
  Les nouveaux champs d'audit (`pdf_localization_issues`) ne contiennent que des types et des nombres.
- Fil dédié aux documents : PyMuPDF n'est jamais utilisé par deux fils ni par la boucle (test) ; délais coopératifs inchangés ; pas de
  traitement orphelin ; `pids` au plus 9 sous charge.
- Normalisation : positions toujours ramenées sur le texte reçu ; coût linéaire au plafond de 20 000 caractères (test) ; sondes ReDoS au
  plafond pour tous les motifs nouveaux, toutes sous 1 s.
- Aucun paramètre client ajouté ; aucun seuil réglable ; aucune sortie réseau nouvelle.
- Aucun littéral au format réel d'un secret dans le dépôt (recherche finale) ; corpus reconstruits identiques à l'octet.
- Aucun caractère invisible ou de contrôle bidirectionnel littéral dans les fichiers créés pendant la phase (séquences d'échappement imposées ;
  EXT-39 pour les fichiers existants).

**Points ouverts**
- EXT-41 (valeur PDF coupée par un retour à la ligne), EXT-42 (secrets non détectés dans les documents), EXT-32 (noms en contexte dense,
  étude faite, pistes mesurées), EXT-40 (CSV à une seule colonne reconnue), EXT-18 (décision à prendre).
- Précision par type CARD 0,28 : les chiffres espacés d'un NIR ou d'un IBAN sont aussi typés « carte » ; masquage correct, libellé trompeur
  pour le relecteur.
- `detection_config` (empreinte de `/version`) ne reflète que les fichiers de configuration, pas le code de normalisation : deux versions de
  la normalisation peuvent avoir la même empreinte. Proposition : y inclure une version de la normalisation (changement du contrat, à décider).
- `X-Auth-Request-User` pour un compte sans courriel : **non vérifié** (§7).

**Tests à différer au pentest externe** : ceux de la phase 1 (contournement de la limitation de débit, injection d'en-têtes, fuzzing du
parseur JSON, attaques temporelles sur le secret de passerelle), plus : charge multi-utilisateurs réelle ; PDF hostiles conçus pour tromper la
localisation (texte invisible, polices sans table ToUnicode, glyphes réordonnés) ; Unicode piégeux hors du corpus (homoglyphes, scripts mêlés) ;
épuisement du fil de documents par des aperçus en rafale dans la limite de débit.

## 7. Incidents, écarts et points à décider

1. **Comptes de test** (observé) : le 2026-10-04, Keycloak exigeait pour `compte-2` et `compte-3` de compléter le profil et pour `compte-4` un
   changement de mot de passe ; je ne les ai pas modifiés. Régularisés par l'humain le 2026-10-05 : la mesure multi-utilisateurs est faite (§4).
   Le compte sans courriel (compte-4, modifié par l'humain le 2026-10-05) a ensuite été testé : **oauth2-proxy refuse sa connexion** (500,
   « email in id_token () isn't verified »), l'application n'est jamais atteinte (EXT-43). La page affiche la version d'oauth2-proxy, comme la
   page de connexion (EXT-44).
10. **Identifiants affichés dans la session de travail** (le 2026-10-05) : en filtrant le journal d'oauth2-proxy pour diagnostiquer compte-4,
    j'ai affiché les noms d'utilisateur et identifiants internes (UUID Keycloak) des comptes 1 à 3. Rien n'est écrit dans le dépôt ni dans les
    rapports (vérifié : 0 fichier contenant une valeur des comptes) ; les commandes suivantes masquent ces champs.
2. **Régression introduite puis corrigée** (`462ab55` → `b5ef39e`) : la normalisation transformait « glyphe sans correspondance + espace » en
   deux espaces ; le NER ne trouvait plus le nom ; le nom fuyait dans le PDF caviardé. Trouvée en rejouant le test de bout en bout EXT-35 pendant
   l'étape D, que je n'avais pas rejoué après l'étape B. Test de non-régression ajouté.
3. **Erreurs de mes outils de mesure**, toutes corrigées avant publication : premier instantané des zones sans les cellules CSV (refait) ;
   mesure de couverture EXT-35 trop naïve puis remplacée par le caviardage réel ; banc de latence qui chronométrait le cadencement du client
   (résultat écarté, non versionné) ; banc EXT-35 qui utilisait encore l'ancienne chaîne d'analyse (refait) ; deux 503 légitimes
   (`MAX_PENDING_JOBS`) dus à mon ordonnancement des bancs.
4. **Mise en pause probable de l'analyseur de la pile** pendant `docker commit` (option `--pause=false` dépréciée et ignorée), vers 19 h 48,
   pendant une mesure de latence ; cette mesure a ensuite été invalidée pour une autre raison (point 3) et refaite.
5. **Image d'origine de l'analyseur absente** : le conteneur `presidio-analyzer` tourne sur une image (`ce18c22c…`) qui n'existe plus
   localement (cohérent avec EXT-01). L'image de mesure d'EXT-18 « avec filtre » est donc un instantané du conteneur en service (`b7295b15…`).
6. **Images locales** : `app` tourne sur une image de test dérivée de l'image de départ (seuls les fichiers modifiés remplacés, aucune image de
   base tirée, EXT-01) ; l'image de départ est conservée sous l'étiquette locale `obfusk8-app-local:phase2-reference` ; l'étiquette locale
   `ghcr.io/epicfail20/obfusk8-app:main` désigne l'image de test. Images de mesure d'EXT-18 sans étiquette.
7. **Écritures dans les volumes réels** : `audit.log` est passé de 49 à 93 lignes (métadonnées des finalisations synthétiques des bouts en bout) ;
   `audit-extension.log` contient les métadonnées des bancs. Rien n'a été effacé.
8. **Commit `b8edf79`** : son message annonce 1 662 zones, le fichier en compte 1 717 ; corrigé dans le message du commit suivant, sans
   réécriture d'historique.
9. Sur la branche de sauvegarde de H-6 : absente de ce clone (EXT-36).

## 8. Observé et supposé

**Supposé, non démontré**
- Le `\x00` dans le texte extrait (glyphe sans correspondance Unicode) est fréquent dans les PDF réels (polices sous-ensemblées sans table
  ToUnicode) : non mesuré sur des PDF réels.
- Les 3 téléphones exposés en PDF le sont parce que le motif ne franchit pas le saut de ligne (EXT-41) : corrélation exacte observée, cause
  supposée.
- L'écart de latence sur les petites tailles vient des reconnaisseurs ajoutés (§3).
- Le filtre PROPN ne coûte aucun faux négatif sur des données réelles : observé seulement sur des corpus synthétiques.
- La supposition de la phase 1 (« `X-Auth-Request-User` renseigné pour un compte sans courriel ») est **levée autrement** (observé) : un compte
  sans courriel ne passe pas oauth2-proxy (EXT-43) ; pour les comptes avec courriel, l'en-tête est renseigné et distinct (audit).
- La hausse de latence à plusieurs utilisateurs vient de la sérialisation voulue des analyses (`MAX_TEXT_CONCURRENCY=1`, un worker d'analyseur
  partagé avec les documents) : cohérent avec la conception, non mesuré composant par composant.

## 9. Vérifications finales (étape H)

Sur la pile finale (image de test : code de `780fcd7`), sous `app-enforce.json` (`Seccomp: 2`) :

| Vérification | Résultat (observé) |
|---|---|
| Suite de tests (conteneur jetable, seccomp) | `460 passed` |
| Zones du flux documents contre la référence (`doc-zones-compare-20261004T220347.txt`) | 16 cas ; seule disparition : « Antécédents » (faux positif, §5) dans les cas DOCX, CSV et PDF ; image : 0 disparition ; ajouts : 9 à 24 par cas |
| Bout en bout flux documents (`e2e_document_flow.py`) | 8/8 (PDF, DOCX, CSV, image × sans thème, médical) : détection, aperçu, finalisation, téléchargement |
| Bout en bout EXT-35 (`e2e_pdf_localization.py`) | 2/2 : le nom a disparu du PDF caviardé téléchargé |
| Bout en bout API texte (`e2e_text_api.py`) | 29/29 (dont 401 texte brut sans fuite d'en-tête, interface web 302 inchangée, plafonds, limitation de débit) |
| Aucun contenu dans les journaux et l'audit (`check_no_content_in_logs.py`, depuis 05 h 40) | 597 valeurs soumises recherchées (corpus principal, complémentaire, secrets), **0 trouvée** dans les journaux de `app`, `presidio-analyzer`, `presidio-anonymizer`, Traefik, oauth2-proxy, Keycloak et dans `audit.log` et `audit-extension.log`. Réserve : les journaux de `app` ne couvrent que le dernier conteneur |
| Littéraux au format réel d'un secret dans le dépôt | 0 (recherche par motifs AWS, Google, Azure, GitHub, GitLab, Slack, Telegram, Stripe, PEM, JWT, URI avec identifiants) |
| Drapeau désactivé (`.env` restauré à l'identique, `ENABLE_EXTENSION_API` absent) | `/api/v1/version`, `/analyze`, `/pseudonymize` → 404 identique à un chemin inconnu ; flux documents 4/4 |
| `pip-audit`, `trivy` | §4 : rien de nouveau (EXT-24, EXT-33) |

## 10. Préparation de D-024 (suivi des téléchargements, étude seule)

La finalisation livre le fichier par `GET /api/download/<job_id>` sous le nom `caviarde_<thème>_<8 premiers caractères du job>.<ext>`
(`FileResponse`, en-tête `Last-Modified` du fichier). Ce qui rendrait le suivi fragile :
- **PDF, PNG et JPG sont servis en `Content-Disposition: inline`** (affichage dans l'onglet ou dans la page d'aperçu) : il n'y a pas de
  téléchargement au sens du navigateur tant que l'utilisateur n'enregistre pas depuis la visionneuse ; `chrome.downloads` verra alors un
  téléchargement dont l'URL et le nom peuvent différer (supposé, à vérifier par le prototype) ;
- le navigateur peut renommer un doublon (« (1) ») ; l'utilisateur peut renommer le fichier ;
- le fichier est purgé côté serveur après `FILE_TTL_SECONDS` : un nouveau téléchargement plus tard est impossible ;
- la date de modification du fichier téléchargé dépend du système et du navigateur (heure du téléchargement ou `Last-Modified`).

## 11. Ce qui reste pour la suite

- **Avant la phase 3** : les décisions du §12.
- **Phase 2 bis** (D-021) : EXT-01, EXT-09, EXT-11, EXT-12, EXT-13, EXT-16, EXT-21, EXT-24, EXT-33 ; EXT-39 et EXT-40 peuvent s'y joindre.
- **Phase 3** : choix entre les options 2 et 3 de D-010 ; le client traite le 401 en texte brut (contrat §8) ; mapping des marqueurs à
  ne garder qu'en mémoire.
- Fusion finale dans `main` : intégrer `ca3c50c` (licence AGPL-3.0, résout EXT-05) et `7a7908e` (icône) (D-025).

## 12. Décisions en attente

Chaque point est une décision humaine : données mesurées, options, et ma recommandation (que je n'applique pas).

| # | Sujet | Données (observé) | Options | Recommandation |
|---|---|---|---|---|
| 1 | **EXT-18 — filtre PROPN** du correctif spaCy | Corpus principal : masquage 0,940 avec et sans filtre ; faux positifs 150 avec, 240 sans. Corpus complémentaire : 1,000 dans les deux cas ; 147 contre 166 | a) garder le filtre ; b) le retirer ; c) le garder et mesurer sur des données plus proches du réel avant de trancher | a + c : aucun faux négatif mesuré, 90 faux positifs évités ; mais corpus synthétique écrit par la même équipe |
| 2 | **EXT-42 — secrets dans les documents** | 6 SECRET exposés sur 6 dans le banc du flux documents (DOCX, CSV, PDF) ; par conception (D-015) les reconnaisseurs de secrets ne servent qu'à l'API texte | a) les appliquer à tous les flux (même mécanisme qu'EXT-08/EXT-23) ; b) garder la séparation | a, en mesurant les faux positifs sur des documents ordinaires (code, configurations) avant activation |
| 3 | **EXT-41 — valeur PDF coupée par un retour à la ligne** | 3 téléphones exposés en PDF, exactement les 3 coupés par un retour à la ligne ; 0 en DOCX et CSV | a) pour l'analyse seulement, remplacer les sauts de ligne internes à un bloc PyMuPDF par une espace (positions conservées) ; b) ne rien faire | a dans la phase suivante, avec banc avant et après (faux positifs de valeurs collées d'une ligne à l'autre) |
| 4 | **Empreinte `detection_config`** de `/version` | Ne reflète que la configuration (thèmes, reconnaisseurs, seuil, langue), pas le code de normalisation | a) y inclure une version de la normalisation ; b) inchangé | a : changement compatible du contrat (même champ, plus sensible), à noter dans le contrat FR/EN |
| 5 | **Précision par type CARD (0,28)** | Les chiffres espacés d'un NIR ou d'un IBAN sont aussi typés « carte » ; masquage correct | a) lors d'un chevauchement, retenir le type le plus spécifique (NIR, IBAN avant carte) ; b) accepter | a, côté serveur (fusion des chevauchements) : le relecteur voit un libellé juste |
| 6 | **EXT-32 — noms en contexte dense** | NER seul 44/64 noms des invites exposées ; NER + motif « titre ou fonction + Prénom Nom » 57/64, 0 correspondance hors nom sur 257 invites | a) ajouter ce motif aux reconnaisseurs communs ; b) attendre | a, avec test et banc ; les 7 restants relèvent d'EXT-19 (noms anglais) |
| 7 | **Latence au repos des petites invites** | +5 ms à 200 car. (+25 %), +11 ms à 1 000 car. (+29 %) ; autres tailles ≤ +12 % | a) accepter l'écart ; b) mesurer le coût par reconnaisseur et optimiser | a : écart absolu faible ; b si le pilote le ressent |
| 8 | **Zone « Antécédents » disparue** du flux documents | Faux positif LOCATION (forme NFD) qui disparaît une fois le texte recomposé ; seule disparition sur 16 cas | a) accepter cet écart à la règle « zones identiques sauf ajouts » ; b) le refuser | a : ce n'est pas une donnée |
| 9 | **Compte sans courriel** (EXT-43) | Testé le 2026-10-05 : connexion refusée par oauth2-proxy (500), application jamais atteinte ; refus fermé | a) documenter pour les administrateurs l'exigence d'un courriel vérifié dans le fournisseur d'identité, et améliorer le message (page d'erreur personnalisée) ; b) autoriser les courriels non vérifiés (`insecure_oidc_allow_unverified_email`) | a ; pas b : l'identité sert à l'audit et à la propriété des tâches |
| 9 bis | **Version d'oauth2-proxy divulguée** (EXT-44) | « Secured with OAuth2 Proxy version v7.15.4 » sur la page de connexion publique et les pages d'erreur | a) `footer = "-"` dans `oauth2-proxy/oauth2-proxy.cfg` (documentation officielle), à appliquer avec ton accord ; b) laisser | a (une ligne, aucune incidence fonctionnelle ; à vérifier de bout en bout) |
| 10 | **Plusieurs utilisateurs simultanés** | Aucune famine ; prompt p95 884 ms pendant un traitement avec 3 utilisateurs intensifs (261 ms avec 1) | a) accepter pour le pilote ; b) augmenter `MAX_TEXT_CONCURRENCY` ou les workers de l'analyseur (ressources, D-021) | a pour un pilote restreint ; b à mesurer si le nombre d'utilisateurs grandit |
| 11 | **Complément de `CLAUDE.md` §1** | Faits nouveaux : fil unique pour PyMuPDF (D-019), point d'entrée `_analyze_normalized` (D-014), `app/tests/fake_secrets.py` (Q5), repli de localisation PDF (D-026), séquences d'échappement imposées pour les caractères invisibles | a) je propose le diff ; b) non | a |
| 12 | **EXT-39, EXT-40** (caractères bidirectionnels littéraux dans des tests existants ; CSV à une seule colonne reconnue) | Consignés, non corrigés | Les joindre à la phase 2 bis ou les traiter à part | Phase 2 bis |
| 13 | **Validation de la phase** | Ce compte rendu | Avancer `feat/text-api` par `git merge --ff-only feat/pilote-serveur` (D-025), puis pousser | À ta décision après relecture du diff |
