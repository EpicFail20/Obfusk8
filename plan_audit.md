# Plan d'audit de sécurité — Projet Anonymiseur de documents

**Dernière mise à jour :** session 10 — retrait complet du journal de debug (`ENABLE_DEBUG_LOG`, endpoint `/api/debug/log`) introduit en session 7 (voir 9.5). Devenu inutile maintenant que la vérification se fait par tests réels construits en session (méthode des sections 11 à 14) plutôt que par inspection a posteriori du texte détecté en clair — et c'était de toute façon prévu comme temporaire, jamais destiné à survivre en production. Retiré de `app/main.py` (configuration, `_write_debug_log`, `_sweep_debug_log`, les deux points d'appel, l'endpoint) et de `docker-compose.yml` (variables d'environnement, volume `/data/debug`). Suite de tests unitaires (13 tests) revérifiée au vert après reconstruction de l'image. Résiduel hors dépôt signalé : le répertoire hôte `/var/log/anonymiseur-debug` devient orphelin, à retirer manuellement au prochain déploiement. Voir note dans 9.5.

**Session 9** — deux volets. (1) Campagne de non-régression demandée explicitement par l'utilisateur : re-vérifier, pour les **trois formats (PDF, DOCX, CSV)**, qu'un document caviardé ne contient plus aucune trace récupérable des données originales, "par aucun moyen". Fixtures fictifs dédiés construits pour chaque format (conservés dans `app/tests/fixtures/`, scripts dans `verification_scripts/`, voir leur README), passés par les vrais points d'entrée, sortie balayée exhaustivement (tous les objets PDF, toutes les entrées du zip DOCX, tous les octets bruts CSV). **Résultat : les corrections des sessions 7 et 8 tiennent, zéro fuite résiduelle sur les données identifiantes testées.** Deux nouveaux angles non couverts jusqu'ici repérés en marge (images DOCX : miniature de document et images incrustées dans le corps) — non exploitables avec les fixtures actuels, signalés comme angle mort à garder à l'œil, pas comme fuite confirmée. Voir section 11. (2) Reprise du point ouvert 9.6.2 (risque XXE non vérifié) : testé empiriquement, non exploitable — voir section 12. (3) Reprise du point ouvert 9.6.1 (zip-bomb DOCX par nombre d'entrées) : **testé empiriquement, celui-ci EST exploitable** (~1,1s CPU + ~150 Mo mémoire par requête malveillante de moins de 24 Mo, sur un service à worker unique — bloque tout le monde) — corrigé par lecture directe de l'EOCD avant tout appel à `zipfile.ZipFile()`, revérifié à ~0s. Voir section 13. (4) Reprise de tous les points restants (9.6.3 à 9.6.11) : **trois nouveaux bugs de sécurité confirmés et corrigés** — ReDoS quadratique sur les motifs IEP/IPP, absence de budget de temps global de détection (~490s pour un document à la limite légale), page de révision CSV sans limite de taille (16,6 Mo générés depuis un upload de 419 Ko) — plus une mise à jour `lxml` (6.1.1→6.1.3) suite à la découverte d'un correctif XXE publié entre-temps. Voir section 14.

**Session 8** — constat critique **[VÉRIFIÉ]** sur le PDF, symétrique à la section 9.1 DOCX de la session 7 : le fichier de sortie "anonymisé" contenait toujours, en clair et récupérable par n'importe quel outil qui parcourt tous les objets du PDF, le nom, la date de naissance et le numéro de dossier du patient d'origine — alors que la page rendue affichait bien le caviardage. Corrigé (purge des objets orphelins + métadonnées) et revérifié par un test de bout en bout via les vrais points d'entrée (`_handle_detect_pdf` / `_finalize_pdf_job`), conformément à la leçon de la session 7. Voir section 10.

**Session 7** — extension du traitement aux formats DOCX et CSV (jusque-là PDF uniquement), avec un lot de constats sévères : 6 fuites de données structurelles confirmées par test dans le format DOCX (indépendantes de tout réglage de détection), plus un patch du moteur Presidio lui-même pour traiter un biais de détection resté hors de portée du seul réglage applicatif.

**Note de traçabilité** : une session intermédiaire (entre la session 6 ci-dessous et celle-ci) a corrigé des faux positifs de détection sur du vocabulaire médical dans `medical.json` (allow_list étendue, exclusion du type `ORGANIZATION`, motif de numéro de dossier) — visible dans l'état actuel de ce fichier, mais cette session n'a jamais été documentée dans ce plan d'audit à l'époque. Signalé ici pour ne pas laisser croire que l'historique ci-dessous est complet à 100 % ; son contenu exact (raisonnement, alternatives écartées) n'a pas pu être reconstitué avec confiance pour cette mise à jour.

**Périmètre :** stack applicative (Traefik, app FastAPI, Presidio analyzer/anonymizer), hébergement (VM Proxmox/Docker), traitement de fichiers uploadés — **désormais PDF, DOCX et CSV** (jusqu'à la session 7, seul le PDF était couvert).

**Hors périmètre (exclu volontairement) :** l'authentification/identité (Keycloak en lab, remplacé par Entra ID en cible) — à l'exception du point 1.28 (durée de session) et du point 1.29 (isolation réseau), qui survivent tous deux à la bascule Entra sans être remis à plat automatiquement.

**Légende des statuts :** ✅ Résolu · 🟡 Risque accepté / reporté / à décider (documenté) · ⏳ Toujours ouvert

---

## 0. Méthodologie

1. **Revue statique / configuration**
2. **Tests dynamiques ciblés** (lab uniquement, jamais de vraies données patient)
3. **Recommandation d'un test d'intrusion externe (pentest)** avant toute mise en situation réelle

**Rappel de gouvernance (session 5, toujours valable) :** vérifier que les documents de test utilisés restent bien fictifs/anonymisés, pas de vraies données patient.

**Principe méthodologique renforcé cette session** : chaque constat de la section 9 marqué **[VÉRIFIÉ]** a été reproduit par un test réel exécuté pendant la session (fichier construit, pipeline exécuté, résultat inspecté), pas seulement déduit par lecture de code. Un bug a d'ailleurs été détecté *pendant la vérification elle-même* (voir 9.1) parce que le premier passage de test appelait des fonctions internes plutôt que les vrais points d'entrée — la leçon a été appliquée immédiatement en refaisant les tests via les fonctions publiques réelles. Réaffirme la valeur de tester plutôt que de supposer, déjà actée aux sessions précédentes (voir 8, leçon session 6).

---

## 1. Risques techniques facilement identifiables

*(Table complète des points 1.1 à 1.29 inchangée depuis la session 6 — voir versions précédentes du document. Aucun nouveau point numéroté cette session ; les constats DOCX/CSV, plus nombreux et de nature différente, sont regroupés en section 9 plutôt qu'ajoutés ici en vrac.)*

---

## 2 à 5. Sections inchangées

Voir versions précédentes du document pour le détail complet (échappement de conteneur, injection, gestion d'erreurs, dépendances).

---

## 6. Isolation de l'accès internet sortant (session 6, inchangé)

Voir version précédente du document pour le détail complet (réseau `app-internal`, limitation Docker sur `internal: true` et publication de ports, résiduel assumé sur `traefik`).

---

## 7. Tests différés au pentest final (inchangé)

Vérification `Range`/`FileResponse`, abus de surface HTTP (Burp), retest 3.7 une fois `/api/audit` corrigé.

---

## 9. Extension DOCX/CSV et durcissement Presidio (nouveau, session 7)

### 9.1 Fuites de données structurelles DOCX — 🔴 CRITIQUE, ✅ toutes corrigées et testées

**Le constat de fond** : la fonction qui parcourait un `.docx` pour y chercher des données sensibles (`_iter_docx_paragraphs`) ne lisait que `paragraph.runs`, qui ne couvre que le texte "visible" en lecture normale. Plusieurs zones d'un `.docx` stockent du texte *ailleurs* dans la structure XML, invisibles à cette API — donc jamais analysées, jamais caviardées, livrées telles quelles dans le fichier "anonymisé" final. **Ce n'est pas un problème de qualité de détection (comme la section "qualité PII" de la session 5) — ces fuites contournent la détection entièrement, quel que soit le réglage du NER ou du seuil.**

Six zones concernées, chacune **[VÉRIFIÉE]** par construction d'un document de test réel et exécution du pipeline complet :

| Zone | Constat | Test |
|---|---|---|
| Suivi des modifications (texte supprimé) | Un texte supprimé en mode suivi (`<w:del>`) reste présent dans le XML — c'est le mécanisme qui permet d'annuler la suppression | Nom + date supprimés en mode suivi : **0 détection**, survivaient intégralement dans le fichier "anonymisé" final |
| Métadonnées du document | Auteur, dernier modificateur, commentaire/sujet (`docProps/core.xml`) jamais ouverts par le pipeline | Auteur, dernier modificateur et un commentaire contenant un numéro de dossier retrouvés intacts dans le fichier final |
| Texte affiché des hyperliens | `paragraph.runs` ne descend pas dans un `<w:hyperlink>` | Un lien "Contacter [Nom]" : le nom n'était jamais vu par la détection |
| Cible des hyperliens (URL) | Stockée dans `word/_rels/document.xml.rels`, partie séparée du corps | Une adresse email en cible de lien mailto retrouvée intacte même après suppression du lien visible |
| Commentaires Word | Partie séparée (`word/comments.xml`), référencée depuis le corps sans jamais être lue | Un commentaire de relecture contenant un nom : jamais lu, jamais retiré |
| Notes de bas de page / de fin | Parties séparées (`word/footnotes.xml`/`endnotes.xml`), aucune API haut niveau python-docx pour les éditer | Une note contenant un nom et une date : jamais analysée |

**Correctifs, tous testés** :
- **Suivi des modifications** → aplati avant toute analyse (suppressions retirées entièrement, insertions dépaquetées) sur le corps, chaque en-tête/pied de page, et chaque note.
- **Métadonnées** → vidées à la finalisation (auteur, dernier modificateur, commentaire, sujet, mots-clés, catégorie) — champs structurés dont la nature est connue par convention, inutile de les faire passer par le NER.
- **Hyperliens** → texte affiché "déplié" (redevient un run normal, analysé comme le reste du corps) ; cible et relation associée supprimées, pas seulement débranchées du corps visible.
- **Commentaires** → retirés entièrement (marqueurs + parties associées), pas caviardés : même un commentaire partiellement masqué révélerait qu'une discussion interne visait une personne précise.
- **Notes de bas de page / de fin** → contrairement aux zones ci-dessus, ce sont analysées et caviardées comme le corps (contenu réellement destiné au lecteur, pas un artefact de travail interne) — nécessite une lecture/réécriture manuelle de leur partie XML, aucune API python-docx dédiée.

**Test de validation combiné** : document construit avec les 6 zones simultanément, passé dans le pipeline complet réel (pas seulement les fonctions internes) — les 6 fuites ne se reproduisent plus, texte normal du corps toujours présent, fichier reste ouvrable sans erreur. Toutes les régressions DOCX de la session ont été revérifiées après ce refactor — aucune casse.

**Bug trouvé pendant la vérification elle-même** : le premier passage de correction avait oublié un point d'appel (encore l'ancienne API dans le point d'entrée réel de détection) — les tests initiaux ne l'avaient pas détecté car ils appelaient les fonctions internes directement. Corrigé, puis revérifié en appelant cette fois les vraies fonctions d'entrée de bout en bout.

### 9.2 Score de confiance Presidio inefficace pour filtrer le bruit administratif — ✅ traité par un patch du moteur

**Le constat** : sur des documents administratifs (formulaires, listes de personnel), des mots courants du français ("Compétences", "Exploiter", "Cliquez"...) étaient détectés comme `PERSON`/`ORGANIZATION` au même titre que de vrais noms. Piste naturelle testée en premier : remonter le seuil de confiance minimal. **Écartée après investigation** — le recognizer spaCy de Presidio attribue un score **fixe** (0.85 par défaut, `ner_strength`) à toute détection, indépendamment de sa justesse réelle ; un seuil ne peut donc jamais distinguer un bon d'un mauvais résultat de ce recognizer précis. Confirmé par le code source du recognizer lui-même (`spacy_recognizer.py`, obtenu depuis le conteneur), pas seulement supposé.

**Risque de régression identifié et évité** : remonter quand même le seuil au-dessus de 0.85 pour forcer le filtrage a été testé — et a effectivement supprimé le bruit, mais a **aussi supprimé la détection de vrais noms** dans un PDF médical réel (même mécanisme de score fixe), en mode "aucun thème" sélectionné. Revert immédiat dès confirmation du problème sur données réelles, avant tout déploiement plus large. Rappel du principe qui a guidé la décision : un faux positif se corrige en un clic en révision, un faux négatif part inaperçu — ne jamais arbitrer en faveur de moins de faux positifs si ça coûte des faux négatifs.

**Solution retenue** : patch du recognizer spaCy de Presidio lui-même (`spacy_recognizer.py`, surchargé au build via le `Dockerfile` de `presidio-analyzer`) — exige qu'au moins un token de l'empan détecté soit étiqueté nom propre (`PROPN`) par le tagger spaCy, déjà actif dans le pipeline chargé (zéro coût d'inférence supplémentaire, confirmé par inspection des composants actifs du modèle `fr_core_news_md`). Testé avec des objets spaCy simulés reproduisant les cas réels observés : mots administratifs (VERB/NOUN) rejetés, vrais noms (PROPN) conservés.

**Limite assumée, découverte en aval** : ce filtre par POS a laissé passer un format de nom compact ("Initiale(s). Nom", ex. `L. Verdurme`, `J-M. Costa`) présent dans un annuaire professionnel réel — le tagger spaCy hésite à étiqueter un nom de famille rare comme nom propre quand il suit directement une initiale isolée, sans les indices habituels. **Confirmé ne pas être une régression du patch** (testé avec et sans le patch actif, même résultat) — limite préexistante du NER sur ce format précis, indépendante du filtre POS.

### 9.3 Nouveaux reconnaisseurs regex ajoutés

- **`FrenchInitialSurnameRecognizer`** (`common.json`, actif quel que soit le thème) — motif "Initiale(s). Nom" pour couvrir la limite du 9.2. **Compromis assumé et documenté** : le même motif de surface correspond aussi à un titre de section numéroté par lettre ("A. Introduction", "B. Méthode") — accepté délibérément, cohérent avec le principe de la section 9.2 (mieux vaut trop caviarder que rater un vrai nom), à surveiller si ce type de faux positif s'avère fréquent en usage réel.
- **Numéro de dossier patient** (`patch_recognizers.py`, intégré au build de `presidio-analyzer`, donc actif indépendamment du thème sélectionné) — étendu avec le motif "chiffres + lettre + chiffres" (ex. `22D0755084`, jusque-là seulement dans `medical.json` donc actif uniquement avec ce thème) et le libellé abrégé "n°" (ex. "Dossier n° : ..."), qui couvre aussi les numéros purement numériques. **Redondance résiduelle non nettoyée** : `medical.json` contient encore des motifs similaires, désormais dupliqués avec ceux intégrés en dur — sans risque fonctionnel (fusion automatique des détections qui se chevauchent en révision), mais source de dérive si l'un est modifié sans l'autre.

### 9.4 Bugs de détection structurelle par tableau — trouvés et corrigés en cours de session

Trois bugs réels dans la nouvelle détection structurelle (repérage de colonnes/lignes de tableau par mot-clé d'étiquette, indépendante du NER) ont été trouvés au fil des tests sur documents réels, pas anticipés à la conception :

1. **Faux positif par sous-chaîne** : le test de correspondance de mot-clé utilisait `"nom" in texte` plutôt qu'une limite de mot — un long paragraphe de description contenant "dénomination" ou "installation" (contenant la sous-chaîne "nom") faisait caviarder à tort toute une ligne de tableau. Corrigé par une correspondance sur mot entier + une longueur maximale de candidat-étiquette (un vrai libellé de champ est court, jamais un paragraphe).
2. **Absorption d'une deuxième étiquette dans la valeur de la première** : une ligne de tableau avec plusieurs paires étiquette:valeur côte à côte ("Nom: X Date: Y") faisait absorber la 2ᵉ étiquette et sa valeur dans la détection de la 1ʳᵉ. Corrigé pour gérer plusieurs étiquettes par ligne, avec changement de type dès qu'une nouvelle étiquette est rencontrée.
3. **Tableau à une seule ligne mal classé** : une ligne unique où 2 colonnes correspondent par coïncidence à des mots-clés se faisait classer à tort comme "tableau à en-tête" (qui suppose des lignes de données en dessous) — 0 détection utile produite. Corrigé en exigeant au moins une ligne de données sous l'en-tête.

Chacun trouvé via un journal de diagnostic temporaire (voir 9.5) plutôt que par relecture de code — sans lui, ces trois bugs seraient probablement restés invisibles, aucun des scénarios de test construits à l'avance ne les ayant révélés.

### 9.5 Journal de diagnostic temporaire (`ENABLE_DEBUG_LOG`) — deux failles trouvées et corrigées

Fonctionnalité ajoutée en cours de session pour accélérer le diagnostic des faux positifs/négatifs sur données réelles (une ligne JSON par détection : type, score, source, texte concerné) — explicitement documentée comme lab-only, à retirer avant toute mise en production. Deux failles identifiées et corrigées avant la fin de la session, pas laissées comme risque accepté :

- **Pas de cloisonnement entre utilisateurs** — `/api/debug/log` n'était protégé que par l'authentification standard : n'importe quel utilisateur authentifié pouvait télécharger les détections (texte en clair) de n'importe quel autre utilisateur. **Corrigé** : chaque entrée est taguée avec l'utilisateur, l'endpoint filtre par l'identité de la requête avant de renvoyer quoi que ce soit. Testé avec plusieurs utilisateurs simulés.
- **Pas d'expiration temporelle** — seulement une rotation par taille, contrairement aux fichiers de sortie (purgés par TTL). **Corrigé** : purge périodique (`DEBUG_LOG_TTL_SECONDS`, 1h par défaut) intégrée à la boucle de nettoyage existante, coordonnée avec le verrou du handler de logging pour éviter toute corruption pendant une écriture concurrente — vérifié explicitement que le journal reste écrivable après une purge.

**Il reste qu'une fonctionnalité de debug loggant du texte en clair est une exception délibérée au principe qui gouverne le reste de l'application** (le journal d'audit, lui, ne stocke que des hashs) — désactivée par défaut, mais à retirer du code avant toute mise en production, pas seulement désactivée par variable d'environnement.

**Mise à jour, session 10 : retiré entièrement.** Devenu inutile maintenant que la vérification se fait par tests réels construits pendant les sessions (méthode des sections 11 à 14) plutôt que par inspection a posteriori du texte détecté — et c'était de toute façon prévu comme temporaire dès l'introduction (voir ci-dessus). Retiré de `app/main.py` : bloc de configuration (`ENABLE_DEBUG_LOG`, `DEBUG_DIR`, `DEBUG_LOG_TTL_SECONDS`, logger `debug_log` + son `RotatingFileHandler`), `_write_debug_log()` et ses deux points d'appel (DOCX, CSV), `_sweep_debug_log()` et son appel dans la boucle de purge périodique et au démarrage, endpoint `GET /api/debug/log`. Retiré de `docker-compose.yml` : variables d'environnement `ENABLE_DEBUG_LOG`/`DEBUG_LOG_TTL_SECONDS` et volume `/var/log/anonymiseur-debug:/data/debug`. Suite de tests unitaires (13 tests) revérifiée au vert après reconstruction de l'image ; import de `main.py` vérifié propre (plus aucun symbole `debug_log`/`ENABLE_DEBUG_LOG`). **Résiduel hors du dépôt, non traité ici** : le répertoire hôte `/var/log/anonymiseur-debug` (ancien point de montage) devient orphelin sur le serveur — à retirer manuellement lors du prochain déploiement, pas du ressort de ce dépôt git.

### 9.6 Nouveaux points ouverts, non traités cette session

| # | Statut | Risque | Constat |
|---|---|--------|---------|
| 9.6.1 | ✅ | **Zip-bomb DOCX par nombre d'entrées — confirmé exploitable et corrigé, voir 13** | Testé empiriquement (pas supposé) : ~260 000 entrées minimales dans ~24 Mo passaient les deux seuils existants tout en coûtant ~1,1s CPU + ~150 Mo mémoire par requête — réel sur un service à worker unique (bloque tout le monde) avec une limite mémoire conteneur de 1 Go. Corrigé par une lecture directe de l'EOCD (`_peek_zip_entry_count`) avant tout appel à `zipfile.ZipFile()` : rejet en ~0s au lieu de ~1,1s. |
| 9.6.2 | ✅ | **Risque XXE — vérifié non exploitable, voir 12.1** | Testé empiriquement (pas seulement lu dans la doc/le changelog) sur les trois classes d'attaque XXE classiques, à la fois au niveau du parseur lxml brut et via le vrai pipeline de bout en bout (`_handle_detect_docx`/`_finalize_docx_job`) sur un `.docx` malveillant : lecture de fichier local, bombe d'entités (déni de service), SSRF via DTD externe. Aucune des trois n'aboutit — voir détail en 12.1. |
| 9.6.3 | 🟡 | **Validation CSV intrinsèquement faible** | Rerevu cette session, toujours accepté : pas de signature binaire possible pour un CSV (contrairement à `%PDF-` ou la structure ZIP d'un docx) — limite de conception, pas un bug, rien à corriger. |
| 9.6.4 | ✅ | **Budget de temps global de détection — confirmé exploitable et corrigé, voir 14.2** | Testé empiriquement : un CSV à la limite exacte de `MAX_CSV_CELLS` (300 000) nécessite ~1500 appels séquentiels à Presidio, ~490s au total (8+ min) sur le worker unique de l'app — bloque tout le monde pendant ce temps. Corrigé par `MAX_DETECTION_SECONDS` (90s, `_check_detection_deadline`). |
| 9.6.5 | ✅ | **Page de révision CSV sans pagination — confirmé exploitable et corrigé, voir 14.3** | Testé empiriquement : un CSV de 300 000 cellules quasi vides (419 Ko de fichier, contourne le budget de détection puisqu'il n'y a rien à analyser) produisait une page de révision de 16,6 Mo avec 300 000 `<td>`. Corrigé par `MAX_REVIEW_ROWS` (2000 lignes affichées ; le caviardage réel, lui, couvre toujours l'intégralité du document, vérifié par test). |
| 9.6.6 | ✅ | **ReDoS sur les nouveaux regex — confirmé exploitable (IEP/IPP) et corrigé, voir 14.1** | Testé empiriquement les 6 regex candidats : `FrenchInitialSurnameRecognizer`, les motifs de numéro de dossier et la correspondance de mot-clé de colonne sont sûrs (croissance linéaire) ; les motifs IEP/IPP (`0+\d{7,}`) montrent une croissance quadratique confirmée (6ms à 1000 caractères, 2,7s à 20 000). Corrigé (`0\d{7,}`), équivalence fonctionnelle vérifiée. |
| 9.6.7 | ✅ | **Seuils numériques DOCX/CSV — désormais stress-testés** | Couvert par les tests réels de cette session : nombre d'entrées ZIP (9.6.1/13), cellules CSV et budget de temps (9.6.4/14.2), lignes de révision (9.6.5/14.3). Les seuils eux-mêmes (`MAX_DOCX_UNCOMPRESSED_MB`, `MAX_DOCX_ZIP_RATIO`) n'ont pas révélé de faille propre — le problème identifié était l'absence de bornes complémentaires (nombre d'entrées, temps, lignes affichées), maintenant comblées. |
| 9.6.8 | ✅ | **`app/Dockerfile` examiné directement** | Confirmé par lecture directe (pas déduit) : `USER appuser` (non-root, UID/GID 1000) et `CMD` sans `--workers` (un seul worker Uvicorn par défaut) — les deux hypothèses de la session 7 étaient exactes. |
| 9.6.9 | ✅ | **`themes/formation.json` — confirmé absent** | Recherché sur le système de fichiers et dans tout l'historique git (`git log --all --diff-filter=A`) : introuvable, aucune trace. Bien nettoyé, aucune action nécessaire. |
| 9.6.10 | — | **MD5 utilisé pour le regroupement visuel côté révision** | Non un enjeu de sécurité : sert uniquement à générer un identifiant HTML stable pour le regroupement visuel côté client, aucun usage cryptographique. Mentionné uniquement pour éviter qu'un futur audit ne le signale à tort. |
| 9.6.11 | ✅ | **Fraîcheur des dépendances DOCX — vérifiée, lxml mis à jour** | `python-docx==1.2.0` toujours la dernière version. `lxml==6.1.1` avait une version corrective plus récente (6.1.3, 2026-09-02) corrigeant une vraie faille de sécurité XXE (entités paramètres externes, LP#2165901) — mis à jour vers `lxml==6.1.3`, revérifié sans régression (suite de tests + XXE + DOCX round-trip). |

---

## 10. Fuite structurelle PDF — métadonnées et objets orphelins (nouveau, session 8)

### 10.1 Contexte et déclencheur

Test repris sur `caviar_test.pdf`, un fichier "anonymisé" produit par une session précédente (même patient fictif que `test_med.pdf`) et laissé de côté sans avoir été audité pour lui-même. Demande explicite de vérifier la présence de métadonnées et d'éventuels "trous" comme ceux trouvés sur DOCX en 9.1, et de corriger sur le même principe.

### 10.2 Constat — 🔴 CRITIQUE, ✅ corrigé et testé

**Métadonnées** : `_finalize_pdf_job` sauvegardait le PDF final via `doc.save(output_path)` sans jamais toucher au dictionnaire `/Info` ni au paquet XMP. Sur `caviar_test.pdf`, `producer` ("cairo 1.18.0") et `creationDate` (date réelle de génération du document source) survivaient intégralement — exactement le même angle que les propriétés `core.xml` en DOCX (9.1), jamais traité côté PDF.

**Objets orphelins — le constat le plus sérieux** : `page.apply_redactions()` (PyMuPDF) ne supprime pas les anciens objets de contenu de page pré-caviardage du fichier ; il les déréférence seulement en pointant la page vers un nouvel objet. Sans collecte des objets inutilisés à la sauvegarde (`garbage=0`, le défaut, utilisé sans le savoir par le code existant), ces objets restent physiquement présents dans le fichier de sortie. **[VÉRIFIÉ]** sur `caviar_test.pdf` : sur 414 objets dans le fichier, seuls 83 étaient réellement atteignables depuis l'arbre de pages courant — les 331 autres incluaient plusieurs flux de contenu complets de l'état pré-caviardage, contenant le nom complet du patient, sa date de naissance et son numéro de dossier en clair, extractibles avec une dizaine de lignes de code PyMuPDF (aucun outil spécialisé de forensic nécessaire — `mutool clean`, `qpdf`, ou l'inspecteur d'objets d'Acrobat auraient tout autant suffi). La page rendue affichait pourtant bien le caviardage — **contournement total de la protection au niveau du fichier, indépendant de la qualité de la détection**, même famille de bug que 9.1 (DOCX, suivi des modifications) mais plus grave : ici c'est le comportement par défaut de tout appel `doc.save()`, pas un cas d'usage particulier (mode suivi activé) — donc probablement présent dans **tous** les PDF jamais produits par ce pipeline jusqu'ici.

### 10.3 Correctif, testé

- **`_wipe_pdf_metadata(doc)`** (nouvelle fonction, miroir de `_wipe_core_properties` côté DOCX) : `doc.set_metadata({})` puis `doc.del_xml_metadata()` si un paquet XMP est présent.
- **`doc.save(output_path, garbage=4, clean=True, deflate=True)`** au lieu de `doc.save(output_path)` — `garbage=4` purge les objets non référencés (le cœur du correctif), `clean=True` réécrit les flux de contenu, `deflate=True` recompresse (bénéfice de taille, pas un enjeu de sécurité en soi).

**Test de validation** : rejoué via les **vrais points d'entrée** (`_handle_detect_pdf` puis `_finalize_pdf_job`, pas des fonctions internes isolées — leçon de la session 7 appliquée directement) sur `test_med.pdf` (mêmes données patient que `caviar_test.pdf`) : 124 détections, 106 zones après regroupement, toutes caviardées. Sur le fichier de sortie : métadonnées vides, objets atteignables ramenés à 82 (contre 414 dans `caviar_test.pdf`), et un balayage de **tous** les objets du fichier (pas seulement l'arbre de pages, la même méthode que celle qui avait révélé la fuite) ne retrouve trace nulle part des 77 chaînes de PII réellement extraites du document d'origine — nom, date de naissance et numéro de dossier inclus. Suite de tests unitaires existante (13 tests) toujours au vert après reconstruction de l'image `anonymiseur-app`.

### 10.4 Constat incident, non corrigé cette session — ⏳ ouvert, diagnostiqué précisément

En construisant le jeu de chaînes PII pour la vérification 10.3, deux mots génériques (`BIOLOGIE`, `Laboratoire`) sont d'abord apparus suspects lors d'un premier passage de test — **ce premier diagnostic s'est révélé partiellement faux et a été corrigé avant d'écrire quoi que ce soit dans le code** : le script de vérification initial chargeait `main.py` depuis une copie isolée sans son dossier `themes/` à côté (`THEMES_DIR = Path(__file__).parent / "themes"` résolu vers un chemin inexistant), donc **aucun thème n'était réellement appliqué** — `excluded_entity_types` ne s'exécutait jamais. Une fois le script corrigé (copie de `main.py` + `themes/` ensemble, thème médical réellement chargé et vérifié `THEMES["medical"]` non vide) :

- **`BIOLOGIE` (`ORGANIZATION`) : déjà résolu, aucune action nécessaire.** Avec le vrai thème médical, `excluded_entity_types: ["ORGANIZATION"]` (déjà en place depuis la session intermédiaire non documentée, voir note de traçabilité en tête de document) filtre entièrement ce type — 0 détection sur les 3 occurrences de la page, de façon cohérente, pas de fuite.
- **`Laboratoire` (`LOCATION`) : trou réel, confirmé, plus profond qu'un simple mot manquant à l'`allow_list`.** Ajouté à `medical.json.allow_list` sur suggestion initiale — **testé, insuffisant** : l'unique occurrence effectivement caviardée sur la page 2 ne provient pas d'un empan NER propre `"Laboratoire"`, mais d'un empan **fusionné à travers un saut de ligne de mise en page** : `"Laboratoire\n \nExemplaire"` (LOCATION, score 0.85) — l'extraction PyMuPDF (`page.get_text()`) concatène en une seule chaîne l'étiquette de formulaire "(s) au(x) : Laboratoire" et le début de la ligne suivante, sans rapport, "Exemplaire patient". Un mot isolé dans l'`allow_list` (correspondance exacte) ne peut pas matcher cette chaîne à saut de ligne. Deux conséquences en cascade, toutes deux **[VÉRIFIÉES]** par test direct sur `test_med.pdf` :
  1. `page.search_for(entity_text)` sur cet empan à saut de ligne renvoie **3 rects disjoints** au lieu d'un seul — dont un qui couvre bien "Laboratoire", mais aussi un second qui caviarde à tort une zone contenant "Exemplaire" (texte sans rapport, effet de bord du bug plutôt qu'un choix délibéré de sur-caviardage comme en 9.3).
  2. La chaîne utilisée comme candidat de propagation inter-pages (passe 2, `propagate_candidates`) est cette même chaîne corrompue par le saut de ligne — elle ne correspond littéralement à rien sur les pages 0/1, donc la propagation vers les 4 autres occurrences propres du mot échoue silencieusement. C'est la vraie cause du symptôme initial ("une seule occurrence sur 5 détectée"), pas un problème de couverture de `PROPAGATED_ENTITY_TYPES` comme supposé au premier passage.

Décision prise avec l'utilisateur (question posée explicitement, compromis non trivial) : **ne pas corriger ce bug cette session**. Une correction sûre nécessiterait de modifier la façon dont les empans multi-lignes sont traités dans `_detect_pdf` (ex. ne garder que le premier segment de ligne pour la recherche/propagation) — risque de régression sur tout document PDF, et un compromis réel à trancher consciemment : tronquer un empan multi-ligne pour éviter le caviardage parasite pourrait, dans un autre document, faire manquer la 2ᵉ ligne d'un vrai nom réparti sur deux lignes — à l'opposé du principe déjà acté en 9.2/9.3 ("ne jamais arbitrer en faveur de moins de faux positifs si ça coûte des faux négatifs"). L'entrée `"Laboratoire"` reste dans `medical.json.allow_list` (inoffensive, ne matche rien actuellement, mais deviendrait utile si un futur document contient un empan NER propre `"Laboratoire"` isolé). Mots génériques de en-tête concernés ici, pas des identifiants (aucun nom de patient, date ou numéro touché) — sévérité bien moindre que 10.2, mais signal de plus, et de nature différente (empans NER corrompus par la mise en page, pas juste vocabulaire mal classé), pour l'item déjà ouvert le plus impactant du projet (mesure de la qualité de détection PII sur corpus varié, section 6 session 5 / synthèse ci-dessous).

### 10.5 Note de traçabilité

`caviar_test` et `caviar_test.pdf` (les deux fichiers ayant servi de déclencheur, identiques) ont été supprimés par erreur pendant le nettoyage des artefacts de test de cette session — non trackés par git, non récupérables. Cas reproductible via `test_med.pdf` (même patient), donc sans perte d'information pour l'audit, mais à signaler pour traçabilité.

---

## 11. Campagne de non-régression multi-format — PDF/DOCX/CSV (nouveau, session 9)

### 11.0 Contexte et méthode

Demande explicite : revérifier, pour les trois formats supportés, qu'une donnée personnelle caviardée n'est récupérable "par aucun moyen" une fois le document "anonymisé" produit. Plutôt que relire le code, méthode identique à celle qui a payé aux sessions 7/8 : construire un document de test fictif par format couvrant toutes les zones déjà corrigées, le faire passer par les **vrais points d'entrée** (`_handle_detect_*` / `_finalize_*_job`), puis balayer **exhaustivement** le fichier de sortie — pas seulement le texte rendu/visible, mais toutes les entrées du zip (DOCX), tous les objets du PDF y compris ceux non référencés par l'arbre de pages courant, et les octets bruts (CSV) — à la recherche de chaque chaîne PII injectée dans le fixture.

Fixtures et scripts conservés (pas supprimés, conformément à la consigne de l'utilisateur) dans `app/tests/fixtures/` — voir le `README.md` de `verification_scripts/` pour les rejouer.

### 11.1 PDF — ✅ tient, [VÉRIFIÉ]

Rejoué sur `test_med.pdf` (133 détections, 84 chaînes PII réelles extraites du document d'origine) via `_handle_detect_pdf`/`_finalize_pdf_job` avec le correctif de la session 8 : métadonnées vides, `xref_length` ramené à 82 objets (contre 414 avant correctif), un seul `%%EOF` (pas de révision incrémentale cachée — vecteur classique de fuite PDF non testé explicitement jusqu'ici, vérifié ici pour la première fois). Balayage de tous les objets : une seule occurrence résiduelle, le mot "Laboratoire" déjà identifié et sciemment non corrigé en 10.4 — aucune fuite sur les identifiants (nom, date de naissance, numéro de dossier, coordonnées).

### 11.2 DOCX — ✅ tient, [VÉRIFIÉ], une fixture invalide a d'abord donné un faux positif

Fixture construit avec les 6 zones de la session 7 (9.1) simultanément : suivi des modifications (texte supprimé), hyperlien (texte affiché + cible mailto), commentaire, note de bas de page, note de fin, en-tête + pied de page, plus métadonnées identifiantes (auteur, dernier modificateur, sujet, commentaire du document). Un premier passage a signalé deux fuites dans `docProps/core.xml` — **fausses**, dues à une erreur de construction du fixture (XML dupliqué : deux éléments `<cp:lastModifiedBy>`/`<dc:subject>` au lieu d'un, python-docx ne traitant que le premier) plutôt qu'à un bug applicatif ; corrigé en construisant les métadonnées via l'API `python-docx` (`document.core_properties`) plutôt qu'une injection XML manuelle, et une fuite `word/footnotes.xml` — également fausse, un numéro de dossier fictif au format inventé avec tiret ne correspondant à aucun des deux regex `PatientDossierNumberRecognizer` existants (aucun des deux n'accepte de tiret), alors que le nom de la même note **avait bien été détecté et caviardé** — preuve que la note était bien lue, pas une fuite structurelle. Fixture corrigé (métadonnées via l'API, numéro de dossier au format `[A-Z]\d{8,12}` déjà supporté) : **balayage complet des 20 parties du zip de sortie (document, en-tête, pied de page, notes, relations, métadonnées, etc.) sans aucune fuite.** Les zones retirées entièrement plutôt que caviardées (commentaire, suivi des modifications) sont bien absentes du fichier final, pas seulement masquées.

### 11.3 CSV — ✅ tient, [VÉRIFIÉ]

Architecture intrinsèquement plus sûre que PDF/DOCX sur ce point précis : la sortie est entièrement reconstruite cellule par cellule depuis la structure `rows` réanalysée (`csv.writer`), jamais une copie du fichier original avec patch chirurgical — aucun octet de l'original ne peut donc survivre ailleurs "par accident" comme pour un objet PDF orphelin ou une partie XML DOCX non lue. Fixture avec doublons de mention dans une même cellule (test du bon fonctionnement des intervalles multiples), cellule avec virgule et guillemets internes, cellule avec saut de ligne interne : balayage des octets bruts du fichier de sortie sans aucune fuite, ré-analyse round-trip (`csv.reader` sur la sortie) cohérente en nombre de lignes/colonnes.

### 11.4 Angles morts repérés en marge, non exploités par les fixtures actuels — 🟡 à garder à l'œil

Aucun des deux n'a pu être testé positivement (les fixtures python-docx ne les déclenchent pas), donc statut "non confirmé comme fuite", pas "fuite" :

- **`docProps/thumbnail.jpeg`** : présent dans le zip de sortie DOCX, jamais touché par le pipeline (qui n'agit que sur le texte). Un document `.docx` réellement créé/enregistré par Microsoft Word (contrairement à nos fixtures générés par `python-docx`, qui n'embarque qu'une image statique du template) peut contenir, si l'option "Enregistrer la vignette" a été active à un moment, un **rendu réel de la première page** — donc potentiellement du texte visible en pixels, jamais analysé ni retiré.
- **Images incrustées dans le corps** (`<w:drawing>` simple, ex. une capture d'écran collée dans le texte) : distinctes des "zones de texte, formes, objets incrustés et SmartArt" déjà documentées comme limite connue (voir note dans `_iter_docx_paragraphs`) — une image insérée simplement (Insertion > Image) n'est pas nommément couverte par cette limite telle que formulée, et n'est de toute façon jamais lue par un pipeline texte-seul.

Pas d'équivalent PDF/CSV à signaler : les images PDF ont déjà été vérifiées en session 8 (logos de petite taille, pas de scan pleine page dans `test_med.pdf`) et CSV est un format texte pur sans conteneur d'image.

---

## 12. Risque XXE (9.6.2) — vérifié non exploitable (nouveau, session 9)

### 12.0 Périmètre et méthode

Reprise du point 9.6.2, resté "non confirmé spécifiquement" depuis la session 7 (extension DOCX). Plutôt que se fier au changelog des versions épinglées (`python-docx==1.2.0`, `lxml==6.1.1`), test empirique en deux temps : (1) sur le parseur lxml exact utilisé par python-docx, isolé ; (2) sur le vrai pipeline de bout en bout, avec un `.docx` malveillant complet, comme pour tous les constats [VÉRIFIÉ] des sessions précédentes.

### 12.1 Constat — ✅ non exploitable, sur les trois classes d'attaque testées

**Configuration du parseur** : `grep` sur l'intégralité du paquet `python-docx` installé montre que **toute** la désérialisation XML (aussi bien le chargement du paquet OPC — `[Content_Types].xml`, relations — que le contenu WordprocessingML lui-même) passe par un seul et même parseur, défini deux fois à l'identique (`docx/opc/oxml.py` et `docx/oxml/parser.py`) :
```python
oxml_parser = etree.XMLParser(remove_blank_text=True, resolve_entities=False)
```
`resolve_entities=False` est le réglage qui neutralise XXE : une entité déclarée dans un DOCTYPE n'est jamais substituée dans l'arbre — elle reste comme référence littérale non résolue. Le code applicatif (`_get_note_part`, pour les notes de bas de page/de fin) réutilise `docx.oxml.parse_xml`, donc le même parseur — vérifié qu'aucun autre point du code n'appelle `lxml.etree.fromstring`/`etree.parse` directement (seul usage direct de `etree` dans `main.py` : `etree.tostring()` en écriture, jamais en lecture).

**Test 1 — parseur isolé**, avec la config exacte ci-dessus, sur trois payloads :
- Lecture de fichier local (entité `SYSTEM "file://..."`, testé à la fois sur un fichier canari créé pour l'occasion et sur `/etc/passwd`) : entité jamais résolue, contenu jamais présent dans l'arbre parsé, temps de traitement quasi instantané (0.000s — pas de tentative d'accès fichier observable).
- Bombe d'entités ("billion laughs", 4 niveaux d'imbrication ×10) : jamais expansée, aucun ralentissement, pas de déni de service.
- SSRF via DTD externe (`SYSTEM "http://169.254.169.254/..."`, adresse de métadonnées cloud classique) : aucune tentative réseau (temps quasi instantané), le DTD externe n'est même pas chargé.

**Test 2 — pipeline réel de bout en bout**, `.docx` malveillant construit à partir du fixture de la section 11 (`word/document.xml` et `word/footnotes.xml` remplacés par des payloads avec `DOCTYPE`+entités pointant vers un fichier canari et `/etc/passwd`), passé par `_handle_detect_docx` puis `_finalize_docx_job` sans aucune modification du code : aucun crash, détection des fausses données PII du reste du document toujours fonctionnelle (preuve que le fichier est bien traité normalement, pas juste rejeté en amont), fichier de sortie généré — **le contenu canari et `/etc/passwd` sont absents à 100 % du document final**, les entités `&xxe1;`/`&xxe2;`/`&xxe3;` survivent telles quelles comme texte littéral non résolu.

### 12.2 Limite du test, assumée

Seul le chemin DOCX a été testé (c'était le périmètre exact de 9.6.2 — introduit avec l'extension DOCX/CSV de la session 7). Non applicable ailleurs : CSV est un format texte pur sans XML ; côté PDF, le seul contenu XML manipulé est le paquet XMP (métadonnées), jamais parsé par le code applicatif — seulement lu comme octets bruts (`xref_stream`) ou supprimé (`del_xml_metadata`), jamais désérialisé avec un parseur XML par `main.py` ou PyMuPDF à notre initiative.

---

## 13. Zip-bomb DOCX par nombre d'entrées (9.6.1) — confirmé exploitable, corrigé (nouveau, session 9)

### 13.0 Contexte et méthode

Reprise du point 9.6.1 (ouvert depuis la session 7), même traitement que 9.6.2 : test empirique plutôt que lecture de code, avec un payload réel construit pour l'occasion, mesuré via les vrais points d'entrée.

### 13.1 Constat — 🔴 confirmé exploitable par test réel

**Le gap précis** : `_validate_docx_zip` borne la taille décompressée totale (`MAX_DOCX_UNCOMPRESSED_MB`, 200 Mo) et le ratio de compression par entrée (`MAX_DOCX_ZIP_RATIO`, 100×), mais rien ne bornait le **nombre** d'entrées. Un zip de fichiers vides ou quasi vides a un ratio de compression non pertinent (0 octet ÷ 0 octet, jamais testé par construction du code) et une taille décompressée totale négligeable, quel que soit le nombre d'entrées.

**[VÉRIFIÉ] par construction d'un payload réel** : archive de ~23,94 Mo (sous `MAX_UPLOAD_MB`, 25 Mo — la limite amont sur la taille brute de l'upload) contenant **258 454 entrées** de 0 octet chacune, déguisée en `.docx` valide (`word/document.xml` présent). Passe les deux contrôles existants sans problème. Mesuré sur le code d'avant correctif, via les vrais points d'entrée (`_validate_docx_zip` et `_detect_file_kind`, appelés par `detect_document` sur **chaque** upload) : **~1,1 à 1,2 s de CPU et ~150 Mo de mémoire consommée par cette seule requête**, avant même que python-docx n'ouvre quoi que ce soit — le coût vient uniquement de `zipfile.ZipFile()`, qui doit désérialiser l'intégralité du répertoire central (un objet `ZipInfo` par entrée) pour pouvoir répondre à `zf.namelist()`.

**Facteurs aggravants, vérifiés plutôt que supposés** :
- Le service `app` tourne avec un seul worker Uvicorn (`Dockerfile` : `CMD ["uvicorn", "main:app", ...]`, pas de `--workers`) et la route `detect_document` est `async def` mais son corps est entièrement synchrone (aucun `await` autour du traitement du fichier) — une requête de ce type **bloque la boucle d'événements pour tous les utilisateurs** pendant ~1,1 s, pas seulement celui qui l'envoie.
- Le service `app` est plafonné à 1 Go de mémoire (`docker-compose.yml`, `deploy.resources.limits.memory`) — quelques requêtes de ce type suffiraient à s'en approcher, avec un risque d'arrêt du conteneur par le mécanisme OOM de Docker (donc une coupure de service complète, pas seulement une lenteur) plutôt qu'un simple ralentissement.

Conclusion : contrairement au risque XXE (12), celui-ci **est** réellement exploitable avec un effort d'attaque trivial (un seul fichier de moins de 24 Mo, aucune authentification supplémentaire requise au-delà de celle déjà nécessaire pour uploader un document).

### 13.2 Correctif, testé

Premier réflexe — ajouter `if len(names) > MAX_DOCX_ZIP_ENTRIES` juste après `zf.namelist()` — **testé et insuffisant** : `zipfile.ZipFile()` a déjà fait tout le travail coûteux au moment où `zf.namelist()` retourne, donc ce garde-fou rejette bien la requête mais ne réduit ni le temps (~1,1s inchangé) ni la mémoire (~150 Mo inchangée). Remplacé par **`_peek_zip_entry_count()`** : lit le nombre d'entrées directement depuis l'enregistrement de fin de répertoire central (EOCD, les 22 derniers octets significatifs du fichier, hors commentaire) **avant** tout appel à `zipfile.ZipFile()` — gère le cas Zip64 (champ 16 bits saturé à `0xFFFF` au-delà de 65 535 entrées, vrai compte dans le "Zip64 EOCD record" retrouvé via son "locator"), nécessaire puisque le payload de test en a justement besoin (258 454 > 65 535). `MAX_DOCX_ZIP_ENTRIES` fixé à 5000 (généreux : un `.docx` réel dépasse rarement quelques dizaines d'entrées).

**Test de validation** : rejoué sur le même payload de 258 454 entrées via les vrais points d'entrée — rejet en **0,000 s** (au lieu de 1,1 s), avant tout appel à `zipfile.ZipFile()`. Fixture légitime (`docx_fixture.docx`, section 11, 22 entrées) revérifié de bout en bout après le correctif : détection et caviardage inchangés, zéro fuite — pas de régression. Suite de tests unitaires (13 tests) toujours au vert après reconstruction de l'image.

### 13.3 Note sur les fixtures

Le payload de test (~24 Mo, généré en quelques secondes) n'est pas conservé dans `app/tests/fixtures/` — seul le script qui le construit (`build_zipbomb_entries.py`) l'est, avec `verify_zipbomb.py` pour rejouer la mesure. Voir le `README.md` de `verification_scripts/`.

---

## 14. Reprise des points 9.6.3 à 9.6.11 (nouveau, session 9)

Tous les points restants de la liste de résidus ouverts en session 7 (9.6), traités dans le même esprit que 9.6.1/9.6.2 : tester réellement plutôt que juger sur lecture de code, corriger ce qui se confirme exploitable, documenter le reste. Trois points se sont révélés être des vrais bugs de sécurité (14.1, 14.2, 14.3) ; les autres sont désormais des vérifications directes closes (14.4) ou des acceptations de conception reconfirmées (9.6.3, tableau ci-dessus).

### 14.1 ReDoS sur les motifs IEP/IPP — 🔴 confirmé exploitable, corrigé

**Méthode** : les 6 regex candidats du point 9.6.6 (`FrenchInitialSurnameRecognizer`, les motifs de numéro de dossier `medical.json`/`patch_recognizers.py`, l'adresse postale, la correspondance de mot-clé de colonne) ont été testés avec des entrées adverses de taille croissante (100 à 50 000 caractères), en cherchant une croissance super-linéaire du temps de traitement.

**Constat [VÉRIFIÉ]** : 5 des 6 motifs restent linéaires même à 50 000 caractères (< 7ms). Les motifs **IEP** et **IPP** (`PatientIdentifierRecognizer`, `medical.json`), de la forme `0+\d{7,}`, montrent une croissance quadratique nette : 0,06ms à 100 caractères, 6,8ms à 1000, 168ms à 5000, **2,7 secondes à 20 000 caractères** — extrapolation à ~70s pour 100 000 caractères, une taille de cellule/paragraphe très plausible (`MAX_CSV_FIELD_CHARS` autorise jusqu'à 100 000 caractères par cellule). Cause : `0` fait partie de la classe `\d`, donc pour une suite de zéros le moteur regex a une ambiguïté sur où couper entre `0+` et `\d{7,}` — un cas d'école de ReDoS quadratique.

**Correctif, testé** : `0+\d{7,}` → `0\d{7,}` (un seul zéro obligatoire, `\d{7,}` absorbe le reste y compris d'autres zéros — même langage reconnu, vérifié par équivalence sur 5 cas limites incluant plusieurs zéros en tête). Reste linéaire jusqu'à 500 000 caractères (11ms). Revérifié via le **vrai service `presidio-analyzer`** (pas seulement `re` en Python isolé) : 0,087s sur le payload qui aurait pris plusieurs secondes avec l'ancien motif.

### 14.2 Pas de budget de temps global — 🔴 confirmé exploitable, corrigé

**Constat [VÉRIFIÉ]** : `_detect_text_blocks` regroupe les blocs par lots de `TEXT_CHUNK_MAX_BLOCKS` (200) avant chaque appel à Presidio. Mesuré directement (5 lots de calibration) : ~0,33s par lot avec le thème médical. Pour un CSV à la limite exacte de `MAX_CSV_CELLS` (300 000 cellules = ~1500 lots) : **~490 secondes estimées, confirmées par un test à échelle réduite qui a dû être interrompu après plus de 3 minutes**. Sur le service à worker unique (9.6.8), cela bloque l'application pour tous les utilisateurs pendant la durée du traitement — pas seulement celui qui a soumis le fichier.

**Correctif, testé** : `MAX_DETECTION_SECONDS` (90s) + `_check_detection_deadline()`, appelé avant chaque page (`_detect_pdf`) et avant chaque lot (`_detect_text_blocks`). Testé sur un document de 70 000 blocs (bien au-delà de ce qui serait traité en 90s) : interruption propre à 90,3s avec un message d'erreur explicite, au lieu de tourner plusieurs minutes. Aucune régression sur les fixtures légitimes des trois formats (détections et caviardage identiques à avant correctif).

### 14.3 Page de révision CSV sans pagination — 🔴 confirmé exploitable, corrigé, distinct de 14.2

**Constat [VÉRIFIÉ] — pas couvert par le correctif 14.2** : un CSV de 300 000 cellules **quasi vides** (une seule cellule non vide par ligne, 419 Ko de fichier) contourne le budget de temps de détection — les cellules vides sont ignorées avant tout appel à Presidio (`if not text.strip(): continue`), donc l'analyse se termine en ~16s, bien sous les 90s. Mais la page de révision affiche **toutes** les cellules, vides ou non : **16,64 Mo de HTML, 300 000 `<td>`**, générés en 16,5s côté serveur, à charger et faire rendre par le navigateur du client. Angle "amplification de ressources" distinct : upload de 419 Ko → réponse de 16,6 Mo (~40×), sans jamais dépasser aucun seuil de détection.

**Correctif, testé** : `MAX_REVIEW_ROWS` (2000) — l'aperçu HTML (CSV : lignes de tableau ; DOCX : paragraphes non vides, par cohérence même si moins critique pour ce format) est tronqué à ce nombre, avec un bandeau explicite. **Le caviardage réel n'est jamais réduit** : `job["detections"]` couvre toujours l'intégralité du document, `_finalize_*_job` ne dépend pas de ce qui a été affiché. Vérifié explicitement par test : une ligne 2400 (bien au-delà de l'aperçu de 2000) contient toujours une détection et est bien remplacée par `[MASQUÉ]` dans le fichier final. Revérifié sur le même payload de 300 000 cellules quasi vides : page ramenée à 1,67 Mo (30 000 `<td>`, 2000 lignes) au lieu de 16,64 Mo.

**Suite immédiate, décidée avec l'utilisateur** : `MAX_CSV_CELLS` ramené de 300 000 à **5000** (défaut Python + variable d'environnement `docker-compose.yml`, qui primait sur le défaut et avait été oubliée au premier passage — repérée en revérifiant après coup que le changement s'appliquait vraiment au conteneur, pas seulement au code). Raisonnement produit, pas seulement technique : la finalisation reste une revue humaine, un CSV de centaines de milliers de cellules n'est de toute façon jamais réellement relisible avant validation — plafonner à la source à un niveau cohérent avec un usage réel réduit la marge de manœuvre de 14.2/14.3 à la racine plutôt que seulement en périphérie ; les deux restent en place comme filets de sécurité (utiles si la limite est un jour remontée) mais ne se déclenchent plus en usage normal. Revérifié : 5000 cellules acceptées, 5010 rejetées proprement, fixture légitime toujours fonctionnelle, aucune régression.

### 14.3bis Même vérification demandée pour PDF et DOCX — un vrai trou trouvé côté DOCX

Demande explicite de vérifier si le même type de limite "orientée réviseur humain" (pas seulement technique) existe aussi pour PDF et DOCX.

**DOCX — même trou que CSV avant correctif, corrigé** : `MAX_DOCX_PARAGRAPHS` valait 20 000, soit **10× `MAX_REVIEW_ROWS`** (2000) — jamais aligné en même temps que 14.3. Un DOCX légitime proche de la limite aurait eu jusqu'à 90 % de son contenu caviardé "à l'aveugle" côté page de révision (le caviardage réel restait correct, voir garantie testée en 14.3, mais l'utilisateur n'aurait jamais pu le voir/corriger avant validation — exactement le problème produit que 14.3 corrigeait côté CSV, resté non traité côté DOCX). Ramené à **5000**, même valeur que `MAX_CSV_CELLS`, aux deux endroits (`main.py` + `docker-compose.yml`). Revérifié : fixture légitime toujours fonctionnelle, zéro fuite, aucune régression.

**PDF — architecture différente, pas le même trou** : la page de révision PDF charge chaque page à la demande via `<img src="/api/preview_image/{job_id}/{page_index}">` — la réponse initiale n'embarque que de légers overlays cliquables (un `<div>` par détection groupée), jamais le contenu texte intégral de toutes les pages d'un coup comme le faisait CSV/DOCX. `MAX_PDF_PAGES` (70) n'a donc pas le même défaut technique de "page HTML géante" que 14.3 a corrigé — chaque page est déjà son unité de révision naturelle, chargée séparément. `MAX_PDF_PAGES` non modifié.

### 14.4 Vérifications directes closes sans correctif nécessaire

- **9.6.8** (`app/Dockerfile`) : lu directement — `USER appuser` (non-root, UID/GID 1000 fixes) et `CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]` (pas de `--workers`, un seul worker par défaut). Les deux hypothèses de la session 7 étaient exactes, maintenant confirmées plutôt que déduites — et c'est justement ce fait (un seul worker) qui rend 14.2/14.3 aussi sérieux qu'ils le sont (une requête lente bloque tout le monde, pas seulement son émetteur).
- **9.6.9** (`themes/formation.json`) : recherché sur le système de fichiers (`find`) et dans tout l'historique git (`git log --all --diff-filter=A --name-only`) — introuvable partout. Confirmé nettoyé, aucune trace résiduelle.
- **9.6.11** (fraîcheur des dépendances) : vérifié sur PyPI. `python-docx` toujours en 1.2.0 (à jour). `lxml` avait une version 6.1.3 (2026-09-02) non appliquée, corrigeant **une vraie faille XXE** (LP#2165901, "external parameter entity parsing was allowed by default") — repérée en vérifiant simplement la fraîcheur des versions, pas en cherchant une faille. Tentative de reproduction directe du bypass par entité paramètre sur la version 6.1.1 non concluante (une restriction de syntaxe XML distincte, "PEReferences forbidden in internal subset", a bloqué les deux constructions de payload essayées) — **mise à jour appliquée quand même** plutôt que de conclure à tort à l'innocuité sur la base d'un test qui n'a pas réussi à reproduire exactement le scénario du correctif amont. `lxml==6.1.3` déployé, testé sans régression (13 tests unitaires, suite XXE complète de la session précédente, round-trip DOCX complet).

---

## 8. Synthèse et priorisation mise à jour

**✅ Fait cette session (7) :**
- 6 fuites de données structurelles DOCX trouvées et corrigées, chacune vérifiée par test réel (9.1) — probablement le constat le plus sérieux du projet à ce jour, dans la continuité de ce que la section "qualité de détection PII" de la session 5 avait commencé à révéler, mais d'une nature différente (contournement total de la détection, pas imprécision de détection)
- Diagnostic et traitement d'un biais de détection Presidio par un patch du moteur lui-même plutôt qu'un réglage applicatif, après qu'une première piste (seuil de score) se soit révélée dangereuse et ait été écartée à temps (9.2)
- Deux failles de sécurité propres trouvées et corrigées sur une fonctionnalité de diagnostic ajoutée pendant la session elle-même, avant la fin de cette même session (9.5)
- Trois bugs de détection structurelle trouvés et corrigés grâce à un outillage de diagnostic construit en cours de route, invisibles aux tests anticipés (9.4)

**🟡 Décisions de risque documentées cette session :**
- Faux positif assumé sur les titres de section numérotés par lettre, pour ne pas rater de vrais noms au format compact (9.3)
- `medical.json`/`patch_recognizers.py` : redondance non nettoyée, sans risque fonctionnel immédiat (9.3)

**✅ Fait cette session (8) :**
- Fuite structurelle PDF critique trouvée et corrigée : objets de contenu pré-caviardage (nom, date de naissance, numéro de dossier patient en clair) et métadonnées non nettoyées survivaient dans tout fichier "anonymisé" produit par le pipeline — comportement par défaut, pas un cas limite (10.2)
- Correctif (`_wipe_pdf_metadata` + `garbage=4, clean=True` à la sauvegarde) revérifié de bout en bout via les vrais points d'entrée, sur les 77 valeurs de PII réellement extraites du document d'origine (10.3)

**🟡 Décisions de risque documentées cette session :**
- `Laboratoire`/`LOCATION` : trou de caviardage diagnostiqué précisément (empan NER fusionné à travers un saut de ligne PDF, avec effet de bord de sur-caviardage parasite) — correction délibérément reportée après arbitrage explicite avec l'utilisateur, le compromis de correction n'étant pas sans risque de régression (10.4)
- `BIOLOGIE`/`ORGANIZATION` : suspecté à tort en premier passage (bug du script de vérification, pas du produit) — confirmé sans action nécessaire, déjà couvert par `excluded_entity_types` (10.4)

**✅ Fait cette session (9) :**
- Campagne de non-régression demandée par l'utilisateur sur les trois formats : fixtures dédiés construits et conservés (`app/tests/fixtures/`), passés par les vrais points d'entrée, sortie balayée exhaustivement — **les correctifs des sessions 7 et 8 tiennent** ; zéro fuite résiduelle confirmée sur PDF, DOCX et CSV (11.1, 11.2, 11.3)
- Un vecteur PDF jusqu'ici non testé explicitement — révisions incrémentales cachées (plusieurs `%%EOF`) — vérifié absent (11.1)
- Deux angles morts DOCX repérés et documentés sans être exploitables avec les fixtures actuels : miniature de document (`docProps/thumbnail.jpeg`, pertinent surtout pour un `.docx` réellement produit par Word, pas par `python-docx`) et images incrustées simples dans le corps, distinctes de la limite déjà connue zones de texte/formes/SmartArt (11.4)
- Risque XXE (9.6.2, ouvert depuis la session 7) levé : testé empiriquement (parseur isolé + pipeline réel de bout en bout sur `.docx` malveillant) sur lecture de fichier local, bombe d'entités et SSRF via DTD externe — aucune des trois exploitable, `resolve_entities=False` appliqué de façon homogène sur tout le parsing XML de python-docx (12.1)
- Zip-bomb DOCX par nombre d'entrées (9.6.1, ouvert depuis la session 7) : **testé empiriquement, confirmé exploitable** (contrairement au XXE) — ~1,1s CPU + ~150 Mo mémoire par requête de moins de 24 Mo, sur un service à worker unique (bloque tout le monde) et une limite mémoire conteneur de 1 Go. Corrigé (`_peek_zip_entry_count`, lecture EOCD avant tout appel à `zipfile.ZipFile()`) et revérifié à ~0s sans régression sur un document légitime (13.1, 13.2)
- Reprise de tous les points restants de 9.6 (9.6.3 à 9.6.11) — trois confirmés exploitables et corrigés, tous testés empiriquement (voir section 14) :
  - **ReDoS sur les motifs IEP/IPP** (croissance quadratique, 2,7s à 20 000 caractères) — corrigé (`0+\d{7,}` → `0\d{7,}`), équivalence fonctionnelle vérifiée (14.1)
  - **Pas de budget de temps global de détection** (~490s estimées sur un CSV à la limite légale de taille) — corrigé (`MAX_DETECTION_SECONDS=90`) (14.2)
  - **Page de révision CSV sans pagination**, vecteur distinct du précédent (419 Ko uploadés → 16,6 Mo de page HTML, sans jamais toucher le budget de détection) — corrigé (`MAX_REVIEW_ROWS=2000`), caviardage réel vérifié intact au-delà de l'aperçu (14.3)
  - Dockerfile lu directement (hypothèses de la session 7 confirmées exactes), `formation.json` confirmé absent de tout l'historique git, dépendances revérifiées sur PyPI — **`lxml` mis à jour 6.1.1 → 6.1.3** suite à la découverte d'un vrai correctif de sécurité XXE publié entre-temps (14.4)

**🟡 Décisions de risque documentées cette session :**
- Angles morts 11.4 : non corrigés, non confirmés comme exploitables — signalés pour suivi plutôt que traités dans l'urgence, cohérent avec la nature "vérification demandée", pas "chasse à la nouvelle fuite", de cette session
- 9.6.3 (validation CSV intrinsèquement faible) : rerevu, confirmé rester une limite de conception acceptée, pas un bug

**✅ Fait cette session (10) :**
- Journal de debug (9.5, LAB UNIQUEMENT) retiré entièrement — code, endpoint, config docker-compose — devenu inutile maintenant que la vérification passe par des tests réels construits en session ; c'était de toute façon prévu comme temporaire dès l'introduction. Tests unitaires revérifiés au vert.

**Reste le plus impactant, toutes sessions confondues :**
1. Un vrai travail de mesure de la qualité de détection PII sur corpus varié (section 6 de la session 5) — toujours en attente ; la session 7 (DOCX) puis le constat 10.4 (PDF) en illustrent encore l'importance
2. Certificat TLS de confiance avant toute préproduction (1.19)
3. Le pentest final (section 7)
4. Processus récurrent de veille CVE (1.13) — le correctif `lxml` (14.4) découvert cette session en est l'illustration directe : une dépendance épinglée sans veille peut rester vulnérable des semaines après la publication d'un correctif
5. Angles morts DOCX non exploités par les fixtures actuels (11.4 : miniature de document, images incrustées)

**Leçon opérationnelle de la session 7, toujours valable** : une vérification qui appelle des fonctions internes plutôt que les vrais points d'entrée peut donner une fausse confiance — un bug d'intégration réel (9.1) est passé inaperçu à travers plusieurs tests unitaires qui passaient tous, découvert seulement en testant le point d'entrée réel de bout en bout. Rejoint la leçon de la session 6 sur la vérification de l'état réel plutôt que supposé, appliquée cette fois au code plutôt qu'à l'infrastructure. **Appliquée directement en session 8** : le correctif PDF a été revérifié via `_handle_detect_pdf`/`_finalize_pdf_job` réels plutôt que par une manipulation isolée de `fitz.Document`.

**Nouvelle leçon, session 9** : sur trois points ouverts testés cette session en s'attendant a priori à les clore sans rien trouver (9.6.1 zip-bomb, 9.6.4 budget de temps, 9.6.5 pagination, 9.6.6 ReDoS), **quatre sur quatre se sont révélés être de vrais bugs exploitables**, contre un seul "non exploitable, confirmé" (le XXE, 9.6.2). Un point marqué ⏳ "à tester" dans ce document ne doit jamais être lu comme "probablement bénin" — le seul moyen de savoir est de construire le payload et de mesurer.

---

*Document de travail — à mettre à jour au fur et à mesure des corrections apportées et des points levés lors de la revue avec le RSSI/DPO.*
