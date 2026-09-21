# Translating the interface

Since the translation system was introduced, every user-facing string
(titles, labels, buttons, error messages, theme labels) lives in
`app/i18n/`, separate from the Python code. Adding a language no longer
requires touching `app/main.py`.

## Files involved

```
app/i18n/
├── fr.json      # reference language — ALWAYS complete, used as fallback
├── en.json      # English translation
└── themes.json  # theme labels (dropdown menu on the home page)
```

- `fr.json` is the mandatory base: every new interface string is added
  there first. Other language files can be partial — a key missing from a
  translation silently falls back to French (with a warning logged at
  startup, never on every request).
- `themes.json` is structured by theme, then by language:
  ```json
  {
    "compta": { "fr": "Comptabilité", "en": "Accounting" },
    "medical": { "fr": "Médical", "en": "Medical" }
  }
  ```
  The `app/themes/*.json` files themselves no longer contain a `"label"`
  field: their other fields (`score_threshold`, `allow_list`,
  `ad_hoc_recognizers`, `excluded_entity_types`...) are Presidio detection
  parameters, not interface strings, and stay unchanged.

## Adding a language

1. Copy `app/i18n/fr.json` to `app/i18n/<language>.json` (e.g. `de.json`
   for German) and translate the **values** — never the keys.
2. Keep everything inside curly braces (`{count}`, `{max_mb}`...) exactly
   as is: these are placeholders that the Python code fills in at runtime
   (`.format(...)`). Any HTML tags present in some values (`<strong>`,
   `<br>`) must also stay unchanged — only the surrounding text is
   translated.
3. Add each theme's label to `app/i18n/themes.json`, under the same
   language code.
4. Restart the application with `UI_LANG=<language>` (see below). Startup
   logs the number of strings loaded and a warning for every key still
   missing compared to `fr.json` — useful for spotting an incomplete
   translation without having to compare files by hand.

A language that hasn't been translated at all (missing file) never
crashes the application: it falls back entirely to French, with a clear
warning at startup.

## Changing the active language

`UI_LANG` is an environment variable read once, at process startup (not
on every request):

- default value: `fr`
- accepted values: any code present as a file in `app/i18n/` (currently
  `fr`, `en`)

In a Docker Compose deployment, `UI_LANG` is set directly in the
`environment:` block of the `app` service in `docker-compose.yml` (same
principle as `MAX_UPLOAD_MB` and the service's other settings) — it is
**not** a value read from `.env`; `env.example` only serves as
documented reference/default. To change the served language, edit this
line and then rebuild/restart the `app` service.

Locally (outside Docker), just set the variable before launching the
application:

```bash
UI_LANG=en uvicorn main:app --reload
```
