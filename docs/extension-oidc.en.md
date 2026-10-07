# Option 2 of D-010: OIDC tokens of the extension — configuration planned, **not enabled**

> Version française : [`extension-oidc.md`](./extension-oidc.md).
> Status: **mandatory before production** (D-045 §C point 18, D-054). Nothing in this document is applied: `oauth2-proxy.cfg` is
> unchanged and the prototype uses the session cookie (option 3, [`api-extension.en.md`](./api-extension.en.md) §8). On the extension
> side, the `oidc` strategy exists and **fails closed** (`obfusk8-extension` repository, `src/lib/auth.ts`, `docs/auth-oidc.en.md`).

## 1. Principle

The extension obtains **its own** access token from the establishment's identity provider (authorization code + PKCE S256,
`chrome.identity.launchWebAuthFlow`), then sends it as `Authorization: Bearer <token>`, **without** cookie (`credentials: "omit"`).
oauth2-proxy verifies the token (signature, issuer, **audience**, expiry) instead of the session; the Traefik chain stays the same
(`oidc-auth`, rate limit, body cap, gateway secret).

## 2. Identity provider

- A **public** OIDC client dedicated to the extension (e.g. `obfusk8-extension`), **no secret**, PKCE S256 required.
- Redirect URI: `https://<extension identifier>.chromiumapp.org/` (value of `chrome.identity.getRedirectURL()`), one per published
  identifier (Chrome Web Store and Edge Add-ons give two distinct identifiers).
- A dedicated audience, e.g. `obfusk8-api` (audience mapper in Keycloak; "Application ID URI" in Entra ID). The token must carry
  `aud` = that value.
- Short access-token lifetime (5 to 15 min); refresh token limited or absent, per policy.
- The token must carry `email` and `email_verified=true`: oauth2-proxy refuses it otherwise (EXT-43, observed with sessions).

## 3. oauth2-proxy (options checked in the official 7.15.x documentation, 2026-10-07)

```ini
# PLANNED, NOT ENABLED.
skip_jwt_bearer_tokens = true
# Issuer=audience of the extension's client. The issuer must publish
# .well-known/openid-configuration or .well-known/jwks.json.
extra_jwt_issuers = [ "https://<provider>/realms/<realm>=obfusk8-api" ]
# Invalid token: 403 instead of a redirect to the sign-in page.
bearer_token_login_fallback = false
```

- **Audience, the critical point**: `skip_jwt_bearer_tokens` accepts a token whose `aud` is oauth2-proxy's client id **or** the audience
  of an `extra_jwt_issuers` pair (official documentation). Never add a broad audience (`account`, an audience shared with other
  applications): any token issued for another application of the same issuer would be accepted. Use **neither** `oidc_extra_audiences`
  nor a wildcard.
- **Identity headers**: the documentation does not say whether a verified Bearer token sets `X-Auth-Request-User` and
  `X-Auth-Request-Email`. The application refuses any request without both headers (403, D-035): **check this first** when implementing
  (end-to-end test with a real lab token).
- The web routes keep the session; only the `app-text` router (`/api/v1/`) needs tokens. If oauth2-proxy cannot restrict them to that
  path, record the finding: an extension token would also open the web interface (same functional scope, to be assessed).

## 4. Origin rule (D-054 point 6): to be reviewed

The `EXTENSION_ALLOWED_ORIGINS` allow-list protects the **cookie session**: without it, any extension with the host permission would
use the user's cookie. With tokens, another extension does not have the token (it stays in the obfusk8 extension's
`chrome.storage.session`). The rule becomes defence in depth; the extension will keep sending its `Origin`. To decide at implementation
time: keep it as is, or require the origin only for cookie-authenticated requests.

## 5. Tests to plan at implementation time

Valid token accepted; token of another audience, another issuer, expired, wrong signature, without `email_verified`: refused; identity
headers present; no cookie sent by the extension; revocation and sign-out; per-user rate limit unchanged (`X-Auth-Request-User`).
