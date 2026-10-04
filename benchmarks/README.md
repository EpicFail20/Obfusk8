# Bancs d'essai de l'API texte

> English version: [`README.en.md`](./README.en.md).

Mesures de **qualité** (rappel, précision) et de **latence** de l'API texte de l'extension (`/api/v1/`, voir `docs/api-extension.md`),
toujours **à travers toute la pile** : Traefik, oauth2-proxy, Keycloak, `app` sous le profil seccomp bloquant, `presidio-analyzer`.
Rien n'est mesuré en contournant Traefik.

**Pas de seuil de réussite.** La première exécution fixe la référence ; les exécutions suivantes se comparent à elle. Les familles de
faux négatifs observées sont consignées dans `docs/FINDINGS.md`.

## Contenu

| Répertoire | Rôle |
|---|---|
| `obfusk8_client.py` | Client commun : connexion par le formulaire Keycloak comme un navigateur, appels paramétrés sous la limitation de débit (60/min) |
| `collect_stack_info.sh` | Versions de la pile (images, Presidio, spaCy et modèles, fichiers de thèmes) pour les rapports ; lecture seule |
| `secret_detection/` | Détection des secrets (étape D) : faux positifs sur du texte et du code sans secret, rappel par format |
| `quality/` | Qualité de détection (étape E) : corpus annoté de prompts et variantes adversariales |
| `latency/` | Latence par taille de prompt, et effet croisé avec le flux documents |
| `documents/` | Flux documents (phase 2) : `doc_zones_snapshot.py`, instantané des zones de caviardage proposées par `/api/detect` (PDF, DOCX, CSV, image, chaque thème) et comparaison de deux instantanés (aucune zone de la référence ne doit disparaître) ; `pdf_localization_bench.py`, détections PDF non localisées ou partiellement exposées après caviardage (EXT-35) |
| `results/` | Rapports horodatés (JSON et Markdown) |

## Corpus : comment il est produit, pourquoi il est fictif

Les corpus sont **générés** par `secret_detection/build_secret_corpus.py` et `quality/build_quality_corpus.py`, de façon déterministe
(graine fixe) : la régénération donne un fichier identique octet pour octet.

- Chaque prompt de `quality/corpus.jsonl` est écrit comme une suite de **segments** (texte libre ou entité d'un type connu). Les positions
  `{start, end, type}` sont calculées par concaténation, jamais par recherche dans le texte : l'annotation est exacte par construction.
- **Variantes adversariales** (segment par segment, positions recalculées) : tirets et apostrophes typographiques, espaces insécables
  et fines insécables, caractères de largeur nulle, forme NFD, chiffres espacés (téléphone, IBAN, NIR, carte), noms en majuscules
  (`DURAND Camille`), adresses sur plusieurs lignes, noms en contexte dense (liste de garde sans phrase).
- **Valeurs fictives** :
  - numéros de téléphone **uniquement** dans les plages réservées à la fiction par le plan de numérotation
    (décision Arcep n° 2019-0954, §2.5.12 « Numéros pour œuvres audiovisuelles » : 01 99 00, 02 61 91, 03 53 01, 04 65 71, 05 36 49, 06 39 98) ;
  - domaines de courriel réservés à la documentation (`example.com`, `example.org`, RFC 2606) et un domaine interne sous `.invalid` (RFC 2606) ;
  - noms : prénoms et noms courants combinés au hasard, aucune personne réelle n'est décrite ;
  - IBAN, NIR et numéros de carte générés avec une clé de contrôle valide à partir de chiffres aléatoires (pour que les reconnaisseurs
    qui valident la clé puissent se déclencher) ; les cartes commencent par 4970 ;
  - secrets : exemples des documentations des fournisseurs (`AKIAIOSFODNN7EXAMPLE`, clé de l'émulateur Azurite, jeton d'exemple Telegram)
    ou chaînes épelées `FAKE` / `Fictif`.

## Lancer les mesures

Sur la VM de la pile, avec un compte de test **synthétique** (jamais dans le dépôt) :

```sh
export BENCH_BASE_URL=https://obfusk8.lab.local BENCH_USER=<compte de test> BENCH_PASSWORD=<mot de passe>
export BENCH_INSECURE_TLS=1        # certificat auto-signé du lab
export BENCH_RESOLVE_LOOPBACK=1    # si *.lab.local n'est pas dans /etc/hosts
sh benchmarks/collect_stack_info.sh > /tmp/stack.json
export BENCH_STACK_INFO=/tmp/stack.json
python3 benchmarks/secret_detection/run_secrets_bench.py
python3 benchmarks/quality/run_quality_bench.py      # environ 1 000 requêtes, un peu moins d'une par seconde
python3 benchmarks/latency/run_latency_bench.py
```

Prérequis : `ENABLE_EXTENSION_API=true`, Python 3 avec `requests`. Les mesures écrivent dans le journal d'audit de l'extension
(`audit-extension.log`, métadonnées seulement) ; le banc de latence crée des tâches de révision de documents (CSV synthétique) qui expirent
d'elles-mêmes (`JOB_REVIEW_TTL_SECONDS`), sans finalisation donc sans écriture dans `audit.log`.

## Lire les résultats

- **Strict** : même intervalle et type accepté (`ACCEPTED` dans `run_quality_bench.py`, par exemple une adresse annotée accepte
  `STREET_ADDRESS` ou `LOCATION`).
- **Chevauchement** : les intervalles se recouvrent et le type est accepté.
- **Masquage** (sans type) : la valeur annotée est **entièrement** couverte par l'union des détections, quel que soit leur type.
  C'est la mesure qui compte pour une fuite : une valeur couverte en partie laisse fuir le reste.
- La précision ne porte que sur les types prédits du vocabulaire annoté ; les autres (`ORGANIZATION`, `URL`…) sont listés à part,
  le corpus ne les annotant pas.

Limites : corpus synthétique et de taille modeste (257 prompts), écrit par la même équipe que les reconnaisseurs ; un seul compte de test,
donc un flux de prompts limité à un utilisateur intensif.
