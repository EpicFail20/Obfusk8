# Phase 3 — prototype de l'extension (panneau latéral) : compte rendu du serveur

Branche locale `feat/extension-serveur`, créée depuis `feat/text-api` (`a6e6a35`, identique à `origin/feat/text-api`), jamais poussée.
Dépôt de l'extension : `~/obfusk8-extension`, branche locale `feat/prototype`, jamais poussée (son compte rendu :
`obfusk8-extension/docs/phase-3-report.md`). Date : 2026-10-07. Décisions : D-054 (humaine, étape A), D-055 (mise en œuvre, à valider).

## 1. Résumé

- **Option 3 de D-010 prouvée puis utilisée** : le cookie de session d'oauth2-proxy (`SameSite=Lax`, `Secure`, `HttpOnly`, inchangés)
  accompagne les requêtes du panneau latéral ; parcours complet réussi contre la pile réelle, sans contournement de certificat.
- **Serveur** : liste blanche `EXTENSION_ALLOWED_ORIGINS` sur `/api/v1/` (vide par défaut) : un POST exige l'origine d'une extension
  autorisée, `GET /version` accepte une origine absente, toute autre origine → 403 `origin_refused`, avant lecture du corps.
- **Certificat du laboratoire** : le certificat par défaut de Traefik, régénéré à chaque démarrage et sans le nom du service (EXT-62),
  est remplacé par un certificat signé par une autorité **restreinte à `.lab.local`**. Le refus d'un autre domaine est prouvé dans Chromium.
- **Conformité de la pseudonymisation locale de l'extension avec `/pseudonymize` : 0 écart** sur 2 404 paires (tout le corpus, 4
  configurations de thème). La propagation D-012 est bien incluse dans `/analyze`.
- **Option 2 prévue** (D-045 §C point 18, bloquante pour la production) : documentation FR/EN d'oauth2-proxy non activée ; côté
  extension, stratégie `oidc` fermée et construction de production qui refuse `session`.
- **Fin de phase** : 599 tests verts sous seccomp ; aucun contenu dans les journaux ni l'audit ; ports sur `BIND_ADDRESS` seulement ;
  **API texte activée au laboratoire** (`ENABLE_EXTENSION_API=true`, origine de l'extension du laboratoire autorisée).

## 2. Commits (`a6e6a35..HEAD`)

| Commit | Objet |
|---|---|
| `eea5837` | `CLAUDE.md` : diff de la phase Python validé |
| `6dd7ed3` | D-054 ; D-045 point 18 (option 2 bloquante pour la production) |
| `f589d2c` | Liste blanche des origines d'extension (code, i18n, tests écrits avant, Compose, `env.*.example`) |
| `6ec21e2` | Bancs : origine de l'extension du laboratoire |
| `5f3dd50` | Autorité du laboratoire et certificat servi par Traefik |
| `528efec` | Documentation FR/EN : contrat, option 2 (`extension-oidc*.md`), guides de déploiement (§6 bis, §8) |
| `71ae205` | `lab-tls.yml` sans options TLS redondantes |
| `52ac38f` | Collecteur de conformité |
| `04e0b64` | Constats EXT-62 à EXT-65, dépendances, D-055, D-045 point 7 |
| `99bc23c` | Collecteur : séquence d'échappement (test d'hygiène, EXT-39) |
| (ce commit) | Compte rendu |

## 3. Étape A : prototype jetable de l'option 3 (rappel des observations)

Extension MV3 minimale, Chromium de Playwright 1.63.0 en conteneur, compte de test n° 1, en-têtes capturés par le protocole de
débogage et recoupés avec les journaux de Traefik et d'`app` :
- cookie `_oauth2_proxy` envoyé (non bloqué), `Sec-Fetch-Site: none` ; `/oauth2/auth` → 202, `/api/v1/*` → 404 (API alors désactivée),
  401 sans session ; mêmes résultats **dans le vrai panneau latéral** (`sidePanel.open()` sur un clic) et dans le service d'arrière-plan ;
- `Origin: chrome-extension://<id>` sur les POST, **absente sur les GET** (d'où la règle de la §1) ;
- certificat : `TypeError: Failed to fetch` tant que l'avertissement n'est pas passé dans un onglet ;
- identifiant stable avec `key` (même identifiant depuis deux chemins, égal au calcul SHA-256 de la clé publique).

## 4. Vérifications (sorties utiles)

**Point de départ** : `feat/text-api` = `origin/feat/text-api` = `a6e6a35` ; `app/run-tests.sh` → `564 passed` ; ports sur `192.168.1.35`.

**Tests** (`app/run-tests.sh`, `app-enforce.json`, `--read-only`, `--network none`) : `599 passed in 11.44s`.
- +33 tests d'origine (`test_text_api_origin.py`), écrits avant le code et observés en échec (`57 failed, 21 errors`) ;
- +2 tests du client des bancs, observés en échec avant le correctif ;
- banc de test existant adapté (D-055 point 2).

**Analyse statique** (outils du verrou de développement, conteneur de l'image `app`, Python 3.14) :
- `ruff check` vert sur les fichiers modifiés ;
- `ruff format` appliqué aux seuls fichiers nouveaux. L'écart de `text_api.py` (`except` au style de la PEP 758) est antérieur : EXT-64.
- `mypy --strict` : `Success: no issues found in 3 source files` (`text_api.py`, `text_api_models.py`, `test_text_api_origin.py`) ;
- `bandit` : 0 constat.

**Bout en bout**, image finale `sha256:4b1b3536…` sous seccomp (`Seccomp: 2`), TLS vérifié par l'autorité du laboratoire
(`REQUESTS_CA_BUNDLE`, plus de `BENCH_INSECURE_TLS`) :

| Mesure | Résultat |
|---|---|
| `e2e_text_api.py` | **34/34**, dont origine absente / interface web / autre extension → 403 (POST), version sans origine → 200, origine non autorisée → 403 |
| Extension, `tools/run.sh e2e-lab` | Vert : 401 réel, connexion Keycloak, analyse, pseudonymisation, restauration, document, 401 après effacement du cookie |
| Extension, tests dans Chromium | 20/20 (serveur simulé : chaque code de réponse, injection, console, stockage, disque) |
| Conformité (`collect_pseudonymize_pairs.py` puis `tools/run.sh conformity`) | `2404 pairs, 7340 entities, 952 pairs with overlapping entities, 0 mismatch(es)` ; contrôle négatif (paire altérée) → 1 écart détecté |
| Journaux et audit (`check_no_content_in_logs.py --since 2026-10-07T15:28:00`) | 5 conteneurs et 2 journaux d'audit (310 et 31 685 lignes) : **633 valeurs recherchées, 0 trouvée** ; valeurs témoins propres à l'extension : 0 |
| Certificat | `openssl s_client -CAfile ca.crt` : `Verify return code: 0 (ok)`, TLS 1.3 ; Chromium avec l'autorité dans le magasin NSS : `test.lab.local` 200, `evil.example.com` et `lab.local.example.com` **`net::ERR_CERT_INVALID`**, sans l'autorité : `ERR_CERT_AUTHORITY_INVALID` |
| trivy 0.75.0, image `app` | 76 HIGH, 1 CRITICAL, **0 corrigeable**, identique à l'image d'avant (paquets Debian, EXT-33) |

**Fin de phase** :
- `.env` : `ENABLE_EXTENSION_API=true`, `EXTENSION_ALLOWED_ORIGINS=chrome-extension://glaimpfdmfkidcgalcblojmkomplcgpa` ;
- journal : `1 origine(s) d'extension autorisée(s)`, `configuration=d2fa3efcf16bdce9` (inchangée : la détection n'a pas changé) ;
- `ss -tlnp` : `192.168.1.35` sur 80, 443 et 8080 seulement ;
- code en service identique au dépôt (`text_api.py`, i18n : empreintes). Une première image, construite avant le découpage d'une ligne
  de journal, ne l'était pas : l'écart a été vu et l'image reconstruite ; toutes les vérifications ci-dessus ont été rejouées sur l'image
  finale.

**Drapeau désactivé** : `ENABLE_EXTENSION_API=false` → routeur non construit, `EXTENSION_ALLOWED_ORIGINS` sans effet ; les tests
existants du drapeau restent verts et `e2e_document_flow.py` vérifie le 404.

## 5. Revue sécurité

**Vérifié (observé)**
- Cookie de session non affaibli (aucun réglage d'oauth2-proxy modifié).
- Liste blanche d'origine : refus avant lecture du corps, aucun appel à l'analyseur, audit sans contenu, 403 traduit ; plusieurs en-têtes
  `Origin` refusés ; une entrée mal formée empêche le démarrage.
- Autorité du laboratoire : contrainte de noms critique effective dans Chromium. Sa clé privée reste dans `~/.obfusk8-lab-ca` (700),
  hors du dépôt. La clé du serveur est en 644 dans `traefik/certs/` (parent en 700, ignoré par git, même convention que le secret de passerelle).
- Aucune dépendance de production ajoutée ; durcissement des conteneurs inchangé ; aucune sortie réseau nouvelle.
- Aucun secret dans les commits : recherche, sans affichage, des valeurs de `secrets/*.txt`, des mots de passe des comptes de test, de
  la clé de l'autorité et de celle du serveur dans `git log -p a6e6a35..HEAD` (serveur) et `git log -p` (extension). Seule
  correspondance : `keycloak_admin_password.txt`, une **coïncidence** : la valeur est un mot anglais courant de 4 lettres minuscules,
  présent 77 et 254 fois comme mot entier dans le code et les tests (contextes vérifiés, valeur masquée).

**Points ouverts**
- EXT-63 (compte de test n° 4), EXT-64 (style `except`), EXT-65 (identifiant du laboratoire réutilisable par une extension non empaquetée).
- La liste d'origine n'est pas une authentification ; elle protège la session contre les autres extensions du navigateur (modèle H-2).
- Option 2 non implémentée : **bloquante pour la production**.

**À différer au pentest**
- Contournement de la règle d'origine par une autre extension (`declarativeNetRequest`, modification d'en-têtes en mode entreprise).
- Lecture de l'interface web par une extension `<all_urls>`.
- Rotation de sessions contre la limitation de débit par utilisateur.
- Robustesse de la chaîne Traefik → oauth2-proxy face à des en-têtes `Origin` piégés (encodages, doublons à travers le proxy).

**Secrets du laboratoire (D-049)** : le mot de passe du compte de test n° 1 a été lu par les scripts sans jamais être affiché ; une copie
temporaire (fichier du répertoire de travail de la session, monté dans le conteneur de test) a été supprimée après chaque usage. Le mot
de passe administrateur du Keycloak de laboratoire est un mot courant de 4 lettres, déduit par la vérification ci-dessus (exposition dans
la session, laboratoire seulement, D-046).

## 6. Observé et supposé

| Observé | Supposé |
|---|---|
| Cookie envoyé depuis le panneau, `Origin` présente sur POST et absente sur GET (Chromium de Playwright) | Même comportement dans Chrome et Edge stables sous Windows et macOS |
| Contrainte de noms respectée par Chromium sous Linux (magasin NSS) | Même respect par le vérificateur de Chrome et d'Edge sous Windows et macOS (magasin du système) |
| Conformité sur 2 404 paires, 952 avec chevauchements | Conformité sur des textes hors corpus (règles portées ligne à ligne, tests unitaires des vecteurs du serveur) |
| Les en-têtes `X-Auth-Request-*` sont posés pour une session | Leur présence avec un jeton Bearer (option 2) : **non documentée**, à vérifier en premier (`extension-oidc.md`) |
| Origine `chrome-extension://` stable avec `key` | Origine `moz-extension://` propre à chaque installation sous Firefox |

## 7. Procédure de test manuel (pour l'humain)

**A. Fusion et push**
1. Serveur : relire puis `git -C ~/obfusk8 checkout feat/text-api && git merge --ff-only feat/extension-serveur && git push origin feat/text-api`.
2. Extension (dépôt privé, alias `github-ext` déjà configuré) : `git -C ~/obfusk8-extension push -u origin feat/prototype`, puis fusion
   dans `main` à ta convenance.

**B. Serveur (VM)**
1. Reconstruire depuis la branche poussée : `docker compose -f docker-compose.yml -f docker-compose.build.yml build app`, puis
   `docker compose up -d app`. L'analyseur ne change pas.
2. `.env` (déjà fait sur la VM) : `ENABLE_EXTENSION_API=true`,
   `EXTENSION_ALLOWED_ORIGINS=chrome-extension://glaimpfdmfkidcgalcblojmkomplcgpa` ; vérifier
   `docker compose logs app | grep "origine(s) d'extension"`.
3. Certificat (déjà fait sur la VM) : `traefik/generate-lab-cert.sh` ; vérifier
   `openssl s_client -connect 192.168.1.35:443 -servername obfusk8.lab.local -CAfile ~/.obfusk8-lab-ca/ca.crt` → `Verify return code: 0`.

**C. Poste** (`deploiement-lab.md` §6 bis)
1. `/etc/hosts` : `192.168.1.35 obfusk8.lab.local keycloak.lab.local` (si ce n'est pas déjà le cas).
2. `scp debian@192.168.1.35:.obfusk8-lab-ca/ca.crt obfusk8-lab-ca.crt` ; comparer l'empreinte (`openssl x509 -noout -fingerprint -sha256`)
   avec celle de la VM ; importer l'autorité **utilisateur** selon ton système (commandes Windows, macOS, Linux du guide) ; redémarrer le
   navigateur. Contrôle : `https://obfusk8.lab.local` s'ouvre **sans** avertissement.
3. Construire l'extension. Sur la VM :
   ```bash
   tools/run.sh ci
   tools/run.sh build-lab
   ```
   Puis copier `dist/lab` sur le poste : `scp -r debian@192.168.1.35:obfusk8-extension/dist/lab obfusk8-extension-lab`. Ou bien
   reproduire ces deux commandes sur le poste, avec Docker.
4. `chrome://extensions` ou `edge://extensions` → **Mode développeur** → **Charger l'extension non empaquetée** → dossier copié.
   Vérifier l'identifiant `glaimpfdmfkidcgalcblojmkomplcgpa`, puis épingler l'icône.

**D. Parcours complet** (données synthétiques uniquement)
1. Cliquer sur l'icône : panneau latéral ouvert, bandeau « Détection automatique : relisez avant d'envoyer. ».
2. Sans session : message « Vous n'êtes pas connecté… » et bouton **Se connecter**. Le cliquer, se connecter dans l'onglet, revenir au
   panneau, cliquer sur **Réessayer** : « Serveur prêt (API 1.0) ».
3. Coller par exemple `Bonjour, je suis Zébulon Canarihaut, joignable au 06 98 76 54 32 ou zebulon.canarihaut@exemple.invalid. Zébulon Canarihaut habite à Rennes.`,
   puis **Analyser** : valeurs surlignées, liste des valeurs avec cases à cocher.
4. Décocher une valeur (par exemple la ville) : barrée, elle ne sera pas masquée. Masquer un passage oublié, de deux façons : sélection
   puis **Masquer la sélection**, et saisie dans « Passage oublié » puis **Masquer ce passage**.
5. **Pseudonymiser**, relire, **Copier**, coller dans un service d'IA avec une consigne qui fait reprendre les marqueurs `⟦…⟧`.
6. Coller la réponse dans « Réponse de l'IA », puis **Restaurer** : les vraies valeurs reviennent. Un marqueur inconnu ou altéré est signalé.
7. Nouveau prompt avec la même personne : même marqueur (`⟦PERSON_1⟧`). **Effacer la correspondance** : « Correspondance vide ».
8. **Caviarder un document** : l'interface obfusk8 s'ouvre dans un onglet, déjà connectée.
9. Clavier seul : Tab jusqu'à **Analyser**, Entrée ; Tab jusqu'aux cases, Espace.

**E. Erreurs à provoquer**
- Déconnexion (`/oauth2/sign_out`) puis **Analyser** → « Vous n'êtes pas connecté… » et « Le texte n'a pas été vérifié ».
- Texte de plus de 20 000 caractères → « Texte trop long », aucune requête.
- `EXTENSION_ALLOWED_ORIGINS=` (vide) et `docker compose up -d app` → « Requête refusée par le serveur… » avec l'identifiant de requête.
  Remettre la valeur ensuite.
- `ENABLE_EXTENSION_API=false` → « Ce serveur obfusk8 n'offre pas l'API de l'extension ». Remettre `true`.
- `docker compose stop presidio-analyzer` → après un réessai automatique, « Moteur de détection indisponible ». Puis
  `docker compose start presidio-analyzer`.
- Retirer l'autorité du poste → « Serveur injoignable ou certificat non reconnu ». La réimporter ensuite.

**F. Fin du laboratoire** : retirer l'autorité du poste (§6 bis point 4) ; `~/.obfusk8-lab-ca` disparaît avec la VM (D-049).

## 8. Décisions qui te reviennent

1. **Valider D-055** (règle d'origine, banc de test adapté, certificat du laboratoire, collecteur, retour arrière).
2. **Extension** : valider ses D-005 à D-008 (pile ; 60 min et copie du texte restauré ; conduite par code ; alternative au clavier) ;
   les points 9 et 10 de l'étape A n'étaient pas tranchés par la décision du 2026-10-07, mes recommandations sont appliquées.
3. **EXT-63** : compte de test n° 4 à vérifier dans Keycloak. **EXT-64** : adopter ou non le style d'`except` de la PEP 758.
4. **Disque** : 3,1 Go libres (87 %). Images d'outils que j'ai tirées, supprimables par toi si besoin :
   - `mcr.microsoft.com/playwright` (3,55 Go), nécessaire aux tests de l'extension ;
   - `ghcr.io/google/osv-scanner` (479 Mo).
   Retour arrière : `obfusk8-local:avant-phase3-app` et `obfusk8-local:avant-phase3-presidio-analyzer`.
5. **Diff de `CLAUDE.md`** ci-dessous.

## 9. Proposition de mise à jour de `CLAUDE.md` (non appliquée)

```diff
-Constatées dans le dépôt le 2 octobre 2026, … et le 7 octobre 2026 (phases « disponibilité » et « Python »).
+Constatées dans le dépôt le 2 octobre 2026, … et le 7 octobre 2026 (phases « disponibilité », « Python » et 3).
@@ Routage Traefik par labels
-`/api/v1/` (routeur `app-text` : API texte de l'extension, **désactivée jusqu'à la phase 3**, `ENABLE_EXTENSION_API=false` dans `.env`, D-041 ;
+`/api/v1/` (routeur `app-text` : API texte de l'extension, activée au laboratoire depuis la phase 3 (D-054), désactivée par défaut ;
+liste blanche `EXTENSION_ALLOWED_ORIGINS` : POST avec l'`Origin` d'une extension autorisée, 403 `origin_refused` sinon, §8.1 du contrat ;
@@ Flux documents
+**Extension** (phase 3) : dépôt séparé et privé `obfusk8-extension` (son propre `CLAUDE.md`), panneau latéral MV3. Prototype authentifié par
+le cookie de session (option 3) ; **option 2 (jeton OIDC) obligatoire avant la production** (D-045 §C point 18, `docs/extension-oidc.md`).
+L'extension pseudonymise elle-même à partir de `/analyze` : toute modification des règles de `text_api.pseudonymize` (D-004) impose de
+rejouer sa conformité (`benchmarks/conformity/`, puis `tools/run.sh conformity` dans l'extension).
@@ Adresse fixe de Traefik
+**Certificat du laboratoire** : autorité restreinte à `.lab.local` (`traefik/generate-lab-cert.sh`, clé privée dans `~/.obfusk8-lab-ca`,
+jamais dans le dépôt), certificat servi par `traefik/dynamic/lab-tls.yml`. Bancs : `REQUESTS_CA_BUNDLE=~/.obfusk8-lab-ca/ca.crt`
+plutôt que `BENCH_INSECURE_TLS`. Le compte de test n° 4 ne se connecte pas (EXT-63).
```
