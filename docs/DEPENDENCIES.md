# Traçabilité des dépendances

Registre tenu selon `CLAUDE.md` §6 : toute dépendance ajoutée, mise à jour ou touchée y figure, avec la dernière version stable observée
**à la source** au moment du travail, le résultat d'audit et la licence. Licence du projet : en-têtes et README annoncent l'AGPL-3.0,
le fichier `LICENSE` contient la GPL-3.0 (EXT-05) ; toutes les licences ci-dessous (MIT, Apache-2.0) sont compatibles avec l'une et l'autre.

## Phase 1 — API texte (2026-10-02)

### Production (image `app`)

**Aucune dépendance de production ajoutée ni mise à jour.** Le nouveau code (`app/text_api.py`, `app/text_api_models.py`) utilise directement
deux paquets déjà présents dans l'image comme dépendances de FastAPI ; ils sont listés ici parce qu'ils sont désormais « touchés ».
Aucune revalidation du profil seccomp n'est donc due à une dépendance (le nouveau chemin de code a été testé sous `app-enforce.json`).

| Paquet | Version dans l'image | Dernière stable observée (PyPI, 2026-10-02) | Audit | Licence | Remarque |
|---|---|---|---|---|---|
| anyio | 4.15.1 | 4.15.1 (publiée le 2026-09-05) | OSV : aucune vulnérabilité | MIT | Dépendance de Starlette ; `CapacityLimiter`, `to_thread`, `fail_after` |
| pydantic | 2.13.5 | 2.13.5 (publiée le 2026-08-28) | OSV : aucune vulnérabilité | MIT | Dépendance de FastAPI ; modèles stricts du contrat |
| pydantic-core | 2.46.5 | — (épinglée par pydantic) | OSV : aucune vulnérabilité | MIT | |

Audit de l'environnement installé dans l'image reconstruite (`pip-audit --path /usr/local/lib/python3.12/site-packages`, service OSV,
37 distributions) : **une seule** distribution vulnérable, `pip` 25.0.1, fournie par l'image de base `python:3.12-slim`
(12 identifiants PYSEC, corrigés à partir de 26.2) — préexistante, hors périmètre, consignée en EXT-24.

### Développement (jamais dans l'image)

Fichiers : `app/requirements-dev.in` (versions de premier niveau) et `app/requirements-dev.txt` (verrou complet, 44 paquets, empreintes SHA-256,
généré par `pip install --dry-run --report` avec le Python 3.12 de l'image ; méthode en tête du fichier). Installation uniquement dans un
environnement isolé : `pip install --require-hashes --no-deps -r requirements-dev.txt` (décision D-016).

| Paquet | Version retenue | Dernière stable observée (PyPI, 2026-10-02) | Audit | Licence | Remarque |
|---|---|---|---|---|---|
| ruff | 0.16.10 | 0.16.10 (2026-10-01) | pip-audit (OSV) : aucune vulnérabilité sur le verrou complet | MIT | Lint et format, fichiers nouveaux seulement |
| mypy | 2.4.0 | 2.4.0 (2026-10-01) | idem | MIT | Strict sur les modules nouveaux |
| bandit | 1.9.4 | 1.9.4 (2026-02-25) | idem | Apache-2.0 | |
| pytest | 9.1.1 | 9.1.1 (2026-06-19) | idem | MIT | Même version que celle présente dans l'image (EXT-09) |
| pytest-cov | 7.1.0 | 7.1.0 (2026-03-21) | idem | MIT | |
| coverage | 7.16.2 | 7.16.2 (2026-09-27) | idem | Apache-2.0 | Sa base SQLite échoue sous `app-enforce.json` : couverture mesurée sans seccomp, suite fonctionnelle sous seccomp |
| pip-audit | 2.10.1 | 2.10.1 (2026-06-10) | idem | Apache-2.0 | |
| regex | 2026.7.10 | 2026.9.29 (2026-09-29) — **non retenue** (D-017) | OSV : aucune vulnérabilité pour 2026.7.10 | Apache-2.0 AND CNRI-Python | Même version que dans `presidio-analyzer`, pour tester les motifs avec le même moteur |

Sources : API JSON de PyPI (`https://pypi.org/pypi/<paquet>/json`, champ `info.version`, hors préversions), API OSV (`https://api.osv.dev/v1/query`).

### Outils d'analyse exécutés hors dépôt (étape F)

| Outil | Version retenue | Dernière stable observée (2026-10-02) | Source | Remarque |
|---|---|---|---|---|
| trivy | 0.75.0, image `aquasec/trivy@sha256:af6acf9a6b85dfe389a1941505c0ce9efef52a4719635e1a962f022a3d855daa` | 0.75.0 (publiée le 2026-10-01) | API GitHub des versions d'aquasecurity/trivy ; condensat par `docker buildx imagetools inspect` | Conteneur jetable, mémoire limitée à 800 Mo, cache et archive d'image sur disque (pas dans le tmpfs `/tmp`, voir EXT-25). Résultat : EXT-33 |
