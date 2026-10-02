# Compte rendu — phase 1 : socle serveur pour l'extension (API texte)

Branche `feat/text-api`, du commit `d833a2a` (départ) à la fin de la phase, le 2026-10-02, sur la VM de test (pile unique `obfusk8`).
Rien n'est poussé ; aucun tag ni release.

Ce compte rendu sépare **observé** (exécuté, sortie lue) et **supposé** (non démontré). Les chiffres viennent des rapports de
`benchmarks/results/` et des sorties de commandes reproduites ci-dessous.

## 1. Résumé

- API texte `/api/v1/version`, `/api/v1/text/analyze`, `/api/v1/text/pseudonymize`, **désactivée par défaut** (`ENABLE_EXTENSION_API=false`),
  avec contrat bilingue (`docs/api-extension.md`), routeur Traefik dédié, plafonds justifiés par mesure, journal d'audit séparé.
- Détection des secrets fréquents dans les prompts et des identifiants manquants au flux sans thème (carte, NIR, courriels internes),
  pour les seules routes texte ; flux documents strictement inchangé.
- Banc d'essai de qualité et de latence à travers toute la pile : la référence est posée, sept familles de faux négatifs consignées.
- 33 constats `EXT-` consignés (dont 6 constats préliminaires confirmés) ; aucun corrigé hors périmètre.

## 2. Commits

| Commit | Contenu |
|---|---|
| `374654c` | `CLAUDE.md` |
| `e202bc3` | Constats de reconnaissance EXT-01 à EXT-21 |
| `42b9913` | Réparation des exécuteurs de tests existants (assertions inchangées) |
| `e33c51c` | Contrat de l'API (étape B), décisions, modèles Pydantic |
| `6721a00` | Outillage de développement isolé, `docs/DEPENDENCIES.md` |
| `761924b` | Implémentation de l'API texte (étape C) |
| `b397b78` | Drapeau, plafonds et routeur Traefik dans Compose et `env.*.example` |
| `368eedb` | Contrat, décisions, constats après l'étape C |
| `5a31484` | Détection des secrets et identifiants (étape D) |
| `12a2b2e` | Décisions D-015, D-017, D-018 — **contient aussi**, par erreur d'enchaînement, `benchmarks/obfusk8_client.py` et un rapport de résultats (voir §7) |
| `adb64cb` | Corpus et scripts du banc des secrets |
| `ecf7af6` | Remplacement d'une valeur égale au mot de passe du compte de test (voir §7) |
| `fe8a7b7` | Banc de qualité et de latence (étape E) |
| `50d5c37` | Tests de bout en bout (étape F) |
| `be55e29` | Documentation utilisateur et administrateur FR/EN, constats E/F |
| *(dernier)* | Ce compte rendu, D-014, registre des dépendances |

## 3. Critères d'acceptation

| Critère | État | Preuve |
|---|---|---|
| Drapeau désactivé : comportement strictement identique | ✅ observé | Routes absentes (test `test_drapeau_desactive_routes_absentes_404_identique`) ; de bout en bout, flux documents identique à la référence de l'étape A (mêmes zones, mêmes totaux, 8 cas) et 404 identique à un chemin inconnu ; aucune écriture dans `audit-extension.log` (1 918 lignes avant et après) |
| Drapeau activé : contrat, routeur dédié, plafonds en bordure et dans l'application, authentification et secret de passerelle | ✅ observé | `e2e_text_api.py` 27/27 ; 413 Traefik au-delà de 244 096 octets et 413 applicatif au-delà de 20 000 caractères ; 302 sans authentification ; 401 sans secret de passerelle depuis un conteneur voisin |
| Aucun paramètre client ne peut affaiblir la détection | ✅ observé | `score_threshold`, `entities` et tout champ inconnu → 422 (tests unitaires et de bout en bout) ; thème inconnu → 422 |
| Les nouvelles routes ne peuvent pas affamer le flux documents (mesuré) | ✅ observé, avec réserve | Détection d'un CSV de 1 200 cellules : 1,55 s seul, 1,53 s sous un flux de prompts à 0,94 requête par seconde. Réserve : un seul compte de test, donc le flux d'un seul utilisateur intensif (limitation 60/min par utilisateur) |
| Tout fonctionne sous `seccomp/app-enforce.json` | ✅ observé | Conteneur `app` : `Seccomp: 2` ; tous les essais de bout en bout et les bancs ; suite unitaire dans un conteneur jetable sous le même profil. Aucun appel système ajouté au profil |
| Aucun contenu utilisateur en journaux ou en audit ; aucun état conservé | ✅ observé, avec réserve | Tests unitaires (journaux capturés et audit) ; `check_no_content_in_logs.py` : 345 valeurs soumises recherchées, 0 trouvée dans 6 conteneurs et 2 journaux d'audit. Réserve : les journaux de `app` ne couvrent que le dernier conteneur (chaque recréation efface les précédents) ; recherche manuelle équivalente à l'étape C (0 occurrence) |
| Dépendances vérifiées et consignées | ✅ observé | `docs/DEPENDENCIES.md` : aucune dépendance de production ajoutée ; outils de développement aux dernières versions (sauf `regex`, aligné sur l'analyseur, D-017), verrou avec empreintes |
| Détection des secrets avec mesure des faux positifs | ✅ observé | 0 faux SECRET sur 30 textes et extraits de code (0 sur 257 prompts du corpus de qualité, contrôle indépendant), rappel 21/21 |
| Référence de qualité et de latence publiée, familles d'erreurs consignées | ✅ observé | `benchmarks/results/quality-20261002T091416.*`, `latency-20261002T091919.*` ; EXT-26 à EXT-32 |
| Constats préliminaires vérifiés et consignés | ✅ observé | EXT-01 à EXT-06, tous confirmés |
| Documentation FR/EN, `DECISIONS.md`, `FINDINGS.md`, modèle de menace, compte rendu | ✅ | Ce document ; modèle de menace : `docs/api-extension.md` §6 |

## 4. Vérifications (sorties utiles)

**Tests** (conteneur jetable, image de production, `--security-opt seccomp=seccomp/app-enforce.json`, sans réseau ni vrais volumes) :

```
1 failed, 295 passed in 10.29s
FAILED tests/test_main_units.py::test_upload_chunke_sans_content_length_interrompu_au_plafond   # préexistant, EXT-22
```

Référence de départ (étape A, dans le conteneur de production) : `4 failed, 164 passed` — trois échecs dus aux exécuteurs de test (EXT-10,
réparés sans toucher aux assertions), le quatrième révélant le défaut réel EXT-22, laissé rouge volontairement.

**Couverture du code nouveau** (sans seccomp : la base SQLite de `coverage` y est bloquée) :

```
text_api.py            371      0     70      1    99%
text_api_models.py      52      0      0      0   100%
```

**Analyse statique** : `ruff check` et `ruff format --check` verts sur tout le code nouveau (modules, tests, bancs) ; `mypy` strict :
`Success: no issues found` (modules et bancs) ; `bandit` : 0 constat sur `text_api.py` et `text_api_models.py`, 6 constats de sévérité
basse sur les bancs (`subprocess` à arguments fixes, générateur pseudo-aléatoire pour des données synthétiques), justifiés en commentaire.

**`pip-audit`** (environnement installé de l'image finale, service OSV) : une seule distribution vulnérable, `pip` 25.0.1 de l'image de base
(EXT-24) ; verrou de développement sans vulnérabilité.

**`trivy` 0.75.0** sur l'image `app` reconstruite : 0 vulnérabilité HIGH/CRITICAL dans les paquets Python ; 79 dans les paquets Debian de
l'image de base (EXT-33), dont une seule corrigeable (`libpcre2-8-0`) ; recoupement OSV des deux plus notables.

**Bancs** (à travers toute la pile) :

| Mesure | Résultat |
|---|---|
| Qualité, sans thème, chevauchement | P 0,886 / R 0,904 / F1 0,895 |
| Qualité, sans thème, strict | P 0,731 / R 0,754 / F1 0,742 |
| Masquage (valeur entièrement couverte) | 0,846 sans thème (157 valeurs exposées sur 1 018) ; 0,919 sur les prompts sans variante |
| Thèmes compta / it / medical | Identiques sans thème, sauf medical : 159 valeurs exposées (2 de plus) |
| Secrets | 0 faux positif, rappel 21/21, tous thèmes |
| Latence `analyze` p95 | 30 ms (200 car.), 48 ms (1 000), 189 ms (5 000), 380 ms (10 000), 972 ms (20 000) |
| Latence `pseudonymize` p95 | 44 ms (1 000), 378 ms (10 000) |
| Prompt 2 000 car. pendant un traitement de document | p50 736 ms contre 67 ms hors traitement (n = 3, EXT-07) |

## 5. Revue sécurité

**Points vérifiés (observé)**
- Chaîne d'authentification inchangée et exigée ; secret de passerelle exigé (401 en contournement, aucune trace de l'identité usurpée dans l'audit).
- `forwardAuth` supprime puis repose `X-Auth-Request-*` (lu dans le code de Traefik 3.7) ; limitation de débit par utilisateur effective (20 × 200 puis 429).
- Plafond de corps en bordure (413 Traefik) et dans l'application (413 avant lecture complète, lecture interrompue au plafond).
- Validation stricte : type de contenu, UTF-8, JSON, schéma ; surrogates isolées rejetées ; aucun écho du texte dans les erreurs (testé avec une valeur témoin).
- File bornée (429), délai global (503 `timeout` sans alerte), analyseur indisponible (503 avec une alerte limitée à une toutes les 5 minutes).
- Aucun contenu dans les journaux et l'audit (voir la réserve au §3) ; `Cache-Control: no-store` sur les réponses.
- Motifs des reconnaisseurs : sondes ReDoS au plafond de 20 000 caractères, toutes sous 0,5 s.
- CSRF : `application/json` exigé (415 sinon) ; aucun en-tête CORS.

**Points ouverts**
- Faux négatifs mesurés (EXT-26 à EXT-32), en particulier caractères de largeur nulle et espaces insécables : une normalisation à table de
  correspondance est proposée (D-014), non implémentée.
- EXT-08 et EXT-23 restent ouverts pour le flux documents.
- Le flux documents bloque la boucle d'événements (EXT-07, hors périmètre) : les prompts attendent pendant un traitement de document.
- Authentification adaptée à une extension (302 sans `Location` aujourd'hui) : options en D-010.
- Révision humaine : déléguée à l'extension (D-011), non imposable par le serveur.
- EXT-22 : 413 converti en 400 sur le flux documents en accès direct.

**Tests à différer au pentest externe** : contournement de la limitation de débit (rotation de sessions, alias d'en-têtes
`X_Auth_Request_User`) ; injection d'en-têtes à travers Traefik et oauth2-proxy ; charge multi-utilisateurs réelle (plusieurs comptes) ;
fuzzing du parseur JSON ; attaques temporelles sur le secret de passerelle depuis le réseau interne ; robustesse des marqueurs `⟦…⟧`
face aux modèles d'IA (aller-retour réel, hors ligne ici).

## 6. Observé et supposé

**Supposé, non démontré**
- Le flux documents partage les familles de faux négatifs EXT-26 à EXT-32 (même moteur et même normalisation) : non mesuré sur des documents.
- Le blocage de l'hôte entre 08:26 et 08:28 vient d'une pression mémoire (EXT-25) : aucun OOM journalisé à cette heure-là.
- `lxml` n'utilise pas la `libxml2` du système (EXT-33).
- `X-Auth-Request-User` est toujours renseigné par oauth2-proxy : observé pour le compte de test, non vérifié pour un compte sans courriel.
- Les marqueurs `⟦TYPE_N⟧` survivent à un aller-retour par un modèle d'IA : non testable hors ligne.

## 7. Incidents, écarts et points à décider

1. **Mot de passe du compte de test dans l'historique local.** J'ai repris le mot de passe réel du compte Keycloak de test comme exemple
   « fictif » dans un test et dans le corpus des secrets (commits `5a31484` et `adb64cb`). Remplacé dans `ecf7af6`, mais **présent dans
   l'historique non poussé**. À décider avant toute poussée : changer le mot de passe du compte de test, et/ou m'autoriser à réécrire
   l'historique de la branche (interdit sans accord, `CLAUDE.md` §3).
2. **Commit `12a2b2e` au message incomplet** : il contient aussi le client des bancs et un rapport (enchaînement interrompu par un `.gitignore`).
   Correction possible par réécriture, sur accord.
3. **Poussée vers GitHub** : le corpus et les tests contiennent des jetons fictifs au format réel (Stripe, Slack, GitHub…). La protection
   contre les secrets de GitHub peut bloquer la poussée. Options : autoriser ces valeurs dans GitHub, ou les générer à l'exécution.
4. **Incident mémoire de mon fait** (EXT-25) : archive `docker save` et cache `trivy` écrits dans le tmpfs `/tmp` → trois OOM globaux qui
   ont tué le worker de l'analyseur ; un premier passage du banc de qualité interrompu. Mémoire libérée, analyseur rétabli, mesures refaites
   sans autre activité ; `trivy` relancé avec cache sur disque et mémoire limitée.
5. **Écritures dans les volumes réels** : la suite de tests lancée à l'étape A dans le conteneur de production a ajouté 4 lignes de test à
   `audit.log` ; le scénario de référence et les essais de bout en bout du flux documents en ont ajouté d'autres (métadonnées de documents
   synthétiques) ; `audit-extension.log` contient les métadonnées des bancs. Rien n'a été effacé.
6. **Image de base mise à jour par mes reconstructions** (non épinglée, EXT-01) : Python 3.12.14 → 3.12.15 dans l'image `app` locale.
   L'étiquette locale `ghcr.io/epicfail20/obfusk8-app:main` désigne désormais l'image de la branche.
7. **Changement hors liste autorisée, avec accord** : une ligne `COPY` dans `app/Dockerfile` (accord du 2026-10-02).
8. **`.env`** restauré à l'identique (drapeau absent, donc désactivé) ; la pile tourne avec le drapeau désactivé.
9. Fichier `phase-1-prompt.md` non suivi, laissé tel quel.

## 8. Ce qui reste pour la phase suivante

Normalisation Unicode à table de correspondance (D-014) et corrections des familles EXT-26 à EXT-32 ; authentification de l'extension (D-010) ;
EXT-07 (traitement des documents hors de la boucle d'événements) ; EXT-22 ; épinglage des images (EXT-01) ; dimensionnement de la VM (EXT-25) ;
mesure multi-utilisateurs avec plusieurs comptes de test.
