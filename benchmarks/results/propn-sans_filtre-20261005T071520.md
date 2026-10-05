# EXT-18 — filtre PROPN : avec_filtre contre sans_filtre (2026-10-05T07:15:20+0000)

Même chaîne que l'application (normalisation, reconnaisseurs communs, seuil par défaut, sans thème), seul l'analyseur change. Masquage : valeur entièrement couverte ; précision : chevauchement. **Rapport, pas de décision** : arbitrage faux positifs contre faux négatifs.

| Corpus | Analyseur | Masquage | Valeurs exposées | Précision | Faux positifs | PERSON exposés | LOCATION exposés |
|---|---|---|---|---|---|---|---|
| main | avec_filtre | 0.940 | 61 | 0.863 | 150 | 20 | 7 |
| main | sans_filtre | 0.940 | 61 | 0.803 | 240 | 20 | 7 |
| supplementary | avec_filtre | 0.927 | 21 | 0.679 | 150 | 21 | 0 |
| supplementary | sans_filtre | 0.944 | 16 | 0.668 | 170 | 16 | 0 |

Masquage par famille du corpus complémentaire (dont `noms_minuscules`, D-027) :

| Famille | avec_filtre | sans_filtre |
|---|---|---|
| adresse_code_postal | 36/36 | 36/36 |
| carte_espace | 36/36 | 36/36 |
| date_lettres_en | 36/36 | 36/36 |
| date_lettres_fr | 36/36 | 36/36 |
| date_tiret_typographique | 36/36 | 36/36 |
| iban_espace | 36/36 | 36/36 |
| nir_espace | 36/36 | 36/36 |
| noms_minuscules | 15/36 | 20/36 |
