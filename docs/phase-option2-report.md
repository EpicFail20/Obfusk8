# Phase « option 2 » — jeton OIDC de l'extension et effacement au verrouillage : compte rendu du serveur

Branche locale `feat/option2-serveur`, créée depuis `feat/text-api` (`d4fedea`, identique à `origin/feat/text-api`), jamais poussée.
Extension : `~/obfusk8-extension`, branche `feat/option2` créée par l'humain depuis `main` (`43bb864`, `feat/ajustements` fusionnée,
vérifié) ; son compte rendu : `obfusk8-extension/docs/phase-option2-report.md`. Date : 2026-10-12.
Décisions : D-056 (mode forcé, consigné), D-057 (étape A, humaine), D-045 points 18, 19 et 21 mis à jour.

## 1. Résumé

- **Option 2 faite et prouvée au laboratoire.** L'extension obtient son propre jeton auprès de Keycloak (PKCE S256, `state`, `nonce`,
  client public, pas de jeton de rafraîchissement). oauth2-proxy le vérifie : signature, émetteur, **audience exacte**, expiration. Un
  jeton d'une autre application du même émetteur est refusé (**vrais jetons**, client témoin `autre-application`). Bout en bout réel
  depuis l'extension, **sans onglet obfusk8**.
- **Parcours par cookie inchangé** : flux documents de bout en bout (8 cas, 0 valeur exposée), quotas, annulation, protections d'origine.
  Un jeton de l'extension **n'ouvre pas** l'interface web : `Authorization` est retiré avant `oidc-auth` sur les routeurs web.
- **Identité unifiée sur le `sub`** (D-057 point 9) dans l'audit, les quotas, la propriété des documents et la limitation de débit.
  oauth2-proxy posait **déjà** le `sub` pour le cookie (observé) ; seule l'application a changé. Cookie et jeton d'un même agent
  partagent un seau de débit (observé). **Chez Entra ID, le `sub` est propre à chaque application** (EXT-68) : à trancher avant un pilote.
- **`EXTENSION_REQUIRE_BEARER`** : désactivé au laboratoire, à activer en production. Avec `true`, le cookie n'ouvre plus l'API (401
  JSON `bearer_required`), observé.
- **Effacement au verrouillage** (D-045 point 19) : fait côté extension, jeton compris.
- **Aucun jeton dans les journaux ni l'audit** (6 conteneurs, 2 journaux d'audit, 26 fragments cherchés) ; aucune dépendance ajoutée.
- **Fin de phase** : 635 tests verts sous seccomp (599 au départ) ; ports sur `BIND_ADDRESS` seulement.

## 2. Commits (`d4fedea..HEAD`)

| Commit | Objet |
|---|---|
| `31ef5ff` | `CLAUDE.md` : diff du compte rendu de la phase 3 appliqué (D-057 point 10) |
| `3832e35` | D-056 (mode forcé configurable), D-057, D-045 point 21, EXT-66 et EXT-67 |
| `688d4a2` | Identité unifiée sur le `sub` dans tous les flux (tests écrits avant) |
| `5c135ee` | `EXTENSION_REQUIRE_BEARER`, couplé à `ENABLE_EXTENSION_OIDC` au démarrage (tests écrits avant) |
| `1842887` | Compose : jetons acceptés par oauth2-proxy derrière `ENABLE_EXTENSION_OIDC`, `strip-authorization` sur les routeurs web, `env.*.example` |
| `d44fa7a` | Clients Keycloak du laboratoire (fichiers versionnés et script) ; bouts en bout `e2e_extension_oidc.py`, `check_tokens_not_logged.py` |
| `9c5046e` | Documentation FR/EN (`extension-oidc`, contrat, guides §8 bis, note DPO), EXT-68, dépendances |
| (ce commit) | D-045 points 18 et 19, EXT-69, compte rendu |

## 3. Étape A : essai jetable sur l'audience (rappel)

Émetteur OIDC minimal et oauth2-proxy v7.15.5 (image en service) sur un réseau Docker interne. Audience `obfusk8-api` → 202. Autre
audience, `account`, audience absente, expiré, signature fausse, `alg: none`, autre émetteur, `email_verified=false` → 403. Sans groupe
autorisé → 401. Jeton sans `sub` ni courriel → 202 **sans identité** (refusé ensuite par l'application, D-035). Code source v7.15.5
lu (`verifier.go`, `options.go`, `oauthproxy.go`, `jwt_session.go`, `session.go`, `provider_data.go`). Détails : D-057.

## 4. Vérifications (sorties utiles)

**Tests de l'application** (`app/run-tests.sh`, image reconstruite, profil `seccomp/app-enforce.json`) :
```
604 passed in 11.54s     (identité sub : test_identity_sub.py échouait 7/7 sur l'image d'avant)
635 passed in 11.62s     (fin de phase ; test_text_api_bearer.py : 29 échecs avant le code)
```
**Analyse statique** (outils du verrou de développement, conteneur de l'image `app`) :
- `ruff check` vert sur les fichiers nouveaux et modifiés ; écart de lint ou de format introduit : aucun (comparaison avant/après
  fichier par fichier ; écarts antérieurs de `main.py`, `test_main_units.py`, `test_audit_log.py` et `text_api.py` (EXT-64) inchangés) ;
- `ruff format` appliqué aux seuls fichiers nouveaux et à `test_text_api.py`, déjà formaté ;
- `mypy --strict text_api.py tests/test_text_api_bearer.py` : `Success: no issues found in 2 source files` ;
  `e2e_extension_oidc.py`, `check_tokens_not_logged.py` : `Success: no issues found in 2 source files` ;
- `bandit text_api.py` : aucun constat (les deux MD5 de `main.py` sont antérieurs, lignes non touchées).

**Dépendances** : aucune ajoutée (`docs/DEPENDENCIES.md`). trivy (image `app` avant et après) : 79 HIGH et 1 CRITICAL, **0 corrigeable**,
identiques (paquets Debian, EXT-33).

**Bout en bout, vrais jetons Keycloak** (`benchmarks/e2e/e2e_extension_oidc.py`, conteneur jetable, TLS vérifié par l'autorité du
laboratoire) :
- mode `tokens` : **40 vérifications, 0 échec**. Couvre :
  - côté client Keycloak : PKCE `plain` et requête sans défi refusés, URI de redirection différente refusée, code à usage unique, CORS
    pour l'origine de l'extension, aucun jeton de rafraîchissement, audience `obfusk8-api` seule, 5 min, silencieux avec et sans session ;
  - côté serveur : jeton valide servi sur les trois routes. Refusés en 403 par oauth2-proxy : jeton de l'autre application, ID token de
    l'extension, signature ou charge utile altérées, émetteur imposteur, chaîne quelconque. Ni jeton ni cookie → 401 ;
  - routes web : le jeton seul est traité comme anonyme sur l'interface, le dépôt, l'aperçu et l'annulation ; un faux jeton sur une route
    web reçoit 401 (en-tête retiré) ;
  - identité : même `sub` pour le cookie et le jeton, et le parcours par cookie inchangé ;
- mode `expired` : jeton expiré (305 s) → 403 sur les deux routes ;
- mode `require-bearer` (`EXTENSION_REQUIRE_BEARER=true` le temps de l'essai) : cookie → 401 JSON, jeton → 200, interface web par
  cookie → 200 ;
- mode `ratelimit` : cookie et jeton du même agent alternés : `20 x 200, 5 x 429` ; l'autre agent → 200.

**Bout en bout de l'extension, vrai flux** (dépôt de l'extension, `tools/run.sh e2e-lab`) : vrai `launchWebAuthFlow` contre Keycloak,
analyse, pseudonymisation, restauration, renouvellement silencieux réel après 4 min, mot de passe redemandé après « Tout effacer »,
**aucun onglet obfusk8** ; audit : 15 lignes `text_analyze ok`, utilisateur de forme `sub`, 0 « eyJ ».

**Parcours par cookie et flux documents** : `e2e_availability.py quota` (13 vérifications, 0 échec : quotas, annulation, document d'un
autre, origine étrangère, session intacte) ; `e2e_document_flow.py` : 8 cas PDF/DOCX/CSV/image, `exposed=0`.

**Journaux et audit** : `check_tokens_not_logged.py` : 26 fragments de jeton cherchés dans 6 conteneurs et 2 journaux d'audit : 0.
`check_no_content_in_logs.py` : 633 valeurs, 0 trouvée. « eyJ » dans les journaux depuis le bout en bout de l'extension : `app` 0,
`oauth2-proxy` 0, Traefik 9, tous `client_data` de Keycloak, sans jeton (EXT-69).

**Ports** : `192.168.1.35:80`, `:443`, `:8080` seulement (`ss -ltn`).

## 5. Revue sécurité

**Vérifié (observé)**
- Audience stricte : jeton d'une autre application du même émetteur refusé (vrai jeton) ; jetons expirés, mal signés, d'un autre
  émetteur refusés ; un jeton sans identité est refusé par l'application (test unitaire, D-035).
- Jeton invalide sans repli sur le cookie (`bearer_token_login_fallback=false`) ; drapeau désactivé : jeton ignoré, comportement
  identique (essai jetable).
- Jeton de l'extension sans effet sur l'interface web (middleware `strip-authorization`).
- Aucun jeton dans les journaux ni l'audit ; audit au `sub`, sans contenu.
- Cookie : réglages inchangés ; protections d'origine du flux documents inchangées (bout en bout).
- Durcissement des conteneurs, seccomp, images tierces : inchangés ; aucune sortie réseau nouvelle (Keycloak du laboratoire déjà joint).
- Aucun secret dans les commits : valeurs des comptes de test et de `secrets/*.txt` cherchées (sans affichage) dans l'index avant le
  commit des scripts : 0.

**Points ouverts**
- **EXT-68** : `sub` par application chez Entra ID (identité unifiée et ré-identification) : à trancher avant un pilote sous Entra ID.
- **EXT-66** (🟡, décision humaine) ; **EXT-67** (réclamations journalisées par oauth2-proxy) ; **EXT-69** (`state` dans le journal de
  Traefik, laboratoire).
- La règle d'origine reste appliquée aux requêtes avec jeton (défense en profondeur) ; Firefox demandera de l'assouplir pour les seules
  requêtes avec jeton.
- Note DPO (`docs/audit-identite.md`) : procédure de ré-identification **proposée**, à valider.

**À différer au pentest**
- Rejeu d'un jeton volé dans sa fenêtre de 5 min (pas de liaison du jeton au client, DPoP non pris en charge par oauth2-proxy, supposé).
- Contournement du retrait d'`Authorization` (encodages, en-têtes en double à travers Traefik, `Authorization: Basic` portant un JWT).
- Confusion d'émetteurs en cas de plusieurs `extra_jwt_issuers`.
- Robustesse de la vérification face à des JWT malformés (fuzzing).

**Secrets du laboratoire (D-049)** : le courriel fictif du compte de test n° 1 est apparu dans une sortie de la session (masquage
incomplet d'une ligne du journal d'oauth2-proxy) ; aucun mot de passe affiché.

## 6. Observé et supposé

| Observé | Supposé |
|---|---|
| Audience stricte d'oauth2-proxy v7.15.5 (essai jetable et vrais jetons) | Même comportement chez Entra ID (vérification générique d'oauth2-proxy) |
| `X-Auth-Request-User` = `sub` pour le cookie et pour le jeton (Keycloak) | Chez Entra ID : deux `sub` différents (documentation Microsoft, EXT-68), non mesuré |
| Variables d'environnement d'oauth2-proxy lues comme prévu ; `true` sans émetteur → refus de démarrer | — |
| Vrai `launchWebAuthFlow` contre Keycloak, silencieux réel, `prompt=login` respecté (Chromium de Playwright) | Même comportement dans Chrome et Edge stables sous Windows |
| Middleware `headers` de Traefik 3.7 : en-tête vide retiré | — |

## 7. Procédure de test manuel (pour toi)

**Fusions et reconstruction**
1. Serveur : `git checkout feat/text-api && git merge --ff-only feat/option2-serveur`, puis push. Extension : `git checkout main &&
   git merge --ff-only feat/option2`, puis push (alias `github-ext`).
2. Reconstruire `app` : `docker compose -f docker-compose.yml -f docker-compose.build.yml build app`.
3. `.env` du laboratoire (déjà fait sur la VM, sauvegarde d'avant : `~/obfusk8-backups/env.avant-option2`) :
   `ENABLE_EXTENSION_OIDC=true`, `EXTENSION_OIDC_ISSUER=http://keycloak.lab.local:8080/realms/lab`, `EXTENSION_OIDC_AUDIENCE=obfusk8-api`,
   `EXTENSION_REQUIRE_BEARER=false`. Puis `docker compose up -d` ;
   `docker compose logs oauth2-proxy | grep "extra JWT issuer"` doit afficher l'émetteur.
4. Keycloak : `keycloak/lab/apply-clients.sh` (déjà fait sur la VM ; relancer ne change rien).

**Extension en mode `oidc`**
5. Dans le dépôt de l'extension : `tools/run.sh build-lab oidc` ; dans Chrome puis Edge : retirer l'ancienne extension du laboratoire,
   **Charger l'extension non empaquetée**, dossier `dist/lab` (même identifiant). Vérifier les permissions affichées : aucune
   permission d'hôte nouvelle ; « Connaître votre statut d'inactivité » peut apparaître selon la version (permission `idle`).
6. Sur le poste : `keycloak.lab.local` doit résoudre vers la VM (comme `obfusk8.lab.local`) ; autorité du laboratoire importée (déjà fait).

**Parcours, sans onglet obfusk8 ouvert** (fermer tous les onglets obfusk8 avant)
7. Ouvrir le panneau : « Non connecté », message « Connexion requise ». **Se connecter** : la fenêtre Keycloak s'ouvre, compte de test
   n° 1 → « Connecté … jusqu'à HH:MM ». Analyser, décocher, pseudonymiser, coller une réponse, restaurer.
8. Attendre plus de 4 min, puis analyser : aucune demande de connexion (renouvellement silencieux).
8 bis. **Échéance du travail** (D-058) : saisir un texte, attendre plus de 30 min **en utilisant le poste** (sans le verrouiller, sans
   rester 60 min inactif), rouvrir le panneau : le texte et la correspondance sont effacés, mais l'état reste « Connecté » et l'analyse
   suivante part **sans** demande de connexion ni saisie du mot de passe.
9. **Verrouiller le poste** (Win+L), déverrouiller : « Votre travail a été effacé au verrouillage du poste » dans le panneau et dans
   l'onglet s'il est ouvert ; **Se connecter** : Keycloak **redemande le mot de passe**.
9 bis. **Verrouillage sous Linux** : `Super + L`, ou `loginctl lock-session` dans un terminal, puis déverrouiller. Effacement immédiat
   attendu (message « au verrouillage du poste ») ; si l'environnement de bureau ne signale pas le verrouillage à Chrome (EXT-E-14 de
   l'extension), l'effacement n'arrive qu'au bout du délai d'inactivité, avec le message « après une période d'inactivité » : noter
   lequel des deux pour chaque système testé.
10. Inactivité : ne rien toucher pendant **60 min** (délai par défaut, D-058), ou stratégie gérée `idleMinutes: 1` pour un essai
    rapide → « … après une période d'inactivité » ; **Se connecter** redemande le mot de passe.
11. **Se déconnecter** : travail effacé, message. **Se déconnecter partout** : confirmation, puis l'interface web d'obfusk8 n'est plus
    connectée non plus.

**Erreurs à provoquer**
12. Fermer la fenêtre de connexion → « Connexion annulée ». Arrêter Keycloak (`docker compose stop keycloak`), puis se connecter →
    « Fournisseur d'identité injoignable » (relancer : `docker compose start keycloak`). Compte retiré du groupe
    `/SG-Anonymiseur-Utilisateurs` → « Accès refusé ».
13. `EXTENSION_REQUIRE_BEARER=true`, puis `docker compose up -d app` : l'extension `oidc` fonctionne ; une construction `session` reçoit
    « Connexion requise » (401). Remettre `false` ensuite (laboratoire).

**Retour arrière** : image d'avant la phase `obfusk8-local:avant-option2-app` ; `.env` d'avant :
`~/obfusk8-backups/env.avant-option2` ; clients Keycloak : à supprimer dans la console (royaume `lab`) si besoin.

## 8. Décisions qui te reviennent

1. **`prompt=login` à chaque connexion interactive** (extension D-014 point 8, au-delà de D-057) : sans lui, une session Keycloak encore
   ouverte rendait un code au premier clic de l'agent suivant. Coût : mot de passe à chaque reconnexion. À valider.
2. **EXT-68 (Entra ID)** : une seule inscription d'application pour oauth2-proxy et l'API (à tester chez l'établissement), ou deux
   pseudonymes acceptés (flux documents et API texte séparés en production).
3. **Note DPO** (`docs/audit-identite.md`) : procédure de ré-identification et conservation des journaux d'oauth2-proxy (EXT-67).
4. **Laboratoire** : garder `ENABLE_EXTENSION_OIDC=true` (actuel) et le client témoin `obfusk8-test-autre-application`, ou revenir en
   arrière.
5. **Commits de l'extension** : les commits intermédiaires ne se construisent pas un par un (modules interdépendants) ; seul l'état final
   est vérifié.
6. **Diffs de `CLAUDE.md`** ci-dessous (serveur) et dans le compte rendu de l'extension.

## 9. Proposition de mise à jour de `CLAUDE.md` (non appliquée)

```diff
-Constatées dans le dépôt le 2 octobre 2026, … et le 7 octobre 2026 (phases « disponibilité », « Python » et 3).
+Constatées dans le dépôt le 2 octobre 2026, … le 7 octobre 2026 (phases « disponibilité », « Python » et 3) et le 12 octobre 2026
+(phase « option 2 »).
@@ Chaîne de requête
+**Identité** (D-057) : le `sub` (`X-Auth-Request-User`) dans tous les flux (audit, quotas, propriété des documents, débit) ; le
+courriel n'est plus une identité. `_document_user` et `_request_user` le lisent ; identité vide refusée (403).
@@ Routage Traefik par labels
+**Jeton de l'extension** (option 2, D-057) : `ENABLE_EXTENSION_OIDC` (variables `OAUTH2_PROXY_SKIP_JWT_BEARER_TOKENS` et
+`OAUTH2_PROXY_EXTRA_JWT_ISSUERS` dans `docker-compose.yml`, jamais dans `oauth2-proxy.cfg`), audience exacte
+`EXTENSION_OIDC_AUDIENCE`, jamais `oidc_extra_audiences` ; `EXTENSION_REQUIRE_BEARER=true` en production. Middleware
+`strip-authorization` avant `oidc-auth` sur tout routeur web : toute nouvelle route web le reprend.
@@ Keycloak = laboratoire uniquement
+Clients de l'extension : `keycloak/lab/apply-clients.sh` (fichiers versionnés), jamais à la main ; ne touche pas `anonymiseur-app`.
+Bancs : `benchmarks/e2e/e2e_extension_oidc.py` (vrais jetons), `check_tokens_not_logged.py`.
```

## 10. Compte rendu complémentaire (décisions humaines du 2026-10-12, D-058)

| Point | Fait | Où |
|---|---|---|
| `prompt=login` validé | Consigné | Serveur D-058, extension D-015 |
| Inactivité 60 min par défaut, verrouillage immédiat | `DEFAULT_IDLE_MINUTES = 60`, construction par défaut, schéma géré, documentation FR/EN, tests | Extension `2882b90`, `664e754` |
| Échéance du travail sans effet sur la connexion | **Vérifié dans le code, aucune correction nécessaire** (`SessionStore.clear()` ne retire que `mapping`, `work`, `deadline` ; la clé `auth` reste). Tests dédiés : unitaire (renouvellement silencieux `prompt=none` après l'échéance) et navigateur (vue rouverte après l'échéance : travail effacé, toujours connectée, aucune demande d'autorisation) ; verrouillage, inactivité, « Tout effacer », déconnexion : reconnexion interactive avec `prompt=login` (tests existants) | Extension `6f1d127` |
| Portées supplémentaires (Entra ID) | Ajout de `oidc.scopes` dans la configuration de l'extension (sans elle, Entra ID ne délivre pas de jeton pour l'API ; écart à l'exigence « sans changement de code » trouvé en documentant l'inscription unique) | Extension `87ce329`, D-015 point 6 |
| Identité chez Entra ID | Cible (inscription unique) et repli documentés | `extension-oidc.md` §6 FR/EN, D-045 point 24 |
| Journal d'oauth2-proxy | Proposition de configuration et de test, **non appliquée** ; le `sub` ne peut pas figurer dans ce journal (aucune variable de gabarit) | EXT-67, D-045 point 25 |
| D-045 | Points 22 à 25 | `docs/DECISIONS.md` |
| Laboratoire | `ENABLE_EXTENSION_OIDC=true` et client témoin gardés | D-058 point 4 |
| EXT-66 | Risque accepté (🟡) confirmé | D-058 point 6 |

**Sorties utiles** (extension, branche `feat/option2`) :
```
tools/run.sh check    → ℹ tests 134  ℹ pass 134  ℹ fail 0   (types, Biome, interdits, construction : verts)
tools/run.sh browser  → ℹ tests 55   ℹ pass 55   ℹ fail 0
  ✔ work expiry (30 min): work and mapping erased, still signed in, no sign-in asked (D-015)
```
Serveur : documentation seulement dans ce complément (aucun code modifié ; suite inchangée, 635 tests verts au commit précédent).

**Décisions qui te reviennent** (nouvelles) :
1. `oidc.scopes` (extension D-015 point 6) : à valider.
2. EXT-67 : couper ou non le journal standard d'oauth2-proxy (il garde les réclamations d'un jeton refusé) ; accepter que le `sub`
   n'apparaisse pas dans le journal d'authentification ; appliquer la configuration proposée (fichier `oauth2-proxy.cfg`, accord requis).

## 11. Complément (D-059)

- `oidc.scopes` : sources de configuration vérifiées et figées par test (construction et stratégie gérée seulement) ; exemple Entra ID
  (extension `2fc76c1`).
- EXT-67 préparé, non appliqué : `git apply docs/patches/EXT-67-oauth2-proxy-logging.patch`, puis `docker compose up -d oauth2-proxy`, puis
  `check_no_identifier_in_logs.py` (attendu : 0 partout ; avant, observé : 6 courriels et 1 nom d'utilisateur dans oauth2-proxy). EXT-70 en
  risque accepté.
- `CLAUDE.md` des deux dépôts : diffs validés appliqués.
