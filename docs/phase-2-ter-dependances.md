# Phase 2 ter — dépendances de production en retard (préparation)

Décision humaine du 2026-10-05 (D-041, point 3) : ces mises à jour se feront dans une phase dédiée, **sous seccomp** ; rien n'est appliqué
ici. Vérifié à la source le 2026-10-05 : API JSON de PyPI (dernière version, date), notes de version GitHub (ruptures), OSV (vulnérabilités).
Aucune version en place n'a de vulnérabilité connue (OSV, `pip-audit` de l'image) : ces mises à jour relèvent de l'entretien, pas d'un correctif.

| Paquet | Image | Version actuelle | Dernière stable vérifiée | Ruptures annoncées (notes de version) | Risque pour le projet |
|---|---|---|---|---|---|
| uvicorn (`[standard]`) | app | 0.35.0 | 0.54.0 (2026-09-25) | 0.36.1 : `Config.setup_event_loop()` retiré (lève une exception) ; 0.40.0 : Python 3.9 abandonné ; 0.49.0 : httptools ≥ 0.8.0 ; 0.50.0 : implémentation `websockets` historique dépréciée, `auto` passe à `websockets-sansio` ; 0.50.2 : websockets ≥ 13.0 ; 0.51.0 : colorama retiré de `[standard]` ; 0.53.0 : HTTP/2 expérimental (`zttp`) et intégration `zuvloop`, toutes deux facultatives | **Élevé** : boucle d'événements et sélection d'uvloop sous seccomp (EXT-11). Rejouer la suite, les bouts en bout et une trace `app-audit.json` (nouveaux appels système possibles) |
| fastapi | app | 0.141.1 | 0.142.2 (2026-09-30) | Aucune rupture d'API dans les notes de 0.142.0 à 0.142.2 (documentation, bannière, sponsors) ; dépendance `starlette>=0.46.0`, satisfaite par 1.7.0 (dernière) | Faible |
| requests | app | 2.33.1 | 2.34.2 (2026-05-14) | 2.34.0 : `usedforsecurity=False` sur les hachages Digest Auth, prise en charge de Python 3.15 ; 2.34.1 : type de l'argument `headers` (`MutableMapping`, `None` retiré des annotations) | Faible (appels vers l'analyseur et l'antivirus) |
| websockets | app (tiré par `uvicorn[standard]`) | 17.1 | 17.2 (2026-10-03) | Aucune rupture annoncée | Faible ; l'application n'utilise pas de WebSocket |
| pydantic-core | app | 2.46.5 | 2.49.0 (2026-09-09) | — | **Non applicable seul** : pydantic 2.13.5 (dernière) exige `pydantic-core==2.46.5` ; suivra une future version de pydantic |
| spaCy | analyseur (fournie par l'image Presidio 2.2.364) | 3.8.13 | 3.8.16 (2026-08-24) | 3.8.15 : dépendance `click` ajoutée (Typer a cessé de la fournir) ; Presidio exclut 3.8.14 sous Python 3.14 seulement | **Change la détection** possible : banc de qualité, secrets et zones avant et après ; changerait aussi `versions.json` et donc `detection_config` |

Méthode proposée pour la phase 2 ter : une mise à jour par commit ; pour chacune, régénération de `app/requirements.lock` (même méthode, en-tête
du fichier), construction sans cache, `app/run-tests.sh`, trace sous `app-audit.json` pour uvicorn, bouts en bout (`e2e_document_flow.py`,
fichiers finaux compris), bancs de latence et de qualité, `trivy` et `pip-audit` ; mise à jour de `docs/DEPENDENCIES.md`.
