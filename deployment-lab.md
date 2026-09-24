# Anonymizer - lab deployment on a Docker VM (Proxmox)

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

## 5. Start Keycloak alone and configure it

```bash
docker compose up -d keycloak
docker compose logs -f keycloak
```

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
3. *Capability config*: turn on **`Client authentication`**. Leave *Standard flow* checked. **Next**.
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
docker compose up -d --build --scale presidio-analyzer=2 --scale presidio-anonymizer=1
docker compose ps
```

(2 replicas are enough for a functional test; scale up to 6/2 for the 50-concurrent-user load test — 12-16 vCPU / 24-32 GB in that case.)

```bash
docker compose ps
```

Every service should show `Up`/`Running`/`Healthy`, with no restart counter that keeps increasing.

## 7. Verify and log in

```bash
docker compose logs -f app
docker compose logs -f oauth2-proxy
```

Open `https://anonymiseur.lab.local` (make sure it's `https`). Accept the certificate warning (self-signed in the lab). You should be redirected to Keycloak, log in with a test user, and land on the app.

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
| `invalid_scope: Invalid scopes: openid email profile groups` (Keycloak, on the login screen) | `oauth2-proxy.cfg` requests a `groups` scope (via `allowed_groups`) that doesn't exist as a Keycloak Client Scope | Step 5.5 — create and assign the `groups` Client Scope |
| `[AuthFailure] Invalid authentication via OAuth2: unauthorized` (oauth2-proxy logs), **despite the user being a group member and the mapper existing** | The *Group Membership* mapper's *Token Claim Name* field was left empty — the greyed-out text shown ("groups") is a placeholder example, not an actually-typed value; the `groups` claim then never appears in the token, with no error to point at it | Decode the token to check (`curl` to `/realms/lab/protocol/openid-connect/token` with `grant_type=password`, then base64-decode the `id_token` payload); if `groups` is missing, reopen every *Group Membership* mapper (dedicated scope AND the `groups` Client Scope) and explicitly retype `groups` in *Token Claim Name* |
| `500 Internal Server Error` / `email in id_token isn't verified` after an otherwise successful Keycloak login | *Email verified* unchecked on the user | Step 5.6 — check *Email verified* on the user's profile |
| Page unreachable at `http://anonymiseur.lab.local` | Traefik only serves over HTTPS | Use `https://` |
| `git clone`/`docker pull` asks for login even though the repo/package is supposed to be public | The visibility change wasn't confirmed all the way through on GitHub's side | Recheck `Settings → Danger Zone` (repo) or `Package settings` (package), redo the confirmation all the way to the end |

## Switching to Entra ID later

In `oauth2-proxy.cfg`, replace `provider = "oidc"` with `provider = "entra-id"` and point `oidc_issuer_url` to `https://login.microsoftonline.com/<tenant-id>/v2.0`, with the real Entra `client_id`/`client_secret` (the latter replaces the content of `secrets/oauth2_client_secret.txt`, same mechanics as step 5.7). `redirect_url` needs no attention here (already derived from `APP_DOMAIN`); only `allowed_groups` in this same file needs adjusting to the real group name on Entra ID's side.
