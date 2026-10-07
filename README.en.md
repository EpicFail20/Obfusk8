# Obfusk8 — automatic document anonymization

🇬🇧 English | 🇫🇷 [Français](./README.md)

Self-hosted document anonymization tool (PDF, DOCX, CSV, and PNG/JPEG images), designed to run entirely on-premises — no data is ever sent to a third-party service. Detects and redacts personal information (names, dates, IDs, addresses...) via [Presidio](https://github.com/microsoft/presidio), with a human review step before final validation.

> ⚠️ **Before deploying this tool on real documents containing sensitive data**, make sure to read [`SECURITY.md`](./SECURITY.md) — it details the protections in place, known limitations, and the points that remain your responsibility (TLS certificate, pentest, monitoring).

## Table of contents

- [Features](#features)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Authentication (SSO / OIDC)](#authentication-sso--oidc)
- [Customization](#customization)
- [Security](#security)
- [Known limitations](#known-limitations)
- [Updates and versioning](#updates-and-versioning)
- [License](#license)

## Features

- Anonymization of **PDF, DOCX, CSV, and images (PNG/JPEG with OCR)**.
- NER-based detection (Presidio) + custom recognizers (business identifiers), with configurable detection themes (e.g. a medical theme).
- **Human review screen** before finalization: each detection can be accepted, dismissed, or manually added.
- Full removal of metadata and leftover objects when saving (EXIF/PDF metadata, orphaned PDF objects, unreferenced DOCX entries).
- Delegated authentication via any standard OIDC provider (works with Keycloak, Entra ID, Okta...).
- Runs **fully offline** after initial setup — no outbound network dependency for document processing.

## Requirements

- **Docker** ≥ 24.0 and **Docker Compose** ≥ v2.20 (the `docker compose` plugin, not the legacy standalone `docker-compose`).
- A server/VM with at least **4 vCPU / 8 GB RAM** for occasional use; see [`SECURITY.md`](./SECURITY.md#known-limitations-and-accepted-risks) for multi-user deployments.
- An accessible **OIDC identity provider** (Keycloak, Entra ID, Okta, or equivalent) — the application does not work without delegated authentication.
- A **domain name or internal DNS entry** pointing to the server (a valid TLS certificate is required for any use beyond a lab, see [Known limitations](#known-limitations)).

## Quick start

For a lab or for testing purposes, you can also refer to the more comprehensive document: [`deployment-lab.md`](./deployment-lab.md)

```bash
git clone https://github.com/EpicFail20/obfusk8.git
cd obfusk8

cp env.en.example .env
# edit .env with your own values (see the Configuration section below)
./generate-secrets.sh

# The images built from this repository (app, analyzer) are built locally;
# third-party images are pinned by digest and pulled.
# Their Python base image is defined in one place: x-python-base in
# docker-compose.build.yml (a plain "docker build" fails without
# --build-arg PYTHON_BASE).
docker compose -f docker-compose.yml -f docker-compose.build.yml build
# Lab: add --profile lab (lab Keycloak, development mode, never in
# production) or set COMPOSE_PROFILES=lab in .env.
docker compose up -d
```

Check that everything is running:

```bash
docker compose ps
docker compose logs -f app
```

Then go to `https://<your-domain>` — you should be redirected to your identity provider before reaching the application.

## Configuration

All configuration goes through the `.env` file, to be copied from [`env.en.example`](./env.en.example) (or [`env.fr.example`](./env.fr.example)) and filled in. **Never commit your real `.env`** — it is excluded via `.gitignore`.

Every variable is documented directly in `env.en.example`. The main categories:

| Category | What it controls |
|---|---|
| `APP_DOMAIN`, `OAUTH2_PROXY_*` | Application domain and connection to the OIDC provider |
| `MAX_*` | Size/volume caps (upload, pages, rows, detection duration...) — protect against oversized or malicious documents |
| `AV_*`, `ICAP_*` | Optional antivirus scanning, via an ICAP server already deployed in your infrastructure |
| `ALERT_SINK`, `SYSLOG_HOST` | Forwarding monitoring alerts to your SIEM/syslog collector |
| `ENABLE_EXTENSION_API`, `MAX_TEXT_*` | Text API for a future browser extension (prompt analysis and pseudonymization), **disabled by default** — see [`docs/api-extension.en.md`](./docs/api-extension.en.md) |

The `MAX_*` caps have safe defaults for standard use; only raise them if you know what you're doing (see [`SECURITY.md`](./SECURITY.md)).

## Authentication (SSO / OIDC)

The application never handles passwords itself: all authentication goes through `oauth2-proxy`, configured to talk to any standard OIDC provider.

### Example with Keycloak (test or lab)

1. Start Keycloak alone: `docker compose --profile lab up -d keycloak traefik` (**lab** Keycloak only, in development mode: never for a
   pilot or production, see `deployment-lab.md`, "Real deployment")
2. Open the admin console, create a dedicated realm.
3. Create a confidential OIDC client, with this redirect URI:
   `https://<your-domain>/oauth2/callback`
4. Add a *Group Membership* mapper → `groups` claim if you want to restrict access by group.
5. Retrieve the generated *client secret* and put it in the Docker secret `secrets/oauth2_client_secret.txt`, **never** in `.env` (an
   `OAUTH2_PROXY_CLIENT_SECRET` variable in `.env` would take precedence over the Docker secret); see `deployment-lab.md`, step 5.7.

### Switching to an enterprise provider (Entra ID, Okta...)

No line of code needs to change. The client ID goes in `.env`, the client secret in the Docker secret, **never** in `.env`:

```
# .env
OAUTH2_PROXY_CLIENT_ID=<your-provider-client-id>
```

```bash
printf '%s' '<your-provider-client-secret>' > ./secrets/oauth2_client_secret.txt
```

and in `oauth2-proxy/oauth2-proxy.cfg`, the provider (`provider`), the issuer URL (`oidc_issuer_url`) pointed to your tenant and the
allowed group (`allowed_groups`). Starting without the `lab` profile and outbound access limited to the provider: see `deployment-lab.md`,
"Real deployment". Example for Entra ID:
`https://login.microsoftonline.com/<tenant-id>/v2.0`

## Customization

Detection behavior is driven by **themes** (`themes/` folder), each defining which entity types to exclude or which additional recognizers apply to a given business context (a medical theme is provided as an example). See [`docs/customization.md`](./docs/customization.md) to create your own theme.

## Security

This project has undergone an in-depth, iterative security audit. A public summary — protections in place, design principles, known limitations, and recommendations before going to production — is available in [`SECURITY.md`](./SECURITY.md).

If you discover a vulnerability, please report it responsibly rather than disclosing it directly — see [`SECURITY.md#reporting-a-vulnerability`](./SECURITY.md#reporting-a-vulnerability).

## Known limitations

- **TLS certificate**: the reference deployment uses a self-signed certificate, suitable for lab use only. A trusted certificate is required before any production use.
- **Sizing**: resource sizing (vCPU/RAM, number of Presidio replicas) has not been validated with a real load test across every configuration — validate it in your own environment before use by several dozen concurrent users.
- **Detection quality**: like any NER-based system, detection is not guaranteed to be 100% exhaustive — the mandatory human review screen before validation is a deliberate step, not a formality.
- **Antivirus scanning and monitoring**: the integrations (ICAP, syslog/SIEM) are ready but disabled by default — they require infrastructure that must already exist on the deployment side to be enabled.
- **External pentest**: recommended before processing any real sensitive data, in addition to the internal audit already carried out.

## Updates and versioning

This project follows [semantic versioning](https://semver.org/). See the [GitHub Releases](../../releases) for version history. For every git tag `vX.Y.Z`, the workflow publishes two images, `ghcr.io/epicfail20/obfusk8-app` and `ghcr.io/epicfail20/obfusk8-presidio-analyzer`, tagged `X.Y.Z` and `sha-<commit>` (never `latest` or `main`). `docker-compose.yml` references them by digest (`:X.Y.Z@sha256:…`), like every third-party image. The version under development, `0.2.0-dev`, is only built locally (`docker-compose.build.yml`) and is not published.

Dependencies (Python packages, base and third-party images) are updated with the [dependency maintenance](docs/dependency-maintenance.md) procedure: inventory at the source, batches tested under seccomp, detection safeguard, rollback, proposed cadence.

## License

This project is licensed under the GNU Affero General Public License v3.0 (AGPL-3.0).
See [LICENSE](./LICENSE) for details.
