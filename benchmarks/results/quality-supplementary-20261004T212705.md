# Banc d'essai — qualité de détection de l'API texte (2026-10-04T21:27:05+0000)

Configuration `b65f237ebbbbfc78`, reconnaisseurs de l'analyseur `51fa36bee769596e`, langue `fr`, thèmes compta, it, medical. Corpus supplementary : 252 prompts, 252 entités annotées. Versions de la pile : voir le JSON (`stack`).

Pas de seuil de réussite : cette exécution fixe la référence.

## Vue d'ensemble

| Thème | Strict P / R / F1 | Chevauchement P / R / F1 | Masquage (rappel, sans type) | Valeurs exposées |
|---|---|---|---|---|
| aucun | 0.622 / 1.000 / 0.767 | 0.743 / 1.000 / 0.853 | 1.000 | 0 |
| compta | 0.622 / 1.000 / 0.767 | 0.743 / 1.000 / 0.853 | 1.000 | 0 |
| it | 0.622 / 1.000 / 0.767 | 0.743 / 1.000 / 0.853 | 1.000 | 0 |
| medical | 0.622 / 1.000 / 0.767 | 0.743 / 1.000 / 0.853 | 1.000 | 0 |

## Thème : aucun

| Type | Strict P / R / F1 | Chevauchement P / R / F1 | Masquage (couvertes / exposées) |
|---|---|---|---|
| ADDRESS | 0.271 / 1.000 / 0.426 | 0.639 / 1.000 / 0.780 | 36 / 0 |
| CARD | 0.522 / 1.000 / 0.686 | 0.522 / 1.000 / 0.686 | 36 / 0 |
| DATE | 1.000 / 1.000 / 1.000 | 1.000 / 1.000 / 1.000 | 108 / 0 |
| IBAN | 1.000 / 1.000 / 1.000 | 1.000 / 1.000 / 1.000 | 36 / 0 |
| NIR | 0.947 / 1.000 / 0.973 | 0.947 / 1.000 / 0.973 | 36 / 0 |

| Variante | Masquage (rappel) | Exposées |
|---|---|---|
| adresse_code_postal | 1.000 | 0 |
| carte_espace | 1.000 | 0 |
| date_lettres_en | 1.000 | 0 |
| date_lettres_fr | 1.000 | 0 |
| date_tiret_typographique | 1.000 | 0 |
| iban_espace | 1.000 | 0 |
| nir_espace | 1.000 | 0 |

Types prédits hors référentiel (non comptés comme faux positifs) : IP_ADDRESS 2, ORGANIZATION 2, MEDICAL_LICENSE 2.

## Thème : compta

| Type | Strict P / R / F1 | Chevauchement P / R / F1 | Masquage (couvertes / exposées) |
|---|---|---|---|
| ADDRESS | 0.271 / 1.000 / 0.426 | 0.639 / 1.000 / 0.780 | 36 / 0 |
| CARD | 0.522 / 1.000 / 0.686 | 0.522 / 1.000 / 0.686 | 36 / 0 |
| DATE | 1.000 / 1.000 / 1.000 | 1.000 / 1.000 / 1.000 | 108 / 0 |
| IBAN | 1.000 / 1.000 / 1.000 | 1.000 / 1.000 / 1.000 | 36 / 0 |
| NIR | 0.947 / 1.000 / 0.973 | 0.947 / 1.000 / 0.973 | 36 / 0 |

| Variante | Masquage (rappel) | Exposées |
|---|---|---|
| adresse_code_postal | 1.000 | 0 |
| carte_espace | 1.000 | 0 |
| date_lettres_en | 1.000 | 0 |
| date_lettres_fr | 1.000 | 0 |
| date_tiret_typographique | 1.000 | 0 |
| iban_espace | 1.000 | 0 |
| nir_espace | 1.000 | 0 |

Types prédits hors référentiel (non comptés comme faux positifs) : IP_ADDRESS 2, ORGANIZATION 2, MEDICAL_LICENSE 2.

## Thème : it

| Type | Strict P / R / F1 | Chevauchement P / R / F1 | Masquage (couvertes / exposées) |
|---|---|---|---|
| ADDRESS | 0.271 / 1.000 / 0.426 | 0.639 / 1.000 / 0.780 | 36 / 0 |
| CARD | 0.522 / 1.000 / 0.686 | 0.522 / 1.000 / 0.686 | 36 / 0 |
| DATE | 1.000 / 1.000 / 1.000 | 1.000 / 1.000 / 1.000 | 108 / 0 |
| IBAN | 1.000 / 1.000 / 1.000 | 1.000 / 1.000 / 1.000 | 36 / 0 |
| NIR | 0.947 / 1.000 / 0.973 | 0.947 / 1.000 / 0.973 | 36 / 0 |

| Variante | Masquage (rappel) | Exposées |
|---|---|---|
| adresse_code_postal | 1.000 | 0 |
| carte_espace | 1.000 | 0 |
| date_lettres_en | 1.000 | 0 |
| date_lettres_fr | 1.000 | 0 |
| date_tiret_typographique | 1.000 | 0 |
| iban_espace | 1.000 | 0 |
| nir_espace | 1.000 | 0 |

Types prédits hors référentiel (non comptés comme faux positifs) : IP_ADDRESS 2, ORGANIZATION 2, MEDICAL_LICENSE 2, PRIVATE_IP_SUFFIX 1.

## Thème : medical

| Type | Strict P / R / F1 | Chevauchement P / R / F1 | Masquage (couvertes / exposées) |
|---|---|---|---|
| ADDRESS | 0.271 / 1.000 / 0.426 | 0.639 / 1.000 / 0.780 | 36 / 0 |
| CARD | 0.522 / 1.000 / 0.686 | 0.522 / 1.000 / 0.686 | 36 / 0 |
| DATE | 1.000 / 1.000 / 1.000 | 1.000 / 1.000 / 1.000 | 108 / 0 |
| IBAN | 1.000 / 1.000 / 1.000 | 1.000 / 1.000 / 1.000 | 36 / 0 |
| NIR | 0.947 / 1.000 / 0.973 | 0.947 / 1.000 / 0.973 | 36 / 0 |

| Variante | Masquage (rappel) | Exposées |
|---|---|---|
| adresse_code_postal | 1.000 | 0 |
| carte_espace | 1.000 | 0 |
| date_lettres_en | 1.000 | 0 |
| date_lettres_fr | 1.000 | 0 |
| date_tiret_typographique | 1.000 | 0 |
| iban_espace | 1.000 | 0 |
| nir_espace | 1.000 | 0 |

Types prédits hors référentiel (non comptés comme faux positifs) : IP_ADDRESS 2, MEDICAL_LICENSE 2.
