# EXT-35 — détections PDF non localisées (2026-10-04T21:44:22+0000)

Même chaîne que `_detect_pdf` (passe 1) : texte de la page, `_analyze_normalized` vers le vrai `presidio-analyzer`, puis `page.search_for` sur la tranche du texte de la page. Une entité **perdue** est une entité détectée pour laquelle `search_for` ne renvoie aucun rectangle : aucune zone de caviardage, aucun avertissement. Seuls des comptes sont publiés.

Une entité **partiellement exposée** a des rectangles, mais après le même caviardage que la finalisation (`add_redact_annot` puis `apply_redactions`), au moins un caractère de la valeur est encore sur la page, au même endroit : le caviardage laisse fuir ce reste.

**Exposées avec l'application** : mêmes valeurs détectées, mais caviardées avec **toutes** les zones que `_detect_pdf` propose (recherche, repli par boîtes de caractères D-026, propagation) ; une valeur est exposée si l'un de ses caractères reste sur la page. Rapport de l'application : localisation absente, incomplète, rattrapée par le repli.

| Scénario | Thème | Pages | Détectées | Perdues | Taux | Perdues avec saut de ligne | Perdues par type | Partielles | Partielles avec saut de ligne | Partielles par type | Exposées avec l'application | Rapport de l'application (absentes / incomplètes / rattrapées) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| dejavu-sans | aucun | 48 | 285 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
| dejavu-sans | medical | 48 | 278 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
| dejavu-serif | aucun | 48 | 283 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
| dejavu-serif | medical | 48 | 276 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
| dejavu-mono | aucun | 48 | 283 | 0 | 0.0 | 0 | — | 16 | 0 | PERSON 12, PHONE_NUMBER 4 | 0 | 0 / 0 / 16 |
| dejavu-mono | medical | 48 | 275 | 0 | 0.0 | 0 | — | 16 | 0 | PERSON 12, PHONE_NUMBER 4 | 0 | 0 / 0 / 16 |
| base14-helvetica | aucun | 24 | 134 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
| base14-helvetica | medical | 24 | 130 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
| base14-times | aucun | 24 | 131 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
| base14-times | medical | 24 | 127 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
| majuscules | aucun | 48 | 274 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
| majuscules | medical | 48 | 265 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
| coupure-fin-de-ligne | aucun | 48 | 282 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
| coupure-fin-de-ligne | medical | 48 | 262 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
| colonnes | aucun | 24 | 289 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
| colonnes | medical | 24 | 281 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
| ligatures-serif | aucun | 24 | 213 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
| ligatures-serif | medical | 24 | 210 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
| ligatures-sans | aucun | 24 | 212 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
| ligatures-sans | medical | 24 | 209 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
