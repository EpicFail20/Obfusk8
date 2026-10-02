# Text API for the browser extension — contract v1

> Version française : [`api-extension.md`](./api-extension.md).
> Status: **phase 1, step B proposal**, pending validation. Nothing is implemented yet.
> The reasoning behind each choice is in [`DECISIONS.md`](./DECISIONS.md) (decisions D-001 to D-016).

## 1. Purpose

Let a future browser extension send a **prompt** (free text) to the Obfusk8 server before it goes to an AI service, in order to **detect**
sensitive data in it, or to **pseudonymize** it (replacement with placeholders that the client can reverse).

Principles, inherited from the project doctrine:

- **Same engine as the document flow**: same themes, same thresholds, same normalization, same value propagation. No parallel detection path.
- **No client parameter can weaken detection**: no threshold, no list of entity types to disable, no client-supplied recognizer.
  Any unknown field is rejected (422).
- **Stateless**: nothing on disk, nothing kept beyond the request, no restore endpoint.
- **Human review remains mandatory**: the API cannot enforce it by itself. It is the extension's job to show the user what was detected
  and let them correct it before sending (later phase, decision D-011).
- **Disabled by default**: `ENABLE_EXTENSION_API=false`. The routes then do not exist (404, the same response as today).

## 2. Endpoints

Versioned prefix **`/api/v1/`** (D-001). A future incompatible change will take the form of a new `/api/v2/` prefix; the extension also checks
`api_version` (major.minor) returned by `GET /api/v1/version`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/version` | API and detection configuration version, available themes, size limit |
| `POST` | `/api/v1/text/analyze` | Detection: list of entities with their offsets |
| `POST` | `/api/v1/text/pseudonymize` | Pseudonymized text and mapping table |

All routes go through the existing chain: Traefik → `oauth2-errors` → `oidc-auth` (oauth2-proxy) → rate limiting → body cap →
`gateway-secret@file` → application. They have their **own Traefik router** (§7).

### 2.1 `GET /api/v1/version`

200 response:

```json
{
  "api_version": "1.0",
  "detection_config": "3f1c9a0b7e2d4c55",
  "analyzer_language": "fr",
  "analyzer_recognizers": "a41c07d29e5b3f18",
  "presidio_version": null,
  "themes": [
    {"key": "compta", "label": "Accounting"},
    {"key": "it", "label": "IT / Infrastructure"},
    {"key": "medical", "label": "Medical"}
  ],
  "max_text_chars": 20000
}
```

- `detection_config`: fingerprint (SHA-256 truncated to 16 hex characters) computed at startup over the canonical content of the themes,
  the common recognizers, the text-API-specific recognizers, `DEFAULT_SCORE_THRESHOLD` and the analysis language. It changes as soon as any
  of these changes. The extension can display or log it to know which configuration a text was checked with.
- `analyzer_recognizers`: fingerprint (16 hex characters) of the list of recognizers actually loaded by `presidio-analyzer` for the analysis
  language, queried on the first call then cached; `null` if the analyzer does not answer (D-013).
- `presidio_version`: always `null` for now, since Presidio's REST API does not expose its version (D-013).
- `label`: label in the server's `UI_LANG` language (`app/i18n/themes.json`).

### 2.2 `POST /api/v1/text/analyze`

Request (`Content-Type: application/json`, UTF-8 encoding):

```json
{"text": "Bonjour, je suis Camille Martin, joignable au 06 12 34 56 78.", "theme": "medical"}
```

| Field | Type | Rule |
|---|---|---|
| `text` | string | Required, at least 1 character, at most `MAX_TEXT_CHARS` code points (beyond: 413). Lone UTF-16 surrogate: rejected. |
| `theme` | string or `null` | Optional. Key of an existing theme (`GET /api/v1/version`). Unknown theme: **422**, never a silent fallback to "no theme". Absent or `null`: `DEFAULT_SCORE_THRESHOLD`, like the document flow without a theme. |

Any other field (for instance `score_threshold`, `entities`, `ad_hoc_recognizers`) is rejected with 422.

200 response:

```json
{
  "request_id": "8f0d6c3e1b2a4f5e9d7c6b5a4f3e2d1c",
  "theme": "medical",
  "text_length": 61,
  "text_length_utf16": 61,
  "entities": [
    {"entity_type": "PERSON", "start": 17, "end": 31, "start_utf16": 17, "end_utf16": 31},
    {"entity_type": "PHONE_NUMBER", "start": 46, "end": 60, "start_utf16": 46, "end_utf16": 60}
  ]
}
```

- **Offsets on the string exactly as received**, given twice (D-002): in Unicode code points (`start`/`end`, Python indexing) and in UTF-16
  code units (`start_utf16`/`end_utf16`, JavaScript indexing: `text.slice(start_utf16, end_utf16)`). The two differ as soon as a character outside
  the Basic Multilingual Plane (emoji, some ideographs) precedes the entity. `end` is exclusive.
- The normalization applied before detection (capitals, typographic dashes) preserves length, hence offsets.
- Entities are sorted by `start`, then `end`. **They may overlap** (two recognizers on the same passage): the client must not assume disjoint intervals.
- Entities include those found by **propagation**: every other exact occurrence of an already detected value is reported too (D-012).
- **No score** (D-003): Presidio scores are not calibrated; a client filtering on them would create false negatives.
- Entity types: those of Presidio and of the themes (`PERSON`, `LOCATION`, `EMAIL_ADDRESS`, `PHONE_NUMBER`, `DATE_TIME`, `PATIENT_ID`, `FR_NIR`…),
  plus the text-API-specific ones, active whatever the theme: `SECRET` (secrets in prompts) and coverage of the identifiers missing from the no-theme
  flow, notably payment card and French social security number (EXT-08). The exact list will be frozen in steps C and D. The client must treat any
  unknown type as sensitive.

### 2.3 `POST /api/v1/text/pseudonymize`

Request: same as `analyze`.

200 response:

```json
{
  "request_id": "0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f",
  "theme": null,
  "text": "Bonjour, je suis ⟦PERSON_1⟧. ⟦PERSON_1⟧ est joignable au ⟦PHONE_NUMBER_1⟧.",
  "mapping": [
    {"placeholder": "⟦PERSON_1⟧", "entity_type": "PERSON", "original": "Camille Martin"},
    {"placeholder": "⟦PHONE_NUMBER_1⟧", "entity_type": "PHONE_NUMBER", "original": "06 12 34 56 78"}
  ]
}
```

Rules:

1. **Detection identical to `analyze`** (same text, same theme: same entities).
2. **Overlaps**: overlapping entities are merged into a single replaced passage (the union of the intervals). The type kept is that of the longest
   entity; on a tie, `SECRET` wins, then alphabetical order (deterministic rule, D-004).
3. **The same value gets the same placeholder throughout the text.** The key is the exact original value (case-sensitive): "Camille Martin"
   and "CAMILLE MARTIN" get two distinct placeholders, which guarantees exact restoration.
4. **Placeholder format**: `⟦TYPE_N⟧`, with `⟦` (U+27E6) and `⟧` (U+27E7), `TYPE` the entity type, `N` an integer starting at 1 per type (D-004).
   These brackets practically never appear in ordinary text or in code.
5. **No collision**: if a candidate placeholder already appears in the received text, the next number is used. A `mapping` placeholder therefore
   appears in the returned text **only** where the server put it.
6. **Client-side restoration**: replace each `placeholder` with its `original` in the AI service's response. The server keeps nothing; there is
   no restore endpoint.
7. `mapping` is sorted by first appearance in the text. **It contains the sensitive data in clear**: the extension must keep it in memory only
   for the duration of the conversation, never in persistent storage or in logs.

## 3. Errors

Same format as the application's existing errors (`detail` field), plus the correlation identifier:

```json
{"detail": "The text exceeds the maximum allowed size (20000 characters).", "request_id": "8f0d6c3e1b2a4f5e9d7c6b5a4f3e2d1c"}
```

- `detail` is translated (`app/i18n/`, `UI_LANG` language) and **generic**: never an echo of the submitted text, a trace, a path or a field value.
  FastAPI's default validation message, which copies the offending value, is not used (D-009).
- `request_id` (32 hex characters) is also sent in the `X-Request-ID` header of **every** v1 response, successes included. It is repeated in the
  application logs and in the audit log so a request can be traced.

| Code | Case |
|---|---|
| 400 | Body not UTF-8, invalid JSON (including a lone surrogate in a `\u` escape) |
| 401 | Missing or wrong gateway secret (request bypassing Traefik) — existing application response, without `request_id` |
| 302 | Unauthenticated through Traefik: `oauth2-errors` rewrites the 401 as a 302 **without a `Location` header**, with oauth2-proxy's HTML sign-in page as body (observed on 2026-10-02; current behavior of every route, unsuited to an extension, see §8) |
| 404 | `ENABLE_EXTENSION_API=false` (standard FastAPI response `{"detail":"Not Found"}`, identical to today) |
| 413 | Body beyond the cap (at the edge by Traefik, otherwise by the application), or `text` beyond `MAX_TEXT_CHARS` |
| 415 | `Content-Type` other than `application/json` (also protects against cross-site form posts, §6) |
| 422 | Well-formed but non-conforming body: missing field, wrong type, unknown field, empty text, unknown or malformed theme |
| 429 | Text analysis queue full (application, `Retry-After` header), or Traefik rate limiting (Traefik's plain-text response, `Retry-After` and `X-Retry-In` headers, **no** JSON body or `request_id`) |
| 503 | `presidio-analyzer` unreachable, failing, or `MAX_TEXT_ANALYSIS_SECONDS` exceeded (waiting included); a supervision alert is sent |
| 500 | Unexpected error: generic message, details in the logs under the same `request_id` |

Note: the document flow answers 502 when Presidio is unavailable. The v1 routes answer **503**, which is more accurate and easier for a retrying
client to handle (D-005).

## 4. Limits and environment variables

All declared in `docker-compose.yml` and in `env.fr.example` / `env.en.example`.

| Variable | Default | Justification |
|---|---|---|
| `ENABLE_EXTENSION_API` | `false` | New feature disabled by default (`CLAUDE.md` §3). |
| `MAX_TEXT_CHARS` | `20000` | Measured on 2026-10-02 on the VM (analyzer alone, no theme, median of 5 calls): 0.29 s at 10,000 characters, **0.71 s at 20,000**, 2.7 s at 50,000 (worse than linear). The analyzer has **a single worker** shared with the document flow: 20,000 characters keep the wait a text request imposes on a document batch under one second. A typical prompt is under 5,000 characters. |
| `MAX_TEXT_ANALYSIS_SECONDS` | `10` | Total time of a request (queue wait included), and the timeout of the HTTP call to Presidio. About 14 times the time measured at the size cap: covers waiting behind a document batch (at most 8,000 characters, about 0.25 s) without leaving an interactive client hanging. |
| `MAX_TEXT_CONCURRENCY` | `1` | Number of text analyses **in progress** at once. The analyzer handles requests one at a time; more than one text analysis in flight speeds nothing up and lengthens the wait of document batches. With 1, a document batch waits for at most one text analysis (under 1 s at the cap). |
| `MAX_TEXT_QUEUE` | `8` | Text requests allowed to **wait** for their turn. Beyond: immediate 429. Bounds the memory held by waiting requests (at most 8 bodies of 244 KiB). |

Body cap, **derived** and not separately configurable (D-006):

```
MAX_TEXT_BODY_BYTES = 12 × MAX_TEXT_CHARS + 4096 = 244096 bytes (238 KiB) by default
```

- **12 bytes per code point** cover the worst JSON encoding case: a character outside the BMP escaped as two `\uXXXX` sequences (12 bytes),
  as produced by a client that escapes all non-ASCII (Python's `json.dumps` default). A raw UTF-8 character is at most 4 bytes.
- **4,096 bytes** for the JSON envelope (`{"text":…,"theme":…}`, whitespace, theme name of at most 64 characters).
- The cap is applied **in the application** (stream reading interrupted beyond it, without reading everything) **and at the edge** by Traefik
  (`maxRequestBodyBytes=244096` on the dedicated router). **Both values must stay equal**; if `MAX_TEXT_CHARS` changes, the Traefik label must be
  recomputed (comment next to the label, as for `MAX_UPLOAD_MB`).
- The existing global cap (`MAX_REQUEST_BODY_BYTES`, 27 MiB) stays in place but is much wider; the v1-specific cap is checked in their own code (D-006).

## 5. Logging and audit

- **Separate audit log**: `/data/audit/audit-extension.log` (same directory, same JSON-per-line format, same 10 MiB × 10 rotation, independent
  rotation). Prompt events, potentially very frequent, thus cannot evict the document history from `audit.log` (D-007). `GET /api/audit` keeps
  reading `audit.log` only.
- One line per request completed or rejected by the application:

  ```json
  {"timestamp": "2026-10-02T10:00:00+0000", "event": "text_pseudonymize", "request_id": "…", "user": "utilisateur.fictif@exemple.invalid",
   "theme": "aucun", "text_chars": 61, "entities_found": {"PERSON": 2, "PHONE_NUMBER": 1}, "total_entities": 3,
   "duration_ms": 42, "outcome": "ok"}
  ```

  `outcome`: `ok`, or a closed category (`too_large`, `invalid`, `busy`, `analyzer_unavailable`, `timeout`, `error`).
- **Never** any text, detected value, placeholder or `mapping`: metadata only (user, types and counts, length, duration, identifier). A test will
  demonstrate it on the audit log **and** on the container logs (`app`, `presidio-analyzer`, Traefik).
- Application logs: one line per request (`request_id`, route, status, duration, number of entities).
- Prometheus metrics: request counter per route and outcome, duration histogram (closed categories, no user data).

## 6. Threat model (summary)

| Threat | Countermeasure |
|---|---|
| Unauthenticated access | Existing `oidc-auth` chain. Bypassing Traefik from a neighboring container: gateway secret (401). |
| Identity spoofing through headers | `forwardAuth` deletes then sets `X-Auth-Request-*` from oauth2-proxy's response (checked in Traefik 3.7 code, `pkg/middlewares/auth/forward.go`). |
| Cross-site request (CSRF) with the session cookie | `Content-Type: application/json` required (415 otherwise): a cross-site form cannot send it, and a cross-site `fetch` with that type triggers a CORS preflight the server refuses (no CORS headers, no allowed origin). `SameSite=Lax` cookie on top. |
| Giant input | Body cap at the edge and in the application (reading interrupted), `MAX_TEXT_CHARS`, maximum duration. |
| Malicious JSON (deep nesting, duplicates) | Pydantic's JSON parser, bounded by the body cap; strict schema, unknown fields refused. |
| Tricky Unicode (lone surrogates, zero-width, NFD, non-breaking spaces, bidirectional controls) | Lone surrogates rejected. The others are accepted as is; their effect on detection is **measured** by the benchmark (step E), and any added normalization will have to preserve offsets. The text is never written to a log, so no visual log spoofing risk. |
| Denial of service, starving the document flow | Per-user rate limiting at the edge; bounded queue and a single text analysis at a time in the application; maximum duration. Known limitation: the document flow itself blocks the event loop (EXT-07, out of scope); text requests then wait for the document processing to finish. |
| Client weakening detection | No threshold, entity or recognizer parameter; unknown fields refused (422); unknown theme refused. |
| Enumeration (themes, users) | Themes are public to an authenticated user (`/version`). No other user's data is reachable: no state, no job identifier. |
| Leak through logs or audit | Metadata only, demonstrated by test (§5). `Cache-Control: no-store` on every response (existing security headers). |
| Leak through the response | The `pseudonymize` response contains the originals by necessity: it only goes to the authenticated user who sent the text, over TLS. Nothing is kept server-side. |
| Forged placeholder in the input text | No-collision rule (§2.3, rule 5): a placeholder present in the input is never reused, restoration stays unambiguous. |

Tests to defer to the external pentest: rate limiting bypass (session rotation, `X_Auth_Request_User` header aliases), header injection across
the Traefik → oauth2-proxy chain, behavior under real multi-user load, JSON parser robustness against fuzzing corpora, timing attacks on the
gateway secret from the internal network.

## 7. Dedicated Traefik router

```
traefik.http.routers.app-text.rule=Host(`${APP_DOMAIN}`) && PathPrefix(`/api/v1/`)
traefik.http.routers.app-text.priority=100
traefik.http.routers.app-text.middlewares=oauth2-errors,oidc-auth,text-ratelimit,text-bodylimit,gateway-secret@file
traefik.http.middlewares.text-ratelimit.ratelimit.average=60
traefik.http.middlewares.text-ratelimit.ratelimit.period=1m
traefik.http.middlewares.text-ratelimit.ratelimit.burst=20
traefik.http.middlewares.text-ratelimit.ratelimit.sourcecriterion.requestheadername=X-Auth-Request-User
traefik.http.middlewares.text-bodylimit.buffering.maxRequestBodyBytes=244096
traefik.http.middlewares.text-bodylimit.buffering.memRequestBodyBytes=244096
```

- **Rate**: 60 requests per minute per user, burst of 20. A sent prompt costs one or two requests (`analyze` then `pseudonymize`, or `pseudonymize`
  alone); sustained interactive use rarely exceeds one send every 2 seconds. More than file uploads (5/min), less than previews (120/min).
  Starting values, to be revisited after the latency benchmark (step E).
- **Per-user key rather than per IP** (D-008): a per-IP key penalizes every user behind the same NAT or corporate proxy. Checked in Traefik 3.7's
  documentation and code:
  - `sourceCriterion.requestHeaderName` groups requests by header value (mutually exclusive with `ipStrategy`);
  - `forwardAuth` sets the `authResponseHeaders` on the forwarded request **after deleting** any client-supplied value. The following middlewares in
    the chain therefore see the authenticated identity: `text-ratelimit` must come **after** `oidc-auth`;
  - if the header is missing, the extracted value is an empty string: all such requests share one bucket (no error). `X-Auth-Request-User` is chosen
    over `X-Auth-Request-Email` because oauth2-proxy fills it from the user identifier, whereas the email address may be missing from the token
    (assumed from oauth2-proxy's documentation, to be checked end to end).
  - The real behavior will be **checked end to end** in step F (two accounts, same IP: separate buckets).
- **Body cap**: `buffering` keeps the whole body in memory up to `memRequestBodyBytes`; at the 238 KiB cap, the disk buffer is never used.

## 8. Options for the next phase (documented, not implemented)

Today, an unauthenticated request gets a **302 without a `Location` header** whose body is the HTML sign-in page (`oauth2-errors`
rewrites the 401). A browser displays that page; an extension expecting JSON cannot use it. The options and their consequences are detailed in `DECISIONS.md` (D-010). In short: (a) a router without `oauth2-errors`
for `/api/v1/` (raw 401); (b) Bearer tokens accepted by oauth2-proxy (`skip_jwt_bearer_tokens`); (c) the extension reuses the browser's session
cookie; (d) Obfusk8-specific API tokens.

## 9. Open decisions

See `DECISIONS.md`, "Open decisions" section: source of `presidio_version` (D-013), final placeholder format (D-004), propagation of exact values
to every type (D-012), additional Unicode normalization (D-014, after measurement).
