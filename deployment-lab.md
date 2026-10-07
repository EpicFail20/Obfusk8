# Anonymizer - lab deployment on a Docker VM (Proxmox)

> **Lab only.** This guide installs a **lab** Keycloak: development mode (`start-dev`), plain HTTP, development H2 database, bootstrap
> admin account. This Keycloak must **never** serve a pilot or production (D-046): a real deployment uses the establishment's identity
> provider, see [Real deployment](#real-deployment-the-establishments-identity-provider). Keycloak only starts with the Compose `lab` profile.

This guide assumes a **completely fresh** install, on a VM that has never run this project before. Follow the steps in order. If you get stuck at any point, check the [Troubleshooting](#troubleshooting) table at the end — every error hit in the lab is listed there with its exact cause.

## 0. Proxmox VM prerequisites

Check the VM's CPU type before starting anything:

1. VM → **Hardware** → **Processor** → **Edit**.
2. Type: **`host`** (or an explicit v2-compatible type, `x86-64-v2-AES`, if you plan to migrate to a different host later).
3. Restart the VM.

```bash
grep -o 'sse4_2' /proc/cpuinfo | head -1
```

If `sse4_2` shows up, you're good.

## 1. Clone the repository

```bash
ssh debian@<vm-ip>
git clone https://github.com/EpicFail20/obfusk8.git
cd obfusk8
```

## 2. Add both domains to `/etc/hosts` (client machine, not the VM)

```
<vm-ip>  anonymiseur.lab.local
<vm-ip>  keycloak.lab.local
```

Both entries are needed. This goes in the hosts file of **your local machine** (the one with the browser), not the VM's.

## 3. Generate the secrets

```bash
chmod +x generate-secrets.sh
./generate-secrets.sh
```

Run this **before** the very first `docker compose up`. It asks for two manual values:

- **`Keycloak admin password`**: choose it yourself.
- **`OAUTH2_PROXY_CLIENT_SECRET`**: type a placeholder (e.g. `placeholder-to-replace`) — replaced in step 5.7.

```bash
wc -c < ./secrets/oauth2_cookie_secret.txt   # should show 32
```

## 4. Copy and fill in `.env`

```bash
cp env.en.example .env
```

Edit `.env` — `APP_DOMAIN` and `OAUTH2_PROXY_CLIENT_ID` are the two values to change first; the others have safe defaults.

`BIND_ADDRESS` is the only host address on which Traefik (80, 443) and Keycloak (8080) are published. It defaults to `127.0.0.1`: the application is then reachable from the VM only. For access from other workstations, set the VM's local network address (for example `BIND_ADDRESS=192.168.1.35`). Never set it to `0.0.0.0`: that would publish the ports on every interface, public IPv6 address included (EXT-56).

Add `COMPOSE_PROFILES=lab` to `.env`: the lab Keycloak then starts with the stack and the usual `docker compose` commands work without any
option. Without that line, add `--profile lab` to every `docker compose` command (otherwise Keycloak does not start and oauth2-proxy keeps
restarting for lack of an identity provider). The example files do not contain that line: a real deployment does not want it.

## 5. Start Keycloak alone and configure it

```bash
docker compose --profile lab up -d keycloak traefik
docker compose logs -f keycloak
```

Keycloak has no published port and no Internet access (internal `app-internal` network): Traefik relays port 8080 of `BIND_ADDRESS` to it,
hence starting `traefik` along with it.

Wait for `Keycloak ... started in ...s. Listening on: http://0.0.0.0:8080` (or `docker compose ps` → `healthy`).

Open `http://keycloak.lab.local:8080`, log in with `admin` / the password typed in step 3.

### 5.1 Create the realm

1. Realm selector (top left) → **Create realm**.
2. Realm name: `lab`.
3. **Create**.

### 5.2 Create the group

1. **Groups** → **Create group**.
2. Name: `SG-Anonymiseur-Utilisateurs`.
3. **Create**.

### 5.3 Create the confidential OIDC client

1. **Clients** → **Create client**.
2. *General settings*: Client ID: `anonymiseur-app`. **Next**.
3. *Capability config*: turn on **`Client authentication`**. Leave *Standard flow* checked. *PKCE method*: **`S256`** (required since phase 2 bis: oauth2-proxy sends `code_challenge_method = "S256"`, see `oauth2-proxy/oauth2-proxy.cfg`). **Next**.
4. *Login settings*: *Valid redirect URIs*: `https://anonymiseur.lab.local/oauth2/callback`. **Save**.
5. **Credentials** tab → copy the **Client secret** value.

### 5.4 Add the "Group Membership" mapper

1. On the client's page, **Client scopes** tab → click `anonymiseur-app-dedicated`.
2. **Add mapper** → **By configuration** → **Group Membership**.
3. *Name*: `groups`. *Token Claim Name*: type `groups` explicitly. *Full group path*: **On**. *Add to ID token* and *Add to access token*: **On**.
4. **Save**.

### 5.5 Create and assign the "groups" Client Scope

1. Left menu → **Client scopes** → **Create client scope**.
2. Name: `groups`. Protocol: `openid-connect`. **Save**.
3. **Mappers** tab → **Add mapper** → **By configuration** → **Group Membership**. Same settings as step 5.4. **Save**.
4. **Clients** → `anonymiseur-app` → **Client scopes** → **Add client scope** → check `groups` → **Default**.

### 5.6 Create one or two test users

1. **Users** → **Add user**. Username: `testuser1`. **Create**.
2. **Details** tab → turn on **Email verified**.
3. **Credentials** tab → **Set password** → turn off *Temporary* → **Save**.
4. **Groups** tab → **Join Group** → check `SG-Anonymiseur-Utilisateurs` → **Join**.
5. (Optional) Repeat for a second user, skipping *Join Group*, to test access denial.

### 5.7 Save the real client secret

```bash
printf '%s' '<real-secret-copied-from-keycloak>' > ./secrets/oauth2_client_secret.txt
chmod 644 ./secrets/oauth2_client_secret.txt
docker compose up -d --force-recreate oauth2-proxy
```

## 6. Start the rest of the stack

```bash
docker compose --profile lab up -d --build --scale presidio-analyzer=2
docker compose ps
```

(2 replicas are enough for a functional test; scale up to 6 for the 50-concurrent-user load test — 12-16 vCPU / 24-32 GB in that case.)

```bash
docker compose ps
```

Every service should show `Up`/`Running`/`Healthy`, with no restart counter that keeps increasing.

## 6 bis. Lab certificate (CA restricted to `.lab.local`)

Without this step, Traefik serves its default certificate: regenerated at every start and not naming the service, it forces you to
accept a warning after each restart, and **the browser extension cannot reach the server** (an extension request fails on an
untrusted certificate; observed on 2026-10-07).

1. On the VM, once (then every 397 days for the server certificate):
   ```bash
   sudo chown "$USER" traefik/certs     # once, if the folder belongs to root
   traefik/generate-lab-cert.sh
   ```
   - The CA (`~/.obfusk8-lab-ca/ca.crt`) carries a critical name constraint: it can vouch **only** for `.lab.local` names. A
     certificate it would sign for any other domain is refused by the browser (checked in Chromium: `ERR_CERT_INVALID` for
     `evil.example.com` and `lab.local.example.com`).
   - Its **private key** (`~/.obfusk8-lab-ca/ca.key`) never leaves the VM and never enters the repository.
   - Traefik picks up the new certificate without a restart (`traefik/dynamic/lab-tls.yml`).
2. On the workstation, copy **only** `ca.crt` and compare its fingerprint with the one shown on the VM:
   ```bash
   scp debian@<VM>:.obfusk8-lab-ca/ca.crt obfusk8-lab-ca.crt
   openssl x509 -in obfusk8-lab-ca.crt -noout -subject -fingerprint -sha256   # on the workstation AND on the VM
   ```
3. Import it as a **user** root authority (Chrome and Edge use the system store):
   - **Windows** (PowerShell, no administrator rights):
     `Import-Certificate -FilePath .\obfusk8-lab-ca.crt -CertStoreLocation Cert:\CurrentUser\Root` (confirm the dialog);
   - **macOS**: `security add-trusted-cert -r trustRoot -k ~/Library/Keychains/login.keychain-db obfusk8-lab-ca.crt`;
   - **Linux** (NSS store of Chrome and Edge; package `libnss3-tools`):
     `certutil -d sql:$HOME/.pki/nssdb -A -t "C,," -n "obfusk8 lab CA" -i obfusk8-lab-ca.crt`.
   Restart the browser, then open `https://<APP_DOMAIN>`: no more warning.
4. **End of the lab**: remove the CA from the workstation, then destroy `~/.obfusk8-lab-ca` with the VM (D-049):
   - **Windows**: `Get-ChildItem Cert:\CurrentUser\Root | Where-Object Subject -like '*obfusk8 lab CA*' | Remove-Item`;
   - **macOS**: `security delete-certificate -c "obfusk8 lab CA (lab.local only)" ~/Library/Keychains/login.keychain-db`;
   - **Linux**: `certutil -d sql:$HOME/.pki/nssdb -D -n "obfusk8 lab CA"`.

Pilot and production: the establishment's certificate, never this CA.

## 7. Verify and log in

```bash
docker compose logs -f app
docker compose logs -f oauth2-proxy
```

Open `https://anonymiseur.lab.local` (make sure it's `https`). With the lab CA imported (§6 bis) there is no certificate warning; otherwise, accept it. You should be redirected to Keycloak, log in with a test user, and land on the app.

## 8. (Optional) Enable the text API for the browser extension

Disabled by default. It lets the browser extension (separate `obfusk8-extension` repository, side panel) analyze a prompt
before it is sent to an AI service. Full contract: [`docs/api-extension.en.md`](./docs/api-extension.en.md).

1. In `.env`: `ENABLE_EXTENSION_API=true`, and the extension's origin in `EXTENSION_ALLOWED_ORIGINS` (phase 3, D-054). Lab
   extension (stable identifier, public key in its `config/lab.json`):
   `EXTENSION_ALLOWED_ORIGINS=chrome-extension://glaimpfdmfkidcgalcblojmkomplcgpa`. Empty: every analysis is refused (403).
   The `MAX_TEXT_*` limits have measured defaults (see `env.en.example`).
2. Recreate the application container:
   ```bash
   docker compose up -d app
   docker compose logs app | grep "API texte activée"
   ```
3. Check, once logged in through the browser: `https://anonymiseur.lab.local/api/v1/version` must return JSON
   (`api_version`, available themes). Flag disabled: 404.

Good to know:
- The `/api/v1/` routes have their own Traefik router (`app-text`): **per-user** rate limiting (60 requests per minute, burst 20) and a
  244,096-byte body cap. **If you change `MAX_TEXT_CHARS`**, recompute the `text-bodylimit` label in `docker-compose.yml`:
  `12 x MAX_TEXT_CHARS + 4096`.
- Prompt requests are recorded in a separate audit log, `/var/log/anonymiseur-audit/audit-extension.log` (metadata only: user, entity
  types and counts, length, duration; never the text).
- `POST /api/v1/text/*` requires the `Origin` of an allowed extension (`chrome-extension://<id>`, sent by Chrome from the side
  panel); `GET /api/v1/version` accepts a request without `Origin`. Any other origin: 403, audit outcome `origin_refused`.
  The extension can only reach a server whose certificate the browser trusts: see §6 bis.
- An unauthenticated request on `/api/v1/` gets a plain text **401** (`Unauthorized`), without redirect: the `app-text` router does not
  use `oauth2-errors` (D-010 option 1, D-020). The web interface keeps the redirect to the sign-in page. `oidc-auth` and the gateway
  secret are still required.
- The Presidio analyzer has a single worker, shared with the document flow: a document being processed delays prompts, and the other
  way round to a lesser extent (measurements in `benchmarks/results/`).

## Changing the domain later

Four places to update if `APP_DOMAIN` changes after a working install:

1. **`.env`**: new `APP_DOMAIN` value.
2. Recreate the affected containers:
   ```bash
   docker compose up -d --force-recreate traefik app oauth2-proxy
   ```
3. **`/etc/hosts`** on the client: replace the old main-domain entry (`keycloak.lab.local` doesn't change).
4. **Keycloak** → **Clients** → `anonymiseur-app` → **Settings** → *Valid redirect URIs*: replace the old domain with the new one.

If the project's folder name also changes, the `traefik.docker.network=obfusk8_app-internal` labels in `docker-compose.yml` need updating too.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Fatal glibc error: CPU does not support x86-64-v2` (Keycloak logs) | VM's CPU type too generic | Step 0 — `host` type in Proxmox |
| Page hangs with no clear error when opening the Keycloak console | `keycloak.lab.local` missing from the client's `/etc/hosts`; Keycloak always redirects to that name (`KC_HOSTNAME`) | Step 2 — add both entries, not just one |
| Keycloak console unreachable on port 8081 | The actual mapped port is 8080 | Use `http://keycloak.lab.local:8080` |
| `PermissionError: ... '/data/audit/audit.log'` (app logs) | Host folder created by Docker as root before the application user (UID 1000) can write to it | Fixed automatically by the `fix-app-dirs-perms` service — if it still happens, check `docker compose ps fix-app-dirs-perms` (should show `Exited (0)`) |
| `./generate-secrets.sh: ... Permission denied` writing to `secrets/` or `traefik/dynamic/` | The script was run after a failed `docker compose up` attempt; the folder is owned by `root` | `sudo chown -R $(whoami):$(whoami) ./secrets ./traefik/dynamic` then rerun with `--force` |
| `cookie_secret must be 16, 24, or 32 bytes... but is 51 bytes` (oauth2-proxy, on a loop, **persists even after regenerating and verifying the file is 32 bytes**) | An `OAUTH2_PROXY_COOKIE_SECRET` line is sitting in `.env` and takes priority over the Docker secret — never add `OAUTH2_PROXY_CLIENT_SECRET`/`OAUTH2_PROXY_COOKIE_SECRET` to `.env`, even temporarily; 51 is the length of the placeholder text, not a real secret | Remove any `OAUTH2_PROXY_*_SECRET` line from `.env`; `docker compose up -d --force-recreate oauth2-proxy` |
| `unauthorized_client` then `invalid_client_credentials` (Keycloak logs), **despite a verified-correct `secrets/oauth2_client_secret.txt`** | Same bug as above, on `OAUTH2_PROXY_CLIENT_SECRET` this time | Check `grep -i client_secret .env` (should be empty) and `docker compose config \| grep CLIENT_SECRET` (only one `_FILE` line should appear) |
| `could not read cookie secret file: /run/secrets/oauth2_cookie_secret` | Secret file is `600`, unreadable by the non-root UID of the `oauth2-proxy` container (different from your VM account's) | `chmod 644` on the files under `./secrets/` (already the default once `generate-secrets.sh` has been regenerated after this fix) |
| A secret fix seems to have no effect even though the host file checks out fine | The container in question was never recreated — a plain restart doesn't reread the file | Always use `docker compose up -d --force-recreate <service>` after changing a secret file |
| `Cannot start the provider *file.Provider: ... /etc/traefik/dynamic: permission denied` (Traefik logs), followed by `middleware "gateway-secret@file" does not exist` on **every** `app` router | `traefik/dynamic` folder is `700`, unreadable by Traefik's non-root UID — breaks the entire file provider | `chmod 755 ./traefik/dynamic && chmod 644 ./traefik/dynamic/gateway-secret.yml`, then `docker compose up -d --force-recreate traefik` |
| `404 page not found` (raw Traefik response) on the main domain, even though `docker compose config \| grep "app.rule"` confirms a correct rule | `traefik.docker.network=...` label pointing to a stale network name (renamed project folder, or a leftover from an old name) — Traefik matches the rule but can't find the target container on the network it's told to use | Check `docker network ls`, fix the label to match `<current-folder-name>_app-internal`, then `docker compose up -d --force-recreate traefik app oauth2-proxy` |
| Keycloak shows `Invalid parameter: redirect_uri`, with the old domain visible in the URL | The Keycloak client's *Valid redirect URIs* wasn't updated after an `APP_DOMAIN` change | See [Changing the domain later](#changing-the-domain-later), point 4 |
| `invalid_scope: Invalid scopes: openid email profile groups` (Keycloak, on the login screen) | `oauth2-proxy/oauth2-proxy.cfg` requests a `groups` scope (via `allowed_groups`) that doesn't exist as a Keycloak Client Scope | Step 5.5 — create and assign the `groups` Client Scope |
| Keycloak answers 403 right at `/oauth2/start`, with no login form (Keycloak log: missing `code_challenge_method` parameter) | The Keycloak client requires PKCE S256 but oauth2-proxy sends none (`code_challenge_method` line missing from `oauth2-proxy/oauth2-proxy.cfg`) | Restore `code_challenge_method = "S256"`, then `docker compose up -d --force-recreate oauth2-proxy` |
| `failed to create network obfusk8_app-internal ... networks have overlapping IPv4` at startup | The `app-internal` network has a fixed subnet (`10.89.18.0/24`, phase 2 bis) so that Traefik gets `10.89.18.10`, the only source oauth2-proxy trusts (`trusted_proxy_ips`). An existing network of the host already uses that range | Pick a free range outside Docker's default pools (172.17.0.0/12, 192.168.0.0/16) and set it in **three** places: `networks.app-internal.ipam` and Traefik's `ipv4_address` in `docker-compose.yml`, `trusted_proxy_ips` in `oauth2-proxy/oauth2-proxy.cfg` |
| `[AuthFailure] Invalid authentication via OAuth2: unauthorized` (oauth2-proxy logs), **despite the user being a group member and the mapper existing** | The *Group Membership* mapper's *Token Claim Name* field was left empty — the greyed-out text shown ("groups") is a placeholder example, not an actually-typed value; the `groups` claim then never appears in the token, with no error to point at it | Decode the token to check (`curl` to `/realms/lab/protocol/openid-connect/token` with `grant_type=password`, then base64-decode the `id_token` payload); if `groups` is missing, reopen every *Group Membership* mapper (dedicated scope AND the `groups` Client Scope) and explicitly retype `groups` in *Token Claim Name* |
| `500 Internal Server Error` / `email in id_token isn't verified` after an otherwise successful Keycloak login | *Email verified* unchecked on the user | Step 5.6 — check *Email verified* on the user's profile |
| `Bind for 192.168.1.35:8080 failed: port is already allocated` when recreating the stack after the `lab` profile update | The former Keycloak container published port 8080 itself, now relayed by Traefik | `docker compose stop keycloak`, then `docker compose --profile lab up -d` |
| oauth2-proxy keeps restarting, `Performing OIDC Discovery...` then a connection error | Stack started without the `lab` profile and without a real identity provider | Lab: `COMPOSE_PROFILES=lab` in `.env` (or `--profile lab`). Real deployment: see [Real deployment](#real-deployment-the-establishments-identity-provider) |
| Page unreachable at `http://anonymiseur.lab.local` | Traefik only serves over HTTPS | Use `https://` |
| `git clone`/`docker pull` asks for login even though the repo/package is supposed to be public | The visibility change wasn't confirmed all the way through on GitHub's side | Recheck `Settings → Danger Zone` (repo) or `Package settings` (package), redo the confirmation all the way to the end |

## Real deployment (the establishment's identity provider)

A pilot or production does **not** use the lab Keycloak (D-046) and reuses **no** lab secret: new install, fresh secrets
(`./generate-secrets.sh` on the new machine, D-049).

1. **No `lab` profile**: do not put `COMPOSE_PROFILES=lab` in `.env` and do not use `--profile lab`. Keycloak does not start; Traefik's port
   8080 then answers 404.
2. **oauth2-proxy** (`oauth2-proxy/oauth2-proxy.cfg`): the establishment provider's `provider` and `oidc_issuer_url`, `client_id` in `.env`
   (`OAUTH2_PROXY_CLIENT_ID`), the client secret in `secrets/oauth2_client_secret.txt`, `allowed_groups` set to the real group name. Keep
   `code_challenge_method = "S256"`: the client must require PKCE S256 on the provider's side (EXT-49). Accounts must carry a verified e-mail
   address (EXT-43).
3. **Outbound access dedicated and limited to that provider**: oauth2-proxy is on the internal `app-internal` network, with no Internet
   access. It must reach the provider (OIDC discovery, tokens) **and nothing else**: an outbound network dedicated to oauth2-proxy, filtered
   to the provider's addresses. This connection is designed and tested during the pilot phase; never attach `app-internal` or `app` to an
   open network.
4. Until the provider is configured, oauth2-proxy keeps restarting (OIDC discovery fails): this is expected.

### Example: Entra ID

In `oauth2-proxy/oauth2-proxy.cfg`, replace `provider = "oidc"` with `provider = "entra-id"` and point `oidc_issuer_url` to `https://login.microsoftonline.com/<tenant-id>/v2.0`, with the real Entra `client_id`/`client_secret` (the latter replaces the content of `secrets/oauth2_client_secret.txt`, same mechanics as step 5.7). `redirect_url` needs no attention here (already derived from `APP_DOMAIN`); only `allowed_groups` in this same file needs adjusting to the real group name on Entra ID's side.
