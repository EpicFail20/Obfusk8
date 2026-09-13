# Plan d'audit de sécurité — Projet Anonymiseur de documents

**Nature de ce document** : synthèse consolidée de l'ensemble des sessions d'audit menées à ce jour (documents successifs fusionnés en un seul état des lieux, organisé par thème plutôt que par ordre chronologique). L'esprit reste le même que les versions précédentes : chaque point porte un statut honnête (résolu, accepté, encore ouvert), les décisions de risque sont documentées avec leur raisonnement, et les échecs/hypothèses erronées en cours de route sont gardés plutôt qu'effacés — la valeur de ce document tient autant au cheminement qu'au résultat final.

**Note de traçabilité** : une session sur les faux positifs de détection médicale (`medical.json` : allow_list étendue, exclusion `ORGANIZATION`, motif de dossier patient) a eu lieu entre les sessions consolidées ici, mais n'a jamais été documentée dans le plan d'audit à l'époque — son contenu exact (raisonnement, alternatives écartées) n'a pas pu être reconstitué avec confiance pour cette synthèse.

**Mise à jour de cette synthèse** : les sessions qui ont suivi la première consolidation ont été repliées dans les sections concernées ci-dessous — fuite PDF critique trouvée et corrigée (métadonnées + objets orphelins, section 7), campagne de non-régression sur les trois formats (nouvelle section 8), levée du risque XXE (3.3), correctif zip-bomb DOCX par nombre d'entrées (3.2), ReDoS sur les motifs IEP/IPP (3.6), budget de temps de détection et pagination de la page de révision CSV/DOCX (6.6/6.7), Dockerfile examiné directement et `formation.json` confirmé absent (6.9/6.10), mise à jour `lxml` (section 4), retrait du journal de debug devenu inutile (6.5), miniature de document DOCX corrigée et efficacité du caviardage manuel d'image PDF vérifiée empiriquement (section 8). Voir `plan_audit.md` pour le détail chronologique complet, notamment le raisonnement et les fausses pistes écartées en route.

**Périmètre** : stack applicative (Traefik, app FastAPI, Presidio analyzer/anonymizer), hébergement (VM Proxmox/Docker), traitement de fichiers uploadés — **PDF, DOCX et CSV** (les deux derniers ajoutés en cours de route, initialement hors périmètre).

**Hors périmètre (exclu volontairement)** : l'authentification/identité (Keycloak en lab, remplacé par Entra ID en cible) — le protocole OIDC reste inchangé lors du basculement. **Exceptions qui survivent à la bascule Entra sans être remises à plat automatiquement** : durée de session (1.28), isolation réseau (1.29 — voir section 5).

**Non couvert par cette exclusion** : le contrôle d'accès applicatif (ce qu'un utilisateur authentifié a le droit de voir/faire une fois connecté) reste dans le périmètre — Entra changera *qui* se connecte, pas *ce que l'app autorise ensuite*.

**Légende des statuts :** ✅ Résolu et vérifié · 🟡 Risque accepté / reporté / décision documentée · ⏳ Toujours ouvert · 🔴 Critique, en cours de traitement actif

---

## 0. Méthodologie

1. **Revue statique / configuration** — lecture de `docker-compose.yml`, des Dockerfiles, du code, sans rien exécuter d'hostile.
2. **Tests dynamiques ciblés** — scénarios provoqués activement (fichiers malveillants, requêtes forgées) en lab uniquement, jamais avec de vraies données patient.
3. **Test d'intrusion externe (pentest)** avant toute mise en situation réelle — ce plan prépare le terrain, ne le remplace pas.

**Principe renforcé au fil des sessions** : chaque constat marqué comme vérifié doit l'avoir été par un test réel exécuté et son résultat inspecté — pas seulement déduit par lecture de code. Plusieurs bugs réels (voir section 6) n'ont été trouvés qu'en poussant jusqu'à ce niveau de vérification, invisibles à la seule relecture.

---

## 1. Risques techniques — état consolidé

| # | Statut | Risque | État actuel |
|---|---|--------|-----------------|
| 1.1 | ✅ | Secrets en clair dans `.env` | Migré vers Docker secrets, secrets déjà exposés régénérés |
| 1.2 | 🟡 | `/api/audit` sans contrôle de rôle | Reporté à l'amélioration continue — tout utilisateur authentifié peut voir les métadonnées d'audit d'autres utilisateurs |
| 1.3 | 🟡 | Type de fichier validé côté client, falsifiable | Signature binaire vérifiée côté serveur pour le PDF (`%PDF-`) dès l'origine ; **étendu significativement** lors de l'ajout DOCX/CSV (validation de structure ZIP + contenu, voir section 6) |
| 1.4 | 🟡 | Absence de rate limiting applicatif | Rate limiting Traefik en place (5 req/min, burst 10, par IP) — borne le débit, pas l'accumulation. Pas de `slowapi` applicatif |
| 1.5 | ✅ | Gestion d'erreurs fragile | Exceptions PyMuPDF tardives interceptées proprement dans `detect_pdf`/`finalize_pdf`, puis dans `preview_image` (gap trouvé lors d'une revue systématique) ; `/api/audit` protégé (`n` borné, lecture fichier protégée) |
| 1.6 | 🟡 | Pas de scan antivirus/anti-malware | Adaptateur ICAP (RFC 3507) implémenté et testé, branché avant tout parsing PDF/DOCX/CSV — mais **désactivé par défaut** (`AV_ENGINE=none`), en attente d'un serveur ICAP réel côté infrastructure cliente. Voir section 9 |
| 1.7 | 🟡 | Dépendances non tenues à jour | Revue complète Python + non-Python menée (voir section 4) — mais c'était un **audit ponctuel**, pas un processus récurrent (voir 1.13) |
| 1.8 | ✅ | Pas de rotation des logs Docker | Résolu (config `logging` explicite par service, après découverte d'un bug d'ancre YAML non résolu silencieusement par `docker compose` v5.5.0) |
| 1.9 | 🟡 | Single point of failure | Volume `app` migré hors tmpfs, survit à un reboot. Sauvegarde des volumes / VM unique toujours ouvert |
| 1.10 | 🟡 | Pas de supervision/alerting | Métriques Prometheus + alertes syslog implémentées et testées (voir section 10) — mais `ALERT_SINK` reste à `none` par défaut, en attente d'un collecteur SIEM réel côté infrastructure cliente |
| 1.11 | ✅ | Fichier orphelin en cas de crash avant TTL | Résolu, TTL réduits |
| 1.12 | ⏳ | Dimensionnement jamais validé par test de charge réel | Toujours ouvert |
| 1.13 | 🟡 | Patch management / veille CVE sans processus récurrent | **Point le plus persistant du document** — resté ouvert malgré plusieurs audits ponctuels (Python session 3, non-Python session 4). Politique automatisée écrite en fin de parcours (script `pip-audit`+`trivy`, cron) — **non encore validée en conditions réelles**. Illustré concrètement depuis par la découverte d'un correctif `lxml` XXE (voir section 4) publié entre-temps et resté non appliqué faute de veille — la dépendance était épinglée, pas surveillée |
| 1.14 | ✅ | Épuisement mémoire par accumulation de jobs | `MAX_PENDING_JOBS=20` |
| 1.15 | ✅ | Document à très grand nombre de pages/lignes | `MAX_PDF_PAGES=70` ; étendu au DOCX/CSV (`MAX_DOCX_PARAGRAPHS`, `MAX_CSV_ROWS`/`CELLS`) |
| 1.16 | ✅ | Champs de formulaire non bornés | `MAX_MANUAL_ZONES`, `MAX_EXCLUDED_IDS` — complète (sans le remplacer) le correctif Starlette CVE-2024-47874, qui agit à un niveau plus bas |
| 1.17 | ✅ | `doc.close()` non garanti en cas d'exception | `try/finally`, étendu à `preview_image` après qu'un premier passage l'ait oublié |
| 1.18 | 🟡 | Pas de vérification propriétaire au téléchargement | Accepté (TTL court, fichier déjà caviardé) — vaut pour les trois formats désormais |
| 1.19 | ⏳ | Certificat TLS auto-signé en lab | **Jamais traité, à ne pas reporter tel quel en production** |
| 1.20 | ⏳ | Proxy d'inspection TLS potentiel côté réseau hôpital | À vérifier avec l'équipe réseau |
| 1.21 | ✅ | CVE-2024-47874 (Starlette, DoS multipart) | Corrigé via mise à jour FastAPI |
| 1.22 | 🟡 | CVE-2025-62727 (Starlette, DoS `Range`/`FileResponse`) | Corrigé par la même mise à jour ; **vérification active jamais exécutée**, reportée au pentest |
| 1.23 | ✅ | CVE critique `h11` (dépendance uvicorn) | Corrigé via mise à jour uvicorn |
| 1.24 | 🟡 | PDF légèrement corrompu "réparé" silencieusement par PyMuPDF | Limite documentée, pas de correctif évident (comportement de réparation généralement souhaitable par ailleurs) |
| 1.25 | ✅ | `n` non borné + lecture non protégée sur `/api/audit` | `n` clampé [1, 500], `try/except OSError` |
| 1.26 | 🟡 | Race condition étroite sur `/api/download` | Fenêtre de quelques ms entre vérification d'existence et lecture réelle — non corrigé, jugé disproportionné vu la fenêtre extrêmement étroite. Généralisé au multi-format (glob `*-anonymise.*`) sans changer ce constat |
| 1.27 | ✅ | Images Presidio non épinglées (`:latest`) | Épinglées à `2.2.364` |
| 1.28 | ⏳ | Durée de session | Mentionné comme exception au hors-périmètre depuis la session sur l'isolation réseau ; contenu détaillé non retrouvé dans les versions consolidées ici — à reconstituer si besoin |
| 1.29 | ✅ | `app`/`oauth2-proxy`/`keycloak` avaient un accès internet sortant non nécessaire | Résolu par un réseau dédié `app-internal` (`internal: true`) — voir section 5 pour le détail, plus mouvementé que prévu |

---

## 2. Échappement de conteneur

| # | Vecteur | État actuel |
|---|---------|--------------|
| 2.1 | Montage de `/var/run/docker.sock` (Traefik) | ✅ Résolu — remplacé par `docker-socket-proxy` (3 endpoints en lecture seule, réseau dédié isolé) |
| 2.2 | Durcissement des autres conteneurs (non-root, `cap_drop`, `read_only`) | ✅ Appliqué à `app`, `presidio-analyzer`, `presidio-anonymizer`, `traefik`. `keycloak` reste ⏳ (lab uniquement, non prioritaire) |
| 2.3 | Surface d'attaque du parsing PDF | ✅ Couverture partielle — corpus de 7 PDF malformés construit, a mené à la découverte et correction de CVE-2026-3308 (integer overflow via `page.get_pixmap()`) |
| 2.4 | Version Docker/runc/containerd sur l'hôte | ✅ Vérifiée — Docker 29.7.2, runc 1.4.3, aucune CVE non corrigée au moment de l'audit |
| 2.5 | Profil seccomp/AppArmor | ✅ Profil seccomp personnalisé construit et activé **en blocage réel** sur le service `app` (voir détail ci-dessous) |
| 2.6 | Isolation réseau `backend: internal` | En place depuis l'origine — documenté comme défense en profondeur, pas comme solution suffisante seule |

**Détail 2.5 — profil seccomp personnalisé `app`** : remplace le profil par défaut de Docker (~370 syscalls autorisés) par une liste construite à partir des syscalls réellement observés (`strace -f`, service réel, sessions PDF/DOCX/CSV nominales + cas d'erreur + purge différée + arrêt propre + bootstrap `runc` pour conteneur non-root `cap_drop: ALL`), plus une marge documentée pour un chemin de code non exercé pendant la trace (rotation du journal d'audit à 10 Mo). Méthode et raisonnement complets dans `seccomp/README.md`.

Déployé en deux temps, conformément au principe "tester réellement, ne jamais supposer" de ce document : d'abord `app-audit.json` (`SCMP_ACT_LOG`, ne bloque rien, journalise seulement les syscalls hors-liste) rejoué contre **tous** les scénarios de test du projet — trois syscalls journalisés, tous avec repli sans conséquence observable (`io_uring_setup`/`io_uring_enter` : tentative optionnelle d'uvloop, bascule sur epoll ; `openat2` : retombe sur `openat`, déjà autorisé) et déjà absents du profil par défaut de Docker aujourd'hui, donc aucune régression introduite. Confirmé une deuxième fois par un test direct en blocage réel sur le bundle OCI exact du service, hors `docker-compose.yml`. Sur cette base, bascule effective de `docker-compose.yml` sur `app-enforce.json` (`SCMP_ACT_ERRNO`) : cycle démarrage → requête HTTP fonctionnelle → arrêt propre revérifié sans aucune erreur sur le conteneur vivant.

**Limite assumée** : la rotation du journal d'audit à 10 Mo (`rename`/`renameat`/`renameat2`, ajoutés par lecture de `main.py`, pas par observation directe) n'a jamais été atteinte pendant la session de test — seul chemin couvert par marge plutôt que par vérification empirique complète. En cas de besoin légitime après coup (nouveau format de document, mise à jour PyMuPDF/python-docx...), revenir à `app-audit.json` le temps de rejouer les scénarios et d'identifier le syscall manquant via `dmesg | grep 'audit: type=1326'`.

**⏳ Gap découvert en marge de la session antivirus (section 9), non corrigé** : `docker compose run app pytest tests/` échoue systématiquement dans un conteneur utilisant `app-enforce.json` — y compris sur la suite **préexistante** (`test_main_units.py` seul), donc sans rapport avec le code du scan antivirus lui-même (confirmé : les 30 tests, existants + nouveaux, passent tous à 100% hors conteneur). Cause identifiée par lecture du code source de pytest (`_pytest/capture.py`, `FDCaptureBase` utilise `os.dup`/`os.dup2` pour rediriger stdout/stderr pendant la capture) recoupée avec `app-enforce.json`/`app-audit.json` : ni `dup`, ni `dup2`, ni `dup3` ne figurent dans la liste autorisée — l'appli elle-même ne les appelle jamais (absents de la trace `strace` d'origine), mais `pytest`, l'outil de vérification du projet, en a besoin. Concrètement : la suite de tests n'a probablement plus pu être rejouée **dans le conteneur** depuis la bascule en blocage réel (commit `4dc38d5`), sans que ça ait été remarqué faute de l'avoir retenté. **Pas corrigé volontairement** — modifier un profil seccomp est une décision de sécurité, pas quelque chose à trancher seul en cours de revue. Deux options pour l'utilisateur : ajouter `dup`/`dup2`/`dup3` à `app-enforce.json` (cohérent avec la méthode déjà suivie : syscalls réellement nécessaires à un usage légitime) ou accepter de ne plus jamais lancer `pytest` à l'intérieur d'un conteneur utilisant le profil de blocage (hors conteneur, ou temporairement sous `app-audit.json` comme déjà prévu ci-dessus).

---

## 3. Injection

| # | Vecteur | État actuel |
|---|---------|--------------|
| 3.1 | Fichiers PDF malveillants | 🟡 Couverture partielle (voir 2.3) |
| 3.2 | Zip bomb (DOCX) | ✅ Implémenté et complété — taille décompressée totale + ratio de compression par fichier bornés dès l'origine ; la limite résiduelle sur le *nombre* d'entrées (repérée en 6.9) s'est révélée être un **vrai déni de service exploitable** (~1,1s CPU + ~150 Mo mémoire par requête de moins de 24 Mo, sur un service à worker unique — bloque tout le monde) — corrigée par lecture directe du répertoire central du zip (EOCD, `_peek_zip_entry_count`) avant tout appel à `zipfile.ZipFile()`, `MAX_DOCX_ZIP_ENTRIES=5000`, revérifié à ~0s |
| 3.3 | XXE (DOCX) | ✅ **Vérifié empiriquement, non exploitable** — testé sur les trois classes d'attaque classiques (lecture de fichier local, bombe d'entités, SSRF via DTD externe), à la fois sur le parseur lxml isolé et via le pipeline réel de bout en bout sur un `.docx` malveillant : `resolve_entities=False` appliqué de façon homogène sur tout le parsing XML de python-docx neutralise les trois. Limite assumée : seul le chemin DOCX a été testé (seul chemin XML manipulé par le code applicatif) |
| 3.4 | Injection de formule CSV | ✅ Implémenté et testé (`_neutralize_csv_formula`, appliqué à toutes les cellules de sortie) |
| 3.5 | Injection dans le journal d'audit (log injection) | ✅ **Testé empiriquement et corrigé** — voir détail ci-dessous |
| 3.6 | ReDoS (regex des recognizers) | ✅ Testé sur les 6 regex candidats avec des entrées adverses croissantes (100 à 50 000 caractères) : 5 restent linéaires, mais les motifs **IEP/IPP** (`0+\d{7,}`) montrent une croissance quadratique confirmée (2,7s à 20 000 caractères, extrapolé à ~70s à 100 000 — taille de cellule CSV plausible) — corrigé (`0+\d{7,}` → `0\d{7,}`), équivalence fonctionnelle vérifiée, reste linéaire jusqu'à 500 000 caractères |
| 3.7 | En-tête `X-Auth-Request-Email` usurpable si contournement du proxy | ⏳ Jamais retesté activement, reporté au pentest |
| 3.8 | Fichier malveillant (virus/malware) uploadé, indépendamment de tout parsing PDF/DOCX/CSV | 🟡 Scan antivirus ICAP ajouté en amont du parsing, **désactivé par défaut** (`AV_ENGINE=none`) — voir section 9 pour le détail et les réserves |

**Détail 3.5 — injection dans le journal d'audit (`_record_audit_event`)** : seul un champ traverse ce point d'écriture sans être ni un identifiant généré serveur (`job_id`, uuid4 hex) ni contraint à un ensemble connu (`format`, `theme`) ni numérique — le champ `user`, alimenté par l'en-tête HTTP `X-Auth-Request-Email` (voir 3.7 pour la question distincte de savoir qui peut réellement en contrôler la valeur). Reproduction fidèle de `_record_audit_event` testée avec des payloads adverses sur ce champ (retour à la ligne LF/CRLF suivi d'un faux objet JSON, évasion par guillemet/antislash, séquence d'échappement ANSI, octet NUL, valeur de 200 000 caractères) :

- **Forge de fausse ligne / évasion du format JSON : non exploitable, confirmé.** `json.dumps()` échappe systématiquement les caractères de contrôle (C0/C1), guillemets et antislashs — chaque appel produit exactement une ligne, valide, avec la valeur d'origine relue à l'identique (round-trip exact vérifié). Un retour à la ligne réel dans la valeur ne peut donc jamais faire apparaître une deuxième ligne JSON forgée dans `audit.log`.
- **🔴 Un vrai gap trouvé malgré tout : spoofing visuel par caractères de formatage Unicode.** `ensure_ascii=False` laisse passer tel quel tout caractère Unicode valide qui n'est pas un caractère de contrôle C0/C1 — y compris les marqueurs de formatage bidirectionnel (catégorie Unicode `Cf`, ex. **U+202E "Right-to-Left Override"**). Confirmé empiriquement : `attacker@x.com‮مصمم.gpj` en valeur de `user` ressort **tel quel, en clair**, aussi bien dans `audit.log` que dans la réponse JSON de `/api/audit` — de quoi faire afficher ce champ dans un ordre trompeur à quiconque relit le journal (terminal ou UI d'audit), sans jamais casser le format JSON lui-même. Pertinent spécifiquement pour ce projet car la valeur du journal d'audit repose sur sa lisibilité humaine en cas d'investigation.
- **✅ Corrigé à la source** (`_strip_unicode_control_and_format_chars`, `app/main.py`) plutôt qu'au seul niveau de l'écriture JSON : retire tout caractère Unicode de catégorie "Other" (`Cc`/`Cf`/`Co`/`Cs`/`Cn`) de `X-Auth-Request-Email` dès sa lecture dans `detect_document`, avant qu'il ne rejoigne le job puis le journal — couvre par construction `audit.log` **et** `/api/audit`, plutôt qu'un correctif ponctuel à un seul point de sortie. Vérifié : préserve les valeurs légitimes (accents, `+`, `<>`, etc.), neutralise RTL/LRO/les isolats directionnels U+2066-2069/NUL/ESC/CR/LF.
- **Couverture de test permanente ajoutée** (`app/tests/test_audit_log.py`, 23 tests) : les deux couches testées séparément (résistance du format JSON d'un côté, neutralisation Unicode de l'autre) pour qu'une régression sur l'une ne soit jamais masquée par l'autre, plus un test de bout en bout (en-tête → sanitisation → job → journal).

---

## 4. Dépendances

### Revue ponctuelle menée (Python + non-Python)

| Dépendance / composant | Avant | Après | CVE corrigée(s) |
|---|---|---|---|
| PyMuPDF | 1.24.10 | 1.28.2 | CVE-2026-3308 (integer overflow) |
| fastapi | 0.115.0 | 0.141.1 | → Starlette : CVE-2024-47874, CVE-2025-62727 |
| uvicorn[standard] | 0.30.6 | 0.35.0 | → h11 : CVE-2025-43859 (critique) |
| python-multipart | 0.0.9 | 0.0.32 | CVE-2026-40347, -42561, -24486 |
| requests | 2.32.3 | 2.33.1 | CVE-2024-47081, CVE-2026-25645 |
| python-docx / lxml | absents | 1.2.0 / 6.1.3 | Épinglés dès l'introduction ; fraîcheur reconfirmée plus tard sur PyPI — `lxml` avait une version corrective 6.1.3 non appliquée (LP#2165901, résolution d'entité paramètre externe autorisée par défaut), repérée en vérifiant simplement les versions, pas en cherchant une faille. Mise à jour appliquée, testée sans régression (suite de tests + suite XXE + round-trip DOCX). `python-docx` toujours à jour (1.2.0) |
| python-icap | absent | 0.2.0 | Nouvelle dépendance (scan antivirus ICAP, section 9). Vérifiée sur PyPI (JSON + page projet directement, pas seulement un résumé) : `0.2.0` est bien la dernière version publiée (08/07/2026), licence MIT, dépôt GitHub réel avec CI (tests/lint/typecheck/CodeQL). Classificateur PyPI "Development Status :: 3 - Alpha" confirmé — cohérent avec les commentaires déjà présents dans `antivirus.py` (mono-mainteneur, projet jeune ; le protocole ICAP lui-même, RFC de 2003, est stable). 🟡 À resurveiller comme toute dépendance récente (politique de veille, ci-dessus) ; aucune alternative plus mature identifiée qui soit à la fois RFC 3507, pure Python et sans dépendance |
| pytest | 8.4.2 (borné `<9`) | 9.1.1 | PYSEC-2026-1845 (répertoire `/tmp/pytest-of-{user}` prévisible, DoS/élévation locale) — la borne `<9` du fichier de dépendances bloquait elle-même le correctif (9.0.3), pas un oubli de veille. Relevée à `>=9.0.3,<10`, testé sans régression |
| Traefik | — | v3.6.25 → **`v3.7.13`, redéployé** | ✅ à l'époque, CVE historiques déjà corrigées. **Ré-audité plus tard (voir plus bas, "Audit complet des dépendances")** : la ligne 3.6 s'est révélée EOL (dernier correctif 3.6.25), 1 CRITICAL + 3 HIGH publiées depuis et corrigées seulement en 3.7 — dont une "Incorrect Authorization" et un contournement du routage par chemin/middlewares, directement pertinentes puisque Traefik est le seul composant exposé à Internet. Redéployé sur autorisation explicite de l'utilisateur, chaîne complète (routage, redirection OIDC) revérifiée sans erreur |
| docker-socket-proxy | — | v0.4.2 → **`v0.5.0`, redéployé** | 22 CVE fixables (dont 2 CRITICAL OpenSSL/PCRE2) ramenées à 2 par la seule mise à jour d'image — trouvé lors du même audit complet, ce composant n'avait jamais été inclus dans une revue de dépendances jusqu'ici. Redéployé, logs propres, Traefik continue de l'interroger normalement juste après |
| Keycloak | — | 26.0.8 | 🟡 Corrige les CVE connues à l'époque, mais des versions plus récentes existent sur la branche 26.x — non patché, décision à prendre vu le remplacement prévu par Entra. **Réévalué depuis** : 77 CVE fixables dont 3 CRITICAL sur l'image réellement utilisée (`netty`, `bouncycastle` ×2, `keycloak-services`) — la décision devient plus pressante |
| Docker / runc | — | 29.7.2 / 1.4.3 | ✅ Rien à faire |
| presidio-analyzer / anonymizer | `:latest` (non déterminable) | `2.2.364` | Épinglage seul a corrigé un doublon potentiel de CVE-2024-47874, présent indépendamment côté Presidio. **Réaudité depuis** : `2.2.364` reste le dernier tag numérique publié par le projet amont (53/40 CVE fixables en attente d'une reconstruction de leur côté) ; une variante `2.2.364-distroless-preview` existe avec 0 CVE fixable mais base OS différente (Azure Linux), non testée avec le `Dockerfile` custom du projet |

**Découverte notable sur la provenance Presidio** : `ghcr.io/data-privacy-stack/presidio-*` est la continuation officielle du projet Microsoft Presidio sous gouvernance communautaire (confirmé via la documentation Microsoft elle-même) — pas un fork tiers douteux. Le seul vrai problème était l'absence d'épinglage.

**Non retenu** : un article relayant un prétendu "CVE-2026-2978" critique dans FastAPI a été vérifié et écarté (concernait un projet sans rapport) — rappel à garder : toujours recouper une CVE citée par un blog avec une base officielle avant d'agir.

### Politique de veille automatisée (nouveau, en clôture du point 1.13)

Écrite après le constat que la vigilance humaine périodique ne tient pas dans la durée (1.13 resté ouvert malgré plusieurs audits ponctuels successifs) :
- Script (`check_dependencies.sh`) couvrant les paquets Python (`pip-audit`) et les images Docker réellement utilisées, obtenues par introspection dynamique (`docker compose config --images`) plutôt qu'une liste codée en dur.
- Rapport daté par exécution, alerte simple si une vulnérabilité CRITICAL est trouvée.
- Prévu pour tourner en tâche planifiée (exemple de cron fourni, hebdomadaire).

**🟡 Limites assumées** : **non testé en conditions réelles** (écrit sans accès à un vrai Docker/trivy pour le vérifier) ; détecte mais ne corrige pas ; ne scanne pas le contenu du `Dockerfile` du service `app` lui-même pour ses propres CVE de paquets (le Dockerfile a depuis été lu directement pour d'autres propriétés — utilisateur non-root, nombre de workers, voir 6.9 — mais pas intégré au périmètre du script de veille) ; notification minimale (`wall`, à remplacer par un mécanisme fiable) ; pas encore de triage formalisé par exposition réelle (une CVE sur un composant isolé du réseau n'a pas la même urgence qu'une CVE sur `traefik`, seul service exposé). **Ce script lui-même reste introuvable dans le dépôt** (jamais committé) — la vigilance a en revanche été exercée manuellement lors d'une session ultérieure (voir ci-dessous), ce n'est toujours pas un processus récurrent automatisé, mais elle a effectivement tourné en conditions réelles cette fois.

### Audit complet des dépendances (tous les imports du projet), campagne ultérieure

Reprise à la demande explicite de l'utilisateur : vérifier **tous les imports du projet**, pas seulement `app/requirements.txt`. Inventaire complet (paquets Python directs + transitifs résolus, 2 Dockerfiles du projet, 5 images tierces de `docker-compose.yml`) audité avec `pip-audit` (environnement Python réellement installé, pas seulement les épingles déclarées) et `trivy` (chaque image Docker, y compris les deux construites par le projet). Aucun JS/`package.json` dans le dépôt.

**Résultat** : une CVE Python réelle (`pytest`, voir tableau ci-dessus — corrigée), zéro CVE sur les autres paquets Python directs/transitifs. Côté images Docker, la découverte la plus significative concerne **Traefik**, seul composant exposé à Internet du projet : la ligne `v3.6` épinglée s'est révélée **EOL** (dernier correctif publié le 2026-07-31), avec depuis une CVE CRITICAL (réutilisation de connexion NTLM) et plusieurs HIGH touchant directement la sécurité applicative — contournement de sanitisation d'en-tête, "Incorrect Authorization", contournement du routage par chemin/middlewares/logging d'accès — toutes corrigées uniquement dans la ligne 3.7, jamais rétroportées en 3.6. `docker-socket-proxy` (jamais inclus dans une revue de dépendances jusqu'ici malgré son rôle de garde-fou sur l'accès au socket Docker, section 2.1) portait 22 CVE fixables par une simple mise à jour d'image Alpine. Les deux épinglages ont été relevés dans `docker-compose.yml` (`v3.7.13`, `v0.5.0`) — **fichier modifié, service vivant non redémarré**, décision de redéploiement laissée à l'utilisateur vu la sensibilité des composants concernés.

Sur `python:3.12-slim` (base de l'image `app`) : 59 CVE HIGH/CRITICAL au niveau paquets système Debian, mais **aucune ne dispose d'un correctif Debian publié** — état normal d'une distribution récente (trixie), rien d'actionnable tant que l'amont n'a pas rétroporté de correctif. Point de clarification vérifié empiriquement : parmi ces CVE, celle sur `libxml2` (CRITICAL) ne concerne pas le `lxml` réellement utilisé par le code applicatif — le wheel `manylinux` embarque sa propre copie statique de `libxml2`, plus récente et distincte du paquet système, confirmé par inspection directe (`LIBXML_COMPILED_VERSION`, absence de lien dynamique `ldd`).

Keycloak et Presidio, déjà signalés comme décisions en attente dans le tableau ci-dessus, ont vu leur situation précisée mais pas résolue par cet audit — voir le tableau et `plan_audit.md` section 16 pour le détail complet (comparatif par image, raisonnement sur chaque décision).

---

## 5. Isolation de l'accès internet sortant

**Constat** : un attaquant qui compromet un conteneur ne peut rien exfiltrer ni téléporter d'outils supplémentaires si ce conteneur n'a techniquement aucune route vers l'extérieur, même en cas de succès de l'exploitation. Vérifié empiriquement (pas supposé) que `app`, `keycloak` et `oauth2-proxy` avaient un accès internet sortant non nécessaire (`presidio-*` étaient déjà isolés depuis l'origine).

**Solution retenue** : séparation des réseaux plutôt qu'isolation du réseau existant — `frontend` (non-internal, seul `traefik`) et `app-internal` (nouveau, `internal: true` : `app`, `oauth2-proxy`, `keycloak`). Entièrement défini dans `docker-compose.yml`, sans dépendance à une règle pare-feu hôte.

**Limitation Docker découverte en chemin** : un réseau `internal: true` bloque aussi la **publication de ports**, pas seulement l'accès sortant — `traefik` ne peut donc techniquement pas partager ce réseau, d'où la séparation en deux réseaux plutôt qu'une bascule du réseau existant.

**Ce qui reste ouvert** : `traefik` garde un accès internet techniquement possible (conséquence structurelle, partiellement compensé par le durcissement déjà en place). `oauth2-proxy` devra ressortir de `app-internal` au moment de la bascule Entra ID (commentaire laissé dans `docker-compose.yml` à cet effet). **Nouveau point ouvert (scan antivirus, section 9)** : `app` reste uniquement sur `app-internal`/`backend`, tous deux `internal: true` — cohérent tant que `AV_ENGINE=none`, mais si le futur serveur ICAP (infrastructure cliente déjà déployée, hors du réseau Docker de ce projet) est activé, l'appli n'aura techniquement **aucune route** vers lui sans ajouter un réseau dédié ou raccorder le serveur ICAP à un réseau existant — à trancher avant toute activation d'`AV_ENGINE=icap` en conditions réelles, même logique que le point oauth2-proxy/Entra ID ci-dessus.

**Leçon opérationnelle** : un changement de topologie réseau doit être appliqué et vérifié service par service, jamais en recréant plusieurs conteneurs dépendants d'un seul coup — une course au démarrage lors d'une recréation simultanée a produit une fausse piste de diagnostic (DNS) qui s'est résolue d'elle-même une fois l'ordre corrigé.

---

## 6. Extension DOCX/CSV — fuites structurelles et durcissement Presidio

### 6.1 Fuites de données structurelles DOCX — ✅ toutes corrigées et testées

**Constat de fond** : la fonction de parcours d'un `.docx` ne lisait que `paragraph.runs`, qui ne couvre que le texte "visible" en lecture normale. Six zones stockent du texte *ailleurs* dans la structure XML, invisibles à cette API — indépendamment de tout réglage de détection (NER, seuil), contrairement aux problèmes de précision de détection traités par ailleurs.

| Zone | Constat | Correctif |
|---|---|---|
| Suivi des modifications | Texte supprimé (`<w:del>`) reste dans le XML — 0 détection, survivait intégralement au test | Révisions aplaties avant analyse (corps, en-têtes/pieds de page, notes) |
| Métadonnées du document | Auteur, dernier modificateur, commentaire jamais ouverts | Vidés à la finalisation (champs structurés, inutile de les faire passer par le NER) |
| Texte affiché des hyperliens | `paragraph.runs` ne descend pas dans `<w:hyperlink>` | Hyperlien "déplié", texte redevient un run normal analysé comme le reste |
| Cible des hyperliens (URL) | Stockée dans une partie séparée du zip | Relation supprimée avec le lien, pas seulement débranchée |
| Commentaires Word | Partie séparée jamais lue | Retirés entièrement (pas caviardés — un commentaire partiellement masqué révélerait la forme d'un échange interne) |
| Notes de bas de page/de fin | Parties séparées, aucune API haut niveau python-docx | Analysées et caviardées comme le corps (contenu réel destiné au lecteur, contrairement aux zones ci-dessus) |

Validé par un document combinant les 6 zones simultanément, passé dans le pipeline complet réel (pas seulement les fonctions internes). **Bug trouvé pendant la vérification elle-même** : un point d'appel avait été oublié lors du premier correctif, invisible aux tests qui appelaient des fonctions internes plutôt que les vrais points d'entrée — corrigé après avoir refait les tests correctement.

### 6.2 Score de confiance Presidio inefficace pour filtrer le bruit administratif — ✅ traité par un patch du moteur

Le recognizer spaCy de Presidio attribue un score **fixe** (0.85) à toute détection, indépendamment de sa justesse — un seuil ne peut donc jamais distinguer un bon d'un mauvais résultat de ce recognizer. **Une tentative de relever le seuil au-dessus de 0.85 a été testée puis abandonnée** après avoir constaté qu'elle supprimait aussi la détection de vrais noms dans un PDF médical réel — revert immédiat avant tout déploiement plus large.

**Solution retenue** : patch du recognizer lui-même (`spacy_recognizer.py`, surchargé au build) — exige qu'au moins un token de l'empan soit étiqueté nom propre (`PROPN`) par le tagger spaCy déjà actif (zéro coût d'inférence supplémentaire). **Limite découverte en aval** : laisse passer un format de nom compact ("Initiale(s). Nom") — confirmé être une limite préexistante du NER, pas une régression du patch.

### 6.3 Nouveaux reconnaisseurs regex

- `FrenchInitialSurnameRecognizer` (actif quel que soit le thème) — couvre la limite du 6.2. **Compromis assumé** : capture aussi les titres de section numérotés par lettre ("A. Introduction") — accepté délibérément (mieux vaut trop caviarder que rater un vrai nom).
- Numéro de dossier patient étendu et rendu **actif indépendamment du thème sélectionné** (jusque-là seulement dans le thème medical). **Redondance résiduelle non nettoyée** entre le thème et le recognizer intégré en dur.

### 6.4 Bugs de détection structurelle par tableau — trouvés et corrigés

Trois bugs réels trouvés au fil des tests sur documents réels, aucun anticipé à la conception :
1. Faux positif par sous-chaîne (`"nom" in texte` au lieu d'une limite de mot) — un paragraphe contenant "dénomination" faisait caviarder toute une ligne de tableau.
2. Absorption d'une deuxième étiquette dans la valeur de la première, sur une ligne à plusieurs paires étiquette:valeur côte à côte.
3. Tableau à une seule ligne mal classé comme "en-tête" (qui suppose des lignes de données en dessous) — 0 détection utile produite.

Chacun trouvé via un journal de diagnostic temporaire, pas par relecture de code.

### 6.5 Journal de diagnostic temporaire (`ENABLE_DEBUG_LOG`) — historique, retiré depuis

Fonctionnalité lab-only ajoutée pour accélérer le diagnostic des faux positifs/négatifs (une ligne JSON par détection : type, score, source, texte concerné). Deux failles trouvées et corrigées avant la fin de la même session où elle a été introduite :
- **Pas de cloisonnement entre utilisateurs** — n'importe quel utilisateur authentifié pouvait télécharger les détections en clair de n'importe quel autre. ✅ Corrigé : chaque entrée taguée par utilisateur, filtrage à la lecture.
- **Pas d'expiration temporelle** — seulement une rotation par taille. ✅ Corrigé : purge périodique par âge, coordonnée avec le verrou du handler de logging pour éviter toute corruption pendant une écriture concurrente.

**Mise à jour ultérieure : retiré entièrement.** Devenu inutile une fois que la vérification s'est mise à passer systématiquement par des tests réels construits en session (voir section 8) plutôt que par inspection du texte détecté en clair — cohérent avec sa nature délibérément temporaire annoncée dès l'introduction (le journal d'audit, lui, ne stocke que des hashs ; celui-ci stockait du texte en clair, une exception qui ne devait pas survivre au-delà du diagnostic). Code (configuration, écriture, purge, endpoint `/api/debug/log`) et configuration Docker (variables d'environnement, volume) retirés ; suite de tests unitaires revérifiée au vert après reconstruction de l'image.

### 6.6 à 6.11 — Points ouverts issus de cette extension, tous désormais traités

| # | Statut | Constat |
|---|---|---------|
| 6.6 | ✅ | Pas de budget de temps global pour la détection — confirmé exploitable (un CSV à la limite légale de taille tournait ~490s, mesuré empiriquement, bloquant le service à worker unique pour tout le monde) — corrigé (`MAX_DETECTION_SECONDS=90`, `_check_detection_deadline`, vérifié avant chaque page/lot) |
| 6.7 | ✅ | Page de révision CSV sans pagination — confirmé exploitable, distinct de 6.6 (un CSV quasi vide contourne le budget de temps sans jamais dépasser aucun seuil de détection : 419 Ko uploadés → 16,6 Mo de page HTML) — corrigé (`MAX_REVIEW_ROWS=2000`, étendu au DOCX par cohérence). Le caviardage réel n'est jamais réduit, vérifié explicitement au-delà de l'aperçu |
| 6.8 | ✅ | Seuils numériques DOCX/CSV — désormais stress-testés (voir 3.2 zip-bomb par entrées, 3.6 ReDoS, 6.6, 6.7). Les seuils eux-mêmes n'ont pas révélé de faille propre — le problème identifié était l'absence de bornes complémentaires (nombre d'entrées, temps, lignes affichées), maintenant comblées |
| 6.9 | ✅ | `app/Dockerfile` examiné directement (pas déduit) : `USER appuser` (non-root, UID/GID 1000) et pas de `--workers` (un seul worker Uvicorn par défaut) — les deux hypothèses étaient exactes |
| 6.10 | ✅ | `themes/formation.json` recherché sur le système de fichiers et dans tout l'historique git (`git log --all --diff-filter=A`) — introuvable partout, confirmé nettoyé |
| 6.11 | ✅ | Décision produit, pas seulement défensive : `MAX_CSV_CELLS` réduit de 300 000 à 5000, `MAX_DOCX_PARAGRAPHS` aligné de 20 000 à 5000 — un document de cette taille n'est de toute façon jamais réellement relisible avant validation humaine ; réduit la marge de manœuvre de 6.6/6.7 à la racine plutôt qu'en périphérie, les deux restant en place comme filets de sécurité |

---

## 7. PDF — fuites structurelles analogues — ✅ corrigé et vérifié

**Constat, confirmé empiriquement** (et non plus seulement par lecture de code, comme au moment de la première version de cette section) : le même type de trou que celui trouvé et corrigé pour le DOCX (6.1) existait bien côté PDF — plus grave, car présent par défaut dans **tout** appel `doc.save()`, pas seulement dans un cas d'usage particulier (contrairement au suivi des modifications Word, qui suppose l'option activée).

- **Objets orphelins, le constat le plus sérieux** : `page.apply_redactions()` (PyMuPDF) ne supprime pas les anciens objets de contenu de page pré-caviardage, il les déréférence seulement en pointant la page vers un nouvel objet. Sans collecte des objets inutilisés à la sauvegarde (`garbage=0`, le défaut, utilisé sans le savoir par le code existant), ces objets restaient physiquement présents. **Vérifié sur un fichier réel** : 414 objets, seuls 83 atteignables depuis l'arbre de pages courant — les 331 autres incluaient plusieurs flux de contenu complets de l'état pré-caviardage, nom complet du patient, date de naissance et numéro de dossier en clair, extractibles avec une dizaine de lignes de code PyMuPDF (aucun outil spécialisé de forensic nécessaire).
- **Métadonnées** : `doc.save(output_path)` était appelé sans aucune option — `producer`, `creationDate` (date réelle du document source) survivaient intégralement, jamais nettoyées.

**Correctif, testé** : `_wipe_pdf_metadata()` (miroir de la purge des métadonnées DOCX, `set_metadata({})` + `del_xml_metadata()`) et `doc.save(output_path, garbage=4, clean=True, deflate=True)` au lieu d'un `save()` sans option — `garbage=4` purge les objets non référencés (le cœur du correctif). Revérifié via les vrais points d'entrée (`_handle_detect_pdf`/`_finalize_pdf_job`) sur les 77 chaînes de PII réellement extraites d'un document réel : objets atteignables ramenés à 82 (contre 414), balayage exhaustif de **tous** les objets (pas seulement l'arbre de pages, la méthode qui avait révélé la fuite) sans aucune trace résiduelle des identifiants d'origine.

**Point résiduel, diagnostiqué précisément, correction sciemment reportée** : un mot générique ("Laboratoire", `LOCATION`) caviardé sur une seule occurrence sur cinq. Cause réelle, pas un problème de couverture des types d'entité comme d'abord supposé : un empan NER **fusionné à travers un saut de ligne de mise en page** par `page.get_text()` (`"Laboratoire\n \nExemplaire"`), qui casse à la fois la recherche de rects (un second rect caviarde à tort un mot sans rapport) et la propagation inter-pages (la chaîne corrompue ne correspond littéralement à rien sur les autres pages). Correction envisageable (ne garder que le premier segment de ligne) mais **risque de régression symétrique**, identifié et discuté explicitement : tronquer un empan multi-ligne pourrait faire manquer un vrai nom réparti sur deux lignes dans un autre document — arbitrage explicite avec l'utilisateur de ne pas corriger cette session, cohérent avec le principe "ne jamais arbitrer en faveur de moins de faux positifs si ça coûte des faux négatifs" (6.2). Aucun identifiant patient touché (nom, date, numéro de dossier) — seulement un mot d'en-tête générique.

**Faux positif de diagnostic écarté avant correction** : un autre mot d'abord suspecté ("BIOLOGIE", `ORGANIZATION`) s'est révélé déjà couvert par `excluded_entity_types` du thème médical — le premier passage de vérification avait chargé le code sans son dossier `themes/` à côté, donnant un faux diagnostic (aucun thème réellement appliqué) ; corrigé avant d'écrire quoi que ce soit dans le code produit.

---

## 8. Vérification de non-régression multi-format — PDF/DOCX/CSV

Demande explicite : revérifier, pour les trois formats supportés, qu'une donnée personnelle caviardée n'est récupérable "par aucun moyen" dans le fichier "anonymisé" produit. Méthode identique à celle qui a payé pour les fuites DOCX/PDF (6.1, 7) : construire un fixture fictif par format couvrant toutes les zones déjà corrigées, le faire passer par les **vrais points d'entrée**, balayer **exhaustivement** la sortie — pas seulement le texte visible/rendu, mais tous les objets PDF (y compris non référencés par l'arbre de pages courant), toutes les entrées du zip DOCX, tous les octets bruts CSV.

**Résultat : les correctifs tiennent sur les trois formats, zéro fuite résiduelle confirmée sur les données identifiantes testées** (nom, date de naissance, numéro de dossier, coordonnées). Fixtures et scripts conservés (`app/tests/fixtures/`, `verification_scripts/`, voir leur README).

- **PDF** : un vecteur jusqu'ici non testé explicitement — révisions incrémentales cachées (plusieurs `%%EOF`) — vérifié absent. Seule trace résiduelle : le mot "Laboratoire" déjà identifié en section 7, sans rapport avec un identifiant.
- **DOCX** : les 6 zones de 6.1 testées simultanément dans un seul fixture, plus métadonnées identifiantes. Un premier passage a signalé deux fausses fuites, dues à des erreurs de construction du fixture (métadonnées XML dupliquées, numéro de dossier dans un format non supporté par les recognizers) plutôt qu'à un bug applicatif — identifiées et corrigées avant conclusion. Balayage complet des 20 parties du zip de sortie sans fuite ; les zones retirées entièrement (commentaires, suivi des modifications) sont bien absentes du fichier final, pas seulement masquées.
- **CSV** : architecture intrinsèquement plus sûre sur ce point précis — la sortie est entièrement reconstruite cellule par cellule depuis la structure réanalysée (`csv.writer`), jamais une copie patchée de l'original, donc aucun octet ne peut survivre "par accident" comme pour un objet PDF orphelin ou une partie XML DOCX non lue.

**Angles morts DOCX repérés en marge — un traité, un hors périmètre par décision explicite** :
- `docProps/thumbnail.jpeg` — ✅ **corrigé depuis** (`_wipe_docx_thumbnail`, retrait entier de la partie via sa relation package-level `_rels/.rels`). Un `.docx` réellement enregistré par Word (contrairement aux fixtures générés par `python-docx`, qui n'embarquent qu'une image statique du template) pouvait y contenir un rendu réel de la première page en pixels, si l'option "Enregistrer la vignette" a été active — jamais analysé ni retiré avant ce correctif. Revérifié via les vrais points d'entrée : partie absente du zip de sortie, détections/caviardage inchangés, fichier toujours ouvrable.
- Images incrustées simples dans le corps (`<w:drawing>`, ex. une capture d'écran collée dans le texte) — 🟡 **explicitement laissées hors périmètre**, décision produit de l'utilisateur : problème distinct de la miniature, traitement différent, à reprendre séparément. Distinctes de la limite déjà connue (zones de texte, formes, objets incrustés, SmartArt), jamais lues par un pipeline texte-seul.

**Orientation produit actée pour les images (PDF et autres formats)** : pour le moment, leur traitement reste à la charge de l'utilisateur final plutôt qu'automatisé. Condition vérifiée empiriquement pour que cette orientation soit viable : le mécanisme de zones manuelles déjà en place pour le PDF (`_apply_manual_redactions`, combiné au `garbage=4`/`clean=True` de la finalisation) réécrit réellement les pixels d'une zone image sélectionnée — testé avec une image à deux couleurs distinctes, aucune trace de la couleur "caviardée" retrouvée nulle part ailleurs dans le fichier de sortie, y compris en balayant tous les objets image (pas seulement ceux référencés par la page). **Limite constatée** : ce mécanisme n'existe que pour le PDF — le DOCX n'a aucune UI de sélection de zone manuelle équivalente, donc la stratégie n'est déployable que sur ce format pour l'instant.

Pas d'équivalent PDF/CSV à signaler pour la miniature/les zones de texte incrustées : CSV est un format texte pur sans conteneur d'image.

**Redemande explicite (session supervision/alerting) : revérifier, pour chaque format, que les images ne peuvent pas être récupérées après caviardage manuel.** Reconfirmé via le VRAI point d'entrée du projet (`main._apply_manual_redactions`, pas une réimplémentation du mécanisme PyMuPDF comme le script jetable initial) et **promu en test permanent** (`test_zone_manuelle_sur_image_pdf_est_irrecuperable`, `app/tests/test_main_units.py`) plutôt que de rester un script ponctuel — vérifié qu'il détecte bien la régression en cassant volontairement `apply_redactions()` avant de le restaurer. Résultat inchangé pour le PDF : irrécupérable. Pour le DOCX, confirmé au niveau du code (pas seulement du docstring) que le mécanisme n'existe littéralement pas — `_finalize_docx_job()` n'accepte même pas de paramètre `manual_zones`, et le gabarit de révision DOCX/CSV (`_build_text_review_page`) ne contient aucune UI de tracé de zone (contrairement au gabarit PDF) : pas de fausse affordance qui laisserait croire à tort qu'un outil existe.

**Gap trouvé au passage, corrigé** : l'avertissement affiché à l'écran de révision DOCX (`limitation_note`) listait "zones de texte, formes, objets incrustés et SmartArt" comme non analysés, mais **omettait le mot "images"** — alors que le docstring interne de `_iter_docx_paragraphs` le mentionne bien séparément, et que les images (captures d'écran, photos de document scanné) sont le cas le plus probable de PII incrustée. Un utilisateur lisant cet avertissement pouvait raisonnablement ne pas comprendre qu'une image collée dans le corps du texte n'est ni détectée ni caviardable ici. ✅ Corrigé : "images" ajouté explicitement à l'avertissement, avec mention qu'aucun outil de zone manuelle n'existe pour ce format (contrairement au PDF).

**Défense en profondeur ajoutée depuis (session ultérieure)** sur un point distinct de tout ce qui précède — pas une fuite de données mais un risque de déni de service/mémoire corrompue : CVE-2026-3308 (integer overflow MuPDF sur dimensions d'image PDF absurdes, `pdf_load_image_imp`) était déjà corrigée par la mise à jour `PyMuPDF` 1.24.10 → 1.28.2 (section 4), mais sans garde-fou applicatif ni test qui l'exercerait si cette version régressait. `_check_page_images_sane()`/`MAX_IMAGE_PIXELS` (40M px, configurable) ajouté avant l'unique appel restant à `page.get_pixmap()` dans tout le code (`preview_image` — confirmé par recherche exhaustive que la détection et la finalisation PDF ne rendent jamais de pixels) : rejette (400) toute page dont une image déclare une largeur/hauteur nulle, négative ou dépassant le seuil, lu depuis les métadonnées sans décodage. Couvert par 7 cas de test unitaire ajoutés à `app/tests/test_main_units.py` (suite passée de 13 à 20 tests, tous au vert) — un pur calcul, contrairement aux vérifications précédentes de cette catégorie qui nécessitaient un script jetable et un pipeline complet. **Construit et redéployé sur autorisation explicite de l'utilisateur** : image `anonymiseur-app` reconstruite, 20 tests revérifiés au vert dans l'image fraîche puis dans le conteneur vivant après recréation, aucune nouvelle erreur Traefik après redémarrage du service.

---

## 9. Scan antivirus pré-traitement (ICAP) — 🟡 implémenté, désactivé par défaut, réserves levées avant déploiement

**Constat/besoin** : possibilité de brancher un antivirus d'entreprise déjà déployé côté infrastructure cliente, via le protocole standard ICAP (RFC 3507), pour scanner chaque fichier uploadé **avant** tout parsing PDF/DOCX/CSV — une couche indépendante de tout ce qui précède (3.1 à 3.6 protègent contre des payloads *dans* un format supporté, pas contre un exécutable ou un document réellement infecté déposé tel quel).

**Conception** (`app/antivirus.py`) : interface `AntivirusScanner` (`NullScanner` par défaut, `IcapScanner` en implémentation réelle), point de configuration unique (`AV_ENGINE`, `none`/`icap`), politique de blocage découplée (`AV_ENFORCE`, indépendante du moteur — permet un déploiement en observation avant blocage réel, même logique que le mode `SCMP_ACT_LOG` avant blocage pour le profil seccomp). **Fail-closed cohérent avec le principe déjà établi sur ce projet** : un moteur injoignable (`AntivirusUnavailableError`) ou un verdict défavorable bloquent tous deux par défaut (503/400), jamais un verdict "propre" par défaut en cas de doute. Testé (`app/tests/test_antivirus.py`, 10 tests via le client ICAP simulé de la bibliothèque — fichier propre, menace détectée, EICAR, timeout, connexion refusée, verdicts indépendants entre fichiers séquentiels, comportement d'`AV_ENFORCE`) : **30/30 tests du projet (20 existants + 10 nouveaux) passés en environnement non conteneurisé** avant déploiement.

**Revue de sécurité menée avant mise à jour de cet audit** (demande explicite de l'utilisateur : failles introduites, exceptions non gérées, fraîcheur de la dépendance, cohérence seccomp) — quatre points trouvés et corrigés avant déploiement :

1. **Fuite de confidentialité** — le nom de fichier brut (potentiellement une donnée patient) était journalisé en clair dans les deux branches d'avertissement de `_run_antivirus_scan`, à rebours de la convention déjà en place partout ailleurs dans le projet (`filename_hash` uniquement — voir 3.5). ✅ Corrigé : `filename_hash` calculé avant l'appel au scanner, seul le hash apparaît désormais dans les logs ; le nom brut n'est transmis qu'au moteur de scan lui-même.
2. **Exception non gérée** — `get_scanner()` pouvait lever `RuntimeError` (`ICAP_HOST` manquant) ou `ValueError` (moteur inconnu, `ICAP_PORT`/`ICAP_TIMEOUT_SECONDS` non numériques), non interceptées par `_run_antivirus_scan` : une mauvaise configuration aurait cassé silencieusement **chaque requête** `/api/detect` en 500 générique, sans détection avant la mise en production. ✅ Corrigé : `get_scanner()` appelé une fois au démarrage (`lifespan`), échec rapide et explicite si la config est invalide, plutôt qu'à la première requête venue.
3. **Bruit de logs** — `get_scanner()` était reconstruit à chaque requête et rejouait l'avertissement "scan désactivé" à chaque upload tant qu'`AV_ENGINE=none`, avec un risque de noyer de vrais événements sous la rotation des logs applicatifs (10 Mo/3 fichiers). ✅ Corrigé : scanner mis en cache (`lru_cache`), avertissement émis une seule fois au démarrage — vérifié dans les logs du conteneur vivant après redéploiement.
4. **Tests mal placés** — `test_antivirus.py` était à la racine de `app/` plutôt que dans `app/tests/` (convention du projet) et n'était donc pas copié dans l'image (`Dockerfile` ne copie que `tests/`), invisible au flux `pytest tests/` utilisé pour vérifier l'image. ✅ Corrigé : déplacé vers `app/tests/test_antivirus.py`.

**Dépendance `python-icap==0.2.0`** : vérifiée directement sur PyPI (métadonnées JSON + page projet, pas seulement un résumé) — dernière version publiée (08/07/2026), licence MIT, dépôt GitHub réel avec CI (tests/lint/typecheck/CodeQL). Statut PyPI "Alpha" confirmé, cohérent avec les réserves déjà notées dans le code (mono-mainteneur, projet jeune ; le protocole ICAP lui-même est stable, RFC de 2003). Voir aussi section 4.

**Découverte en marge, sans rapport avec le code applicatif ajouté** : tenter de vérifier ces correctifs via `docker compose run app pytest tests/` a révélé que **la suite de tests entière** (pas seulement les nouveaux tests) crashe dans un conteneur utilisant le profil seccomp de blocage réel (`app-enforce.json`) — `dup`/`dup2`/`dup3`, nécessaires à la capture de sortie de pytest, absents de la liste autorisée. Confirmé sans rapport avec cette fonctionnalité (30/30 tests passent hors conteneur). Voir détail et options en section 2.5 — laissé ouvert, une décision seccomp revient à l'utilisateur.

**Cohérence réseau** : signalée comme point ouvert en section 5 (réseaux `app-internal`/`backend` tous deux `internal: true`, sans route sortante — à réconcilier avec un futur `ICAP_HOST` externe au réseau Docker avant toute activation réelle).

**Déployé** : image `anonymiseur-app` reconstruite avec les quatre correctifs, testée hors conteneur (30/30), conteneur recréé (`docker compose up -d app`), démarrage confirmé propre (thèmes chargés, avertissement "AV_ENGINE=none" affiché **une seule fois**, `/health` répond `ok`). **`AV_ENGINE` reste à `none`** — aucun changement de comportement pour les utilisateurs tant qu'il n'est pas explicitement activé avec un `ICAP_HOST` réel.

---

## 10. Supervision et alerting (1.10) — 🟡 implémenté, syslog inactif par défaut

**Constat/besoin** : jusqu'ici, aucune visibilité opérationnelle au-delà des journaux applicatifs locaux — un dépôt Presidio injoignable, un disque plein, un certificat proche de l'expiration ou une menace antivirus détectée (section 9) ne remontaient nulle part en dehors de `docker logs`. Deux briques ajoutées, sur le même principe d'architecture que l'antivirus ICAP : un point de configuration unique, un comportement sûr par défaut, actif seulement sur configuration explicite.

**Métriques (`app/metrics.py`)** : exposition Prometheus standard sur `/metrics` (bibliothèque `prometheus_client`, aucun système d'adaptateurs nécessaire — contrairement à l'antivirus/alerting — le format d'exposition suffit à couvrir la quasi-totalité des outils d'entreprise). Compteurs par catégorie fermée uniquement (`format`, `reason`, `entity_type`, `verdict`, `volume`, `service`) — jamais de nom de fichier, d'email ou d'identifiant de job en étiquette, à la fois pour la confidentialité et pour éviter l'explosion de cardinalité côté serveur de métriques. Couvre : documents traités/rejetés par format et par raison, entités caviardées par type, jobs en attente de révision, durée de détection par format, disponibilité Presidio (analyzer/anonymizer), verdict antivirus, espace disque libre par volume surveillé.

**Alerting (`app/supervision.py`)** : interface `AlertSink` générique (`NullAlertSink` par défaut — journalise localement sans jamais perdre l'information silencieusement — `SyslogAlertSink` en implémentation réelle, RFC 5424 via `logging.handlers.SysLogHandler` de la bibliothèque standard, aucune dépendance externe). Déclenché sur : antivirus indisponible ou menace détectée (CRITICAL), Presidio injoignable (WARNING), espace disque sous deux seuils cumulatifs — pourcentage ET valeur absolue (WARNING sous 10%/500 Mo, CRITICAL sous 5%/100 Mo), le plus restrictif des deux l'emportant pour rester pertinent aussi bien sur un petit volume que sur un très gros. **Même règle que pour les logs d'audit (3.5)** : jamais de donnée personnelle dans une alerte, une alerte partant potentiellement vers un SIEM tiers hors du contrôle direct du projet — seul `filename_hash` apparaît, jamais le nom de fichier brut.

**Testé** : 14 tests (`test_metrics.py`, `test_supervision.py`) — `test_supervision.py` via un vrai récepteur syslog UDP local (pas un simulacre), `test_metrics.py` d'après l'API stable de `prometheus_client`, les deux vérifiés avec succès dans un venv externe où la dépendance était déjà installée (réseau toujours indisponible dans l'environnement principal de développement pour l'installer autrement). **67/67 tests du projet passés** après intégration (aucune régression sur les suites PDF/DOCX/CSV/antivirus existantes).

**Revue de sécurité et gestion des erreurs menée avant mise à jour de cet audit** (même exercice que pour le scan antivirus, section 9 — demande explicite de l'utilisateur) — quatre points trouvés et corrigés :

1. **🔴 Une alerte défaillante pouvait casser le flux qu'elle est censée surveiller** — `get_alert_sink().send(...)` était appelé directement, sans filet, à 5 endroits (dont `_run_antivirus_scan`, sur le chemin **synchrone de `/api/detect`**). Une config `ALERT_SINK` invalide, un DNS injoignable ou une connexion TCP refusée levaient une exception **avant** le `HTTPException` 503/400 pourtant déjà géré proprement — transformant un rejet antivirus propre en 500 générique non intercepté. Pire : le même appel dans `_cleanup_sweep_loop` (thread de fond, aucun `try/except` autour du corps de boucle) **tuait le thread silencieusement et pour toujours** — plus aucun nettoyage des fichiers orphelins (1.11) ni des jobs expirés (1.14) jusqu'au redémarrage du conteneur, sans qu'aucune trace ne le signale. Confirmé en reproduisant volontairement l'échec (`get_alert_sink` remplacé par une fonction qui lève) : les deux tests de non-régression échouent bien sans le correctif, passent avec. ✅ Corrigé : nouvelle fonction `_send_alert()` dans `main.py`, point de passage unique qui intercepte toute exception et journalise un avertissement plutôt que de la laisser remonter ; boucle de fond également enveloppée dans un `try/except` de précaution, indépendant de ce correctif, pour survivre à un futur bug non lié à l'alerting.
2. **Fuite mémoire/FD sans borne** — `get_alert_sink()` n'était pas mis en cache (contrairement à `get_scanner()` pour l'antivirus, dont le docstring de `get_alert_sink()` prétendait pourtant suivre "le même principe") : chaque alerte reconstruisait un `SyslogAlertSink` complet, donc un nouveau socket et un nouveau logger nommé (`anonymiseur.supervision.syslog.<id>`) **jamais nettoyé** par le module `logging` (les loggers nommés restent enregistrés indéfiniment). Amplifiable par un attaquant capable de déclencher des alertes à volonté (ex. uploads détectés comme menace en boucle). ✅ Corrigé : `@lru_cache(maxsize=1)` sur `get_alert_sink()`. Propriété vérifiée par test : `lru_cache` ne mémorise jamais une exception, donc une config valide reste mise en cache pour de bon, tandis qu'un hôte syslog temporairement injoignable est retenté à l'appel suivant plutôt que de rester cassé pour toujours (`test_get_alert_sink_reessaie_apres_un_echec`).
3. **Incohérence avec le patron déjà établi pour l'antivirus** — `get_scanner()` est validé une fois au démarrage (`lifespan`, échec rapide et fatal) ; `get_alert_sink()` ne l'était pas du tout, une mauvaise config `ALERT_SINK` ne se découvrant qu'au premier événement déclenchant une alerte, potentiellement bien après la mise en production. ✅ Corrigé, avec une nuance assumée par rapport à l'antivirus : appelé au démarrage lui aussi, mais **jamais fatal** — une supervision mal configurée ne doit pas empêcher le service principal (anonymisation de documents, fonction critique) de démarrer, contrairement à un contrôle de sécurité comme l'antivirus qui, lui, peut légitimement bloquer le démarrage.
4. **Spoofing visuel par caractère de formatage Unicode, même classe que 3.5** — `threat` (nom de menace remonté par le scanner ICAP, `X-Virus-ID`/`X-Infection-Found`) rejoignait tel quel l'alerte syslog, le journal applicatif et la réponse HTTP au client, sans passer par `_strip_unicode_control_and_format_chars` (le correctif déjà appliqué à `X-Auth-Request-Email` en 3.5). Confiance moyenne sur l'exploitabilité réelle : dans un flux ICAP conforme, ce nom vient du moteur antivirus (base de signatures), pas du contenu du fichier uploadé — mais rien dans le code ne garantit qu'un serveur ICAP non conforme ou compromis ne pourrait pas y injecter du texte arbitraire. ✅ Assaini par précaution, même principe défensif que pour toute donnée texte externe au projet destinée à être relue par un humain (vérifié par test avec un caractère RTL override U+202E).

**67/67 → 73/73 tests** après ces quatre correctifs (4 nouveaux tests de non-régression ciblés + 2 tests de comportement du cache, en plus des 14 déjà existants).

**Exposition `/metrics` et Traefik** : un scraper Prometheus ne peut pas passer par la dance OAuth d'`oidc-auth` — mais Traefik reste exposé côté Internet (réseau `frontend` non-`internal`), donc pas question de laisser l'endpoint ouvert à quiconque pour autant. Router Traefik dédié `app-metrics`, protégé par `ipallowlist` plutôt que par l'authentification applicative, restreint à `127.0.0.1/32` par défaut (donc inaccessible depuis l'extérieur tant que ce n'est pas explicitement ouvert à l'adresse réelle du collecteur Prometheus une fois celui-ci déployé).

**Reste ouvert** : `ALERT_SINK=none` par défaut (aucune alerte transmise hors journaux locaux, seulement journalisée) — à passer à `syslog` avec un `SYSLOG_HOST` réel dès qu'un collecteur SIEM est disponible côté infrastructure cliente, même logique que `AV_ENGINE=none` (section 9) : le composant est prêt, l'activation attend l'infrastructure réelle. Le seuil d'espace disque et la vérification de santé Presidio tournent dans la même boucle de fond que le nettoyage des jobs expirés (`_cleanup_sweep_loop`, intervalle 60s) — pas encore de test de charge réel validant ce dimensionnement (voir 1.12, toujours ouvert).

---

## 11. Tests différés au pentest final

- Vérification active du correctif `FileResponse`/`Range` (1.22) — jamais exécutée
- Abus de la surface HTTP côté utilisateur authentifié (Burp, forced browsing, falsification de paramètres)
- Retest du point 3.7 (en-tête `X-Auth-Request-Email`) une fois `/api/audit` corrigé (1.2)

---

## 12. Synthèse finale

### Priorités actuelles, par ordre d'impact

1. ~~Redéployer les mises à jour `traefik`/`docker-socket-proxy`~~ — **fait** : redéployés sur autorisation explicite de l'utilisateur, chaîne complète revérifiée. Au passage, le conteneur `app` vivant s'est révélé tourner sur une image ancienne (antérieure à toutes les corrections récentes) — redéployé aussi. Reste ouvert : nouvel avertissement Traefik 3.7 sur `aliasHeadersStrategy`, potentiellement pertinent pour la confiance accordée à `X-Auth-Request-Email` (3.7), à examiner
2. **Décider du sort de Keycloak** — 77 CVE fixables dont 3 CRITICAL découvertes en scannant l'image réellement utilisée (section 4) ; patcher vers 26.7.x ou accepter le risque jusqu'au remplacement par Entra, arbitrage devenu plus pressant qu'avant
3. **Un vrai travail de mesure de la qualité de détection PII** sur corpus varié — jamais mené de façon systématique malgré plusieurs mentions. Le bug "Laboratoire" (section 7) et les angles morts DOCX (section 8) en illustrent encore l'importance
4. **Certificat TLS de confiance** avant toute préproduction (1.19)
5. **Le pentest externe final** (section 11)
6. **Processus récurrent de veille CVE** (1.13) — toujours une passe manuelle, pas un automatisme ; deux illustrations concrètes de son coût maintenant disponibles (`lxml` en 2026, obsolescence de `traefik:v3.6` découverte lors de l'audit complet des dépendances, section 4)
7. Traitement des images incrustées DOCX/PDF (section 8) — hors périmètre par décision produit explicite, traitement séparé à concevoir ; inclut la construction d'un mécanisme de zone manuelle équivalent au PDF pour le DOCX, qui n'existe pas aujourd'hui
8. Presidio : évaluer la variante `-distroless-preview` (0 CVE fixable, base OS différente non testée) ; `spacy`/`pyyaml` non épinglés dans `presidio/analyzer-build/Dockerfile` (gap de reproductibilité, pas de risque actif) — section 4
9. ~~Reconstruire et redéployer l'image `anonymiseur-app` avec le garde-fou `_check_page_images_sane`/`MAX_IMAGE_PIXELS`~~ — **fait**, sur autorisation explicite de l'utilisateur (20/20 tests unitaires revérifiés dans le conteneur vivant, aucune régression Traefik)
10. ~~Implémenter un scan antivirus pré-parsing (1.6)~~ — **fait** (adaptateur ICAP, section 9), redéployé après correction de 4 réserves trouvées en revue. **Reste à trancher par l'utilisateur** : activer réellement `AV_ENGINE=icap` suppose (a) un serveur ICAP joignable côté infrastructure cliente et (b) de réconcilier son adressage avec l'isolation réseau actuelle (section 5, réseaux `app-internal`/`backend` sans sortie)
11. **Décider du sort de la découverte seccomp `dup`/`dup2`/`dup3`** (section 2.5) — `pytest` ne peut plus être exécuté à l'intérieur d'un conteneur utilisant `app-enforce.json` depuis la bascule en blocage réel ; sans impact sur l'application en production (jamais appelés par le code applicatif), mais bloque la vérification en conditions réelles telle que documentée dans `seccomp/README.md`
12. ~~Implémenter la supervision/alerting (1.10)~~ — **fait** (métriques Prometheus + alertes syslog, section 10), redéployé après correction de 4 réserves trouvées en revue de sécurité (alerte défaillante pouvant casser le flux principal ou tuer silencieusement le thread de nettoyage, fuite mémoire/FD par absence de cache, incohérence avec le patron antivirus, spoofing Unicode du nom de menace) — 73/73 tests au total. **Reste à trancher par l'utilisateur** : activer réellement `ALERT_SINK=syslog` suppose un collecteur SIEM réel côté infrastructure cliente, et l'IP autorisée à scraper `/metrics` (`ipallowlist`, actuellement `127.0.0.1/32`) devra être ouverte à l'adresse du serveur Prometheus une fois celui-ci déployé

### Leçons méthodologiques cumulées

- **Tester réellement, ne jamais supposer** — vaut pour le code (un bug d'intégration réel est passé inaperçu à travers des tests qui appelaient des fonctions internes plutôt que les vrais points d'entrée) autant que pour l'infrastructure (l'isolation réseau a réservé plusieurs surprises que la seule lecture de la documentation Docker n'aurait pas révélées).
- **Un changement de topologie doit être appliqué service par service**, jamais en recréant plusieurs conteneurs dépendants d'un coup.
- **Toujours recouper une CVE citée par une source tierce avec une base officielle** avant d'agir dessus.
- **Une revue ponctuelle n'est pas un processus** — plusieurs points (dépendances, qualité de détection) ont été "traités" au sens d'un audit à un instant T, sans que ça empêche la question de ressurgir plus tard faute de mécanisme récurrent.
- **Un faux positif se corrige en un clic en révision ; un faux négatif part inaperçu** — principe qui a guidé plusieurs arbitrages (score de détection, nouveaux recognizers, décision de ne pas corriger le bug "Laboratoire" en section 7) : ne jamais réduire les faux positifs au prix de faux négatifs.
- **Un point marqué ⏳ "à tester" ne doit jamais être lu comme "probablement bénin"** — sur quatre points ouverts testés en s'attendant a priori à les clore sans rien trouver (zip-bomb par nombre d'entrées, budget de temps, pagination de révision, ReDoS), les quatre se sont révélés être de vrais bugs exploitables, contre un seul "non exploitable, confirmé" (le XXE). Le seul moyen de savoir est de construire le payload et de mesurer.
- **Une convention de sécurité établie (ici : ne jamais journaliser de nom de fichier brut) ne se maintient pas toute seule** — un nouveau point d'entrée de code (le scan antivirus, section 9) l'a violée sans intention, simplement en suivant un réflexe de logging ordinaire ; seule une relecture ciblée l'a repérée avant déploiement.
- **L'outil de vérification du projet doit lui-même être testé sous les mêmes contraintes que le service qu'il vérifie** — le profil seccomp de blocage réel (section 2.5) a été validé contre l'application, jamais recontrôlé contre `pytest` après la bascule ; le premier a tenu, le second casse (`dup`/`dup2`/`dup3` manquants), et personne ne l'aurait su sans retenter concrètement `docker compose run app pytest`.
- **"Échapper le JSON" et "neutraliser les caractères dangereux" ne sont pas la même garantie** (3.5) — `json.dumps` empêche totalement la forge d'une fausse ligne de log (contrôle C0/C1, guillemets, antislashs tous échappés), mais laisse passer intact tout caractère Unicode "valide" au sens du format, y compris les marqueurs de formatage bidirectionnel (RTL override) qui n'ont aucune vocation à apparaître dans une valeur comme un email. Un format de sortie sûr contre l'injection structurelle n'est pas automatiquement sûr contre le spoofing visuel d'un journal destiné à être lu par un humain.

---

*Document de travail — à mettre à jour au fur et à mesure des corrections apportées et des points levés lors de la revue avec le RSSI/DPO.*
