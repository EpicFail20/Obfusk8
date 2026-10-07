# Option 2 de D-010 : jetons OIDC de l'extension — configuration prévue, **non activée**

> English version: [`extension-oidc.en.md`](./extension-oidc.en.md).
> Statut : **obligatoire avant la production** (D-045 §C point 18, D-054). Rien de ce document n'est appliqué : `oauth2-proxy.cfg`
> est inchangé et le prototype utilise le cookie de session (option 3, [`api-extension.md`](./api-extension.md) §8). Côté extension, la
> stratégie `oidc` existe et **échoue fermée** (dépôt `obfusk8-extension`, `src/lib/auth.ts`, `docs/auth-oidc.md`).

## 1. Principe

L'extension obtient **son propre** jeton d'accès auprès du fournisseur d'identité de l'établissement (code d'autorisation + PKCE S256,
`chrome.identity.launchWebAuthFlow`), puis l'envoie en `Authorization: Bearer <jeton>`, **sans** cookie (`credentials: "omit"`).
oauth2-proxy vérifie le jeton (signature, émetteur, **audience**, expiration) au lieu de la session ; la chaîne Traefik reste la même
(`oidc-auth`, limitation de débit, plafond de corps, secret de passerelle).

## 2. Fournisseur d'identité

- Client OIDC **public** dédié à l'extension (par exemple `obfusk8-extension`), **sans secret**, PKCE S256 exigé.
- URI de redirection : `https://<identifiant de l'extension>.chromiumapp.org/` (valeur de `chrome.identity.getRedirectURL()`), une par
  identifiant publié (Chrome Web Store, Edge Add-ons : deux identifiants distincts).
- Audience dédiée, par exemple `obfusk8-api` (mappeur d'audience chez Keycloak ; « Application ID URI » chez Entra ID). Le jeton doit
  porter `aud` = cette valeur.
- Durée de vie courte du jeton d'accès (5 à 15 min) ; jeton de rafraîchissement à durée limitée ou absent selon la politique.
- Le jeton doit porter `email` et `email_verified=true` : oauth2-proxy refuse sinon (EXT-43, constaté avec les sessions).

## 3. oauth2-proxy (options vérifiées dans la documentation officielle 7.15.x, 2026-10-07)

```ini
# PRÉVU, NON ACTIVÉ.
skip_jwt_bearer_tokens = true
# Émetteur=audience du client de l'extension. L'émetteur doit publier
# .well-known/openid-configuration ou .well-known/jwks.json.
extra_jwt_issuers = [ "https://<fournisseur>/realms/<royaume>=obfusk8-api" ]
# Jeton invalide : 403 au lieu d'une redirection vers la connexion.
bearer_token_login_fallback = false
```

- **Audience, point critique** : `skip_jwt_bearer_tokens` accepte un jeton dont `aud` est l'identifiant du client d'oauth2-proxy **ou**
  l'audience d'un couple de `extra_jwt_issuers` (documentation officielle). Ne jamais ajouter d'audience large (`account`, audience commune
  à d'autres applications) : tout jeton émis pour une autre application du même émetteur serait accepté. N'utiliser **ni**
  `oidc_extra_audiences` ni un joker.
- **En-têtes d'identité** : la documentation ne dit pas si un jeton Bearer vérifié renseigne `X-Auth-Request-User` et
  `X-Auth-Request-Email`. L'application refuse toute requête sans ces deux en-têtes (403, D-035) : **à vérifier en premier** à
  l'implémentation (test de bout en bout avec un jeton réel du laboratoire).
- Les routes web gardent la session ; seul le routeur `app-text` (`/api/v1/`) a besoin des jetons. Si oauth2-proxy ne permet pas de les
  limiter à ce chemin, consigner le constat : un jeton de l'extension donnerait aussi accès à l'interface web (même périmètre fonctionnel,
  à évaluer).

## 4. Règle d'origine (D-054 point 6) : à réexaminer

La liste blanche `EXTENSION_ALLOWED_ORIGINS` protège la **session par cookie** : sans elle, toute extension qui a la permission d'hôte
utiliserait le cookie de l'utilisateur. Avec des jetons, une autre extension n'a pas le jeton (il reste dans `chrome.storage.session`
de l'extension obfusk8). La règle devient une défense en profondeur ; l'extension continuera d'envoyer son `Origin`. À décider à
l'implémentation : la conserver telle quelle, ou n'exiger l'origine que pour les requêtes authentifiées par cookie.

## 5. Tests à prévoir à l'implémentation

Jeton valide accepté ; jeton d'une autre audience, d'un autre émetteur, expiré, à signature fausse, sans `email_verified` : refusés ;
en-têtes d'identité présents ; aucun cookie envoyé par l'extension ; révocation et déconnexion ; limitation de débit par utilisateur
inchangée (`X-Auth-Request-User`).
