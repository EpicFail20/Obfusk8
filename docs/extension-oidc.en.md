# Option 2 of D-010: the extension's own OIDC token

> Version française : [`extension-oidc.md`](./extension-oidc.md).
> Status ("option 2" phase, 2026-10-12, D-057): **server side done and tested in the lab** (Keycloak), disabled by default
> (`ENABLE_EXTENSION_OIDC=false`). Extension side: the `oidc` strategy of the `obfusk8-extension` repository (`docs/auth-oidc.en.md`).
> **Mandatory before production** (D-045 §C point 18); still to be done at the establishment's identity provider (§6).

## 1. Principle and flow

The extension obtains **its own** access token from the identity provider, then sends it as `Authorization: Bearer <token>`,
**without** cookie. It no longer depends on a signed-in obfusk8 tab.

1. The extension generates a PKCE verifier (32 random bytes), its S256 challenge, a `state` and a `nonce`, then opens
   `chrome.identity.launchWebAuthFlow` on the provider's authorization endpoint (public client, no secret).
2. The provider authenticates the agent and redirects to `https://<extension identifier>.chromiumapp.org/`; the extension checks the
   `state`, redeems the code (with the verifier) at the token endpoint through CORS (the client's "Web origins"), checks the ID token's
   `nonce` and keeps the access token in memory (`chrome.storage.session`).
3. Every `/api/v1/` request carries `Authorization: Bearer <access token>`. Traefik (router `app-text`) calls oauth2-proxy
   (`oidc-auth`), which **verifies the token**: signature (keys published by the issuer), issuer, **audience**, expiry, `email_verified`
   (when present), allowed group (`allowed_groups`).
4. oauth2-proxy answers 202 with `X-Auth-Request-User` = the token's `sub`; Traefik replaces any client-supplied value, applies the
   per-user rate limit (key `X-Auth-Request-User`), the body cap and the gateway secret.
5. The application checks a non-empty identity (D-035), the bearer token when `EXTENSION_REQUIRE_BEARER=true` (§4), the Origin rule
   (§5), then serves the request. The audit records the `sub`, never the token.

## 2. Server configuration

`.env` variables, declared in `docker-compose.yml` and `env.*.example`:

| Variable | Default | Role |
|---|---|---|
| `ENABLE_EXTENSION_OIDC` | `false` | `true`: oauth2-proxy accepts bearer tokens (`OAUTH2_PROXY_SKIP_JWT_BEARER_TOKENS`). `false`: `Authorization` ignored, sessions only (observed: a valid token gets 401, as before the phase). |
| `EXTENSION_OIDC_ISSUER` | empty | Exact issuer (`iss` claim). HTTPS mandatory outside the lab (D-045 point 21). `true` without an issuer: oauth2-proxy does not start (observed). |
| `EXTENSION_OIDC_AUDIENCE` | empty | **Exact** audience of the tokens issued for obfusk8 (`OAUTH2_PROXY_EXTRA_JWT_ISSUERS=<issuer>=<audience>`). |
| `EXTENSION_REQUIRE_BEARER` | `false` | `true` (production): `/api/v1/` refuses any request without a bearer token (401 `bearer_required`). Requires `ENABLE_EXTENSION_OIDC=true`, otherwise the application does not start. |

Always in place, whatever the switch: `OAUTH2_PROXY_BEARER_TOKEN_LOGIN_FALLBACK=false` (an invalid token gets 403 instead of falling back
to the cookie) and the Traefik middleware `strip-authorization`, which drops `Authorization` **before** `oidc-auth` on the web routers
(`app`, `app-upload`, `app-cancel`, `app-preview`): an extension token opens `/api/v1/` **only**, never the document flow.

**Audience, the critical point.** oauth2-proxy (v7.15.5, source code read) accepts a token whose audience is its own client id **or** the
audience of an `extra_jwt_issuers` pair. Never add a broad audience (`account`, one shared with other applications) nor
`oidc_extra_audiences`: any token issued to another application of the same issuer would be accepted. The first case (a token issued to
oauth2-proxy's client) is an accepted risk (EXT-66): only oauth2-proxy, which holds that client's secret, can obtain one.

## 3. What was proven

- **Throwaway trial** (step A; minimal OIDC issuer and oauth2-proxy v7.15.5 on an internal network): audience `obfusk8-api` → 202;
  another audience, `account`, no audience, expired, wrong signature, `alg: none`, another issuer, `email_verified=false` → 403;
  no allowed group → 401; a token with neither `sub` nor e-mail → 202 **without identity**: refused by the application (D-035, 403).
- **End to end in the lab**, real Keycloak tokens (`benchmarks/e2e/e2e_extension_oidc.py`, 40 checks, plus the `expired`,
  `require-bearer` and `ratelimit` modes):
  - client: PKCE `plain` and a request without challenge refused, another redirect URI refused, single-use code, no refresh token,
    CORS granted to the extension's origin, silent renewal (`prompt=none`) with the provider's session, `login_required` without it;
  - server: valid token served; token of **another application of the same issuer** (audience `autre-application`), the extension's ID
    token, tampered signature or payload, impostor issuer, arbitrary string → 403; expired token → 403; neither token nor cookie → 401;
    token alone on the web interface, upload, preview or cancellation: treated as anonymous;
  - identity: same `sub` for the cookie and the token (Keycloak), one rate-limit bucket (429 after the burst), audit keyed on the `sub`;
  - `EXTENSION_REQUIRE_BEARER=true`: cookie refused on the API (401), token served, web interface by cookie unchanged;
  - no token fragment in the logs of the six containers nor in the two audit logs (`benchmarks/e2e/check_tokens_not_logged.py`).

## 4. `EXTENSION_REQUIRE_BEARER`

With the cookie, any extension of the browser holding a host permission on the server could call the API as the agent (D-054). In
production the API therefore accepts the extension's token only: a request that reaches the application with `Authorization: Bearer …`
was necessarily authenticated by that token, since oauth2-proxy refuses any invalid header without falling back to the cookie. Several
`Authorization` headers: refused (oauth2-proxy verified the first one only).

## 5. Origin rule (D-054 point 6): kept

It still applies as is to token requests: POST with the `Origin` of an allowed extension, `GET /version` without `Origin` accepted.
Chrome always sends that origin from the extension: no cost, defence in depth. With `EXTENSION_REQUIRE_BEARER=true`, the real protection
against other extensions becomes that no cookie is accepted on the API. **Firefox** (later): the `moz-extension://` origin changes with
each installation; it will have to be accepted for token requests only.

## 6. Identity provider

### Lab Keycloak (reproducible)

```bash
keycloak/lab/apply-clients.sh   # lab stack running (lab profile)
```

The script (re)creates exactly, in the `lab` realm, the clients described by the versioned files of `keycloak/lab/`:
- `obfusk8-extension`: public, standard flow only, PKCE S256 required, exact redirect URI
  `https://glaimpfdmfkidcgalcblojmkomplcgpa.chromiumapp.org/`, "Web origins" `chrome-extension://glaimpfdmfkidcgalcblojmkomplcgpa`,
  audience `obfusk8-api` (access token only), scopes `basic`, `email`, `groups` (not `roles`, which would add `aud: account`),
  5-minute access token, **no refresh token** (D-057 point 5);
- `obfusk8-test-autre-application`: **test fixture**, audience `autre-application`, to prove that another application's token is
  refused. Accepted by nothing.

oauth2-proxy's client (`anonymiseur-app`) is never modified. The admin password is read inside the container, never printed.
Lab `.env`: `ENABLE_EXTENSION_OIDC=true`, `EXTENSION_OIDC_ISSUER=http://keycloak.lab.local:8080/realms/lab`,
`EXTENSION_OIDC_AUDIENCE=obfusk8-api`, `EXTENSION_REQUIRE_BEARER=false` (the extension in `session` mode remains usable).

### Entra ID (to configure at the establishment, not done)

- **API application** ("obfusk8-api"): expose a scope (e.g. `access_as_user`), version 2 access tokens
  (`requestedAccessTokenVersion: 2`). `EXTENSION_OIDC_ISSUER=https://login.microsoftonline.com/<tenant>/v2.0`;
  `EXTENSION_OIDC_AUDIENCE` = **the API application's client id (GUID)** (in v2, `aud` is always the API's client id, Microsoft
  documentation).
- **Extension application**: public client; "Single-page application" platform to redeem the code through CORS from the extension's page
  (assumed, to be validated); one redirect URI `https://<identifier>.chromiumapp.org/` per published identifier (Chrome Web Store, Edge
  Add-ons); delegated permission on the API's scope, admin consent.
- **Claims**: groups as **GUIDs** (adapt `allowed_groups`); `email` absent by default from access tokens (the `sub` then replaces it in
  `X-Auth-Request-Email`, with no effect on the identity, which is the `sub`). `email_verified` absent: treated as true by oauth2-proxy
  (source code).
- **Identity, to settle before the pilot (EXT-68)**: in Entra ID the `sub` is **specific to each application**; the cookie
  (oauth2-proxy's client) and the token (API application) may carry two different `sub` values for one agent. Lead to test: a single app
  registration for oauth2-proxy and the API.
- **Network**: oauth2-proxy must reach the provider's public keys (outbound access of D-046 / D-045 §C point 4).

## 7. Identity and audit

See [`audit-identity.md`](./audit-identity.md): the identity of every flow is the `sub` (D-057 point 9), so the audit is pseudonymised;
re-identification procedure for the DPO.
