# Plan d'audit de sécurité — Projet Anonymiseur de documents

**Nature de ce document** : synthèse consolidée de l'ensemble des sessions d'audit menées à ce jour (documents successifs fusionnés en un seul état des lieux, organisé par thème plutôt que par ordre chronologique). L'esprit reste le même que les versions précédentes : chaque point porte un statut honnête (résolu, accepté, encore ouvert), les décisions de risque sont documentées avec leur raisonnement, et les échecs/hypothèses erronées en cours de route sont gardés plutôt qu'effacés — la valeur de ce document tient autant au cheminement qu'au résultat final.

**Note de traçabilité** : une session sur les faux positifs de détection médicale (`medical.json` : allow_list étendue, exclusion `ORGANIZATION`, motif de dossier patient) a eu lieu entre les sessions consolidées ici, mais n'a jamais été documentée dans le plan d'audit à l'époque — son contenu exact (raisonnement, alternatives écartées) n'a pas pu être reconstitué avec confiance pour cette synthèse.

**Mise à jour de cette synthèse** : les sessions qui ont suivi la première consolidation ont été repliées dans les sections concernées ci-dessous — fuite PDF critique trouvée et corrigée (métadonnées + objets orphelins, section 7), campagne de non-régression sur les trois formats (nouvelle section 8), levée du risque XXE (3.3), correctif zip-bomb DOCX par nombre d'entrées (3.2), ReDoS sur les motifs IEP/IPP (3.6), budget de temps de détection et pagination de la page de révision CSV/DOCX (6.6/6.7), Dockerfile examiné directement et `formation.json` confirmé absent (6.9/6.10), mise à jour `lxml` (section 4), retrait du journal de debug devenu inutile (6.5). Voir `plan_audit.md` pour le détail chronologique complet, notamment le raisonnement et les fausses pistes écartées en route.

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
| 1.6 | ⏳ | Pas de scan antivirus/anti-malware | Toujours absent |
| 1.7 | 🟡 | Dépendances non tenues à jour | Revue complète Python + non-Python menée (voir section 4) — mais c'était un **audit ponctuel**, pas un processus récurrent (voir 1.13) |
| 1.8 | ✅ | Pas de rotation des logs Docker | Résolu (config `logging` explicite par service, après découverte d'un bug d'ancre YAML non résolu silencieusement par `docker compose` v5.5.0) |
| 1.9 | 🟡 | Single point of failure | Volume `app` migré hors tmpfs, survit à un reboot. Sauvegarde des volumes / VM unique toujours ouvert |
| 1.10 | ⏳ | Pas de supervision/alerting | Toujours absent |
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
| 2.5 | Profil seccomp/AppArmor | 🟡 Partiel — `no-new-privileges` partout, pas de profil personnalisé |
| 2.6 | Isolation réseau `backend: internal` | En place depuis l'origine — documenté comme défense en profondeur, pas comme solution suffisante seule |

---

## 3. Injection

| # | Vecteur | État actuel |
|---|---------|--------------|
| 3.1 | Fichiers PDF malveillants | 🟡 Couverture partielle (voir 2.3) |
| 3.2 | Zip bomb (DOCX) | ✅ Implémenté et complété — taille décompressée totale + ratio de compression par fichier bornés dès l'origine ; la limite résiduelle sur le *nombre* d'entrées (repérée en 6.9) s'est révélée être un **vrai déni de service exploitable** (~1,1s CPU + ~150 Mo mémoire par requête de moins de 24 Mo, sur un service à worker unique — bloque tout le monde) — corrigée par lecture directe du répertoire central du zip (EOCD, `_peek_zip_entry_count`) avant tout appel à `zipfile.ZipFile()`, `MAX_DOCX_ZIP_ENTRIES=5000`, revérifié à ~0s |
| 3.3 | XXE (DOCX) | ✅ **Vérifié empiriquement, non exploitable** — testé sur les trois classes d'attaque classiques (lecture de fichier local, bombe d'entités, SSRF via DTD externe), à la fois sur le parseur lxml isolé et via le pipeline réel de bout en bout sur un `.docx` malveillant : `resolve_entities=False` appliqué de façon homogène sur tout le parsing XML de python-docx neutralise les trois. Limite assumée : seul le chemin DOCX a été testé (seul chemin XML manipulé par le code applicatif) |
| 3.4 | Injection de formule CSV | ✅ Implémenté et testé (`_neutralize_csv_formula`, appliqué à toutes les cellules de sortie) |
| 3.5 | Injection dans le journal d'audit (log injection) | ⏳ Jamais testé activement |
| 3.6 | ReDoS (regex des recognizers) | ✅ Testé sur les 6 regex candidats avec des entrées adverses croissantes (100 à 50 000 caractères) : 5 restent linéaires, mais les motifs **IEP/IPP** (`0+\d{7,}`) montrent une croissance quadratique confirmée (2,7s à 20 000 caractères, extrapolé à ~70s à 100 000 — taille de cellule CSV plausible) — corrigé (`0+\d{7,}` → `0\d{7,}`), équivalence fonctionnelle vérifiée, reste linéaire jusqu'à 500 000 caractères |
| 3.7 | En-tête `X-Auth-Request-Email` usurpable si contournement du proxy | ⏳ Jamais retesté activement, reporté au pentest |

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
| Traefik | — | v3.6.25 | ✅ Rien à faire, CVE historiques déjà corrigées ou hors provider utilisé |
| Keycloak | — | 26.0.8 | 🟡 Corrige les CVE connues à l'époque, mais des versions plus récentes existent sur la branche 26.x — non patché, décision à prendre vu le remplacement prévu par Entra |
| Docker / runc | — | 29.7.2 / 1.4.3 | ✅ Rien à faire |
| presidio-analyzer / anonymizer | `:latest` (non déterminable) | `2.2.364` | Épinglage seul a corrigé un doublon potentiel de CVE-2024-47874, présent indépendamment côté Presidio |

**Découverte notable sur la provenance Presidio** : `ghcr.io/data-privacy-stack/presidio-*` est la continuation officielle du projet Microsoft Presidio sous gouvernance communautaire (confirmé via la documentation Microsoft elle-même) — pas un fork tiers douteux. Le seul vrai problème était l'absence d'épinglage.

**Non retenu** : un article relayant un prétendu "CVE-2026-2978" critique dans FastAPI a été vérifié et écarté (concernait un projet sans rapport) — rappel à garder : toujours recouper une CVE citée par un blog avec une base officielle avant d'agir.

### Politique de veille automatisée (nouveau, en clôture du point 1.13)

Écrite après le constat que la vigilance humaine périodique ne tient pas dans la durée (1.13 resté ouvert malgré plusieurs audits ponctuels successifs) :
- Script (`check_dependencies.sh`) couvrant les paquets Python (`pip-audit`) et les images Docker réellement utilisées, obtenues par introspection dynamique (`docker compose config --images`) plutôt qu'une liste codée en dur.
- Rapport daté par exécution, alerte simple si une vulnérabilité CRITICAL est trouvée.
- Prévu pour tourner en tâche planifiée (exemple de cron fourni, hebdomadaire).

**🟡 Limites assumées** : **non testé en conditions réelles** (écrit sans accès à un vrai Docker/trivy pour le vérifier) ; détecte mais ne corrige pas ; ne scanne pas le contenu du `Dockerfile` du service `app` lui-même pour ses propres CVE de paquets (le Dockerfile a depuis été lu directement pour d'autres propriétés — utilisateur non-root, nombre de workers, voir 6.9 — mais pas intégré au périmètre du script de veille) ; notification minimale (`wall`, à remplacer par un mécanisme fiable) ; pas encore de triage formalisé par exposition réelle (une CVE sur un composant isolé du réseau n'a pas la même urgence qu'une CVE sur `traefik`, seul service exposé).

---

## 5. Isolation de l'accès internet sortant

**Constat** : un attaquant qui compromet un conteneur ne peut rien exfiltrer ni téléporter d'outils supplémentaires si ce conteneur n'a techniquement aucune route vers l'extérieur, même en cas de succès de l'exploitation. Vérifié empiriquement (pas supposé) que `app`, `keycloak` et `oauth2-proxy` avaient un accès internet sortant non nécessaire (`presidio-*` étaient déjà isolés depuis l'origine).

**Solution retenue** : séparation des réseaux plutôt qu'isolation du réseau existant — `frontend` (non-internal, seul `traefik`) et `app-internal` (nouveau, `internal: true` : `app`, `oauth2-proxy`, `keycloak`). Entièrement défini dans `docker-compose.yml`, sans dépendance à une règle pare-feu hôte.

**Limitation Docker découverte en chemin** : un réseau `internal: true` bloque aussi la **publication de ports**, pas seulement l'accès sortant — `traefik` ne peut donc techniquement pas partager ce réseau, d'où la séparation en deux réseaux plutôt qu'une bascule du réseau existant.

**Ce qui reste ouvert** : `traefik` garde un accès internet techniquement possible (conséquence structurelle, partiellement compensé par le durcissement déjà en place). `oauth2-proxy` devra ressortir de `app-internal` au moment de la bascule Entra ID (commentaire laissé dans `docker-compose.yml` à cet effet).

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

**🟡 Angles morts DOCX repérés en marge, non exploitables avec les fixtures actuels** (statut "non confirmé comme fuite", à garder à l'œil plutôt qu'à traiter dans l'urgence) :
- `docProps/thumbnail.jpeg` : présent dans le zip de sortie, jamais touché par le pipeline. Un `.docx` réellement enregistré par Word (contrairement aux fixtures générés par `python-docx`, qui n'embarquent qu'une image statique du template) peut contenir un rendu réel de la première page en pixels, si l'option "Enregistrer la vignette" a été active — jamais analysé ni retiré.
- Images incrustées simples dans le corps (`<w:drawing>`, ex. une capture d'écran collée dans le texte) : distinctes de la limite déjà connue (zones de texte, formes, objets incrustés, SmartArt), jamais lues par un pipeline texte-seul.

Pas d'équivalent PDF/CSV à signaler : les images PDF ont déjà été vérifiées (logos de petite taille, pas de scan pleine page dans le fixture réel utilisé), CSV est un format texte pur sans conteneur d'image.

---

## 9. Tests différés au pentest final

- Vérification active du correctif `FileResponse`/`Range` (1.22) — jamais exécutée
- Abus de la surface HTTP côté utilisateur authentifié (Burp, forced browsing, falsification de paramètres)
- Retest du point 3.7 (en-tête `X-Auth-Request-Email`) une fois `/api/audit` corrigé (1.2)

---

## 10. Synthèse finale

### Priorités actuelles, par ordre d'impact

1. **Un vrai travail de mesure de la qualité de détection PII** sur corpus varié — jamais mené de façon systématique malgré plusieurs mentions ; devenu le point le plus impactant maintenant que les fuites structurelles PDF/DOCX (6.1, 7) et le risque XXE (3.3) sont résolus. Le bug "Laboratoire" (section 7) et les angles morts DOCX (section 8) en illustrent encore l'importance
2. **Validation du script de veille dépendances** sur la VM réelle (section 4) — pour que la clôture du point 1.13 soit complète, pas seulement écrite ; la découverte `lxml` (section 4) montre concrètement le coût de son absence
3. **Certificat TLS de confiance** avant toute préproduction (1.19)
4. **Le pentest externe final** (section 9)
5. Décider du sort de Keycloak (patcher un composant temporaire, ou assumer le risque vu le remplacement prévu)
6. Angles morts DOCX non exploités par les fixtures actuels (section 8 : miniature de document, images incrustées) — non confirmés comme fuites, à surveiller

### Leçons méthodologiques cumulées

- **Tester réellement, ne jamais supposer** — vaut pour le code (un bug d'intégration réel est passé inaperçu à travers des tests qui appelaient des fonctions internes plutôt que les vrais points d'entrée) autant que pour l'infrastructure (l'isolation réseau a réservé plusieurs surprises que la seule lecture de la documentation Docker n'aurait pas révélées).
- **Un changement de topologie doit être appliqué service par service**, jamais en recréant plusieurs conteneurs dépendants d'un coup.
- **Toujours recouper une CVE citée par une source tierce avec une base officielle** avant d'agir dessus.
- **Une revue ponctuelle n'est pas un processus** — plusieurs points (dépendances, qualité de détection) ont été "traités" au sens d'un audit à un instant T, sans que ça empêche la question de ressurgir plus tard faute de mécanisme récurrent.
- **Un faux positif se corrige en un clic en révision ; un faux négatif part inaperçu** — principe qui a guidé plusieurs arbitrages (score de détection, nouveaux recognizers, décision de ne pas corriger le bug "Laboratoire" en section 7) : ne jamais réduire les faux positifs au prix de faux négatifs.
- **Un point marqué ⏳ "à tester" ne doit jamais être lu comme "probablement bénin"** — sur quatre points ouverts testés en s'attendant a priori à les clore sans rien trouver (zip-bomb par nombre d'entrées, budget de temps, pagination de révision, ReDoS), les quatre se sont révélés être de vrais bugs exploitables, contre un seul "non exploitable, confirmé" (le XXE). Le seul moyen de savoir est de construire le payload et de mesurer.

---

*Document de travail — à mettre à jour au fur et à mesure des corrections apportées et des points levés lors de la revue avec le RSSI/DPO.*
