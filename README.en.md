# Obfusk8 — automatic document anonymization

🇬🇧 English | 🇫🇷 [Français](./README.md)

Self-hosted anonymization tool for documents (PDF, DOCX, CSV and PNG/JPEG images), designed to run entirely on-premises — no data is ever sent to a third-party service. Detects and redacts personal information (names, dates, identifiers, addresses...) via [Presidio](https://github.com/microsoft/presidio), with a human review step before final validation.

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

- Anonymization of **PDF, DOCX, CSV and images (PNG/JPEG with OCR)**.
- Detection via NER (Presidio) + custom recognizers (business identifiers), with configurable detection themes (e.g. a medical theme).
- **Human review screen** before finalization: each detection can be accepted, dismissed, or manually completed.
- Complete removal of metadata and residual objects on save (EXIF/PDF metadata, orphaned PDF objects, unreferenced DOCX entries).
- Delegated authentication via a standard OIDC provider (compatible with Keycloak, Entra ID, Okta...).
- Runs **100% offline** after the initial installation — no outbound network dependency for document processing.

## Requirements

- **Docker** ≥ 24.0 and **Docker Compose** ≥ v2.20 (the `docker compose` plugin, not the legacy standalone `docker-compose`).
- A server/VM with at least **4 vCPU / 8 GB RAM** for occasional use; see [`SECURITY.md`](./SECURITY.md#sizing) for multi-user usage.
- An accessible **OIDC identity provider** (Keycloak, Entra ID, Okta, or equivalent) — the application does not work without delegated authentication.
- A **domain name or internal DNS entry** pointing to the server (a valid TLS certificate will be required for any use beyond a lab, see [Known limitations](#known-limitations)).

## Quick start

```bash
git clone https://github.com/EpicFail20/0bfusk8.git
cd 0bfusk8

cp .env.example .env
# edit .env with your own values (see the Configuration section below)

docker compose pull
docker compose up -d
```

Check that everything is running:

```bash
docker compose ps
docker compose logs -f app
```

Then navigate to `https://<your-domain>` — you should be redirected to your identity provider before being able to access the application.

## Configuration

All configuration goes through the `.env` file, to be copied from [`.env.example`](./.env.example) and filled in. **Never commit your real `.env`** — it is excluded via `.gitignore`.

Each variable is documented directly in `.env.example`. Main categories:

| Category | What it controls |
|---|---|
| `APP_DOMAIN`, `OAUTH2_PROXY_*` | Application domain and connection to the OIDC provider |
| `MAX_*` | Size/volume caps (upload, pages, rows, detection duration...) — protect against oversized or malicious documents |
| `AV_*`, `ICAP_*` | Optional antivirus scanning, via an ICAP server already deployed in your infrastructure |
| `ALERT_SINK`, `SYSLOG_HOST` | Sending monitoring alerts to your SIEM/syslog collector |

The `MAX_*` caps have safe default values for standard use; only increase them knowingly (see [`SECURITY.md`](./SECURITY.md)).

## Authentication (SSO / OIDC)

The application never manages passwords itself: all authentication goes through `oauth2-proxy`, configured to talk to any standard OIDC provider.

### Example with Keycloak (test or lab)

1. Start Keycloak alone: `docker compose up -d keycloak`
2. Open the admin console, create a dedicated realm.
3. Create a confidential OIDC client, with the following redirect URI:
   `https://<your-domain>/oauth2/callback`
4. Add a *Group Membership* mapper → `groups` claim if you want to restrict access by group.
5. Retrieve the generated *client secret* and place it in `.env` (`OAUTH2_PROXY_CLIENT_SECRET`).

### Switching to an enterprise provider (Entra ID, Okta...)

Only the OIDC-related `.env` variables change — no other file or line of code needs to be modified:

```
OAUTH2_PROXY_CLIENT_ID=<your-provider-client-id>
OAUTH2_PROXY_CLIENT_SECRET=<your-provider-client-secret>
```

and in `oauth2-proxy.cfg`, the issuer URL (`oidc_issuer_url`) pointing to your tenant. Example for Entra ID:
`https://login.microsoftonline.com/<tenant-id>/v2.0`

## Customization

Detection behavior is driven by **themes** (the `themes/` folder), each defining the entity types to exclude or additional recognizers specific to a business context (a medical theme is provided as an example). See [`docs/customization.md`](./docs/customization.md) to create your own theme.

## Security

This project has undergone a thorough, iterative security audit, whose public summary — protections in place, design principles, known limitations, and recommendations before going to production — is available in [`SECURITY.md`](./SECURITY.md).

If you discover a vulnerability, please report it responsibly rather than disclosing it directly — see [`SECURITY.md#reporting-a-vulnerability`](./SECURITY.md#reporting-a-vulnerability).

## Known limitations

- **TLS certificate**: the reference deployment uses a self-signed certificate, suitable for a lab only. A trusted certificate is required before any production use.
- **Sizing**: resource sizing (vCPU/RAM, number of Presidio replicas) has not been validated by a real load test in every configuration — validate it in your own environment before use by several dozen concurrent users.
- **Detection quality**: like any NER-based system, detection is not guaranteed to be 100% exhaustive — the human review step before validation is deliberately mandatory, not a formality.
- **Antivirus scanning and monitoring**: the integrations (ICAP, syslog/SIEM) are ready but disabled by default — they require infrastructure that already exists on your side to be enabled.
- **External pentest**: recommended before processing any real sensitive data, in addition to the internal audit already carried out.

## Updates and versioning

This project follows [semantic versioning](https://semver.org/). See the [`CHANGELOG.md`](./CHANGELOG.md) and the [GitHub Releases](../../releases) for version history. Docker images are automatically built and tagged on each release (`ghcr.io/epicfail20/0bfusk8:vX.Y.Z`).

## License

This project is licensed under the GNU Affero General Public License v3.0 (AGPL-3.0).
See [LICENSE](./LICENSE) for details.
