# Traduire l'interface

Depuis la mise en place du système de traduction, tous les textes affichés
à l'utilisateur (titres, labels, boutons, messages d'erreur, libellés de
thème) vivent dans `app/i18n/`, séparément du code Python. Ajouter une
langue ne nécessite plus de toucher à `app/main.py`.

## Fichiers concernés

```
app/i18n/
├── fr.json      # langue de référence — TOUJOURS complet, sert de repli
├── en.json      # traduction anglaise
└── themes.json  # libellés des thèmes (menu déroulant de la page d'accueil)
```

- `fr.json` est la base obligatoire : toute nouvelle chaîne d'interface y
  est ajoutée en premier. Les autres fichiers de langue peuvent être
  partiels — une clé absente d'une traduction retombe silencieusement sur
  le français (avec un avertissement journalisé au démarrage, jamais à
  chaque requête).
- `themes.json` est structuré par thème puis par langue :
  ```json
  {
    "compta": { "fr": "Comptabilité", "en": "Accounting" },
    "medical": { "fr": "Médical", "en": "Medical" }
  }
  ```
  Les fichiers `app/themes/*.json` eux-mêmes ne contiennent plus de champ
  `"label"` : leurs autres champs (`score_threshold`, `allow_list`,
  `ad_hoc_recognizers`, `excluded_entity_types`...) sont des paramètres de
  détection Presidio, pas des chaînes d'interface, et ne bougent pas.

## Ajouter une langue

1. Copier `app/i18n/fr.json` vers `app/i18n/<langue>.json` (ex: `de.json`
   pour l'allemand) et traduire les **valeurs** — jamais les clés.
2. Conserver tel quel tout ce qui est entre accolades (`{count}`,
   `{max_mb}`...) : ce sont des emplacements que le code Python remplit à
   l'exécution (`.format(...)`). Les balises HTML présentes dans certaines
   valeurs (`<strong>`, `<br>`) doivent elles aussi rester à l'identique —
   seul le texte autour se traduit.
3. Ajouter le libellé de chaque thème dans `app/i18n/themes.json`, sous
   le même code langue.
4. Redémarrer l'application avec `UI_LANG=<langue>` (voir ci-dessous). Le
   démarrage journalise le nombre de chaînes chargées et un avertissement
   pour chaque clé encore manquante par rapport à `fr.json` — utile pour
   repérer une traduction incomplète sans avoir à comparer les fichiers à
   la main.

Une langue non traduite du tout (fichier absent) ne fait jamais planter
l'application : elle retombe intégralement sur le français, avec un
avertissement clair au démarrage.

## Changer la langue active

`UI_LANG` est une variable d'environnement lue une seule fois, au
démarrage du processus (pas à chaque requête) :

- valeur par défaut : `fr`
- valeurs acceptées : n'importe quel code présent comme fichier dans
  `app/i18n/` (actuellement `fr`, `en`)

En déploiement Docker Compose, `UI_LANG` est définie directement dans le
bloc `environment:` du service `app` de `docker-compose.yml` (même
principe que `MAX_UPLOAD_MB` et les autres réglages du service) — ce
n'est **pas** une valeur lue depuis `.env`, `env.example` ne sert que de
référence/valeur par défaut documentée. Pour changer la langue servie,
éditer cette ligne puis reconstruire/redémarrer le service `app`.

En local (hors Docker), il suffit de positionner la variable avant de
lancer l'application :

```bash
UI_LANG=en uvicorn main:app --reload
```
