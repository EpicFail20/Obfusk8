# EXT-18 — filtre PROPN : avec_filtre contre sans_filtre (2026-10-04T21:46:28+0000)

Même chaîne que l'application (normalisation, reconnaisseurs communs, seuil par défaut, sans thème), seul l'analyseur change. Masquage : valeur entièrement couverte ; précision : chevauchement. **Rapport, pas de décision** : arbitrage faux positifs contre faux négatifs.

| Corpus | Analyseur | Masquage | Valeurs exposées | Précision | Faux positifs | PERSON exposés | LOCATION exposés |
|---|---|---|---|---|---|---|---|
| main | avec_filtre | 0.940 | 61 | 0.863 | 150 | 20 | 7 |
| main | sans_filtre | 0.940 | 61 | 0.803 | 240 | 20 | 7 |
| supplementary | avec_filtre | 1.000 | 0 | 0.672 | 147 | 0 | 0 |
| supplementary | sans_filtre | 1.000 | 0 | 0.656 | 166 | 0 | 0 |
