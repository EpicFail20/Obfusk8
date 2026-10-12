# Identity in the audit: pseudonymisation and re-identification (note for the DPO)

> Version française : [`audit-identite.md`](./audit-identite.md).
> Human decision of 2026-10-12 (D-057 point 9). Re-identification procedure **proposed, to be validated by the DPO**.

## 1. What the audit records

Since the "option 2" phase, the user's identity, in **every** flow (document interface and the extension's text API), is the identity
provider's **`sub`**: an opaque technical identifier (in the lab, Keycloak's internal user id, UUID-shaped), passed by oauth2-proxy in the
`X-Auth-Request-User` header. The e-mail address is no longer used as an identity.

The `sub` keys:
- the document **audit log** (`/var/log/anonymiseur-audit/audit.log`, field `user`) and the text API's (`audit-extension.log`, field
  `user`);
- the per-user **quota** of pending documents and the **ownership** of a document (preview, finalization, cancellation);
- the per-user **rate limit** (Traefik).

The audit never holds content (text, detected values, plain file names): metadata only (user, entity types and counts, lengths,
durations, request identifiers).

## 2. Extent of the pseudonymisation

The audit is **pseudonymised**, not anonymised: the `sub` leads back to the person through the identity provider's directory. The data
therefore remain personal data under the GDPR.

Other places where the correspondence exists (observed in the lab on 2026-10-12):
- **the oauth2-proxy container's log**: one line per sign-in links the `sub`, the e-mail and the username
  (`[AuthSuccess] … Session{email:… user:<sub> PreferredUsername:…}`), as well as the claims of a refused token (EXT-67). Rotation:
  3 files of 10 MiB (`docker-compose.yml`); the actual retention depends on the sign-in volume;
- **the identity provider**: the directory itself, and its sign-in logs.

## 3. Re-identification procedure (proposal)

**When**: investigation of a security incident, request from an authority, exercise of a right by the data subject. Never for activity
monitoring.

**Who**:
- the request is made and justified by the DPO (or the CISO for an incident, informing the DPO);
- it is carried out by an administrator of the identity provider, **distinct** from obfusk8's administrator (separation of duties: the
  person who reads the audit does not hold the directory).

**How**:
- *Keycloak (lab)*: the `sub` is the user's id. Admin console, realm `lab`, "Users", search by id; or
  `kcadm.sh get users/<sub> -r lab --fields username,email`.
- *Entra ID (establishment)*: the `sub` is **specific to each application** (EXT-68) and cannot be looked up directly in the directory.
  To be settled before the pilot (a single app registration for oauth2-proxy and the API, or another mechanism). Until then,
  re-identification goes through the oauth2-proxy container's log (§2), within its retention window.

**With what trace**:
- the request, its reason, who carries it out, the date and the result are recorded in the **DPO's register** (outside obfusk8);
- the administrator's action is logged by the identity provider (in the lab, Keycloak's admin events are **not** enabled:
  `eventsEnabled: false`, observed; to be enabled in a real provider);
- nothing is added to obfusk8: the audit is never modified nor enriched with the name found.

## 4. Points for the DPO to validate

1. The procedure of §3 (who, how, what trace).
2. The retention of the oauth2-proxy container's logs, which hold the `sub` ↔ e-mail correspondence (§2).
3. The handling of EXT-68 before a pilot on Entra ID.
4. Mentioning the pseudonymised audit in the information given to agents and in the DPIA.
