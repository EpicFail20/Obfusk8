# Backlog — amélioration continue de la détection

Document de travail (D-039) : la qualité de détection n'est plus un préalable aux phases suivantes. Chaque point indique l'état **mesuré**,
la piste proposée et un coût estimé ; **rien n'est implémenté ici**. Les décisions humaines déjà prises restent valables et sont citées.

Rappel de la doctrine (`CLAUDE.md` §0.1) : un faux négatif est plus grave qu'un faux positif. Toute piste est mesurée **avant et après** avec
le banc de qualité (`benchmarks/quality/`), le banc des secrets et le banc du flux documents, et ne doit jamais réduire le masquage pour gagner
en précision. La révision humaine obligatoire et l'avertissement à l'utilisateur couvrent les faux négatifs résiduels.

Coût : **S** (motif ou réglage, moins d'une journée, banc compris) · **M** (quelques jours, code et bancs) · **L** (changement d'approche,
nouvelle dépendance ou revalidation seccomp).

| # | Sujet | État mesuré | Piste proposée | Coût | Décision |
|---|---|---|---|---|---|
| 1 | **EXT-46 — noms tapés en minuscules** (style messagerie, surtout l'API texte) | 20/36 noms couverts sans le filtre PROPN, 15/36 avec (`benchmarks/results/propn-*-20261005T07*.json`, famille `noms_minuscules`) | Pour l'analyse seulement (longueur conservée), mettre en capitale les mots minuscules précédés d'un indice (« à », « relancer », « dis à »…) ou présents dans une liste de prénoms ; propagation des noms confirmés (D-012) | M | À prendre |
| 2 | **EXT-18 — filtre PROPN** du correctif spaCy | Corpus principal : masquage 0,940 avec et sans filtre, 150 faux positifs avec contre 240 sans ; corpus complémentaire 1,000 dans les deux cas ; noms en minuscules : **5 noms perdus** par le filtre (15/36 contre 20/36) | Décision définitive après le point 1 (une mise en capitale rendrait le filtre neutre sur cette famille) ; mesurer sur le jeu indépendant (point 8) | S (mesure) | D-027 : conservé à titre provisoire |
| 3 | **EXT-32 — noms en contexte dense** (listes de garde, formulaires) | NER seul 44/64 noms des invites exposées ; NER + motif « titre ou fonction + Prénom Nom » 57/64, 0 correspondance hors nom sur 257 invites ; les 7 restants relèvent d'EXT-19 (noms anglais) | Reconnaisseur commun, liste hospitalière (Dr, Pr, Mme, M., IDE, AS, cadre, interne…), sondes ReDoS au plafond, faux positifs mesurés sur les documents de référence | S | D-032 |
| 4 | **EXT-41 — valeur coupée par un retour à la ligne dans un PDF** | 3 téléphones exposés en PDF, exactement les 3 coupés par un retour à la ligne ; 0 en DOCX et CSV | Pour l'analyse, joindre par une espace les sauts de ligne internes à un bloc PyMuPDF (positions conservées) ; test sur le PDF final (chaque morceau caviardé sur chaque ligne) ; risques : cellules de tableau jointes, « Jean-\nPierre » | M | D-029 |
| 5 | **EXT-42 — secrets dans les documents** | 6 SECRET exposés sur 6 dans le banc du flux documents (DOCX, CSV, PDF) ; par conception (D-015) les reconnaisseurs de secrets ne servent qu'à l'API texte | Les appliquer à tous les flux (référence unique, comme EXT-08/EXT-23), après mesure des faux positifs sur un corpus synthétique de documents ordinaires (code, configurations, comptes rendus) | M | D-028 |
| 6 | **EXT-40 — CSV à une seule colonne reconnue** | Non mesuré ; comportement documenté dans le code (deux en-têtes reconnus requis pour le masquage structurel) | Accepter un seul en-tête reconnu quand la ligne a la forme d'un en-tête (aucune valeur détectée), mesuré sur le banc CSV | S | À prendre |
| 7 | **Libellé lors d'un chevauchement** (décision 5 du 2026-10-05) | Précision par type CARD 0,28 : les chiffres espacés d'un NIR ou d'un IBAN sont aussi typés « carte » ; masquage correct | Ordre de priorité fixe et documenté (SECRET, NIR, IBAN, carte, téléphone…) dans `_merge_overlaps` ; la zone reste l'union des détections | S | D-031 |
| 8 | **Jeu de test indépendant** (`BENCH_CORPUS=holdout`) | Banc prêt (D-038) ; `~/obfusk8-holdout.jsonl` absent au 2026-10-05 | Fournir le fichier (hors dépôt), puis rejouer après chaque changement de détection ; seuls des chiffres agrégés sont rapportés | S (une fois le fichier fourni) | D-038 |

## Points liés, hors détection proprement dite

- **D-030** (version de la normalisation dans l'empreinte `detection_config`) : traité en phase 2 bis, étape G.
- **EXT-19** (analyse toujours en français, noms anglais manqués) : changement de langue d'analyse, coût L (second modèle, mémoire de
  l'analyseur).
- **Latence** (D-033) : objectif absolu p95 < 100 ms sous 2 000 caractères ; chaque reconnaisseur ajouté par ce backlog est mesuré.
