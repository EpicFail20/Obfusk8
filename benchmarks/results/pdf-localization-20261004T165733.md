# EXT-35 — détections PDF non localisées (2026-10-04T16:55:58+0000)

Même chaîne que `_detect_pdf` (passe 1) : texte de la page, normalisations, `_analyze_text` vers le vrai `presidio-analyzer`, puis `page.search_for` sur la tranche du texte de la page. Une entité **perdue** est une entité détectée pour laquelle `search_for` ne renvoie aucun rectangle : aucune zone de caviardage, aucun avertissement. Seuls des comptes sont publiés.

Une entité **partiellement exposée** a des rectangles, mais après le même caviardage que la finalisation (`add_redact_annot` puis `apply_redactions`), au moins un caractère de la valeur est encore sur la page, au même endroit : le caviardage laisse fuir ce reste.

**Exposées avec l'application** : mêmes valeurs détectées, mais caviardées avec **toutes** les zones que `_detect_pdf` propose (recherche, repli par boîtes de caractères D-026, propagation) ; une valeur est exposée si l'un de ses caractères reste sur la page. Rapport de l'application : localisation absente, incomplète, rattrapée par le repli.

| Scénario | Thème | Pages | Détectées | Perdues | Taux | Perdues avec saut de ligne | Perdues par type | Partielles | Partielles avec saut de ligne | Partielles par type | Exposées avec l'application | Rapport de l'application (absentes / incomplètes / rattrapées) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| dejavu-sans | aucun | 48 | 231 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
| dejavu-sans | medical | 48 | 254 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
| dejavu-serif | aucun | 48 | 232 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
| dejavu-serif | medical | 48 | 250 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
| dejavu-mono | aucun | 48 | 229 | 0 | 0.0 | 0 | — | 6 | 0 | PERSON 6 | 0 | 0 / 0 / 6 |
| dejavu-mono | medical | 48 | 248 | 0 | 0.0 | 0 | — | 6 | 0 | PERSON 6 | 0 | 0 / 0 / 6 |
| base14-helvetica | aucun | 24 | 113 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
| base14-helvetica | medical | 24 | 120 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
| base14-times | aucun | 24 | 113 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
| base14-times | medical | 24 | 120 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
| majuscules | aucun | 48 | 225 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
| majuscules | medical | 48 | 245 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
| coupure-fin-de-ligne | aucun | 48 | 237 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
| coupure-fin-de-ligne | medical | 48 | 237 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
| colonnes | aucun | 24 | 233 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
| colonnes | medical | 24 | 252 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
| ligatures-serif | aucun | 24 | 191 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
| ligatures-serif | medical | 24 | 202 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
| ligatures-sans | aucun | 24 | 190 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
| ligatures-sans | medical | 24 | 202 | 0 | 0.0 | 0 | — | 0 | 0 | — | 0 | 0 / 0 / 0 |
