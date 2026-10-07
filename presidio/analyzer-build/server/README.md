# Serveur REST de Presidio Analyzer (copie amont)

*English summary below.*

Ces fichiers ne sont **pas** de nous : ce sont les fichiers du serveur REST de Presidio Analyzer, copiés **sans modification** depuis
l'étiquette `2.2.364` du dépôt amont (`https://github.com/data-privacy-stack/presidio`, répertoire `presidio-analyzer/`), sous licence
MIT (`LICENSE`, copiée de la même étiquette). Ils ne sont publiés ni dans la roue ni dans l'archive source de `presidio-analyzer` sur
PyPI ; l'image publiée par le projet les plaçait dans `/app`. Depuis la phase « Python » (D-052 point 1), l'analyseur est construit sur
notre propre base Python et la bibliothèque vient de la roue PyPI (avec empreinte, `requirements.lock`) : ces trois fichiers sont la
seule partie de Presidio copiée dans le dépôt.

| Fichier | SHA-256 (identique à l'étiquette `2.2.364` et à l'image `ghcr.io/data-privacy-stack/presidio-analyzer:2.2.364`) |
|---|---|
| `app.py` | `b6cc79eccbade1e6886a6eb5b79c307872e3e32f2e674cebc34e458c47185ca5` |
| `logging.ini` | `00788ea22b0bbe6052c2e28fe63cc9178a2752d2b8664f58f8681db09018f2df` |
| `entrypoint.sh` | `52e05a2bda2413429665b13bdcd9fb7d7fc80be61b5c99073070d3a5e3b0c653` |

**À chaque mise à jour de Presidio** (condition de D-052 point 1) : recopier ces fichiers depuis la nouvelle étiquette, les comparer à
la version précédente (`git diff`), lire toute différence, mettre à jour ce tableau. Procédure : `docs/maintenance-dependances.md`.

---

**English summary.** Unmodified copies of Presidio Analyzer's REST server files (`app.py`, `logging.ini`, `entrypoint.sh`, MIT license)
from upstream tag `2.2.364`. They are not shipped on PyPI; the analyzer image is built on our own Python base with the library installed
from its hash-pinned PyPI wheel. On every Presidio update, copy them again from the new tag, review the diff, and update the table above
(`docs/dependency-maintenance.md`).
