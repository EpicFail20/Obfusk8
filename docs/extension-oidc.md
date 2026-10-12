# Option 2 de D-010 : jeton OIDC propre à l'extension

> English version: [`extension-oidc.en.md`](./extension-oidc.en.md).
> Statut (phase « option 2 », 2026-10-12, D-057) : **serveur fait et testé au laboratoire** (Keycloak), désactivé par défaut
> (`ENABLE_EXTENSION_OIDC=false`). Côté extension : stratégie `oidc` du dépôt `obfusk8-extension` (`docs/auth-oidc.md`).
> **Obligatoire avant la production** (D-045 §C point 18) ; reste à faire chez le fournisseur d'identité de l'établissement (§6).

## 1. Principe et flux

L'extension obtient **son propre** jeton d'accès auprès du fournisseur d'identité, puis l'envoie en `Authorization: Bearer <jeton>`,
**sans** cookie. Elle ne dépend plus d'un onglet obfusk8 connecté.

1. L'extension génère un vérificateur PKCE (32 octets aléatoires), son défi S256, un `state` et un `nonce`, puis ouvre
   `chrome.identity.launchWebAuthFlow` sur le point d'autorisation du fournisseur (client public, sans secret).
2. Le fournisseur authentifie l'agent et redirige vers `https://<identifiant de l'extension>.chromiumapp.org/` ; l'extension vérifie
   le `state`, échange le code (avec le vérificateur) au point de jeton par CORS (« Web origins » du client), vérifie le `nonce` de
   l'ID token et garde le jeton d'accès en mémoire (`chrome.storage.session`).
3. Chaque requête `/api/v1/` porte `Authorization: Bearer <jeton d'accès>`. Traefik (routeur `app-text`) appelle oauth2-proxy
   (`oidc-auth`), qui **vérifie le jeton** : signature (clés publiées par l'émetteur), émetteur, **audience**, expiration,
   `email_verified` (s'il est présent), groupe autorisé (`allowed_groups`).
4. oauth2-proxy répond 202 avec `X-Auth-Request-User` = `sub` du jeton ; Traefik remplace toute valeur fournie par le client, applique la
   limitation de débit par utilisateur (clé `X-Auth-Request-User`), le plafond de corps et le secret de passerelle.
5. L'application contrôle l'identité non vide (D-035), le jeton porteur si `EXTENSION_REQUIRE_BEARER=true` (§4), la règle d'origine
   (§5), puis traite la requête. L'audit porte le `sub`, jamais le jeton.

## 2. Configuration du serveur

Variables de `.env`, déclarées dans `docker-compose.yml` et `env.*.example` :

| Variable | Défaut | Rôle |
|---|---|---|
| `ENABLE_EXTENSION_OIDC` | `false` | `true` : oauth2-proxy accepte les jetons porteurs (`OAUTH2_PROXY_SKIP_JWT_BEARER_TOKENS`). `false` : en-tête `Authorization` ignoré, sessions seulement (observé : un jeton valide reçoit 401, comme avant la phase). |
| `EXTENSION_OIDC_ISSUER` | vide | Émetteur exact (réclamation `iss`). HTTPS obligatoire hors laboratoire (D-045 point 21). `true` sans émetteur : oauth2-proxy ne démarre pas (observé). |
| `EXTENSION_OIDC_AUDIENCE` | vide | Audience **exacte** des jetons émis pour obfusk8 (`OAUTH2_PROXY_EXTRA_JWT_ISSUERS=<émetteur>=<audience>`). |
| `EXTENSION_REQUIRE_BEARER` | `false` | `true` (production) : `/api/v1/` refuse toute requête sans jeton porteur (401 `bearer_required`). Exige `ENABLE_EXTENSION_OIDC=true`, sinon l'application ne démarre pas. |

Toujours en place, quel que soit le drapeau : `OAUTH2_PROXY_BEARER_TOKEN_LOGIN_FALLBACK=false` (un jeton invalide reçoit 403 au lieu d'un
repli sur le cookie) et le middleware Traefik `strip-authorization`, qui retire `Authorization` **avant** `oidc-auth` sur les routeurs web
(`app`, `app-upload`, `app-cancel`, `app-preview`) : un jeton de l'extension n'ouvre **que** `/api/v1/`, jamais le flux documents.

**Audience, point critique.** oauth2-proxy (v7.15.5, code source lu) accepte un jeton dont l'audience est l'identifiant de son propre client
**ou** celle d'un couple `extra_jwt_issuers`. Ne jamais ajouter d'audience large (`account`, audience commune à d'autres applications) ni
`oidc_extra_audiences` : tout jeton émis pour une autre application du même émetteur serait accepté. Le premier cas (jeton émis pour le
client d'oauth2-proxy) est un risque accepté (EXT-66) : seul oauth2-proxy, qui détient le secret de ce client, peut en obtenir un.

## 3. Ce qui a été prouvé

- **Essai jetable** (étape A ; émetteur OIDC minimal et oauth2-proxy v7.15.5 sur un réseau interne) : audience `obfusk8-api` → 202 ;
  autre audience, `account`, audience absente, expiré, signature fausse, `alg: none`, autre émetteur, `email_verified=false` → 403 ;
  sans groupe autorisé → 401 ; jeton sans `sub` ni courriel → 202 **sans identité** : refusé par l'application (D-035, 403).
- **Bout en bout au laboratoire**, vrais jetons Keycloak (`benchmarks/e2e/e2e_extension_oidc.py`, 40 vérifications, plus les modes
  `expired`, `require-bearer` et `ratelimit`) :
  - client : PKCE `plain` et requête sans défi refusés, URI de redirection différente refusée, code à usage unique, aucun jeton de
    rafraîchissement, CORS accordé à l'origine de l'extension, renouvellement silencieux (`prompt=none`) avec la session du fournisseur,
    `login_required` sans elle ;
  - serveur : jeton valide servi ; jeton d'une **autre application du même émetteur** (audience `autre-application`), ID token de
    l'extension, signature ou charge utile altérées, émetteur imposteur, chaîne quelconque → 403 ; jeton expiré → 403 ; ni jeton ni
    cookie → 401 ; jeton seul sur l'interface web, le dépôt, l'aperçu ou l'annulation : traité comme anonyme ;
  - identité : même `sub` pour le cookie et le jeton (Keycloak), un seul seau de limitation de débit (429 après la rafale), audit au `sub` ;
  - `EXTENSION_REQUIRE_BEARER=true` : cookie refusé sur l'API (401), jeton servi, interface web par cookie inchangée ;
  - aucun fragment de jeton dans les journaux des six conteneurs ni dans les deux journaux d'audit
    (`benchmarks/e2e/check_tokens_not_logged.py`).

## 4. `EXTENSION_REQUIRE_BEARER`

Avec le cookie, toute extension du navigateur qui a une permission d'hôte sur le serveur pourrait appeler l'API au nom de l'agent (D-054).
En production, l'API n'accepte donc que le jeton de l'extension : une requête qui atteint l'application avec `Authorization: Bearer …` a
forcément été authentifiée par ce jeton, puisqu'oauth2-proxy refuse tout en-tête invalide sans repli sur le cookie. Plusieurs en-têtes
`Authorization` : refus (oauth2-proxy n'a vérifié que le premier).

## 5. Règle d'origine (D-054 point 6) : conservée

Elle reste appliquée telle quelle aux requêtes avec jeton : POST avec l'`Origin` d'une extension autorisée, `GET /version` sans `Origin`
accepté. Chrome envoie toujours cette origine depuis l'extension : aucun coût, et une défense en profondeur. Avec
`EXTENSION_REQUIRE_BEARER=true`, la vraie protection contre les autres extensions devient l'absence de cookie accepté sur l'API.
**Firefox** (plus tard) : l'origine `moz-extension://` change à chaque installation ; il faudra accepter cette origine pour les seules
requêtes avec jeton.

## 6. Fournisseur d'identité

### Keycloak du laboratoire (reproductible)

```bash
keycloak/lab/apply-clients.sh   # pile du laboratoire démarrée (profil lab)
```

Le script (re)crée exactement, dans le royaume `lab`, les clients décrits par les fichiers versionnés de `keycloak/lab/` :
- `obfusk8-extension` : public, flux standard seul, PKCE S256 exigé, URI de redirection exacte
  `https://glaimpfdmfkidcgalcblojmkomplcgpa.chromiumapp.org/`, « Web origins » `chrome-extension://glaimpfdmfkidcgalcblojmkomplcgpa`,
  audience `obfusk8-api` (jeton d'accès seulement), portées `basic`, `email`, `groups` (pas `roles`, qui ajouterait `aud: account`),
  jeton d'accès de 5 min, **aucun jeton de rafraîchissement** (D-057 point 5) ;
- `obfusk8-test-autre-application` : **témoin de test**, audience `autre-application`, pour prouver qu'un jeton d'une autre application
  est refusé. N'est accepté par rien.

Le client d'oauth2-proxy (`anonymiseur-app`) n'est jamais modifié. Le mot de passe administrateur est lu dans le conteneur, jamais affiché.
`.env` du laboratoire : `ENABLE_EXTENSION_OIDC=true`, `EXTENSION_OIDC_ISSUER=http://keycloak.lab.local:8080/realms/lab`,
`EXTENSION_OIDC_AUDIENCE=obfusk8-api`, `EXTENSION_REQUIRE_BEARER=false` (l'extension en mode `session` reste utilisable).

### Entra ID (à configurer chez l'établissement, non fait)

- **Application API** (« obfusk8-api ») : exposer une portée (par exemple `access_as_user`), jetons d'accès en version 2
  (`requestedAccessTokenVersion: 2`). `EXTENSION_OIDC_ISSUER=https://login.microsoftonline.com/<tenant>/v2.0` ;
  `EXTENSION_OIDC_AUDIENCE` = **identifiant (GUID) de l'application API** (en v2, `aud` est toujours l'identifiant client de l'API,
  documentation Microsoft).
- **Application de l'extension** : cliente publique ; plateforme « Single-page application » pour l'échange du code par CORS depuis la
  page de l'extension (supposé, à valider) ; une URI de redirection `https://<identifiant>.chromiumapp.org/` par identifiant publié
  (Chrome Web Store, Edge Add-ons) ; permission déléguée sur la portée de l'API, consentement de l'administrateur.
- **Réclamations** : groupes en **GUID** (`allowed_groups` à adapter) ; `email` absent par défaut des jetons d'accès (le `sub` le remplace
  alors dans `X-Auth-Request-Email`, sans effet sur l'identité, qui est le `sub`). `email_verified` absent : considéré vrai par
  oauth2-proxy (code source).
- **Identité, à trancher avant le pilote (EXT-68)** : chez Entra ID, le `sub` est **propre à chaque application** ; le cookie (client
  d'oauth2-proxy) et le jeton (application API) peuvent porter deux `sub` différents pour un même agent. Piste à tester : une seule
  inscription d'application pour oauth2-proxy et l'API.
- **Réseau** : oauth2-proxy doit joindre les clés publiques du fournisseur (accès sortant de D-046 / D-045 §C point 4).

## 7. Identité et audit

Voir [`audit-identite.md`](./audit-identite.md) : l'identité de tous les flux est le `sub` (D-057 point 9), l'audit est donc
pseudonymisé ; procédure de ré-identification pour la DPO.
